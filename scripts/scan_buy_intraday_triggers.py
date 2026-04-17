"""
Run buy timing1 and timing2 intraday trigger scans in one command.

Safety:
- no order placement
- optional signal recording with per-strategy write flags
- one strategy scan failure does not hide the other strategy result
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
from services import (
    IntradayTriggerCombinedScanService,
    IntradayTriggerStrategyStatus,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository
from strategy import (
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
)

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
        description="Run timing1 and timing2 intraday trigger scans together."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--scan-timing1",
        action="store_true",
        help="Run timing1 intraday trigger scan.",
    )
    parser.add_argument(
        "--scan-timing2",
        action="store_true",
        help="Run timing2 intraday trigger scan.",
    )
    parser.add_argument(
        "--write-timing1-signals",
        action="store_true",
        help="Record timing1 intraday trigger signals into SQLite.",
    )
    parser.add_argument(
        "--write-timing2-signals",
        action="store_true",
        help="Record timing2 intraday trigger signals into SQLite.",
    )
    parser.add_argument(
        "--timing1-start-time",
        default="09:00:00",
        help="Timing1 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing1-cutoff-time",
        default="12:00:00",
        help="Timing1 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--timing1-daily-count",
        type=int,
        default=5,
        help="Timing1 daily candle count for next-trading-day validation. Default: 5",
    )
    parser.add_argument(
        "--timing2-tolerance-rate",
        type=float,
        default=0.003,
        help="Timing2 trigger tolerance rate. Default: 0.003",
    )
    parser.add_argument(
        "--timing2-start-time",
        default="09:00:00",
        help="Timing2 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing2-cutoff-time",
        default="12:00:00",
        help="Timing2 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many candidate rows to print per strategy. Default: 20",
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


def _resolve_scan_selection(args: argparse.Namespace) -> tuple[bool, bool]:
    run_timing1 = args.scan_timing1
    run_timing2 = args.scan_timing2
    if not run_timing1 and not run_timing2:
        return True, True
    return run_timing1, run_timing2


def _validate_args(
    args: argparse.Namespace,
    *,
    run_timing1: bool,
    run_timing2: bool,
) -> None:
    if args.write_timing1_signals and not run_timing1:
        raise ValueError(
            "--write-timing1-signals requires timing1 scan to be enabled."
        )
    if args.write_timing2_signals and not run_timing2:
        raise ValueError(
            "--write-timing2-signals requires timing2 scan to be enabled."
        )


def _print_timing1_status(
    *,
    status: IntradayTriggerStrategyStatus,
    limit: int,
) -> None:
    _section("Timing1 Intraday Trigger")
    _ok("outcome", status.outcome)
    if status.reason:
        _warn("reason", status.reason)
    if status.result is None:
        return

    result = status.result
    _ok("convergence_signal_count", str(result.convergence_signal_count))
    _ok(
        "skipped_not_next_trading_day_count",
        str(result.skipped_not_next_trading_day_count),
    )
    _ok("candidate_count", str(result.candidate_count))
    _ok("transition_count", str(result.transition_count))
    _ok("triggered_count", str(result.triggered_count))
    _ok("expired_count", str(result.expired_count))
    _ok("recorded_count", str(result.recorded_count))
    _ok("scanned_at", result.scanned_at)

    visible_candidates = result.candidates[: max(0, limit)]
    if not visible_candidates:
        _warn("candidates", "No timing1 intraday candidates found.")
        return

    _section("Timing1 Candidates")
    for candidate in visible_candidates:
        print(
            f"{candidate.symbol} name={candidate.name} "
            f"convergence_trade_date={candidate.convergence_trade_date} "
            f"stage_before={candidate.decision.stage_before.value} "
            f"stage_after={candidate.decision.stage_after.value} "
            f"transition={candidate.decision.transition.value} "
            f"current_price={candidate.decision.current_price} "
            f"target_price={candidate.decision.target_price} "
            f"recorded={candidate.transition_recorded}"
        )


def _print_timing2_status(
    *,
    status: IntradayTriggerStrategyStatus,
    limit: int,
) -> None:
    _section("Timing2 Intraday Trigger")
    _ok("outcome", status.outcome)
    if status.reason:
        _warn("reason", status.reason)
    if status.result is None:
        return

    result = status.result
    _ok("setup_signal_count", str(result.setup_signal_count))
    _ok("candidate_count", str(result.candidate_count))
    _ok("transition_count", str(result.transition_count))
    _ok("triggered_count", str(result.triggered_count))
    _ok("expired_count", str(result.expired_count))
    _ok("recorded_count", str(result.recorded_count))
    _ok("scanned_at", result.scanned_at)

    visible_candidates = result.candidates[: max(0, limit)]
    if not visible_candidates:
        _warn("candidates", "No timing2 intraday candidates found.")
        return

    _section("Timing2 Candidates")
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


def _build_timing1_payload(status: IntradayTriggerStrategyStatus) -> dict[str, Any]:
    payload = {
        "outcome": status.outcome,
        "reason": status.reason,
        "result": None,
    }
    if status.result is None:
        return payload

    result = status.result
    payload["result"] = {
        "trade_date": result.trade_date,
        "scanned_at": result.scanned_at,
        "convergence_signal_count": result.convergence_signal_count,
        "skipped_not_next_trading_day_count": (
            result.skipped_not_next_trading_day_count
        ),
        "candidate_count": result.candidate_count,
        "transition_count": result.transition_count,
        "triggered_count": result.triggered_count,
        "expired_count": result.expired_count,
        "recorded_count": result.recorded_count,
        "candidates": [
            {
                "symbol": candidate.symbol,
                "name": candidate.name,
                "market": candidate.market,
                "convergence_signal_id": candidate.convergence_signal_id,
                "convergence_trade_date": candidate.convergence_trade_date,
                "stage_before": candidate.decision.stage_before.value,
                "stage_after": candidate.decision.stage_after.value,
                "transition": candidate.decision.transition.value,
                "observed_at": candidate.decision.observed_at,
                "target_price": candidate.decision.target_price,
                "current_price": candidate.decision.current_price,
                "transition_strategy_name": candidate.transition_strategy_name,
                "transition_recorded": candidate.transition_recorded,
            }
            for candidate in result.candidates
        ],
        "recorded_signal_ids": [row.id for row in result.recorded_signals],
    }
    return payload


def _build_timing2_payload(status: IntradayTriggerStrategyStatus) -> dict[str, Any]:
    payload = {
        "outcome": status.outcome,
        "reason": status.reason,
        "result": None,
    }
    if status.result is None:
        return payload

    result = status.result
    payload["result"] = {
        "trade_date": result.trade_date,
        "scanned_at": result.scanned_at,
        "setup_signal_count": result.setup_signal_count,
        "candidate_count": result.candidate_count,
        "transition_count": result.transition_count,
        "triggered_count": result.triggered_count,
        "expired_count": result.expired_count,
        "recorded_count": result.recorded_count,
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
    return payload


def _resolve_exit_code(
    *,
    timing1_status: IntradayTriggerStrategyStatus,
    timing2_status: IntradayTriggerStrategyStatus,
) -> int:
    outcomes = {timing1_status.outcome, timing2_status.outcome}
    if "FAILED" in outcomes:
        return 5
    if outcomes == {"SKIPPED"}:
        return 4
    return 0


def main() -> int:
    args = _parse_args()
    run_timing1, run_timing2 = _resolve_scan_selection(args)

    try:
        _validate_args(
            args,
            run_timing1=run_timing1,
            run_timing2=run_timing2,
        )
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None

    _section("Scan Buy Intraday Triggers")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("scan_timing1", str(run_timing1))
    _ok("scan_timing2", str(run_timing2))
    _ok("write_timing1_signals", str(args.write_timing1_signals))
    _ok("write_timing2_signals", str(args.write_timing2_signals))
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
            signal_repo = SignalRepository(conn)
            combined_service = IntradayTriggerCombinedScanService(
                broker=broker,
                conn=conn,
                signal_repo=signal_repo,
            )
            combined_result = combined_service.scan(
                trade_date=args.trade_date,
                run_timing1=run_timing1,
                run_timing2=run_timing2,
                timing1_settings=Timing1IntradayTriggerSettings(
                    start_time=args.timing1_start_time,
                    cutoff_time=args.timing1_cutoff_time,
                ),
                timing1_daily_count=args.timing1_daily_count,
                write_timing1_signals=args.write_timing1_signals,
                timing2_settings=Timing2IntradayTriggerSettings(
                    tolerance_rate=args.timing2_tolerance_rate,
                    start_time=args.timing2_start_time,
                    cutoff_time=args.timing2_cutoff_time,
                ),
                write_timing2_signals=args.write_timing2_signals,
            )

        timing1_status = combined_result.timing1
        timing2_status = combined_result.timing2

        _print_timing1_status(status=timing1_status, limit=args.limit)
        _print_timing2_status(status=timing2_status, limit=args.limit)

        if output_path is not None:
            payload = {
                "trade_date": args.trade_date,
                "scan_timing1": run_timing1,
                "scan_timing2": run_timing2,
                "write_timing1_signals": args.write_timing1_signals,
                "write_timing2_signals": args.write_timing2_signals,
                "timing1": _build_timing1_payload(timing1_status),
                "timing2": _build_timing2_payload(timing2_status),
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return _resolve_exit_code(
            timing1_status=timing1_status,
            timing2_status=timing2_status,
        )

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
