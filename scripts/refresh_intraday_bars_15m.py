"""
Refresh persisted same-day 15-minute bars for live positions.

Safety:
- same-day only, because KIS stock minute backfill is same-day only
- default is preview
- write mode skips symbols whose new completed bar count regresses
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
from services import IntradayBar15mRefreshService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import IntradayBar15mRepository, PositionRepository

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
        description="Refresh same-day 15-minute bars for live positions."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--bar-minutes",
        type=int,
        default=15,
        help="Fixed intraday bar size. Default: 15",
    )
    parser.add_argument(
        "--end-time",
        default=None,
        help="Optional KIS backfill end time HHMMSS. Default: now in KST",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually persist the refreshed bars into SQLite.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many symbol rows to print. Default: 20",
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

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None

    _section("Refresh Intraday Bars 15m")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("bar_minutes", str(args.bar_minutes))
    _ok("end_time", str(args.end_time))
    _ok("write", str(args.write))
    _ok("db_path", str(db_path))

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
        with KisBroker(settings) as broker:
            service = IntradayBar15mRefreshService(
                broker=broker,
                conn=conn,
                position_repo=PositionRepository(conn),
                intraday_bar_repo=IntradayBar15mRepository(conn),
            )
            result = service.refresh_live_positions(
                trade_date=args.trade_date,
                end_time=args.end_time,
                bar_minutes=args.bar_minutes,
                write=args.write,
            )

        _section("Refresh Result")
        _ok("refreshed_at", result.refreshed_at)
        _ok("position_count", str(result.position_count))
        _ok("candidate_count", str(result.candidate_count))
        _ok("preview_ready_count", str(result.preview_ready_count))
        _ok("refreshed_symbol_count", str(result.refreshed_symbol_count))
        _ok("skipped_count", str(result.skipped_count))
        _ok("failed_count", str(result.failed_count))

        visible = result.candidates[: max(0, args.limit)]
        if visible:
            _section("Symbols")
            for row in visible:
                print(
                    f"{row.symbol} outcome={row.outcome.value} "
                    f"existing={row.existing_bar_count} "
                    f"completed={row.completed_bar_count} "
                    f"stored={row.stored_bar_count} "
                    f"minute_candles={row.minute_candle_count}"
                )
                if row.reason:
                    print(f"  reason={row.reason}")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "refreshed_at": result.refreshed_at,
                "position_count": result.position_count,
                "candidate_count": result.candidate_count,
                "preview_ready_count": result.preview_ready_count,
                "refreshed_symbol_count": result.refreshed_symbol_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": row.symbol,
                        "qty": row.qty,
                        "avg_price": row.avg_price,
                        "minute_candle_count": row.minute_candle_count,
                        "existing_bar_count": row.existing_bar_count,
                        "completed_bar_count": row.completed_bar_count,
                        "stored_bar_count": row.stored_bar_count,
                        "outcome": row.outcome.value,
                        "reason": row.reason,
                    }
                    for row in result.candidates
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("refresh", f"{type(exc).__name__}: {exc}")
        return 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
