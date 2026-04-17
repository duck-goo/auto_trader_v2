"""
Poll current prices for timing2 setup symbols and advance intraday trigger state.

Scope:
- read-only intraday trigger scan
- no order placement
- optional signal DB recording with --write
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
from services import Timing2IntradayTriggerService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository
from strategy import Timing2IntradayTriggerSettings

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
        description="Scan buy timing2 intraday trigger from stored setup signals."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--tolerance-rate",
        type=float,
        default=0.003,
        help="Trigger tolerance rate. Default: 0.003",
    )
    parser.add_argument(
        "--start-time",
        default="09:00:00",
        help="Monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--cutoff-time",
        default="12:00:00",
        help="Monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually record intraday transition signals into SQLite.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many candidate rows to print. Default: 20",
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
    strategy_settings = Timing2IntradayTriggerSettings(
        tolerance_rate=args.tolerance_rate,
        start_time=args.start_time,
        cutoff_time=args.cutoff_time,
    )

    _section("Scan Buy Timing2 Intraday Trigger")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("tolerance_rate", str(args.tolerance_rate))
    _ok("start_time", args.start_time)
    _ok("cutoff_time", args.cutoff_time)
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
            service = Timing2IntradayTriggerService(
                broker=broker,
                conn=conn,
                signal_repo=SignalRepository(conn),
            )
            result = service.scan(
                trade_date=args.trade_date,
                settings=strategy_settings,
                write_signals=args.write,
            )

        _section("Scan Result")
        _ok("setup_signal_count", str(result.setup_signal_count))
        _ok("candidate_count", str(result.candidate_count))
        _ok("transition_count", str(result.transition_count))
        _ok("triggered_count", str(result.triggered_count))
        _ok("expired_count", str(result.expired_count))
        _ok("recorded_count", str(result.recorded_count))
        _ok("scanned_at", result.scanned_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Candidates")
            for candidate in visible_candidates:
                print(
                    f"{candidate.symbol} name={candidate.name} "
                    f"stage_before={candidate.decision.stage_before.value} "
                    f"stage_after={candidate.decision.stage_after.value} "
                    f"transition={candidate.decision.transition.value} "
                    f"current_price={candidate.decision.current_price} "
                    f"base_open_price={candidate.decision.base_open_price} "
                    f"recorded={candidate.transition_recorded}"
                )
        else:
            _warn("candidates", "No timing2 intraday candidates found.")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "scanned_at": result.scanned_at,
                "setup_signal_count": result.setup_signal_count,
                "candidate_count": result.candidate_count,
                "transition_count": result.transition_count,
                "triggered_count": result.triggered_count,
                "expired_count": result.expired_count,
                "recorded_count": result.recorded_count,
                "write_requested": args.write,
                "candidates": [
                    {
                        "symbol": candidate.symbol,
                        "name": candidate.name,
                        "market": candidate.market,
                        "setup_signal_id": candidate.setup_signal_id,
                        "stage_before": candidate.decision.stage_before.value,
                        "stage_after": candidate.decision.stage_after.value,
                        "transition": candidate.decision.transition.value,
                        "observed_at": candidate.decision.observed_at,
                        "base_open_price": candidate.decision.base_open_price,
                        "current_price": candidate.decision.current_price,
                        "breakout_trigger_price": (
                            candidate.decision.breakout_trigger_price
                        ),
                        "pullback_trigger_price": (
                            candidate.decision.pullback_trigger_price
                        ),
                        "transition_strategy_name": candidate.transition_strategy_name,
                        "transition_recorded": candidate.transition_recorded,
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
        return 4 if "Timing2 setup signals are missing" in str(exc) else 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
