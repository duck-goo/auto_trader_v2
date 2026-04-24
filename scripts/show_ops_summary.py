"""
Show a concise summary from one mock operational rehearsal result.

Input:
- rehearsal_summary.json created by run_mock_operational_rehearsal.py

Safety:
- read-only
- accepts either the summary file path or the rehearsal output directory
- falls back to child JSON files when a step result is not embedded
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TIMING2_30S_STEP_NAMES = (
    "timing2_price_sample_capture",
    "timing2_30s_bar_build",
    "timing2_30s_trigger_scan",
)
OK_STEP_OUTCOMES = frozenset({"READY", "COMPLETED"})


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
        description="Show a concise summary from one operational rehearsal result."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        default=None,
        help="Path to rehearsal_summary.json.",
    )
    group.add_argument(
        "--output-dir",
        default=None,
        help="Path to rehearsal output directory containing rehearsal_summary.json.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional normalized JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _resolve_summary_path(args: argparse.Namespace) -> Path:
    if args.input:
        return _resolve_path(args.input)
    output_dir = _resolve_path(args.output_dir)
    return output_dir / "rehearsal_summary.json"


def _coerce_step_result(step: dict[str, Any]) -> dict[str, Any] | None:
    result = step.get("result")
    if isinstance(result, dict):
        return result
    output_path = step.get("output_path")
    if not isinstance(output_path, str) or not output_path.strip():
        return None
    path = Path(output_path)
    if not path.exists():
        return None
    return _load_json(path)


def _coerce_step_status(cycle: dict[str, Any], step_name: str) -> dict[str, Any]:
    step = cycle.get(step_name)
    if not isinstance(step, dict):
        return {
            "present": False,
            "outcome": None,
            "reason": None,
            "summary": None,
        }
    return {
        "present": True,
        "outcome": step.get("outcome"),
        "reason": step.get("reason"),
        "summary": step.get("summary") if isinstance(step.get("summary"), dict) else None,
    }


def _coerce_timing2_setup_readiness(polling_result: Any) -> dict[str, Any]:
    if not isinstance(polling_result, dict):
        return {}
    readiness = polling_result.get("timing2_setup_readiness")
    if not isinstance(readiness, dict):
        return {}
    return {
        "trade_date": readiness.get("trade_date"),
        "required": readiness.get("required"),
        "setup_signal_count": readiness.get("setup_signal_count"),
        "ready": readiness.get("ready"),
        "reason": readiness.get("reason"),
    }


def _build_timing2_30s_pipeline_summary(
    polling_result: Any,
) -> dict[str, Any]:
    if not isinstance(polling_result, dict):
        return {
            "cycle_found": False,
            "cycle_no": None,
            "all_steps_present": False,
            "all_steps_completed": False,
            "steps": {},
        }

    cycles = polling_result.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        return {
            "cycle_found": False,
            "cycle_no": None,
            "all_steps_present": False,
            "all_steps_completed": False,
            "steps": {},
        }

    first_cycle = cycles[0]
    if not isinstance(first_cycle, dict):
        return {
            "cycle_found": False,
            "cycle_no": None,
            "all_steps_present": False,
            "all_steps_completed": False,
            "steps": {},
        }

    steps = {
        step_name: _coerce_step_status(first_cycle, step_name)
        for step_name in TIMING2_30S_STEP_NAMES
    }
    return {
        "cycle_found": True,
        "cycle_no": first_cycle.get("cycle_no"),
        "all_steps_present": all(row["present"] for row in steps.values()),
        "all_steps_completed": all(
            row["present"] and row["outcome"] == "COMPLETED"
            for row in steps.values()
        ),
        "steps": steps,
    }


def _build_step_status_level(
    outcome: Any,
    warning_flags: list[str],
) -> str:
    outcome_text = _optional_text(outcome)
    if outcome_text == "FAILED":
        return "FAILED"
    if outcome_text in OK_STEP_OUTCOMES and not warning_flags:
        return "OK"
    if outcome_text in OK_STEP_OUTCOMES:
        return "WARNING"
    if outcome_text is None and not warning_flags:
        return "OK"
    return "WARNING"


def _build_startup_warning_flags(
    startup_outcome: Any,
    universe_exists: Any,
    unresolved_order_count: Any,
) -> list[str]:
    warning_flags: list[str] = []
    startup_outcome_text = _optional_text(startup_outcome)
    if startup_outcome_text == "BLOCKED":
        warning_flags.append("STARTUP_BLOCKED")
    elif startup_outcome_text and startup_outcome_text not in OK_STEP_OUTCOMES:
        warning_flags.append("STARTUP_NOT_READY")
    if universe_exists is False:
        warning_flags.append("UNIVERSE_MISSING")
    if isinstance(unresolved_order_count, int) and unresolved_order_count > 0:
        warning_flags.append("UNRESOLVED_ORDERS_PRESENT")
    return warning_flags


def _build_trading_warning_flags(
    session_outcome: Any,
    preopen_readiness_outcome: Any,
    polling_stop_reason: Any,
    timing2_setup_required: Any,
    timing2_setup_ready: Any,
    timing2_30s_pipeline: dict[str, Any],
) -> list[str]:
    warning_flags: list[str] = []
    session_outcome_text = _optional_text(session_outcome)
    if session_outcome_text and session_outcome_text not in OK_STEP_OUTCOMES:
        warning_flags.append("TRADING_SESSION_NOT_COMPLETED")
    preopen_outcome_text = _optional_text(preopen_readiness_outcome)
    if preopen_outcome_text and preopen_outcome_text != "READY":
        warning_flags.append("PREOPEN_NOT_READY")
    if timing2_setup_required is True and timing2_setup_ready is not True:
        warning_flags.append("TIMING2_SETUP_NOT_READY")
    if (
        isinstance(timing2_30s_pipeline, dict)
        and timing2_30s_pipeline.get("cycle_found")
        and not timing2_30s_pipeline.get("all_steps_completed")
    ):
        warning_flags.append("TIMING2_30S_PIPELINE_INCOMPLETE")

    stop_reason_text = _optional_text(polling_stop_reason)
    if stop_reason_text == "MAX_DAILY_LOSS_REACHED":
        warning_flags.append("MAX_DAILY_LOSS_REACHED")
    elif stop_reason_text == "MAX_CONSECUTIVE_FAILURES":
        warning_flags.append("POLLING_FAILURE_THRESHOLD_REACHED")
    elif stop_reason_text == "INTERRUPTED":
        warning_flags.append("POLLING_INTERRUPTED")
    elif stop_reason_text and stop_reason_text.startswith("FAILED:"):
        warning_flags.append("POLLING_FAILED")

    return warning_flags


def _build_after_close_warning_flags(
    session_outcome: Any,
    step_rows: list[dict[str, Any]],
) -> list[str]:
    warning_flags: list[str] = []
    session_outcome_text = _optional_text(session_outcome)
    if session_outcome_text and session_outcome_text not in OK_STEP_OUTCOMES:
        warning_flags.append("AFTER_CLOSE_NOT_COMPLETED")
    if any(
        _optional_text(row.get("outcome")) not in (None, "COMPLETED")
        for row in step_rows
        if isinstance(row, dict)
    ):
        warning_flags.append("AFTER_CLOSE_STEP_INCOMPLETE")
    return warning_flags


def _build_step_status_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "ok": 0,
        "warning": 0,
        "failed": 0,
    }
    for step in steps:
        if not isinstance(step, dict):
            continue
        status_level = _optional_text(step.get("status_level"))
        if status_level == "FAILED":
            counts["failed"] += 1
        elif status_level == "WARNING":
            counts["warning"] += 1
        else:
            counts["ok"] += 1
    return counts


def _build_startup_summary(step: dict[str, Any]) -> dict[str, Any]:
    result = _coerce_step_result(step) or {}
    unresolved_orders = result.get("unresolved_orders")
    live_positions = result.get("live_positions")
    universe_snapshot = result.get("universe_snapshot")
    universe_exists = (
        None
        if not isinstance(universe_snapshot, dict)
        else universe_snapshot.get("exists")
    )
    unresolved_order_count = (
        None if not isinstance(unresolved_orders, list) else len(unresolved_orders)
    )
    warning_flags = _build_startup_warning_flags(
        startup_outcome=result.get("outcome"),
        universe_exists=universe_exists,
        unresolved_order_count=unresolved_order_count,
    )
    return {
        "name": step.get("name"),
        "exit_code": step.get("exit_code"),
        "outcome": step.get("outcome"),
        "reason": step.get("reason"),
        "trade_date": result.get("trade_date"),
        "checked_at": result.get("checked_at"),
        "startup_outcome": result.get("outcome"),
        "startup_reason": result.get("reason"),
        "universe_exists": universe_exists,
        "universe_candidate_count": (
            None
            if not isinstance(universe_snapshot, dict)
            else universe_snapshot.get("candidate_count")
        ),
        "unresolved_order_count": unresolved_order_count,
        "live_position_count": (
            None if not isinstance(live_positions, list) else len(live_positions)
        ),
        "warning_flags": warning_flags,
        "status_level": _build_step_status_level(
            step.get("outcome"),
            warning_flags,
        ),
        "output_path": step.get("output_path"),
    }


def _build_trading_summary(step: dict[str, Any]) -> dict[str, Any]:
    result = _coerce_step_result(step) or {}
    preopen_result = result.get("preopen_result")
    polling_result = result.get("polling_result")
    timing2_setup_readiness = _coerce_timing2_setup_readiness(polling_result)
    timing2_30s_pipeline = _build_timing2_30s_pipeline_summary(
        polling_result
    )
    warning_flags = _build_trading_warning_flags(
        session_outcome=result.get("session_outcome"),
        preopen_readiness_outcome=(
            None
            if not isinstance(preopen_result, dict)
            else preopen_result.get("readiness_outcome")
        ),
        polling_stop_reason=(
            None
            if not isinstance(polling_result, dict)
            else polling_result.get("stop_reason")
        ),
        timing2_setup_required=timing2_setup_readiness.get("required"),
        timing2_setup_ready=timing2_setup_readiness.get("ready"),
        timing2_30s_pipeline=timing2_30s_pipeline,
    )
    return {
        "name": step.get("name"),
        "exit_code": step.get("exit_code"),
        "outcome": step.get("outcome"),
        "reason": step.get("reason"),
        "trade_date": result.get("trade_date"),
        "session_outcome": result.get("session_outcome"),
        "session_reason": result.get("session_reason"),
        "preopen_exit_code": result.get("preopen_exit_code"),
        "preopen_readiness_outcome": (
            None
            if not isinstance(preopen_result, dict)
            else preopen_result.get("readiness_outcome")
        ),
        "preopen_readiness_reason": (
            None
            if not isinstance(preopen_result, dict)
            else (
                preopen_result.get("readiness_reason")
                or preopen_result.get("error_message")
            )
        ),
        "polling_started": result.get("polling_started"),
        "polling_exit_code": result.get("polling_exit_code"),
        "polling_stop_reason": (
            None
            if not isinstance(polling_result, dict)
            else polling_result.get("stop_reason")
        ),
        "timing2_setup_readiness": timing2_setup_readiness,
        "timing2_setup_required": timing2_setup_readiness.get("required"),
        "timing2_setup_ready": timing2_setup_readiness.get("ready"),
        "timing2_setup_signal_count": timing2_setup_readiness.get(
            "setup_signal_count"
        ),
        "timing2_setup_reason": timing2_setup_readiness.get("reason"),
        "timing2_30s_pipeline": timing2_30s_pipeline,
        "warning_flags": warning_flags,
        "status_level": _build_step_status_level(
            step.get("outcome"),
            warning_flags,
        ),
        "output_path": step.get("output_path"),
    }


def _build_after_close_summary(step: dict[str, Any]) -> dict[str, Any]:
    result = _coerce_step_result(step) or {}
    steps = result.get("steps")
    step_rows: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for row in steps:
            if not isinstance(row, dict):
                continue
            step_rows.append(
                {
                    "name": row.get("name"),
                    "outcome": row.get("outcome"),
                    "reason": row.get("reason"),
                    "exit_code": row.get("exit_code"),
                }
            )
    warning_flags = _build_after_close_warning_flags(
        session_outcome=result.get("session_outcome"),
        step_rows=step_rows,
    )
    return {
        "name": step.get("name"),
        "exit_code": step.get("exit_code"),
        "outcome": step.get("outcome"),
        "reason": step.get("reason"),
        "trade_date": result.get("trade_date"),
        "session_outcome": result.get("session_outcome"),
        "session_reason": result.get("session_reason"),
        "write_mode": result.get("write_mode"),
        "lock_acquired": result.get("lock_acquired"),
        "lock_released": result.get("lock_released"),
        "steps": step_rows,
        "warning_flags": warning_flags,
        "status_level": _build_step_status_level(
            step.get("outcome"),
            warning_flags,
        ),
        "output_path": step.get("output_path"),
    }


def _build_normalized_payload(summary_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps")
    normalized_steps: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            name = _optional_text(step.get("name"))
            if name == "Startup Check":
                normalized_steps.append(_build_startup_summary(step))
            elif name == "Trading Session Preview":
                normalized_steps.append(_build_trading_summary(step))
            elif name == "After Close Preview":
                normalized_steps.append(_build_after_close_summary(step))
            else:
                normalized_steps.append(
                    {
                        "name": name,
                        "exit_code": step.get("exit_code"),
                        "outcome": step.get("outcome"),
                        "reason": step.get("reason"),
                        "output_path": step.get("output_path"),
                    }
                )
    return {
        "summary_path": str(summary_path),
        "trade_date": payload.get("trade_date"),
        "mode": payload.get("mode"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "overall_outcome": payload.get("overall_outcome"),
        "overall_reason": payload.get("overall_reason"),
        "include_after_close": payload.get("include_after_close"),
        "intraday_window": payload.get("intraday_window"),
        "scan_settings": payload.get("scan_settings"),
        "step_status_counts": _build_step_status_counts(normalized_steps),
        "steps": normalized_steps,
    }


def _print_startup_summary(step: dict[str, Any]) -> None:
    _section("Startup Check")
    _ok("outcome", str(step.get("startup_outcome")))
    _ok("reason", "" if step.get("startup_reason") is None else str(step.get("startup_reason")))
    _ok("universe_exists", str(step.get("universe_exists")))
    _ok("universe_candidate_count", str(step.get("universe_candidate_count")))
    _ok("unresolved_order_count", str(step.get("unresolved_order_count")))
    _ok("live_position_count", str(step.get("live_position_count")))
    warning_flags = step.get("warning_flags")
    if isinstance(warning_flags, list) and warning_flags:
        _warn("warning_flags", ", ".join(str(row) for row in warning_flags))


def _print_trading_summary(step: dict[str, Any]) -> None:
    _section("Trading Session Preview")
    _ok("session_outcome", str(step.get("session_outcome")))
    _ok("session_reason", "" if step.get("session_reason") is None else str(step.get("session_reason")))
    _ok("preopen_readiness_outcome", str(step.get("preopen_readiness_outcome")))
    _ok("preopen_readiness_reason", "" if step.get("preopen_readiness_reason") is None else str(step.get("preopen_readiness_reason")))
    _ok("polling_started", str(step.get("polling_started")))
    _ok("polling_stop_reason", "" if step.get("polling_stop_reason") is None else str(step.get("polling_stop_reason")))
    warning_flags = step.get("warning_flags")
    if isinstance(warning_flags, list) and warning_flags:
        _warn("warning_flags", ", ".join(str(row) for row in warning_flags))
    if step.get("timing2_setup_required") is True:
        _ok(
            "timing2_setup_signal_count",
            str(step.get("timing2_setup_signal_count")),
        )
        _ok("timing2_setup_ready", str(step.get("timing2_setup_ready")))
        if step.get("timing2_setup_reason"):
            _warn("timing2_setup_reason", str(step.get("timing2_setup_reason")))
    pipeline = step.get("timing2_30s_pipeline")
    if not isinstance(pipeline, dict) or not pipeline.get("cycle_found"):
        return
    _ok("timing2_30s_cycle_no", str(pipeline.get("cycle_no")))
    _ok("timing2_30s_all_steps_present", str(pipeline.get("all_steps_present")))
    _ok("timing2_30s_all_steps_completed", str(pipeline.get("all_steps_completed")))
    steps = pipeline.get("steps")
    if not isinstance(steps, dict):
        return
    for step_name in TIMING2_30S_STEP_NAMES:
        row = steps.get(step_name)
        if not isinstance(row, dict):
            continue
        _ok(f"{step_name}_outcome", str(row.get("outcome")))
        if row.get("reason"):
            _warn(f"{step_name}_reason", str(row.get("reason")))


def _print_after_close_summary(step: dict[str, Any]) -> None:
    _section("After Close Preview")
    _ok("session_outcome", str(step.get("session_outcome")))
    _ok("session_reason", "" if step.get("session_reason") is None else str(step.get("session_reason")))
    _ok("lock_acquired", str(step.get("lock_acquired")))
    _ok("lock_released", str(step.get("lock_released")))
    warning_flags = step.get("warning_flags")
    if isinstance(warning_flags, list) and warning_flags:
        _warn("warning_flags", ", ".join(str(row) for row in warning_flags))
    step_rows = step.get("steps")
    if not isinstance(step_rows, list):
        return
    for row in step_rows:
        if not isinstance(row, dict):
            continue
        print(
            f"{row.get('name')} outcome={row.get('outcome')} "
            f"reason={'' if row.get('reason') is None else row.get('reason')}"
        )


def main() -> int:
    args = _parse_args()

    try:
        summary_path = _resolve_summary_path(args)
        output_path = _resolve_path(args.output) if args.output else None
        payload = _load_json(summary_path)
        normalized = _build_normalized_payload(summary_path, payload)
    except Exception as exc:
        _fail("summary", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Ops Summary")
    _ok("summary_path", str(summary_path))
    _ok("trade_date", str(normalized.get("trade_date")))
    _ok("mode", str(normalized.get("mode")))
    _ok("overall_outcome", str(normalized.get("overall_outcome")))
    if normalized.get("overall_reason"):
        _warn("overall_reason", str(normalized.get("overall_reason")))

    for step in normalized["steps"]:
        name = step.get("name")
        if name == "Startup Check":
            _print_startup_summary(step)
        elif name == "Trading Session Preview":
            _print_trading_summary(step)
        elif name == "After Close Preview":
            _print_after_close_summary(step)

    if output_path is not None:
        _save_json(output_path, normalized)
        _ok("json_saved", str(output_path))

    overall_outcome = _optional_text(normalized.get("overall_outcome")) or "UNKNOWN"
    if overall_outcome == "COMPLETED":
        return 0
    if overall_outcome.endswith("_BLOCKED"):
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
