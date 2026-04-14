"""
Prepare market master and first-stage universe in one run.

Flow:
1. Refresh market master snapshot from JSON, or load the current DB snapshot
2. Build raw universe inputs from KIS daily candles
3. Apply first-stage filter
4. Optionally save the universe snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisBroker
from config.loader import load_settings
from logger import setup_logging
from market import JsonUniverseMasterSource, UniverseMasterItem
from services import (
    MarketMasterRefreshItem,
    PreopenReadinessOutcome,
    PreopenReadinessService,
    UniverseBuildOutcome,
    UniverseFilterSettings,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterRepository,
    OrderRepository,
    PositionRepository,
    UniverseCandidateRepository,
)

KST = pytz.timezone("Asia/Seoul")


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh market master and prepare pre-open universe."
    )
    parser.add_argument(
        "--master-input",
        help="Path to JSON array of market master items.",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Use the current market master snapshot already stored in SQLite.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block the run if market master refreshed_at date does not match trade_date.",
    )
    parser.add_argument(
        "--min-master-count",
        type=int,
        default=None,
        help="Optional minimum allowed symbol count for market master.",
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--daily-count",
        type=int,
        default=40,
        help="How many daily candles to request per symbol. Default: 40",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=5_000,
        help="Minimum allowed close price. Default: 5000",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=200_000,
        help="Maximum allowed close price. Default: 200000",
    )
    parser.add_argument(
        "--min-avg-trade-value-20",
        type=int,
        default=100_000_000,
        help="Minimum 20-day average trade value. Default: 100000000",
    )
    parser.add_argument(
        "--write-universe",
        action="store_true",
        help="Actually replace the universe snapshot in SQLite.",
    )
    parser.add_argument(
        "--run-startup-check",
        action="store_true",
        help="Run startup safety gate after universe snapshot is saved.",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Allow startup check to continue even if unresolved orders exist.",
    )
    parser.add_argument(
        "--allow-empty-save",
        action="store_true",
        help="Allow saving an empty snapshot when accepted_count is 0.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many accepted/rejected rows to print. Default: 20",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _to_refresh_items(items: list[UniverseMasterItem]) -> list[MarketMasterRefreshItem]:
    return [
        MarketMasterRefreshItem(
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            is_managed=item.is_managed,
            is_investment_warning=item.is_investment_warning,
            is_investment_risk=item.is_investment_risk,
            is_attention_issue=item.is_attention_issue,
            is_disclosure_violation=item.is_disclosure_violation,
            is_liquidation_trade=item.is_liquidation_trade,
            is_trading_halt=item.is_trading_halt,
            is_rights_ex_date=item.is_rights_ex_date,
            is_preferred_stock=item.is_preferred_stock,
            is_etf=item.is_etf,
            is_etn=item.is_etn,
            is_spac=item.is_spac,
        )
        for item in items
    ]


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    if bool(args.master_input) == bool(args.use_db_master):
        _fail(
            "master_source",
            "Choose exactly one of --master-input or --use-db-master.",
        )
        return 2

    master_input_path = None
    if args.master_input:
        master_input_path = _resolve_path(args.master_input)
        if not master_input_path.exists():
            _fail("master_input", f"File not found: {master_input_path}")
            return 2

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path

    filter_settings = UniverseFilterSettings(
        min_price=args.min_price,
        max_price=args.max_price,
        min_avg_trade_value_20=args.min_avg_trade_value_20,
    )

    _section("Prepare Preopen Universe")
    _ok("mode", settings.mode)
    _ok("master_source", "db" if args.use_db_master else "json")
    if master_input_path is not None:
        _ok("master_input", str(master_input_path))
    _ok("trade_date", args.trade_date)
    _ok("daily_count", str(args.daily_count))
    _ok("write_universe", str(args.write_universe))
    _ok("run_startup_check", str(args.run_startup_check))
    _ok("require_same_day_master", str(args.require_same_day_master))
    _ok("min_master_count", str(args.min_master_count))
    _ok("allow_unresolved_orders", str(args.allow_unresolved_orders))
    _ok("allow_empty_save", str(args.allow_empty_save))
    _ok("db_path", str(db_path))

    master_items = None
    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
        if master_input_path is not None:
            master_items = JsonUniverseMasterSource(master_input_path).load()
    except Exception as exc:
        _fail("prepare", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        service = PreopenReadinessService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            order_repo=OrderRepository(conn),
            position_repo=PositionRepository(conn),
        )
        with KisBroker(settings) as broker:
            result = service.prepare_and_check(
                broker=broker,
                trade_date=args.trade_date,
                master_items=(
                    None
                    if master_items is None
                    else _to_refresh_items(master_items)
                ),
                use_existing_market_master=args.use_db_master,
                require_same_day_market_master=args.require_same_day_master,
                min_market_master_count=args.min_master_count,
                filter_settings=filter_settings,
                daily_count=args.daily_count,
                write_universe=args.write_universe,
                run_startup_check=args.run_startup_check,
                allow_unresolved_orders=args.allow_unresolved_orders,
                allow_empty_save=args.allow_empty_save,
            )
    except Exception as exc:
        _fail("pipeline", f"{type(exc).__name__}: {exc}")
        return 5
    finally:
        conn.close()

    build_result = result.preopen_universe_result.universe_build_result
    filter_result = build_result.filter_result

    _section("Market Master Result")
    _ok(
        "master_source",
        result.preopen_universe_result.market_master_result.source.value,
    )
    _ok(
        "symbol_count",
        str(result.preopen_universe_result.market_master_result.symbol_count),
    )
    _ok(
        "master_refreshed_at",
        result.preopen_universe_result.market_master_result.refreshed_at,
    )
    _ok(
        "master_refreshed_trade_date",
        result.preopen_universe_result.market_master_result.refreshed_trade_date,
    )
    _ok(
        "master_is_same_trade_date",
        str(result.preopen_universe_result.market_master_result.is_same_trade_date),
    )

    _section("Universe Result")
    _ok("source_item_count", str(result.preopen_universe_result.source_item_count))
    _ok("total_count", str(filter_result.total_count))
    _ok("accepted_count", str(filter_result.accepted_count))
    _ok("rejected_count", str(filter_result.rejected_count))
    _ok("build_outcome", build_result.outcome.value)

    if filter_result.accepted_items:
        _section("Accepted")
        for item in filter_result.accepted_items[: max(0, args.limit)]:
            print(
                f"{item.symbol} name={item.name} market={item.market} "
                f"close_price={item.close_price} "
                f"prev_day_trade_value={item.prev_day_trade_value} "
                f"avg_trade_value_20={item.avg_trade_value_20}"
            )

    if filter_result.rejected_items:
        _section("Rejected")
        for rejected in filter_result.rejected_items[: max(0, args.limit)]:
            reason_text = ",".join(reason.value for reason in rejected.reasons)
            item = rejected.item
            print(f"{item.symbol} name={item.name} reasons={reason_text}")

    if build_result.reason:
        _warn("build_reason", build_result.reason)
    if result.reason:
        _warn("readiness_reason", result.reason)

    if result.startup_check_result is not None:
        startup_result = result.startup_check_result
        _section("Startup Result")
        _ok("startup_outcome", startup_result.outcome.value)
        _ok("checked_at", startup_result.checked_at)
        _ok("universe_exists", str(startup_result.universe_snapshot.exists))
        _ok(
            "universe_candidate_count",
            str(startup_result.universe_snapshot.candidate_count),
        )
        _ok(
            "universe_refreshed_at",
            str(startup_result.universe_snapshot.refreshed_at),
        )
        _ok(
            "reconcile_changed_rows",
            (
                "not_run"
                if startup_result.reconcile_result is None
                else str(startup_result.reconcile_result.changed_rows)
            ),
        )
        _ok(
            "unresolved_orders",
            (
                "0"
                if startup_result.reconcile_result is None
                else str(len(startup_result.reconcile_result.unresolved_orders))
            ),
        )
        _ok("live_positions", str(len(startup_result.live_positions)))

    exit_code = 0
    if build_result.outcome == UniverseBuildOutcome.SAVED:
        _section("Refresh Result")
        _ok("candidate_count", str(build_result.refresh_result.candidate_count))
        _ok("refreshed_at", build_result.refresh_result.refreshed_at)
    elif build_result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY:
        exit_code = 4

    if result.outcome in (
        PreopenReadinessOutcome.STARTUP_SKIPPED,
        PreopenReadinessOutcome.BLOCKED,
    ):
        exit_code = 4

    if output_path is not None:
        payload = {
            "trade_date": result.trade_date,
            "readiness_outcome": result.outcome.value,
            "readiness_reason": result.reason,
            "market_master_result": {
                "source": result.preopen_universe_result.market_master_result.source.value,
                "symbol_count": result.preopen_universe_result.market_master_result.symbol_count,
                "refreshed_at": result.preopen_universe_result.market_master_result.refreshed_at,
                "refreshed_trade_date": result.preopen_universe_result.market_master_result.refreshed_trade_date,
                "is_same_trade_date": result.preopen_universe_result.market_master_result.is_same_trade_date,
                "min_master_count": args.min_master_count,
            },
            "source_item_count": result.preopen_universe_result.source_item_count,
            "universe_build_result": {
                "build_outcome": build_result.outcome.value,
                "build_reason": build_result.reason,
                "filter_result": {
                    "total_count": filter_result.total_count,
                    "accepted_count": filter_result.accepted_count,
                    "rejected_count": filter_result.rejected_count,
                },
                "refresh_result": (
                    None
                    if build_result.refresh_result is None
                    else {
                        "candidate_count": build_result.refresh_result.candidate_count,
                        "refreshed_at": build_result.refresh_result.refreshed_at,
                    }
                ),
            },
            "startup_check_result": (
                None
                if result.startup_check_result is None
                else {
                    "outcome": result.startup_check_result.outcome.value,
                    "checked_at": result.startup_check_result.checked_at,
                    "trade_date": result.startup_check_result.trade_date,
                    "reason": result.startup_check_result.reason,
                    "universe_snapshot": {
                        "exists": result.startup_check_result.universe_snapshot.exists,
                        "candidate_count": result.startup_check_result.universe_snapshot.candidate_count,
                        "refreshed_at": result.startup_check_result.universe_snapshot.refreshed_at,
                    },
                    "reconcile_changed_rows": (
                        None
                        if result.startup_check_result.reconcile_result is None
                        else result.startup_check_result.reconcile_result.changed_rows
                    ),
                    "unresolved_orders": (
                        []
                        if result.startup_check_result.reconcile_result is None
                        else [
                            {
                                "client_order_id": row.client_order_id,
                                "status": row.status.value,
                                "symbol": row.symbol,
                                "side": row.side,
                                "qty": row.qty,
                                "kis_order_no": row.kis_order_no,
                            }
                            for row in result.startup_check_result.reconcile_result.unresolved_orders
                        ]
                    ),
                    "live_positions": [
                        {
                            "symbol": row.symbol,
                            "qty": row.qty,
                            "avg_price": row.avg_price,
                            "updated_at": row.updated_at,
                        }
                        for row in result.startup_check_result.live_positions
                    ],
                }
            ),
        }
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
