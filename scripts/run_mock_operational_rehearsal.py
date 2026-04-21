"""
Run a quick mock-only operational rehearsal in one command.

Flow:
1. Run startup_check.py.
2. If startup is READY, run a one-cycle preview trading session.
3. Optionally run an after-close preview.

Safety:
- this wrapper is allowed only in mock mode
- all child runs stay in preview mode
- the intraday preview is forced into one quick cycle to avoid long waits
- it saves every child JSON plus one combined summary JSON
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging

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
        description="Run startup check plus a quick mock operational rehearsal."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--master-input",
        default=None,
        help="Optional JSON or CSV market master input path.",
    )
    parser.add_argument(
        "--master-format",
        default="auto",
        choices=("auto", "json", "csv"),
        help="Market master input format. Default: auto",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Use the current market master snapshot already stored in SQLite.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block the run if market master refreshed date does not match trade_date.",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Allow startup/preopen checks to continue even if unresolved orders exist.",
    )
    parser.add_argument(
        "--per-order-budget",
        type=int,
        required=True,
        help="Max KRW budget per buy order for the preview trading session.",
    )
    parser.add_argument(
        "--max-holdings",
        type=int,
        required=True,
        help="Max concurrent holdings/unresolved-buy symbols for the preview trading session.",
    )
    parser.add_argument(
        "--max-daily-order-count",
        type=int,
        default=None,
        help="Optional max total order count for the trade date.",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=int,
        default=None,
        help="Optional max realized daily loss in KRW.",
    )
    parser.add_argument(
        "--scan-timing1",
        action="store_true",
        help="Also run timing1 intraday trigger scan during the one-cycle preview.",
    )
    parser.add_argument(
        "--scan-timing2",
        action="store_true",
        help="Also run timing2 intraday trigger scan during the one-cycle preview.",
    )
    parser.add_argument(
        "--include-after-close",
        action="store_true",
        help="Also run run_after_close_session.py in preview mode after the trading rehearsal.",
    )
    parser.add_argument(
        "--rehearsal-window-seconds",
        type=int,
        default=60,
        help="Seconds before/after now to build the one-cycle preview window. Default: 60",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory to store child JSON outputs. Default: data/ops/<trade-date>/rehearsal_<timestamp>",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional combined JSON output path. Default: <output-dir>/rehearsal_summary.json",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    return parser.parse_args()


def _now() -> datetime:
    return datetime.now(KST)


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


def _validate_positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer: {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} integer: {value!r}")
    return value


def _resolve_output_dir(
    *,
    trade_date: str,
    output_dir_arg: str | None,
    started_at: datetime,
) -> Path:
    if output_dir_arg:
        return _resolve_path(output_dir_arg)
    timestamp = started_at.strftime("%H%M%S")
    return (PROJECT_ROOT / "data" / "ops" / trade_date / f"rehearsal_{timestamp}").resolve()


def _resolve_summary_output_path(
    *,
    output_arg: str | None,
    output_dir: Path,
) -> Path:
    if output_arg:
        return _resolve_path(output_arg)
    return output_dir / "rehearsal_summary.json"


def _resolve_master_source(args: argparse.Namespace) -> tuple[str, str | None]:
    has_master_input = bool(args.master_input)
    if has_master_input and args.use_db_master:
        raise ValueError(
            "Use either --master-input or --use-db-master, not both."
        )
    if has_master_input:
        return "MASTER_INPUT", str(args.master_input)
    return "DB_MASTER", None


def _build_quick_window(
    *,
    reference_at: datetime,
    rehearsal_window_seconds: int,
) -> dict[str, str]:
    start_time = (reference_at - timedelta(seconds=rehearsal_window_seconds)).strftime(
        "%H:%M:%S"
    )
    cutoff_time = (reference_at + timedelta(seconds=rehearsal_window_seconds)).strftime(
        "%H:%M:%S"
    )
    return {
        "reference_at": reference_at.isoformat(),
        "start_time": start_time,
        "cutoff_time": cutoff_time,
    }


def _run_child(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    return int(completed.returncode)


def _build_startup_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "startup_check.py"),
        "--trade-date",
        args.trade_date,
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]


def _build_trading_session_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
    quick_window: dict[str, str],
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_trading_session.py"),
        "--trade-date",
        args.trade_date,
        "--master-format",
        args.master_format,
        "--per-order-budget",
        str(args.per_order_budget),
        "--max-holdings",
        str(args.max_holdings),
        "--interval-seconds",
        "1",
        "--max-cycles",
        "1",
        "--buy-start-time",
        quick_window["start_time"],
        "--buy-cutoff-time",
        quick_window["cutoff_time"],
        "--sell-start-time",
        quick_window["start_time"],
        "--sell-cutoff-time",
        quick_window["cutoff_time"],
        "--timing1-start-time",
        quick_window["start_time"],
        "--timing1-cutoff-time",
        quick_window["cutoff_time"],
        "--timing2-start-time",
        quick_window["start_time"],
        "--timing2-cutoff-time",
        quick_window["cutoff_time"],
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]
    if args.master_input:
        command.extend(["--master-input", args.master_input])
    else:
        command.append("--use-db-master")
    if args.require_same_day_master:
        command.append("--require-same-day-master")
    if args.allow_unresolved_orders:
        command.append("--allow-unresolved-orders")
    if args.max_daily_order_count is not None:
        command.extend(
            ["--max-daily-order-count", str(args.max_daily_order_count)]
        )
    if args.max_daily_loss is not None:
        command.extend(["--max-daily-loss", str(args.max_daily_loss)])
    if args.scan_timing1:
        command.append("--scan-timing1")
    if args.scan_timing2:
        command.append("--scan-timing2")
    return command


def _build_after_close_command(
    *,
    args: argparse.Namespace,
    db_path: str,
    output_path: Path,
) -> list[str]:
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_after_close_session.py"),
        "--trade-date",
        args.trade_date,
        "--db-path",
        db_path,
        "--output",
        str(output_path),
    ]


def _step_payload(
    *,
    name: str,
    exit_code: int | None,
    outcome: str,
    reason: str | None,
    output_path: Path,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "exit_code": exit_code,
        "outcome": outcome,
        "reason": reason,
        "output_path": str(output_path),
        "result": result,
    }


def _startup_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "startup_check.py did not write a JSON payload."
    outcome = str(payload.get("outcome", "")).strip()
    reason = _optional_text(payload.get("reason"))
    if exit_code == 0 and outcome == "READY":
        return "READY", None
    if exit_code == 4 or outcome == "BLOCKED":
        return "BLOCKED", reason or "Startup check blocked the rehearsal."
    return "FAILED", reason or f"Unexpected startup result: exit_code={exit_code}, outcome={outcome}"


def _session_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "run_trading_session.py did not write a JSON payload."
    outcome = _optional_text(payload.get("session_outcome"))
    reason = _optional_text(payload.get("session_reason"))
    if exit_code == 0 and outcome == "COMPLETED":
        return "COMPLETED", None
    if exit_code == 4 or outcome in (
        "PREOPEN_BLOCKED",
        "POLLING_BLOCKED",
        "POLLING_LOCK_BUSY",
    ):
        return "BLOCKED", reason or "Trading session preview was blocked."
    return "FAILED", reason or (
        "Unexpected trading session result: "
        f"exit_code={exit_code}, session_outcome={outcome}"
    )


def _after_close_step_outcome(
    *,
    exit_code: int,
    payload: dict[str, Any] | None,
) -> tuple[str, str | None]:
    if payload is None:
        return "FAILED", "run_after_close_session.py did not write a JSON payload."
    outcome = _optional_text(payload.get("session_outcome"))
    reason = _optional_text(payload.get("session_reason"))
    if exit_code == 0 and outcome == "COMPLETED":
        return "COMPLETED", None
    if exit_code == 4 or outcome == "LOCK_BUSY":
        return "BLOCKED", reason or "After-close preview was blocked."
    return "FAILED", reason or (
        "Unexpected after-close result: "
        f"exit_code={exit_code}, session_outcome={outcome}"
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def main() -> int:
    args = _parse_args()

    try:
        _validate_positive_int("per_order_budget", args.per_order_budget)
        _validate_positive_int("max_holdings", args.max_holdings)
        if args.max_daily_order_count is not None:
            _validate_positive_int(
                "max_daily_order_count",
                args.max_daily_order_count,
            )
        if args.max_daily_loss is not None:
            _validate_positive_int("max_daily_loss", args.max_daily_loss)
        _validate_positive_int(
            "rehearsal_window_seconds",
            args.rehearsal_window_seconds,
        )
        master_source_type, master_source_value = _resolve_master_source(args)

        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    if settings.mode != "mock":
        _fail(
            "mode",
            f"run_mock_operational_rehearsal.py only supports mock mode. current_mode={settings.mode}",
        )
        return 5

    started_at_dt = _now()
    db_path = args.db_path or settings.db_path
    output_dir = _resolve_output_dir(
        trade_date=args.trade_date,
        output_dir_arg=args.output_dir,
        started_at=started_at_dt,
    )
    summary_output_path = _resolve_summary_output_path(
        output_arg=args.output,
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    quick_window = _build_quick_window(
        reference_at=started_at_dt,
        rehearsal_window_seconds=args.rehearsal_window_seconds,
    )

    _section("Run Mock Operational Rehearsal")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))
    _ok("master_source", master_source_type)
    if master_source_value:
        _ok("master_input", master_source_value)
    _ok("output_dir", str(output_dir))
    _ok("include_after_close", str(args.include_after_close))
    _ok("quick_start_time", quick_window["start_time"])
    _ok("quick_cutoff_time", quick_window["cutoff_time"])
    _warn(
        "rehearsal_note",
        "This wrapper stays in preview mode, but the trading-session preview still writes preopen session state.",
    )

    steps: list[dict[str, Any]] = []
    overall_outcome = "UNKNOWN"
    overall_reason: str | None = None

    startup_output_path = output_dir / "startup_check.json"
    startup_command = _build_startup_command(
        args=args,
        db_path=str(db_path),
        output_path=startup_output_path,
    )
    startup_exit_code = _run_child(startup_command)
    startup_result = _load_json(startup_output_path)
    startup_step_outcome, startup_reason = _startup_step_outcome(
        exit_code=startup_exit_code,
        payload=startup_result,
    )
    steps.append(
        _step_payload(
            name="Startup Check",
            exit_code=startup_exit_code,
            outcome=startup_step_outcome,
            reason=startup_reason,
            output_path=startup_output_path,
            result=startup_result,
        )
    )

    if startup_step_outcome != "READY":
        overall_outcome = (
            "STARTUP_BLOCKED"
            if startup_step_outcome == "BLOCKED"
            else "STARTUP_FAILED"
        )
        overall_reason = startup_reason
    else:
        trading_output_path = output_dir / "trading_session_preview.json"
        trading_command = _build_trading_session_command(
            args=args,
            db_path=str(db_path),
            output_path=trading_output_path,
            quick_window=quick_window,
        )
        trading_exit_code = _run_child(trading_command)
        trading_result = _load_json(trading_output_path)
        trading_step_outcome, trading_reason = _session_step_outcome(
            exit_code=trading_exit_code,
            payload=trading_result,
        )
        steps.append(
            _step_payload(
                name="Trading Session Preview",
                exit_code=trading_exit_code,
                outcome=trading_step_outcome,
                reason=trading_reason,
                output_path=trading_output_path,
                result=trading_result,
            )
        )

        if trading_step_outcome != "COMPLETED":
            overall_outcome = (
                "TRADING_SESSION_BLOCKED"
                if trading_step_outcome == "BLOCKED"
                else "TRADING_SESSION_FAILED"
            )
            overall_reason = trading_reason
        elif args.include_after_close:
            after_close_output_path = output_dir / "after_close_preview.json"
            after_close_command = _build_after_close_command(
                args=args,
                db_path=str(db_path),
                output_path=after_close_output_path,
            )
            after_close_exit_code = _run_child(after_close_command)
            after_close_result = _load_json(after_close_output_path)
            after_close_step_outcome, after_close_reason = _after_close_step_outcome(
                exit_code=after_close_exit_code,
                payload=after_close_result,
            )
            steps.append(
                _step_payload(
                    name="After Close Preview",
                    exit_code=after_close_exit_code,
                    outcome=after_close_step_outcome,
                    reason=after_close_reason,
                    output_path=after_close_output_path,
                    result=after_close_result,
                )
            )
            if after_close_step_outcome != "COMPLETED":
                overall_outcome = (
                    "AFTER_CLOSE_BLOCKED"
                    if after_close_step_outcome == "BLOCKED"
                    else "AFTER_CLOSE_FAILED"
                )
                overall_reason = after_close_reason
            else:
                overall_outcome = "COMPLETED"
                overall_reason = None
        else:
            overall_outcome = "COMPLETED"
            overall_reason = None

    finished_at = _now().isoformat()
    payload = {
        "trade_date": args.trade_date,
        "mode": settings.mode,
        "db_path": str(db_path),
        "started_at": started_at_dt.isoformat(),
        "finished_at": finished_at,
        "master_source": {
            "type": master_source_type,
            "value": master_source_value,
        },
        "include_after_close": args.include_after_close,
        "rehearsal_window_seconds": args.rehearsal_window_seconds,
        "intraday_window": quick_window,
        "output_dir": str(output_dir),
        "overall_outcome": overall_outcome,
        "overall_reason": overall_reason,
        "steps": steps,
    }
    _save_json(summary_output_path, payload)

    _section("Rehearsal Result")
    _ok("overall_outcome", overall_outcome)
    if overall_reason:
        _warn("overall_reason", overall_reason)
    _ok("json_saved", str(summary_output_path))

    if overall_outcome == "COMPLETED":
        return 0
    if overall_outcome.endswith("_BLOCKED"):
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
