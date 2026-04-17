"""
Scan buy timing 1 convergence from persisted and same-day 15-minute bars.

This script is intentionally an after-close step.
It captures same-day minute candles from KIS, resamples them into 15-minute
bars, optionally persists them, and records timing1 convergence signals.
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
from services import Timing1ConvergenceScanService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import IntradayBar15mRepository, SignalRepository
from strategy import Timing1ConvergenceSettings


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
        description="Scan buy timing 1 convergence after the close."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Must be today's KST date.",
    )
    parser.add_argument(
        "--bar-minutes",
        type=int,
        default=15,
        help="Fixed intraday bar size. Default: 15",
    )
    parser.add_argument(
        "--ma-short-window",
        type=int,
        default=20,
        help="Short moving-average window. Default: 20",
    )
    parser.add_argument(
        "--ma-long-window",
        type=int,
        default=60,
        help="Long moving-average window. Default: 60",
    )
    parser.add_argument(
        "--convergence-threshold-rate",
        type=float,
        default=0.02,
        help="Maximum spread threshold. Default: 0.02",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=300,
        help="How many stored 15-minute bars to load per symbol. Default: 300",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist captured 15-minute bars and convergence signals.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many scanned rows to print. Default: 20",
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
    convergence_settings = Timing1ConvergenceSettings(
        bar_minutes=args.bar_minutes,
        ma_short_window=args.ma_short_window,
        ma_long_window=args.ma_long_window,
        convergence_threshold_rate=args.convergence_threshold_rate,
    )

    _section("Scan Buy Timing1 Convergence")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
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
            service = Timing1ConvergenceScanService(
                broker=broker,
                conn=conn,
                signal_repo=SignalRepository(conn),
                intraday_bar_repo=IntradayBar15mRepository(conn),
            )
            result = service.scan(
                trade_date=args.trade_date,
                settings=convergence_settings,
                history_limit=args.history_limit,
                write=args.write,
            )

        _section("Scan Result")
        _ok("setup_signal_count", str(result.setup_signal_count))
        _ok("processed_count", str(result.processed_count))
        _ok("stored_symbol_count", str(result.stored_symbol_count))
        _ok("matched_count", str(result.matched_count))
        _ok("recorded_count", str(result.recorded_count))
        _ok("skipped_existing_count", str(result.skipped_existing_count))
        _ok("scanned_at", result.scanned_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Candidates")
            for candidate in visible_candidates:
                if candidate.match is None:
                    print(
                        f"{candidate.symbol} name={candidate.name} "
                        f"strong_day={candidate.strong_day_date} "
                        f"minute_count={candidate.minute_candle_count} "
                        f"bars15={candidate.intraday_bar_count} "
                        f"history_bars={candidate.history_bar_count} "
                        f"match=False"
                    )
                    continue
                print(
                    f"{candidate.symbol} name={candidate.name} "
                    f"strong_day={candidate.strong_day_date} "
                    f"convergence_bar_end_at={candidate.match.bar_end_at} "
                    f"day_high={candidate.match.day_high} "
                    f"already_recorded={candidate.already_recorded}"
                )
        else:
            _warn(
                "candidates",
                "No timing1 setup signals found for this trade_date.",
            )

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "scanned_at": result.scanned_at,
                "setup_signal_count": result.setup_signal_count,
                "processed_count": result.processed_count,
                "stored_symbol_count": result.stored_symbol_count,
                "matched_count": result.matched_count,
                "recorded_count": result.recorded_count,
                "skipped_existing_count": result.skipped_existing_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "market": candidate.market,
                        "strong_day_date": candidate.strong_day_date,
                        "minute_candle_count": candidate.minute_candle_count,
                        "intraday_bar_count": candidate.intraday_bar_count,
                        "history_bar_count": candidate.history_bar_count,
                        "already_recorded": candidate.already_recorded,
                        "match": (
                            None
                            if candidate.match is None
                            else {
                                "trade_date": candidate.match.trade_date,
                                "strong_day_date": candidate.match.strong_day_date,
                                "convergence_trade_date": (
                                    candidate.match.convergence_trade_date
                                ),
                                "bar_start_at": candidate.match.bar_start_at,
                                "bar_end_at": candidate.match.bar_end_at,
                                "close_price": candidate.match.close_price,
                                "ma20": candidate.match.ma_short,
                                "ma60": candidate.match.ma_long,
                                "convergence_threshold_rate": (
                                    candidate.match.convergence_threshold_rate
                                ),
                                "convergence_spread": (
                                    candidate.match.convergence_spread
                                ),
                                "day_high": candidate.match.day_high,
                            }
                        ),
                    }
                    for candidate in result.candidates
                ],
                "recorded_signal_ids": [row.id for row in result.recorded_signals],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("scan", f"{type(exc).__name__}: {exc}")
        return 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
