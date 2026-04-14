"""
Build first-stage universe snapshot from market master data and KIS daily candles.

Flow:
1. Load market master from JSON or SQLite
2. Fetch recent daily candles from KIS for each symbol
3. Build raw universe inputs
4. Apply first-stage filter
5. Optionally save snapshot into SQLite

Safety:
- default is dry-run
- any symbol-level KIS failure aborts the whole run
- empty accepted result is not saved unless --allow-empty-save is passed
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
from market import (
    JsonUniverseMasterSource,
    KisDailyUniverseSource,
    UniverseMasterItem,
    UniverseSourceItem,
)
from services import (
    MarketMasterQueryService,
    UniverseBuildOutcome,
    UniverseBuildService,
    UniverseFilterInput,
    UniverseFilterService,
    UniverseFilterSettings,
    UniverseRefreshService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterRepository,
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
        description="Build first-stage universe from KIS daily candles."
    )
    parser.add_argument(
        "--master-input",
        default=None,
        help="Path to JSON array of universe master items.",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Load the current market master snapshot from SQLite.",
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
        "--write",
        action="store_true",
        help="Actually replace the universe snapshot in SQLite.",
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


def _rows_to_master_items(rows) -> list[UniverseMasterItem]:
    return [
        UniverseMasterItem(
            symbol=row.symbol,
            name=row.name,
            market=row.market,
            is_managed=row.is_managed,
            is_investment_warning=row.is_investment_warning,
            is_investment_risk=row.is_investment_risk,
            is_attention_issue=row.is_attention_issue,
            is_disclosure_violation=row.is_disclosure_violation,
            is_liquidation_trade=row.is_liquidation_trade,
            is_trading_halt=row.is_trading_halt,
            is_rights_ex_date=row.is_rights_ex_date,
            is_preferred_stock=row.is_preferred_stock,
            is_etf=row.is_etf,
            is_etn=row.is_etn,
            is_spac=row.is_spac,
        )
        for row in rows
    ]


def _to_filter_inputs(items: list[UniverseSourceItem]) -> list[UniverseFilterInput]:
    return [
        UniverseFilterInput(
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            close_price=item.close_price,
            prev_day_trade_value=item.prev_day_trade_value,
            avg_trade_value_20=item.avg_trade_value_20,
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

    _section("Build Universe From KIS")
    _ok("mode", settings.mode)
    _ok("master_source", "db" if args.use_db_master else "json")
    if master_input_path is not None:
        _ok("master_input", str(master_input_path))
    _ok("trade_date", args.trade_date)
    _ok("daily_count", str(args.daily_count))
    _ok("write", str(args.write))
    _ok("allow_empty_save", str(args.allow_empty_save))
    _ok("db_path", str(db_path))

    conn = None
    master_snapshot = None
    master_items: list[UniverseMasterItem] = []
    source_items: list[UniverseSourceItem] = []
    try:
        if args.use_db_master or args.write:
            run_migrations(db_path)
            conn = get_connection(
                db_path,
                busy_timeout_ms=settings.db_busy_timeout_ms,
            )

        if master_input_path is not None:
            master_items = JsonUniverseMasterSource(master_input_path).load()
        else:
            if conn is None:
                raise RuntimeError("DB connection is required for --use-db-master.")
            market_master_repo = MarketMasterRepository(conn)
            master_snapshot = MarketMasterQueryService(
                market_master_repo=market_master_repo,
            ).get_snapshot()
            if not master_snapshot.exists:
                _fail(
                    "master_source",
                    "No market master snapshot found in SQLite.",
                )
                return 4
            master_items = _rows_to_master_items(master_snapshot.rows)

        if args.write:
            repo = UniverseCandidateRepository(conn)
            refresh_service = UniverseRefreshService(
                conn=conn,
                universe_repo=repo,
            )
        else:
            refresh_service = None

        with KisBroker(settings) as broker:
            source = KisDailyUniverseSource(
                broker=broker,
                master_items=master_items,
                trade_date=args.trade_date,
                daily_count=args.daily_count,
            )
            source_items = source.load()

        build_service = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=refresh_service,
        )
        build_result = build_service.build_snapshot(
            trade_date=args.trade_date,
            items=_to_filter_inputs(source_items),
            settings=filter_settings,
            write=args.write,
            allow_empty_save=args.allow_empty_save,
        )
    except Exception as exc:
        _fail("build", f"{type(exc).__name__}: {exc}")
        return 5
    finally:
        if conn is not None:
            conn.close()

    filter_result = build_result.filter_result

    _section("Source Result")
    _ok("master_count", str(len(master_items)))
    if master_snapshot is not None:
        _ok("master_refreshed_at", str(master_snapshot.refreshed_at))
    _ok("source_count", str(len(source_items)))

    _section("Filter Result")
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

    exit_code = 0
    if build_result.outcome == UniverseBuildOutcome.SAVED:
        _section("Refresh Result")
        _ok("candidate_count", str(build_result.refresh_result.candidate_count))
        _ok("refreshed_at", build_result.refresh_result.refreshed_at)
    elif build_result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY:
        exit_code = 4

    if output_path is not None:
        payload = {
            "trade_date": build_result.trade_date,
            "build_outcome": build_result.outcome.value,
            "build_reason": build_result.reason,
            "source_result": {
                "master_source": "db" if args.use_db_master else "json",
                "master_refreshed_at": (
                    None if master_snapshot is None else master_snapshot.refreshed_at
                ),
                "master_count": len(master_items),
                "source_count": len(source_items),
                "items": [
                    {
                        "symbol": item.symbol,
                        "name": item.name,
                        "market": item.market,
                        "close_price": item.close_price,
                        "prev_day_trade_value": item.prev_day_trade_value,
                        "avg_trade_value_20": item.avg_trade_value_20,
                    }
                    for item in source_items
                ],
            },
            "filter_result": {
                "total_count": filter_result.total_count,
                "accepted_count": filter_result.accepted_count,
                "rejected_count": filter_result.rejected_count,
                "accepted_items": [
                    {
                        "symbol": item.symbol,
                        "name": item.name,
                        "market": item.market,
                        "close_price": item.close_price,
                        "prev_day_trade_value": item.prev_day_trade_value,
                        "avg_trade_value_20": item.avg_trade_value_20,
                    }
                    for item in filter_result.accepted_items
                ],
                "rejected_items": [
                    {
                        "symbol": rejected.item.symbol,
                        "name": rejected.item.name,
                        "market": rejected.item.market,
                        "close_price": rejected.item.close_price,
                        "prev_day_trade_value": rejected.item.prev_day_trade_value,
                        "avg_trade_value_20": rejected.item.avg_trade_value_20,
                        "reasons": [reason.value for reason in rejected.reasons],
                    }
                    for rejected in filter_result.rejected_items
                ],
            },
            "refresh_result": (
                None
                if build_result.refresh_result is None
                else {
                    "candidate_count": build_result.refresh_result.candidate_count,
                    "refreshed_at": build_result.refresh_result.refreshed_at,
                }
            ),
        }
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
