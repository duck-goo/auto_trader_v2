"""
Poll timing1 and timing2 intraday trigger scans until the trading cutoff.

Safety:
- no order placement
- repeated scans reuse DB-persisted signal state
- repeated failures stop the loop instead of retrying forever
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, time as dt_time
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
    RuntimeLockBusyError,
    RuntimeLockService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import RuntimeLockRepository, SignalRepository
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
        description="Poll timing1 and timing2 intraday trigger scans repeatedly."
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
        "--interval-seconds",
        type=int,
        default=20,
        help="Polling interval in seconds. Default: 20",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Maximum cycle count. 0 means keep polling until stop condition. Default: 0",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive full-cycle failures. Default: 3",
    )
    parser.add_argument(
        "--lock-name",
        default=None,
        help="Optional runtime lock name. Default: intraday_trigger_polling:<trade_date>",
    )
    parser.add_argument(
        "--lock-lease-seconds",
        type=int,
        default=0,
        help="Runtime lock lease seconds. 0 means auto (max(interval*3, 90)). Default: 0",
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


def _validate_positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer: {value!r}")
    if allow_zero:
        if value < 0:
            raise ValueError(f"{name} must be >= 0: {value!r}")
    elif value <= 0:
        raise ValueError(f"{name} must be > 0: {value!r}")
    return value


def _parse_time_text(value: str) -> dt_time:
    return datetime.strptime(value, "%H:%M:%S").time()


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
    _validate_positive_int("interval_seconds", args.interval_seconds)
    _validate_positive_int("max_cycles", args.max_cycles, allow_zero=True)
    _validate_positive_int(
        "max_consecutive_failures",
        args.max_consecutive_failures,
    )
    if args.lock_lease_seconds != 0:
        _validate_positive_int("lock_lease_seconds", args.lock_lease_seconds)


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"intraday_trigger_polling:{args.trade_date}"


def _resolve_lock_lease_seconds(args: argparse.Namespace) -> int:
    if args.lock_lease_seconds > 0:
        lease_seconds = args.lock_lease_seconds
    else:
        lease_seconds = max(args.interval_seconds * 3, 90)
    if lease_seconds <= args.interval_seconds:
        raise ValueError(
            "lock lease must be longer than interval_seconds: "
            f"lease={lease_seconds}, interval={args.interval_seconds}"
        )
    return lease_seconds


def _resolve_window(
    *,
    run_timing1: bool,
    run_timing2: bool,
    timing1_settings: Timing1IntradayTriggerSettings,
    timing2_settings: Timing2IntradayTriggerSettings,
) -> tuple[dt_time, dt_time]:
    start_candidates: list[dt_time] = []
    cutoff_candidates: list[dt_time] = []
    if run_timing1:
        normalized = timing1_settings.validated()
        start_candidates.append(_parse_time_text(normalized.start_time))
        cutoff_candidates.append(_parse_time_text(normalized.cutoff_time))
    if run_timing2:
        normalized = timing2_settings.validated()
        start_candidates.append(_parse_time_text(normalized.start_time))
        cutoff_candidates.append(_parse_time_text(normalized.cutoff_time))
    return min(start_candidates), max(cutoff_candidates)


def _seconds_until(
    *,
    now: datetime,
    target_time: dt_time,
) -> int:
    target = now.astimezone(KST).replace(
        hour=target_time.hour,
        minute=target_time.minute,
        second=target_time.second,
        microsecond=0,
    )
    delta = target - now.astimezone(KST)
    return max(0, int(delta.total_seconds()))


def _build_cycle_payload(cycle_no: int, combined_result) -> dict[str, Any]:
    timing1_result = combined_result.timing1.result
    timing2_result = combined_result.timing2.result
    return {
        "cycle_no": cycle_no,
        "trade_date": combined_result.trade_date,
        "timing1": {
            "outcome": combined_result.timing1.outcome,
            "reason": combined_result.timing1.reason,
            "candidate_count": (
                None if timing1_result is None else timing1_result.candidate_count
            ),
            "transition_count": (
                None if timing1_result is None else timing1_result.transition_count
            ),
            "triggered_count": (
                None if timing1_result is None else timing1_result.triggered_count
            ),
            "expired_count": (
                None if timing1_result is None else timing1_result.expired_count
            ),
            "recorded_count": (
                None if timing1_result is None else timing1_result.recorded_count
            ),
            "scanned_at": (
                None if timing1_result is None else timing1_result.scanned_at
            ),
        },
        "timing2": {
            "outcome": combined_result.timing2.outcome,
            "reason": combined_result.timing2.reason,
            "candidate_count": (
                None if timing2_result is None else timing2_result.candidate_count
            ),
            "transition_count": (
                None if timing2_result is None else timing2_result.transition_count
            ),
            "triggered_count": (
                None if timing2_result is None else timing2_result.triggered_count
            ),
            "expired_count": (
                None if timing2_result is None else timing2_result.expired_count
            ),
            "recorded_count": (
                None if timing2_result is None else timing2_result.recorded_count
            ),
            "scanned_at": (
                None if timing2_result is None else timing2_result.scanned_at
            ),
        },
    }


def _print_cycle_summary(cycle_payload: dict[str, Any]) -> None:
    _section(f"Polling Cycle {cycle_payload['cycle_no']}")
    timing1 = cycle_payload["timing1"]
    timing2 = cycle_payload["timing2"]
    _ok("timing1_outcome", str(timing1["outcome"]))
    if timing1["reason"]:
        _warn("timing1_reason", str(timing1["reason"]))
    if timing1["candidate_count"] is not None:
        _ok("timing1_candidates", str(timing1["candidate_count"]))
        _ok("timing1_transitions", str(timing1["transition_count"]))
        _ok("timing1_triggered", str(timing1["triggered_count"]))
        _ok("timing1_expired", str(timing1["expired_count"]))
        _ok("timing1_recorded", str(timing1["recorded_count"]))

    _ok("timing2_outcome", str(timing2["outcome"]))
    if timing2["reason"]:
        _warn("timing2_reason", str(timing2["reason"]))
    if timing2["candidate_count"] is not None:
        _ok("timing2_candidates", str(timing2["candidate_count"]))
        _ok("timing2_transitions", str(timing2["transition_count"]))
        _ok("timing2_triggered", str(timing2["triggered_count"]))
        _ok("timing2_expired", str(timing2["expired_count"]))
        _ok("timing2_recorded", str(timing2["recorded_count"]))


def _all_requested_failed(
    *,
    run_timing1: bool,
    run_timing2: bool,
    cycle_payload: dict[str, Any],
) -> bool:
    outcomes: list[str] = []
    if run_timing1:
        outcomes.append(str(cycle_payload["timing1"]["outcome"]))
    if run_timing2:
        outcomes.append(str(cycle_payload["timing2"]["outcome"]))
    return bool(outcomes) and all(outcome == "FAILED" for outcome in outcomes)


def _all_requested_skipped(
    *,
    run_timing1: bool,
    run_timing2: bool,
    cycle_payload: dict[str, Any],
) -> bool:
    outcomes: list[str] = []
    if run_timing1:
        outcomes.append(str(cycle_payload["timing1"]["outcome"]))
    if run_timing2:
        outcomes.append(str(cycle_payload["timing2"]["outcome"]))
    return bool(outcomes) and all(outcome == "SKIPPED" for outcome in outcomes)


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
        lock_name = _resolve_lock_name(args)
        lock_lease_seconds = _resolve_lock_lease_seconds(args)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path
    timing1_settings = Timing1IntradayTriggerSettings(
        start_time=args.timing1_start_time,
        cutoff_time=args.timing1_cutoff_time,
    )
    timing2_settings = Timing2IntradayTriggerSettings(
        tolerance_rate=args.timing2_tolerance_rate,
        start_time=args.timing2_start_time,
        cutoff_time=args.timing2_cutoff_time,
    )
    earliest_start, latest_cutoff = _resolve_window(
        run_timing1=run_timing1,
        run_timing2=run_timing2,
        timing1_settings=timing1_settings,
        timing2_settings=timing2_settings,
    )

    _section("Run Intraday Trigger Polling")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("scan_timing1", str(run_timing1))
    _ok("scan_timing2", str(run_timing2))
    _ok("write_timing1_signals", str(args.write_timing1_signals))
    _ok("write_timing2_signals", str(args.write_timing2_signals))
    _ok("interval_seconds", str(args.interval_seconds))
    _ok("max_cycles", str(args.max_cycles))
    _ok("max_consecutive_failures", str(args.max_consecutive_failures))
    _ok("earliest_start", earliest_start.strftime("%H:%M:%S"))
    _ok("latest_cutoff", latest_cutoff.strftime("%H:%M:%S"))
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

    started_at = datetime.now(KST).isoformat()
    cycles: list[dict[str, Any]] = []
    stop_reason = "UNKNOWN"
    consecutive_failures = 0
    lock_acquired = False
    lock_owner_id: str | None = None
    released_lock = False
    lock_service: RuntimeLockService | None = None

    try:
        lock_service = RuntimeLockService(
            conn=conn,
            lock_repo=RuntimeLockRepository(conn),
        )
        lock_owner_id = lock_service.owner_id
        _ok("lock_name", lock_name)
        _ok("lock_lease_seconds", str(lock_lease_seconds))
        _ok("lock_owner_id", lock_owner_id)

        try:
            lease = lock_service.acquire(
                lock_name=lock_name,
                lease_seconds=lock_lease_seconds,
            )
            lock_acquired = True
            _ok("lock_acquired_at", lease.acquired_at)
            _ok("lock_expires_at", lease.expires_at)
        except RuntimeLockBusyError as exc:
            stop_reason = "LOCK_BUSY"
            _warn("lock_busy", str(exc))
            finished_at = datetime.now(KST).isoformat()
            if output_path is not None:
                _save_json(
                    output_path,
                    {
                        "trade_date": args.trade_date,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "stop_reason": stop_reason,
                        "scan_timing1": run_timing1,
                        "scan_timing2": run_timing2,
                        "write_timing1_signals": args.write_timing1_signals,
                        "write_timing2_signals": args.write_timing2_signals,
                        "interval_seconds": args.interval_seconds,
                        "max_cycles": args.max_cycles,
                        "max_consecutive_failures": args.max_consecutive_failures,
                        "lock_name": lock_name,
                        "lock_owner_id": lock_owner_id,
                        "lock_lease_seconds": lock_lease_seconds,
                        "lock_acquired": False,
                        "cycle_count": 0,
                        "cycles": [],
                    },
                )
                _ok("json_saved", str(output_path))
            return 4

        with KisBroker(settings) as broker:
            signal_repo = SignalRepository(conn)
            combined_service = IntradayTriggerCombinedScanService(
                broker=broker,
                conn=conn,
                signal_repo=signal_repo,
            )

            cycle_no = 0
            while True:
                now = datetime.now(KST)
                lock_service.heartbeat(
                    lock_name=lock_name,
                    lease_seconds=lock_lease_seconds,
                )

                if cycle_no == 0 and now.time() < earliest_start:
                    wait_seconds = min(
                        args.interval_seconds,
                        _seconds_until(now=now, target_time=earliest_start),
                    )
                    _warn(
                        "waiting_for_start",
                        f"sleeping {wait_seconds}s until monitoring window starts",
                    )
                    time.sleep(wait_seconds)
                    continue

                cycle_no += 1
                combined_result = combined_service.scan(
                    trade_date=args.trade_date,
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    timing1_settings=timing1_settings,
                    timing1_daily_count=args.timing1_daily_count,
                    write_timing1_signals=args.write_timing1_signals,
                    timing2_settings=timing2_settings,
                    write_timing2_signals=args.write_timing2_signals,
                )
                cycle_payload = _build_cycle_payload(cycle_no, combined_result)
                cycles.append(cycle_payload)
                _print_cycle_summary(cycle_payload)
                lock_service.heartbeat(
                    lock_name=lock_name,
                    lease_seconds=lock_lease_seconds,
                )

                if _all_requested_failed(
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    cycle_payload=cycle_payload,
                ):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= args.max_consecutive_failures:
                    stop_reason = "MAX_CONSECUTIVE_FAILURES"
                    break

                if args.max_cycles > 0 and cycle_no >= args.max_cycles:
                    stop_reason = "MAX_CYCLES_REACHED"
                    break

                if datetime.now(KST).time() >= latest_cutoff:
                    stop_reason = "CUTOFF_REACHED"
                    break

                time.sleep(args.interval_seconds)

    except KeyboardInterrupt:
        stop_reason = "INTERRUPTED"
    except Exception as exc:
        _fail("polling", f"{type(exc).__name__}: {exc}")
        stop_reason = f"FAILED:{type(exc).__name__}"
        finished_at = datetime.now(KST).isoformat()
        if output_path is not None:
            _save_json(
                output_path,
                {
                    "trade_date": args.trade_date,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "stop_reason": stop_reason,
                    "scan_timing1": run_timing1,
                    "scan_timing2": run_timing2,
                    "write_timing1_signals": args.write_timing1_signals,
                    "write_timing2_signals": args.write_timing2_signals,
                    "interval_seconds": args.interval_seconds,
                    "max_cycles": args.max_cycles,
                    "max_consecutive_failures": args.max_consecutive_failures,
                    "lock_name": lock_name,
                    "lock_owner_id": lock_owner_id,
                    "lock_lease_seconds": lock_lease_seconds,
                    "lock_acquired": lock_acquired,
                    "cycle_count": len(cycles),
                    "cycles": cycles,
                },
            )
        return 5
    finally:
        if lock_acquired and lock_service is not None:
            try:
                released_lock = lock_service.release(lock_name=lock_name)
            except Exception:
                released_lock = False
        conn.close()

    finished_at = datetime.now(KST).isoformat()
    payload = {
        "trade_date": args.trade_date,
        "started_at": started_at,
        "finished_at": finished_at,
        "stop_reason": stop_reason,
        "scan_timing1": run_timing1,
        "scan_timing2": run_timing2,
        "write_timing1_signals": args.write_timing1_signals,
        "write_timing2_signals": args.write_timing2_signals,
        "interval_seconds": args.interval_seconds,
        "max_cycles": args.max_cycles,
        "max_consecutive_failures": args.max_consecutive_failures,
        "lock_name": lock_name,
        "lock_owner_id": lock_owner_id,
        "lock_lease_seconds": lock_lease_seconds,
        "lock_acquired": lock_acquired,
        "lock_released": released_lock,
        "cycle_count": len(cycles),
        "cycles": cycles,
    }
    if output_path is not None:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    if stop_reason == "INTERRUPTED":
        _warn("polling", "Interrupted by user.")
        return 130
    if stop_reason.startswith("FAILED") or stop_reason == "MAX_CONSECUTIVE_FAILURES":
        return 5
    if cycles and all(
        _all_requested_skipped(
            run_timing1=run_timing1,
            run_timing2=run_timing2,
            cycle_payload=cycle,
        )
        for cycle in cycles
    ):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
