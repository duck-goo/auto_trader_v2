"""
Show the current market master snapshot stored in SQLite.
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

from config.loader import load_settings
from logger import setup_logging
from services import (
    MarketMasterHealthOutcome,
    MarketMasterHealthService,
    MarketMasterQueryService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository

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
    parser = argparse.ArgumentParser(description="Show current market master.")
    parser.add_argument(
        "--trade-date",
        default=None,
        help="Optional trade date YYYY-MM-DD for health checks. Default: today in KST when require_same_day is set.",
    )
    parser.add_argument(
        "--require-same-day",
        action="store_true",
        help="Require market master refreshed_at date to match trade_date.",
    )
    parser.add_argument(
        "--min-symbol-count",
        type=int,
        default=None,
        help="Optional minimum allowed symbol count.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many rows to print. Default: 20",
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

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path
    trade_date = args.trade_date
    if trade_date is None and args.require_same_day:
        trade_date = datetime.now(KST).strftime("%Y-%m-%d")

    _section("Show Market Master")
    _ok("mode", settings.mode)
    _ok("db_path", str(db_path))
    _ok("trade_date", str(trade_date))
    _ok("require_same_day", str(args.require_same_day))
    _ok("min_symbol_count", str(args.min_symbol_count))
    _ok("limit", str(args.limit))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        repo = MarketMasterRepository(conn)
        query_service = MarketMasterQueryService(market_master_repo=repo)
        result = query_service.get_snapshot()
        health_result = MarketMasterHealthService(
            query_service=query_service,
        ).check_snapshot(
            trade_date=trade_date,
            require_same_trade_date=args.require_same_day,
            min_symbol_count=args.min_symbol_count,
        )

        _section("Snapshot Result")
        _ok("exists", str(result.exists))
        _ok("symbol_count", str(result.symbol_count))
        _ok("refreshed_at", str(result.refreshed_at))
        _ok("health_outcome", health_result.outcome.value)
        _ok("refreshed_trade_date", str(health_result.refreshed_trade_date))
        _ok("is_same_trade_date", str(health_result.is_same_trade_date))
        _ok("meets_min_symbol_count", str(health_result.meets_min_symbol_count))

        if not result.exists:
            _warn("snapshot", "No market master snapshot found.")
        else:
            if health_result.reason:
                _warn("health_reason", health_result.reason)
            _section("Rows")
            visible_rows = result.rows[: max(0, args.limit)]
            for row in visible_rows:
                print(
                    f"{row.symbol} name={row.name} market={row.market} "
                    f"is_etf={row.is_etf} is_attention_issue={row.is_attention_issue}"
                )

            hidden_count = len(result.rows) - len(visible_rows)
            if hidden_count > 0:
                _warn("rows", f"{hidden_count} more rows omitted from console output.")

        if output_path is not None:
            payload = {
                "exists": result.exists,
                "symbol_count": result.symbol_count,
                "refreshed_at": result.refreshed_at,
                "health_outcome": health_result.outcome.value,
                "health_reason": health_result.reason,
                "refreshed_trade_date": health_result.refreshed_trade_date,
                "required_trade_date": health_result.required_trade_date,
                "is_same_trade_date": health_result.is_same_trade_date,
                "min_symbol_count": health_result.min_symbol_count,
                "meets_min_symbol_count": health_result.meets_min_symbol_count,
                "rows": [
                    {
                        "symbol": row.symbol,
                        "name": row.name,
                        "market": row.market,
                        "is_managed": row.is_managed,
                        "is_investment_warning": row.is_investment_warning,
                        "is_investment_risk": row.is_investment_risk,
                        "is_attention_issue": row.is_attention_issue,
                        "is_disclosure_violation": row.is_disclosure_violation,
                        "is_liquidation_trade": row.is_liquidation_trade,
                        "is_trading_halt": row.is_trading_halt,
                        "is_rights_ex_date": row.is_rights_ex_date,
                        "is_preferred_stock": row.is_preferred_stock,
                        "is_etf": row.is_etf,
                        "is_etn": row.is_etn,
                        "is_spac": row.is_spac,
                        "refreshed_at": row.refreshed_at,
                    }
                    for row in result.rows
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0 if health_result.outcome == MarketMasterHealthOutcome.READY else 4
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
