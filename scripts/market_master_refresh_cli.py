"""Shared CLI helpers for market master refresh scripts."""

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
from market import (
    SUPPORTED_UNIVERSE_MASTER_FORMATS,
    load_universe_master_items,
    resolve_universe_master_format,
)
from services import (
    MarketMasterImportService,
    MarketMasterValidationService,
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


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def build_parser(
    *,
    description: str,
    input_help: str,
    include_input_format: bool,
    default_input_format: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--input",
        required=True,
        help=input_help,
    )
    if include_input_format:
        parser.add_argument(
            "--input-format",
            default=default_input_format,
            choices=SUPPORTED_UNIVERSE_MASTER_FORMATS,
            help="Market master input format. Default: auto",
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
    return parser


def run_market_master_refresh_cli(
    *,
    title: str,
    description: str,
    input_help: str,
    include_input_format: bool = False,
    forced_input_format: str | None = None,
) -> int:
    parser = build_parser(
        description=description,
        input_help=input_help,
        include_input_format=include_input_format,
        default_input_format="auto" if include_input_format else "auto",
    )
    args = parser.parse_args()

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

    requested_input_format = (
        forced_input_format
        if forced_input_format is not None
        else getattr(args, "input_format", "auto")
    )
    try:
        resolved_input_format = resolve_universe_master_format(
            input_path,
            source_format=requested_input_format,
        )
    except Exception as exc:
        _fail("input_format", f"{type(exc).__name__}: {exc}")
        return 2

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path

    _section(title)
    _ok("mode", settings.mode)
    _ok("input", str(input_path))
    _ok("input_format", resolved_input_format)
    _ok("min_symbol_count", str(args.min_symbol_count))
    _ok("required_markets", str(args.required_market or []))
    _ok(
        "allow_validation_failures",
        str(args.allow_validation_failures),
    )
    _ok("db_path", str(db_path))

    try:
        items = load_universe_master_items(
            input_path,
            source_format=resolved_input_format,
        )
        validation_result = MarketMasterValidationService().validate_items(
            items=items,
            min_symbol_count=args.min_symbol_count,
            required_markets=args.required_market,
        )
    except Exception as exc:
        _fail("validation", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Validation Result")
    _ok("total_count", str(validation_result.total_count))
    _ok("is_valid", str(validation_result.is_valid))

    if validation_result.market_counts:
        _section("Market Counts")
        for row in validation_result.market_counts:
            print(f"{row.name} count={row.count}")

    positive_flag_counts = [
        row
        for row in validation_result.flag_counts
        if row.count > 0
    ]
    if positive_flag_counts:
        _section("Flag Counts")
        for row in positive_flag_counts:
            print(f"{row.name} count={row.count}")

    if validation_result.warnings:
        for warning in validation_result.warnings:
            _fail("validation_warning", warning)

    if validation_result.warnings and not args.allow_validation_failures:
        return 4

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("prepare", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        service = MarketMasterImportService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
        )
        result = service.import_items(
            items=items,
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
                "input_format": resolved_input_format,
                "validation_result": {
                    "total_count": validation_result.total_count,
                    "is_valid": validation_result.is_valid,
                    "market_counts": [
                        {
                            "name": row.name,
                            "count": row.count,
                        }
                        for row in validation_result.market_counts
                    ],
                    "flag_counts": [
                        {
                            "name": row.name,
                            "count": row.count,
                        }
                        for row in validation_result.flag_counts
                    ],
                    "warnings": list(validation_result.warnings),
                },
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
