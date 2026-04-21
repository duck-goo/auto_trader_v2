"""
Run after-close scans in one command.

Flow:
1. Refresh same-day 15-minute bars for live positions.
2. Scan timing1 convergence after the close.
3. Scan sell MACD exit signals.

Safety:
- preview is the default
- write mode uses one persisted runtime lock for the whole session
- if 15-minute refresh fails in write mode, sell MACD scan is skipped to avoid
  recording signals from stale persisted bars
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
from services import RuntimeLockBusyError, RuntimeLockService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import RuntimeLockRepository

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
        description="Run after-close scans in one command."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Must be today's KST date for refresh/convergence.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually persist refreshed bars and scan signals.",
    )
    parser.add_argument(
        "--skip-refresh-bars",
        action="store_true",
        help="Skip same-day 15-minute bar refresh step.",
    )
    parser.add_argument(
        "--skip-timing1-convergence",
        action="store_true",
        help="Skip timing1 convergence scan step.",
    )
    parser.add_argument(
        "--skip-sell-macd",
        action="store_true",
        help="Skip sell MACD scan step.",
    )
    parser.add_argument(
        "--bar-minutes",
        type=int,
        default=15,
        help="Fixed intraday bar size for refresh/convergence. Default: 15",
    )
    parser.add_argument(
        "--refresh-end-time",
        default=None,
        help="Optional KIS same-day minute backfill end time HHMMSS.",
    )
    parser.add_argument(
        "--convergence-ma-short-window",
        type=int,
        default=20,
        help="Timing1 convergence short moving-average window. Default: 20",
    )
    parser.add_argument(
        "--convergence-ma-long-window",
        type=int,
        default=60,
        help="Timing1 convergence long moving-average window. Default: 60",
    )
    parser.add_argument(
        "--convergence-threshold-rate",
        type=float,
        default=0.02,
        help="Timing1 convergence threshold rate. Default: 0.02",
    )
    parser.add_argument(
        "--convergence-history-limit",
        type=int,
        default=300,
        help="Stored 15-minute history limit for convergence scan. Default: 300",
    )
    parser.add_argument(
        "--sell-macd-fast-window",
        type=int,
        default=12,
        help="Sell MACD fast EMA window. Default: 12",
    )
    parser.add_argument(
        "--sell-macd-slow-window",
        type=int,
        default=26,
        help="Sell MACD slow EMA window. Default: 26",
    )
    parser.add_argument(
        "--sell-macd-signal-window",
        type=int,
        default=9,
        help="Sell MACD signal EMA window. Default: 9",
    )
    parser.add_argument(
        "--sell-macd-consecutive-decline-bars",
        type=int,
        default=2,
        help="Sell MACD consecutive decline bars. Default: 2",
    )
    parser.add_argument(
        "--sell-macd-history-limit",
        type=int,
        default=300,
        help="Stored 15-minute history limit for sell MACD scan. Default: 300",
    )
    parser.add_argument(
        "--lock-name",
        default=None,
        help="Optional runtime lock name for write mode.",
    )
    parser.add_argument(
        "--lock-lease-seconds",
        type=int,
        default=900,
        help="Runtime lock lease seconds for write mode. Default: 900",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional combined JSON output path.",
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


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _validate_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"after_close_session:{args.trade_date}"


def _run_child(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    return int(completed.returncode)


def _build_refresh_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "refresh_intraday_bars_15m.py"),
        "--trade-date",
        args.trade_date,
        "--bar-minutes",
        str(args.bar_minutes),
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.refresh_end_time:
        command.extend(["--end-time", args.refresh_end_time])
    if args.write:
        command.append("--write")
    return command


def _build_convergence_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "scan_buy_timing1_convergence.py"),
        "--trade-date",
        args.trade_date,
        "--bar-minutes",
        str(args.bar_minutes),
        "--ma-short-window",
        str(args.convergence_ma_short_window),
        "--ma-long-window",
        str(args.convergence_ma_long_window),
        "--convergence-threshold-rate",
        str(args.convergence_threshold_rate),
        "--history-limit",
        str(args.convergence_history_limit),
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.write:
        command.append("--write")
    return command


def _build_sell_macd_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "scan_sell_macd_exit_signals.py"),
        "--trade-date",
        args.trade_date,
        "--fast-window",
        str(args.sell_macd_fast_window),
        "--slow-window",
        str(args.sell_macd_slow_window),
        "--signal-window",
        str(args.sell_macd_signal_window),
        "--consecutive-decline-bars",
        str(args.sell_macd_consecutive_decline_bars),
        "--history-limit",
        str(args.sell_macd_history_limit),
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.write:
        command.append("--write")
    return command


def _step_payload(
    *,
    name: str,
    outcome: str,
    exit_code: int | None,
    reason: str | None,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "outcome": outcome,
        "exit_code": exit_code,
        "reason": reason,
        "result": result,
    }


def _print_step(step: dict[str, Any]) -> None:
    _section(step["name"])
    _ok("outcome", str(step["outcome"]))
    _ok("exit_code", str(step["exit_code"]))
    if step["reason"]:
        _warn("reason", str(step["reason"]))


def _extract_reason(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    for key in (
        "error_message",
        "reason",
        "session_reason",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def main() -> int:
    args = _parse_args()

    try:
        _validate_positive_int("bar_minutes", args.bar_minutes)
        _validate_positive_int(
            "convergence_ma_short_window",
            args.convergence_ma_short_window,
        )
        _validate_positive_int(
            "convergence_ma_long_window",
            args.convergence_ma_long_window,
        )
        _validate_positive_int(
            "convergence_history_limit",
            args.convergence_history_limit,
        )
        _validate_positive_int(
            "sell_macd_fast_window",
            args.sell_macd_fast_window,
        )
        _validate_positive_int(
            "sell_macd_slow_window",
            args.sell_macd_slow_window,
        )
        _validate_positive_int(
            "sell_macd_signal_window",
            args.sell_macd_signal_window,
        )
        _validate_positive_int(
            "sell_macd_consecutive_decline_bars",
            args.sell_macd_consecutive_decline_bars,
        )
        _validate_positive_int(
            "sell_macd_history_limit",
            args.sell_macd_history_limit,
        )
        if args.write:
            _validate_positive_int("lock_lease_seconds", args.lock_lease_seconds)

        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None
    lock_name = _resolve_lock_name(args)
    started_at = datetime.now(KST).isoformat()

    _section("Run After Close Session")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("write", str(args.write))
    _ok("db_path", str(db_path))
    _ok("lock_name", lock_name)
    if not args.write:
        _warn(
            "preview_note",
            "Preview mode does not persist refreshed 15-minute bars, so "
            "sell MACD preview still reads previously stored bars.",
        )

    steps: list[dict[str, Any]] = []
    session_outcome = "UNKNOWN"
    session_reason: str | None = None
    lock_conn: sqlite3.Connection | None = None
    lock_service: RuntimeLockService | None = None
    lock_acquired = False
    lock_was_acquired = False
    lock_released = False
    lock_owner_id: str | None = None

    try:
        if args.write:
            run_migrations(db_path)
            lock_conn = get_connection(
                db_path,
                busy_timeout_ms=settings.db_busy_timeout_ms,
            )
            lock_service = RuntimeLockService(
                conn=lock_conn,
                lock_repo=RuntimeLockRepository(lock_conn),
            )
            lock_owner_id = lock_service.owner_id
            lock_service.acquire(
                lock_name=lock_name,
                lease_seconds=args.lock_lease_seconds,
            )
            lock_acquired = True
            lock_was_acquired = True
    except RuntimeLockBusyError as exc:
        session_outcome = "LOCK_BUSY"
        session_reason = str(exc)
        if lock_conn is not None:
            lock_conn.close()
            lock_conn = None
        _fail("runtime lock", session_reason)
        if output_path is not None:
            _save_json(
                output_path,
                {
                    "trade_date": args.trade_date,
                    "started_at": started_at,
                    "finished_at": datetime.now(KST).isoformat(),
                    "write_mode": args.write,
                    "session_outcome": session_outcome,
                    "session_reason": session_reason,
                    "lock_name": lock_name,
                    "lock_owner_id": lock_owner_id,
                    "lock_acquired": False,
                    "steps": [],
                },
            )
            _ok("json_saved", str(output_path))
        return 4
    except Exception as exc:
        session_outcome = "LOCK_FAILED"
        session_reason = f"{type(exc).__name__}: {exc}"
        if lock_conn is not None:
            lock_conn.close()
            lock_conn = None
        _fail("lock", session_reason)
        return 5

    try:
        with tempfile.TemporaryDirectory(prefix="auto_trader_v2_after_close_") as temp_dir_text:
            temp_dir = Path(temp_dir_text)

            refresh_result = None
            refresh_exit_code = None
            refresh_success = False
            if args.skip_refresh_bars:
                steps.append(
                    _step_payload(
                        name="Refresh Intraday Bars 15m",
                        outcome="SKIPPED",
                        exit_code=None,
                        reason="Skipped by request.",
                        result=None,
                    )
                )
            else:
                refresh_output_path = temp_dir / "refresh_intraday_bars_15m.json"
                refresh_command = _build_refresh_command(
                    args=args,
                    db_path=str(db_path),
                    output_path=refresh_output_path,
                )
                refresh_exit_code = _run_child(refresh_command)
                refresh_result = _load_json(refresh_output_path)
                refresh_success = refresh_exit_code == 0
                steps.append(
                    _step_payload(
                        name="Refresh Intraday Bars 15m",
                        outcome="COMPLETED" if refresh_success else "FAILED",
                        exit_code=refresh_exit_code,
                        reason=_extract_reason(refresh_result),
                        result=refresh_result,
                    )
                )
                if lock_acquired and lock_service is not None:
                    lock_service.heartbeat(
                        lock_name=lock_name,
                        lease_seconds=args.lock_lease_seconds,
                    )

            if args.skip_timing1_convergence:
                steps.append(
                    _step_payload(
                        name="Scan Buy Timing1 Convergence",
                        outcome="SKIPPED",
                        exit_code=None,
                        reason="Skipped by request.",
                        result=None,
                    )
                )
            else:
                convergence_output_path = temp_dir / "timing1_convergence.json"
                convergence_command = _build_convergence_command(
                    args=args,
                    db_path=str(db_path),
                    output_path=convergence_output_path,
                )
                convergence_exit_code = _run_child(convergence_command)
                convergence_result = _load_json(convergence_output_path)
                steps.append(
                    _step_payload(
                        name="Scan Buy Timing1 Convergence",
                        outcome=(
                            "COMPLETED" if convergence_exit_code == 0 else "FAILED"
                        ),
                        exit_code=convergence_exit_code,
                        reason=_extract_reason(convergence_result),
                        result=convergence_result,
                    )
                )
                if lock_acquired and lock_service is not None:
                    lock_service.heartbeat(
                        lock_name=lock_name,
                        lease_seconds=args.lock_lease_seconds,
                    )

            skip_sell_macd_due_refresh = (
                args.write
                and not args.skip_refresh_bars
                and not refresh_success
            )
            if args.skip_sell_macd:
                steps.append(
                    _step_payload(
                        name="Scan Sell MACD Exit Signals",
                        outcome="SKIPPED",
                        exit_code=None,
                        reason="Skipped by request.",
                        result=None,
                    )
                )
            elif skip_sell_macd_due_refresh:
                steps.append(
                    _step_payload(
                        name="Scan Sell MACD Exit Signals",
                        outcome="SKIPPED",
                        exit_code=None,
                        reason=(
                            "Skipped because 15-minute bar refresh failed in "
                            "write mode."
                        ),
                        result=None,
                    )
                )
            else:
                sell_macd_output_path = temp_dir / "sell_macd_exit.json"
                sell_macd_command = _build_sell_macd_command(
                    args=args,
                    db_path=str(db_path),
                    output_path=sell_macd_output_path,
                )
                sell_macd_exit_code = _run_child(sell_macd_command)
                sell_macd_result = _load_json(sell_macd_output_path)
                steps.append(
                    _step_payload(
                        name="Scan Sell MACD Exit Signals",
                        outcome=("COMPLETED" if sell_macd_exit_code == 0 else "FAILED"),
                        exit_code=sell_macd_exit_code,
                        reason=_extract_reason(sell_macd_result),
                        result=sell_macd_result,
                    )
                )
                if lock_acquired and lock_service is not None:
                    lock_service.heartbeat(
                        lock_name=lock_name,
                        lease_seconds=args.lock_lease_seconds,
                    )
    except Exception as exc:
        session_outcome = "SESSION_FAILED"
        session_reason = f"{type(exc).__name__}: {exc}"
        _fail("session", session_reason)
    finally:
        if lock_acquired and lock_service is not None:
            try:
                lock_released = lock_service.release(lock_name=lock_name)
            finally:
                lock_acquired = False
        if lock_conn is not None:
            lock_conn.close()
            lock_conn = None

    for step in steps:
        _print_step(step)

    if session_outcome == "UNKNOWN":
        failed_steps = [row for row in steps if row["outcome"] == "FAILED"]
        if failed_steps:
            session_outcome = "FAILED"
            session_reason = (
                f"{failed_steps[0]['name']} failed"
                if failed_steps[0]["reason"] is None
                else f"{failed_steps[0]['name']}: {failed_steps[0]['reason']}"
            )
        else:
            session_outcome = "COMPLETED"
            session_reason = None

    _section("Session Result")
    _ok("session_outcome", session_outcome)
    if session_reason:
        _warn("session_reason", session_reason)

    if output_path is not None:
        payload = {
            "trade_date": args.trade_date,
            "started_at": started_at,
            "finished_at": datetime.now(KST).isoformat(),
            "write_mode": args.write,
            "session_outcome": session_outcome,
            "session_reason": session_reason,
            "lock_name": lock_name,
            "lock_owner_id": lock_owner_id,
            "lock_acquired": lock_was_acquired,
            "lock_released": lock_released,
            "steps": steps,
        }
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    if session_outcome == "COMPLETED":
        return 0
    if session_outcome == "LOCK_BUSY":
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
