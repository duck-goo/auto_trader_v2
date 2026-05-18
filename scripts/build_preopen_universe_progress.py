"""
Build the pre-open universe with resumable per-symbol progress.

This script is for full-market KIS runs where thousands of daily-candle
requests can take a while under mock-account rate limits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
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
    KisDailyUniverseSource,
    NaverDailyUniverseSource,
    UniverseMasterItem,
)
from services import (
    MarketMasterHealthOutcome,
    MarketMasterHealthService,
    MarketMasterQueryService,
    MarketMasterValidationService,
    UniverseBuildOutcome,
    UniverseBuildService,
    UniverseFilterInput,
    UniverseFilterService,
    UniverseFilterSettings,
    UniverseRefreshService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository, UniverseCandidateRepository

KST = pytz.timezone("Asia/Seoul")


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""), flush=True)


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""), flush=True)


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""), flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build pre-open universe with resumable progress."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block if market master refreshed date does not match trade_date.",
    )
    parser.add_argument(
        "--min-master-count",
        type=int,
        default=None,
        help="Optional minimum allowed market master count.",
    )
    parser.add_argument(
        "--required-market",
        action="append",
        default=[],
        help="Required market name. Repeatable.",
    )
    parser.add_argument(
        "--daily-count",
        type=int,
        default=40,
        help="How many daily candles to request per symbol. Default: 40.",
    )
    parser.add_argument(
        "--daily-source",
        choices=("kis", "naver"),
        default="kis",
        help="Daily candle source. Default: kis.",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=5_000,
        help="Minimum close price. Default: 5000.",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=200_000,
        help="Maximum close price. Default: 200000.",
    )
    parser.add_argument(
        "--min-avg-trade-value-20",
        type=int,
        default=100_000_000,
        help="Minimum 20-day average trade value. Default: 100000000.",
    )
    parser.add_argument(
        "--write-universe",
        action="store_true",
        help="Replace the SQLite universe snapshot after filtering.",
    )
    parser.add_argument(
        "--allow-empty-save",
        action="store_true",
        help="Allow replacing the SQLite snapshot with an empty result.",
    )
    parser.add_argument(
        "--skip-symbol-errors",
        action="store_true",
        help="Record bad symbols and continue instead of aborting.",
    )
    parser.add_argument(
        "--symbol-delay-seconds",
        type=float,
        default=0.7,
        help="Extra delay after each symbol request. Default: 0.7.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=10.0,
        help="Per-symbol HTTP timeout for non-KIS sources. Default: 10.",
    )
    parser.add_argument(
        "--source-cache",
        default=None,
        help="JSONL cache for successful source rows.",
    )
    parser.add_argument(
        "--skip-cache",
        default=None,
        help="JSONL cache for skipped symbols.",
    )
    parser.add_argument(
        "--progress-output",
        default=None,
        help="JSON progress file updated after each symbol.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Final JSON output path.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=None,
        help="Optional cap for smoke runs.",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="Optional master-row offset for smoke/chunk runs. Default: 0.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing cache files and rebuild them.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many accepted/rejected/skipped rows to include in output.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _default_ops_path(trade_date: str, name: str) -> Path:
    return PROJECT_ROOT / "data" / "ops" / trade_date / name


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _load_jsonl_by_symbol(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row {line_number} must be an object: {path}")
            symbol = payload.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError(f"JSONL row {line_number} is missing symbol: {path}")
            rows[symbol] = payload
    return rows


def _clear_cache(path: Path) -> None:
    if path.exists():
        path.unlink()


def _row_to_master_item(row) -> UniverseMasterItem:
    return UniverseMasterItem(
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


def _source_item_to_payload(item) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "market": item.market,
        "close_price": item.close_price,
        "prev_day_trade_value": item.prev_day_trade_value,
        "avg_trade_value_20": item.avg_trade_value_20,
        "is_managed": item.is_managed,
        "is_investment_warning": item.is_investment_warning,
        "is_investment_risk": item.is_investment_risk,
        "is_attention_issue": item.is_attention_issue,
        "is_disclosure_violation": item.is_disclosure_violation,
        "is_liquidation_trade": item.is_liquidation_trade,
        "is_trading_halt": item.is_trading_halt,
        "is_rights_ex_date": item.is_rights_ex_date,
        "is_preferred_stock": item.is_preferred_stock,
        "is_etf": item.is_etf,
        "is_etn": item.is_etn,
        "is_spac": item.is_spac,
    }


def _payload_to_filter_input(payload: dict[str, Any]) -> UniverseFilterInput:
    return UniverseFilterInput(
        symbol=str(payload["symbol"]),
        name=str(payload["name"]),
        market=str(payload["market"]),
        close_price=int(payload["close_price"]),
        prev_day_trade_value=int(payload["prev_day_trade_value"]),
        avg_trade_value_20=int(payload["avg_trade_value_20"]),
        is_managed=bool(payload.get("is_managed", False)),
        is_investment_warning=bool(payload.get("is_investment_warning", False)),
        is_investment_risk=bool(payload.get("is_investment_risk", False)),
        is_attention_issue=bool(payload.get("is_attention_issue", False)),
        is_disclosure_violation=bool(payload.get("is_disclosure_violation", False)),
        is_liquidation_trade=bool(payload.get("is_liquidation_trade", False)),
        is_trading_halt=bool(payload.get("is_trading_halt", False)),
        is_rights_ex_date=bool(payload.get("is_rights_ex_date", False)),
        is_preferred_stock=bool(payload.get("is_preferred_stock", False)),
        is_etf=bool(payload.get("is_etf", False)),
        is_etn=bool(payload.get("is_etn", False)),
        is_spac=bool(payload.get("is_spac", False)),
    )


def _skipped_item_to_payload(item) -> dict[str, Any]:
    return asdict(item)


def _select_master_items(
    rows,
    *,
    start_offset: int,
    max_symbols: int | None,
) -> list[UniverseMasterItem]:
    if start_offset < 0:
        raise ValueError(f"start_offset must be >= 0: {start_offset}")
    if max_symbols is not None and max_symbols < 1:
        raise ValueError(f"max_symbols must be >= 1 when provided: {max_symbols}")
    selected_rows = rows[start_offset:]
    if max_symbols is not None:
        selected_rows = selected_rows[:max_symbols]
    return [_row_to_master_item(row) for row in selected_rows]


def _progress_payload(
    *,
    trade_date: str,
    status: str,
    total_selected_count: int,
    processed_count: int,
    success_count: int,
    skipped_count: int,
    cached_success_count: int,
    cached_skipped_count: int,
    latest_symbol: str | None,
    message: str | None,
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "status": status,
        "updated_at": datetime.now(KST).isoformat(),
        "total_selected_count": total_selected_count,
        "processed_count": processed_count,
        "remaining_count": max(0, total_selected_count - processed_count),
        "success_count": success_count,
        "skipped_count": skipped_count,
        "cached_success_count": cached_success_count,
        "cached_skipped_count": cached_skipped_count,
        "latest_symbol": latest_symbol,
        "message": message,
    }


def _build_final_payload(
    *,
    trade_date: str,
    master_count: int,
    selected_count: int,
    source_cache: Path,
    skip_cache: Path,
    progress_output: Path,
    output_path: Path | None,
    build_result,
    skipped_payloads: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    filter_result = build_result.filter_result
    return {
        "trade_date": trade_date,
        "master_count": master_count,
        "selected_count": selected_count,
        "source_cache": str(source_cache),
        "skip_cache": str(skip_cache),
        "progress_output": str(progress_output),
        "output": None if output_path is None else str(output_path),
        "build_outcome": build_result.outcome.value,
        "build_reason": build_result.reason,
        "source_item_count": filter_result.total_count,
        "source_skipped_count": len(skipped_payloads),
        "filter_result": {
            "total_count": filter_result.total_count,
            "accepted_count": filter_result.accepted_count,
            "rejected_count": filter_result.rejected_count,
            "accepted_items": [
                _source_item_to_payload(item)
                for item in filter_result.accepted_items[: max(0, limit)]
            ],
            "rejected_items": [
                {
                    "symbol": row.item.symbol,
                    "name": row.item.name,
                    "market": row.item.market,
                    "close_price": row.item.close_price,
                    "prev_day_trade_value": row.item.prev_day_trade_value,
                    "avg_trade_value_20": row.item.avg_trade_value_20,
                    "reasons": [reason.value for reason in row.reasons],
                }
                for row in filter_result.rejected_items[: max(0, limit)]
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
        "skipped_items": skipped_payloads[: max(0, limit)],
    }


def main() -> int:
    args = _parse_args()
    source_cache = _resolve_path(args.source_cache) if args.source_cache else (
        _default_ops_path(args.trade_date, "preopen_universe.source_items.jsonl")
    )
    skip_cache = _resolve_path(args.skip_cache) if args.skip_cache else (
        _default_ops_path(args.trade_date, "preopen_universe.skipped_items.jsonl")
    )
    progress_output = (
        _resolve_path(args.progress_output)
        if args.progress_output
        else _default_ops_path(args.trade_date, "preopen_universe.progress.json")
    )
    output_path = _resolve_path(args.output) if args.output else None

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path

    _section("Build Preopen Universe Progress")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))
    _ok("source_cache", str(source_cache))
    _ok("skip_cache", str(skip_cache))
    _ok("progress_output", str(progress_output))
    _ok("write_universe", str(args.write_universe))
    _ok("skip_symbol_errors", str(args.skip_symbol_errors))
    _ok("daily_source", str(args.daily_source))
    _ok("symbol_delay_seconds", str(args.symbol_delay_seconds))

    if args.no_resume:
        _clear_cache(source_cache)
        _clear_cache(skip_cache)

    conn = None
    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
        market_repo = MarketMasterRepository(conn)
        query_service = MarketMasterQueryService(market_master_repo=market_repo)
        snapshot = query_service.get_snapshot()
        health = MarketMasterHealthService(
            query_service=query_service,
        ).check_snapshot(
            trade_date=args.trade_date,
            require_same_trade_date=args.require_same_day_master,
            min_symbol_count=args.min_master_count,
        )
        validation = MarketMasterValidationService().validate_items(
            items=[_row_to_master_item(row) for row in snapshot.rows],
            min_symbol_count=args.min_master_count,
            required_markets=args.required_market,
        )

        if health.outcome != MarketMasterHealthOutcome.READY:
            raise RuntimeError(health.reason or "Market master is not ready.")
        if validation.warnings:
            raise RuntimeError(
                "Market master validation failed: "
                + "; ".join(validation.warnings)
            )

        selected_master_items = _select_master_items(
            snapshot.rows,
            start_offset=args.start_offset,
            max_symbols=args.max_symbols,
        )
        source_payloads = _load_jsonl_by_symbol(source_cache)
        skipped_payloads_by_symbol = _load_jsonl_by_symbol(skip_cache)

        processed_count = 0
        success_count = 0
        skipped_count = 0

        broker_context = KisBroker(settings) if args.daily_source == "kis" else None
        broker = None
        if broker_context is not None:
            broker = broker_context.__enter__()
        try:
            for index, master_item in enumerate(selected_master_items, start=1):
                if (
                    master_item.symbol in source_payloads
                    or master_item.symbol in skipped_payloads_by_symbol
                ):
                    processed_count += 1
                    continue

                _ok(
                    "symbol",
                    (
                        f"{index}/{len(selected_master_items)} "
                        f"{master_item.symbol} {master_item.name}"
                    ),
                )
                if args.daily_source == "kis":
                    source = KisDailyUniverseSource(
                        broker=broker,
                        master_items=[master_item],
                        trade_date=args.trade_date,
                        daily_count=args.daily_count,
                        skip_symbol_errors=args.skip_symbol_errors,
                    )
                else:
                    source = NaverDailyUniverseSource(
                        master_items=[master_item],
                        trade_date=args.trade_date,
                        daily_count=args.daily_count,
                        timeout_seconds=args.request_timeout_seconds,
                        skip_symbol_errors=args.skip_symbol_errors,
                    )
                items = source.load()
                if items:
                    payload = _source_item_to_payload(items[0])
                    _append_jsonl(source_cache, payload)
                    source_payloads[master_item.symbol] = payload
                    success_count += 1
                for skipped in source.skipped_items:
                    payload = _skipped_item_to_payload(skipped)
                    _append_jsonl(skip_cache, payload)
                    skipped_payloads_by_symbol[skipped.symbol] = payload
                    skipped_count += 1

                processed_count += 1
                _save_json(
                    progress_output,
                    _progress_payload(
                        trade_date=args.trade_date,
                        status="RUNNING",
                        total_selected_count=len(selected_master_items),
                        processed_count=processed_count,
                        success_count=success_count,
                        skipped_count=skipped_count,
                        cached_success_count=len(source_payloads),
                        cached_skipped_count=len(skipped_payloads_by_symbol),
                        latest_symbol=master_item.symbol,
                        message=None,
                    ),
                )
                if args.symbol_delay_seconds > 0:
                    time.sleep(args.symbol_delay_seconds)
        finally:
            if broker_context is not None:
                broker_context.__exit__(None, None, None)

        filter_inputs = [
            _payload_to_filter_input(payload)
            for payload in sorted(
                source_payloads.values(),
                key=lambda row: str(row["symbol"]),
            )
        ]
        build_result = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=UniverseRefreshService(
                conn=conn,
                universe_repo=UniverseCandidateRepository(conn),
            ),
        ).build_snapshot(
            trade_date=args.trade_date,
            items=filter_inputs,
            settings=UniverseFilterSettings(
                min_price=args.min_price,
                max_price=args.max_price,
                min_avg_trade_value_20=args.min_avg_trade_value_20,
            ),
            write=args.write_universe,
            allow_empty_save=args.allow_empty_save,
        )

        final_payload = _build_final_payload(
            trade_date=args.trade_date,
            master_count=snapshot.symbol_count,
            selected_count=len(selected_master_items),
            source_cache=source_cache,
            skip_cache=skip_cache,
            progress_output=progress_output,
            output_path=output_path,
            build_result=build_result,
            skipped_payloads=list(skipped_payloads_by_symbol.values()),
            limit=args.limit,
        )
        _save_json(
            progress_output,
            _progress_payload(
                trade_date=args.trade_date,
                status="COMPLETED",
                total_selected_count=len(selected_master_items),
                processed_count=len(selected_master_items),
                success_count=success_count,
                skipped_count=skipped_count,
                cached_success_count=len(source_payloads),
                cached_skipped_count=len(skipped_payloads_by_symbol),
                latest_symbol=None,
                message=f"build_outcome={build_result.outcome.value}",
            ),
        )
        if output_path is not None:
            _save_json(output_path, final_payload)

        _section("Result")
        _ok("build_outcome", build_result.outcome.value)
        _ok("source_item_count", str(final_payload["source_item_count"]))
        _ok("source_skipped_count", str(final_payload["source_skipped_count"]))
        _ok("accepted_count", str(final_payload["filter_result"]["accepted_count"]))
        _ok("rejected_count", str(final_payload["filter_result"]["rejected_count"]))
        if build_result.refresh_result is not None:
            _ok("saved_candidate_count", str(build_result.refresh_result.candidate_count))
        if output_path is not None:
            _ok("json_saved", str(output_path))

        if build_result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY:
            return 4
        return 0
    except Exception as exc:
        _fail("build", f"{type(exc).__name__}: {exc}")
        _save_json(
            progress_output,
            _progress_payload(
                trade_date=args.trade_date,
                status="FAILED",
                total_selected_count=0,
                processed_count=0,
                success_count=0,
                skipped_count=0,
                cached_success_count=0,
                cached_skipped_count=0,
                latest_symbol=None,
                message=f"{type(exc).__name__}: {exc}",
            ),
        )
        return 5
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
