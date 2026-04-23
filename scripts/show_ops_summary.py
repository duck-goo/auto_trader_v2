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


def _build_startup_summary(step: dict[str, Any]) -> dict[str, Any]:
    result = _coerce_step_result(step) or {}
    unresolved_orders = result.get("unresolved_orders")
    live_positions = result.get("live_positions")
    universe_snapshot = result.get("universe_snapshot")
    return {
        "name": step.get("name"),
        "exit_code": step.get("exit_code"),
        "outcome": step.get("outcome"),
        "reason": step.get("reason"),
        "trade_date": result.get("trade_date"),
        "checked_at": result.get("checked_at"),
        "startup_outcome": result.get("outcome"),
        "startup_reason": result.get("reason"),
        "universe_exists": (
            None
            if not isinstance(universe_snapshot, dict)
            else universe_snapshot.get("exists")
        ),
        "universe_candidate_count": (
            None
            if not isinstance(universe_snapshot, dict)
            else universe_snapshot.get("candidate_count")
        ),
        "unresolved_order_count": (
            None if not isinstance(unresolved_orders, list) else len(unresolved_orders)
        ),
        "live_position_count": (
            None if not isinstance(live_positions, list) else len(live_positions)
        ),
        "output_path": step.get("output_path"),
    }


def _build_trading_summary(step: dict[str, Any]) -> dict[str, Any]:
    result = _coerce_step_result(step) or {}
    preopen_result = result.get("preopen_result")
    polling_result = result.get("polling_result")
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
        "timing2_30s_pipeline": _build_timing2_30s_pipeline_summary(
            polling_result
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


def _print_trading_summary(step: dict[str, Any]) -> None:
    _section("Trading Session Preview")
    _ok("session_outcome", str(step.get("session_outcome")))
    _ok("session_reason", "" if step.get("session_reason") is None else str(step.get("session_reason")))
    _ok("preopen_readiness_outcome", str(step.get("preopen_readiness_outcome")))
    _ok("preopen_readiness_reason", "" if step.get("preopen_readiness_reason") is None else str(step.get("preopen_readiness_reason")))
    _ok("polling_started", str(step.get("polling_started")))
    _ok("polling_stop_reason", "" if step.get("polling_stop_reason") is None else str(step.get("polling_stop_reason")))
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
