"""
Refresh the current market master snapshot from a JSON file.

Input format:
[
  {
    "symbol": "005930",
    "name": "Samsung Electronics",
    "market": "KOSPI",
    "is_etf": false
  }
]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
from market import JsonUniverseMasterSource, UniverseMasterItem
from services import (
    MarketMasterRefreshItem,
    MarketMasterRefreshService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh market master snapshot from a JSON file."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to JSON array of market master items.",
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

    input_path = _resolve_path(args.input)
    if not input_path.exists():
        _fail("input", f"File not found: {input_path}")
        return 2

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path

    _section("Refresh Market Master From JSON")
    _ok("mode", settings.mode)
    _ok("input", str(input_path))
    _ok("db_path", str(db_path))

    try:
        items = JsonUniverseMasterSource(input_path).load()
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("prepare", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        repo = MarketMasterRepository(conn)
        service = MarketMasterRefreshService(
            conn=conn,
            market_master_repo=repo,
        )
        result = service.refresh_snapshot(
            items=_to_refresh_items(items),
        )

        _section("Refresh Result")
        _ok("symbol_count", str(result.symbol_count))
        _ok("refreshed_at", result.refreshed_at)

        for row in result.rows:
            print(
                f"{row.symbol} name={row.name} market={row.market} "
                f"is_etf={row.is_etf} is_attention_issue={row.is_attention_issue}"
            )

        if output_path is not None:
            payload = {
                "refreshed_at": result.refreshed_at,
                "symbol_count": result.symbol_count,
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
                    }
                    for row in result.rows
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
