"""
Scan the stored universe for buy timing 1 daily setup signals.

Scope:
- read-only strategy scan
- no order placement
- optional signal DB recording with --write

Current assumption:
- only the daily setup part is evaluated in this phase
- minute-based convergence and live trigger are intentionally left for a later step
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
from services import Timing1SetupScanService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository, UniverseCandidateRepository
from strategy import Timing1SetupSettings

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
        description="Scan buy timing 1 daily setup from stored universe snapshot."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--daily-count",
        type=int,
        default=90,
        help="How many daily candles to request per symbol. Default: 90",
    )
    parser.add_argument(
        "--strong-gain-rate",
        type=float,
        default=0.15,
        help="Minimum strong-day gain rate. Default: 0.15",
    )
    parser.add_argument(
        "--strong-volume-multiplier",
        type=float,
        default=2.0,
        help="Minimum strong-day volume multiple. Default: 2.0",
    )
    parser.add_argument(
        "--strong-lookback-days",
        type=int,
        default=5,
        help="How many recent completed days to search. Default: 5",
    )
    parser.add_argument(
        "--strong-volume-avg-window",
        type=int,
        default=20,
        help="Average-volume window for strong-day check. Default: 20",
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
        "--ma-slope-lookback-days",
        type=int,
        default=5,
        help="Lookback days for MA slope comparison. Default: 5",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually record signals into SQLite.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many matched rows to print. Default: 20",
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

    strategy_settings = Timing1SetupSettings(
        strong_gain_rate=args.strong_gain_rate,
        strong_volume_multiplier=args.strong_volume_multiplier,
        strong_lookback_days=args.strong_lookback_days,
        strong_volume_avg_window=args.strong_volume_avg_window,
        ma_short_window=args.ma_short_window,
        ma_long_window=args.ma_long_window,
        ma_slope_lookback_days=args.ma_slope_lookback_days,
    )

    _section("Scan Buy Timing1 Setup")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("daily_count", str(args.daily_count))
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
            service = Timing1SetupScanService(
                broker=broker,
                conn=conn,
                universe_repo=UniverseCandidateRepository(conn),
                signal_repo=SignalRepository(conn),
            )
            result = service.scan(
                trade_date=args.trade_date,
                settings=strategy_settings,
                daily_count=args.daily_count,
                write_signals=args.write,
            )

        _section("Scan Result")
        _ok("universe_count", str(result.universe_count))
        _ok("matched_count", str(result.matched_count))
        _ok("recorded_count", str(result.recorded_count))
        _ok("skipped_existing_count", str(result.skipped_existing_count))
        _ok("scanned_at", result.scanned_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Matched")
            for candidate in visible_candidates:
                strong_day = candidate.match.strong_day
                print(
                    f"{candidate.symbol} name={candidate.name} "
                    f"latest_daily_date={candidate.match.latest_daily_date} "
                    f"strong_day={strong_day.date} "
                    f"gain_rate={strong_day.gain_rate:.4f} "
                    f"volume_ratio={strong_day.volume_ratio:.4f} "
                    f"already_recorded={candidate.already_recorded}"
                )
        else:
            _warn("matched", "No timing1 setup matches found.")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "scanned_at": result.scanned_at,
                "universe_count": result.universe_count,
                "matched_count": result.matched_count,
                "recorded_count": result.recorded_count,
                "skipped_existing_count": result.skipped_existing_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "market": candidate.market,
                        "already_recorded": candidate.already_recorded,
                        "latest_daily_date": candidate.match.latest_daily_date,
                        "latest_close": candidate.match.latest_close,
                        "ma20_now": candidate.match.ma_short_now,
                        "ma20_past": candidate.match.ma_short_past,
                        "ma60_now": candidate.match.ma_long_now,
                        "ma60_past": candidate.match.ma_long_past,
                        "strong_day": {
                            "date": candidate.match.strong_day.date,
                            "open_price": candidate.match.strong_day.open_price,
                            "close_price": candidate.match.strong_day.close_price,
                            "prev_close": candidate.match.strong_day.prev_close,
                            "gain_rate": candidate.match.strong_day.gain_rate,
                            "volume": candidate.match.strong_day.volume,
                            "avg_volume_before": (
                                candidate.match.strong_day.avg_volume_before
                            ),
                            "volume_ratio": candidate.match.strong_day.volume_ratio,
                        },
                    }
                    for candidate in result.candidates
                ],
                "recorded_signal_ids": [
                    row.id for row in result.recorded_signals
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("scan", f"{type(exc).__name__}: {exc}")
        return 4 if "Universe snapshot is missing" in str(exc) else 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
