"""
Scan the stored universe for buy timing 2 daily setup signals.

Scope:
- read-only strategy scan
- no order placement
- optional signal DB recording with --write

Current assumption:
- only the daily setup part is evaluated in this phase
- intraday re-break trigger is intentionally left for a later step
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
from services import Timing2SetupScanService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository, UniverseCandidateRepository
from strategy import Timing2SetupSettings

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
        description="Scan buy timing 2 daily setup from stored universe snapshot."
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
        "--new-high-lookback-days",
        type=int,
        default=60,
        help="Lookback window for prior new-high check. Default: 60",
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

    strategy_settings = Timing2SetupSettings(
        new_high_lookback_days=args.new_high_lookback_days,
    )

    _section("Scan Buy Timing2 Setup")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("daily_count", str(args.daily_count))
    _ok("new_high_lookback_days", str(args.new_high_lookback_days))
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
            service = Timing2SetupScanService(
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
                print(
                    f"{candidate.symbol} name={candidate.name} "
                    f"latest_daily_date={candidate.match.latest_daily_date} "
                    f"latest_close={candidate.match.latest_close} "
                    f"upper_limit={candidate.match.official_upper_limit_price} "
                    f"prior_lookback_high={candidate.match.prior_lookback_high} "
                    f"already_recorded={candidate.already_recorded}"
                )
        else:
            _warn("matched", "No timing2 setup matches found.")

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
                        "previous_close": candidate.match.previous_close,
                        "official_upper_limit_price": (
                            candidate.match.official_upper_limit_price
                        ),
                        "prior_lookback_high": candidate.match.prior_lookback_high,
                        "lookback_start_date": candidate.match.lookback_start_date,
                        "lookback_end_date": candidate.match.lookback_end_date,
                    }
                    for candidate in result.candidates
                ],
                "recorded_signal_ids": [
                    row.id
                    for row in result.recorded_signals
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
