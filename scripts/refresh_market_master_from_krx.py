"""Refresh the market master snapshot from KRX listed-stock finder data."""

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
from market import KrxListedStockSource
from services import MarketMasterImportService, MarketMasterValidationService
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
        description="Refresh market master from KRX listed-stock finder data."
    )
    parser.add_argument(
        "--market",
        action="append",
        default=None,
        choices=("KOSPI", "KOSDAQ"),
        help="Market to include. Repeatable. Default: KOSPI and KOSDAQ.",
    )
    parser.add_argument(
        "--min-symbol-count",
        type=int,
        default=None,
        help="Optional minimum allowed item count before saving.",
    )
    parser.add_argument(
        "--required-market",
        action="append",
        default=None,
        help="Optional required market name. Repeatable.",
    )
    parser.add_argument(
        "--allow-validation-failures",
        action="store_true",
        help="Allow DB refresh even when validation warnings exist.",
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
    markets = None if args.market is None else set(args.market)
    required_markets = args.required_market or args.market or ["KOSPI", "KOSDAQ"]

    _section("Refresh Market Master From KRX")
    _ok("mode", settings.mode)
    _ok("markets", str(sorted(markets) if markets else ["KOSPI", "KOSDAQ"]))
    _ok("min_symbol_count", str(args.min_symbol_count))
    _ok("required_markets", str(required_markets))
    _ok("allow_validation_failures", str(args.allow_validation_failures))
    _ok("db_path", str(db_path))

    try:
        items = KrxListedStockSource(markets=markets).load()
        validation_result = MarketMasterValidationService().validate_items(
            items=items,
            min_symbol_count=args.min_symbol_count,
            required_markets=required_markets,
        )
    except Exception as exc:
        _fail("load", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Validation Result")
    _ok("total_count", str(validation_result.total_count))
    _ok("is_valid", str(validation_result.is_valid))
    for row in validation_result.market_counts:
        _ok(f"market:{row.name}", str(row.count))
    for row in validation_result.flag_counts:
        if row.count > 0:
            _ok(f"flag:{row.name}", str(row.count))

    if validation_result.warnings:
        for warning in validation_result.warnings:
            _fail("validation_warning", warning)
        if not args.allow_validation_failures:
            if output_path is not None:
                _save_json(
                    output_path,
                    {
                        "source": "KRX_LISTED_STOCK_FINDER",
                        "saved": False,
                        "validation_result": _validation_payload(validation_result),
                    },
                )
                _ok("json_saved", str(output_path))
            return 4

    conn = None
    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
        result = MarketMasterImportService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
        ).import_items(items=items)
    except Exception as exc:
        _fail("refresh", f"{type(exc).__name__}: {exc}")
        return 5
    finally:
        if conn is not None:
            conn.close()

    _section("Refresh Result")
    _ok("symbol_count", str(result.symbol_count))
    _ok("refreshed_at", result.refreshed_at)

    if output_path is not None:
        _save_json(
            output_path,
            {
                "source": "KRX_LISTED_STOCK_FINDER",
                "saved": True,
                "validation_result": _validation_payload(validation_result),
                "refreshed_at": result.refreshed_at,
                "symbol_count": result.symbol_count,
            },
        )
        _ok("json_saved", str(output_path))

    return 0


def _validation_payload(validation_result) -> dict[str, Any]:
    return {
        "total_count": validation_result.total_count,
        "is_valid": validation_result.is_valid,
        "market_counts": [
            {"name": row.name, "count": row.count}
            for row in validation_result.market_counts
        ],
        "flag_counts": [
            {"name": row.name, "count": row.count}
            for row in validation_result.flag_counts
        ],
        "warnings": list(validation_result.warnings),
    }


if __name__ == "__main__":
    raise SystemExit(main())
