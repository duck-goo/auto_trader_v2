"""
Build one frontend-ready dashboard snapshot from ops artifacts.

Inputs:
- daily_ops_report.json created by show_daily_ops_report.py
- daily_ops_check.json created by run_daily_ops_check.py
- latest rehearsal_summary.json created by run_mock_operational_rehearsal.py

Safety:
- read-only for source artifacts
- tolerates missing sources and still writes one stable JSON snapshot
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

import scripts.show_ops_summary as ops_summary_script
from strategy import (
    BUY_STRATEGY_BOTH,
    BUY_STRATEGY_CHOICES,
    resolve_buy_strategy_selection,
)

KST = pytz.timezone("Asia/Seoul")
BUY_STRATEGY_SELECTION_FILE = "buy_strategy.selection.json"
DAILY_OPS_CHECK_FILE = "daily_ops_check.json"


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
        description="Build one frontend-ready dashboard snapshot from ops artifacts."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST.",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>",
    )
    parser.add_argument(
        "--daily-report-input",
        default=None,
        help="Optional path to daily_ops_report.json.",
    )
    parser.add_argument(
        "--daily-check-input",
        default=None,
        help="Optional path to daily_ops_check.json.",
    )
    parser.add_argument(
        "--rehearsal-input",
        default=None,
        help="Optional path to rehearsal_summary.json. Default: latest rehearsal under ops dir.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional dashboard snapshot JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _resolve_optional_path(path_value: str | Path | None) -> Path | None:
    if path_value is None:
        return None
    return _resolve_path(str(path_value))


def _resolve_ops_dir(args: argparse.Namespace) -> Path:
    if args.ops_dir:
        return _resolve_path(args.ops_dir)
    return (PROJECT_ROOT / "data" / "ops" / args.trade_date).resolve()


def _resolve_daily_report_path(args: argparse.Namespace, ops_dir: Path) -> Path:
    if args.daily_report_input:
        return _resolve_path(args.daily_report_input)
    return ops_dir / "daily_ops_report.json"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _find_latest_rehearsal_summary(ops_dir: Path) -> Path | None:
    if not ops_dir.exists():
        return None
    candidates: list[tuple[str, str, Path]] = []
    for child in sorted(ops_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        summary_path = child / "rehearsal_summary.json"
        payload = _load_optional_json(summary_path)
        if payload is None:
            continue
        finished_at = _optional_text(payload.get("finished_at")) or ""
        started_at = _optional_text(payload.get("started_at")) or ""
        candidates.append((finished_at, started_at, summary_path))
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (row[0], row[1], str(row[2])),
        reverse=True,
    )
    return candidates[0][2]


def _resolve_rehearsal_path(args: argparse.Namespace, ops_dir: Path) -> Path | None:
    if args.rehearsal_input:
        return _resolve_path(args.rehearsal_input)
    return _find_latest_rehearsal_summary(ops_dir)


def _status_level_from_health_outcome(health_outcome: str | None) -> str:
    if health_outcome in ("READY", "WARNING", "CRITICAL"):
        return health_outcome
    return "NO_DATA"


def _normalize_status_level(value: Any, fallback: str = "MISSING") -> str:
    text = _optional_text(value)
    if text in ("READY", "WARNING", "CRITICAL", "FAILED", "MISSING", "NO_DATA"):
        return text
    return fallback


def _artifact_row(artifacts: dict[str, Any], key: str) -> dict[str, Any]:
    row = artifacts.get(key)
    if isinstance(row, dict):
        return row
    return {
        "exists": False,
        "status_level": "MISSING",
        "highest_severity": "NONE",
        "attention_flags": [],
    }


def _coerce_attention_flags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    flags: list[str] = []
    for row in value:
        text = _optional_text(row)
        if text is not None:
            flags.append(text)
    return flags


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            rows.append(text)
    return rows


def _extract_symbol_hint(text: Any) -> str | None:
    normalized = _optional_text(text)
    if normalized is None:
        return None
    markers = ("Affected symbols:", "Review executions first:")
    for marker in markers:
        if marker not in normalized:
            continue
        suffix = normalized.split(marker, 1)[1].strip()
        return suffix or None
    return None


def _build_overview(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "daily_report_available": False,
            "status_level": "NO_DATA",
            "health_outcome": None,
            "highest_severity": "NONE",
            "report_outcome": None,
            "artifact_count": 0,
            "attention_flag_count": 0,
            "critical_flag_count": 0,
            "warning_flag_count": 0,
            "action_required": False,
            "top_action_codes": [],
        }

    action_items = report.get("action_items")
    if not isinstance(action_items, list):
        action_items = []
    top_action_codes: list[str] = []
    for row in action_items:
        if not isinstance(row, dict):
            continue
        action_code = _optional_text(row.get("action_code"))
        if action_code is None:
            continue
        top_action_codes.append(action_code)
        if len(top_action_codes) >= 5:
            break

    alert = report.get("alert")
    critical_count = None
    warning_count = None
    if isinstance(alert, dict):
        critical_count = alert.get("critical_count")
        warning_count = alert.get("warning_count")

    return {
        "daily_report_available": True,
        "status_level": _status_level_from_health_outcome(
            _optional_text(report.get("health_outcome"))
        ),
        "health_outcome": report.get("health_outcome"),
        "highest_severity": report.get("highest_severity"),
        "report_outcome": report.get("report_outcome"),
        "artifact_count": report.get("artifact_count"),
        "attention_flag_count": len(_coerce_attention_flags(report.get("attention_flags"))),
        "critical_flag_count": 0 if not isinstance(critical_count, int) else critical_count,
        "warning_flag_count": 0 if not isinstance(warning_count, int) else warning_count,
        "action_required": bool(action_items),
        "top_action_codes": top_action_codes,
    }


def _build_controls(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "kill_switch_enabled": None,
            "kill_switch_note": None,
            "kill_switch_updated_at": None,
            "kill_switch_status_level": "MISSING",
        }

    latest_kill_switch = report.get("latest_kill_switch")
    if not isinstance(latest_kill_switch, dict):
        return {
            "kill_switch_enabled": None,
            "kill_switch_note": None,
            "kill_switch_updated_at": None,
            "kill_switch_status_level": "MISSING",
        }

    enabled = latest_kill_switch.get("enabled")
    if enabled is True:
        status_level = "CRITICAL"
    elif enabled is False:
        status_level = "READY"
    else:
        status_level = "MISSING"

    return {
        "kill_switch_enabled": enabled,
        "kill_switch_note": latest_kill_switch.get("note"),
        "kill_switch_updated_at": latest_kill_switch.get("updated_at"),
        "kill_switch_status_level": status_level,
    }


def _build_startup_section(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "available": False,
            "status_level": "MISSING",
            "highest_severity": "NONE",
            "outcome": None,
            "reason": None,
            "checked_at": None,
            "universe_exists": None,
            "universe_candidate_count": None,
            "reconcile_changed_rows": None,
            "unresolved_order_count": None,
            "live_position_count": None,
            "reconcile_reason_code": None,
            "reconcile_reason_message": None,
            "attention_flags": [],
        }

    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    row = _artifact_row(artifacts, "startup_check")
    return {
        "available": bool(row.get("exists")),
        "status_level": row.get("status_level"),
        "highest_severity": row.get("highest_severity"),
        "outcome": row.get("outcome"),
        "reason": row.get("reason"),
        "checked_at": row.get("checked_at"),
        "universe_exists": row.get("universe_exists"),
        "universe_candidate_count": row.get("universe_candidate_count"),
        "reconcile_changed_rows": row.get("reconcile_changed_rows"),
        "unresolved_order_count": row.get("unresolved_order_count"),
        "live_position_count": row.get("live_position_count"),
        "reconcile_reason_code": row.get("reconcile_reason_code"),
        "reconcile_reason_message": row.get("reconcile_reason_message"),
        "attention_flags": _coerce_attention_flags(row.get("attention_flags")),
    }


def _build_strategy_section(
    strategy_selection_path: Path,
    strategy_selection: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(strategy_selection, dict):
        return {
            "selection_available": False,
            "source": "default",
            "selection_path": str(strategy_selection_path),
            "buy_strategy": BUY_STRATEGY_BOTH,
            "effective_buy_strategy": BUY_STRATEGY_BOTH,
            "run_timing1": True,
            "run_timing2": True,
            "updated_at": None,
            "note": None,
            "applies_to_next_run": True,
        }

    buy_strategy = _optional_text(strategy_selection.get("buy_strategy"))
    if buy_strategy not in BUY_STRATEGY_CHOICES:
        return {
            "selection_available": True,
            "source": "selection_artifact",
            "selection_path": str(strategy_selection_path),
            "buy_strategy": None,
            "effective_buy_strategy": None,
            "run_timing1": None,
            "run_timing2": None,
            "updated_at": strategy_selection.get("updated_at"),
            "note": strategy_selection.get("note"),
            "applies_to_next_run": True,
            "status_level": "WARNING",
            "warning": "Invalid buy_strategy in selection artifact.",
        }

    run_timing1, run_timing2 = resolve_buy_strategy_selection(
        buy_strategy=buy_strategy,
        scan_timing1=False,
        scan_timing2=False,
    )
    return {
        "selection_available": True,
        "source": "selection_artifact",
        "selection_path": str(strategy_selection_path),
        "buy_strategy": buy_strategy,
        "effective_buy_strategy": buy_strategy,
        "run_timing1": run_timing1,
        "run_timing2": run_timing2,
        "updated_at": strategy_selection.get("updated_at"),
        "note": strategy_selection.get("note"),
        "applies_to_next_run": True,
        "status_level": "READY",
    }


def _build_trading_session_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(row.get("exists")),
        "status_level": row.get("status_level"),
        "highest_severity": row.get("highest_severity"),
        "session_outcome": row.get("session_outcome"),
        "session_reason": row.get("session_reason"),
        "preopen_readiness_outcome": row.get("preopen_readiness_outcome"),
        "preopen_readiness_reason": row.get("preopen_readiness_reason"),
        "polling_started": row.get("polling_started"),
        "polling_exit_code": row.get("polling_exit_code"),
        "polling_stop_reason": row.get("polling_stop_reason"),
        "timing2_setup_required": row.get("timing2_setup_required"),
        "timing2_setup_ready": row.get("timing2_setup_ready"),
        "timing2_setup_signal_count": row.get("timing2_setup_signal_count"),
        "attention_flags": _coerce_attention_flags(row.get("attention_flags")),
    }


def _build_execution_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(row.get("exists")),
        "status_level": row.get("status_level"),
        "highest_severity": row.get("highest_severity"),
        "stop_reason": row.get("stop_reason"),
        "blocked_count": row.get("blocked_count"),
        "preview_ready_count": row.get("preview_ready_count"),
        "submitted_count": row.get("submitted_count"),
        "acted_count": row.get("acted_count"),
        "attention_flags": _coerce_attention_flags(row.get("attention_flags")),
    }


def _build_recovery_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(row.get("exists")),
        "status_level": row.get("status_level"),
        "highest_severity": row.get("highest_severity"),
        "manual_recovery_required_count": row.get("manual_recovery_required_count"),
        "attention_flags": _coerce_attention_flags(row.get("attention_flags")),
    }


def _with_card_key(payload: dict[str, Any], card_key: str) -> dict[str, Any]:
    row = dict(payload)
    row["card_key"] = card_key
    return row


def _find_ops_summary_step(
    normalized_summary: dict[str, Any] | None,
    name: str,
) -> dict[str, Any] | None:
    if not isinstance(normalized_summary, dict):
        return None
    steps = normalized_summary.get("steps")
    if not isinstance(steps, list):
        return None
    for row in steps:
        if isinstance(row, dict) and row.get("name") == name:
            return row
    return None


def _build_rehearsal_status_level(normalized_summary: dict[str, Any] | None) -> str:
    if not isinstance(normalized_summary, dict):
        return "MISSING"
    counts = normalized_summary.get("step_status_counts")
    if isinstance(counts, dict):
        failed = counts.get("failed")
        warning = counts.get("warning")
        if isinstance(failed, int) and failed > 0:
            return "WARNING"
        if isinstance(warning, int) and warning > 0:
            return "WARNING"
        return "READY"
    overall_outcome = _optional_text(normalized_summary.get("overall_outcome"))
    if overall_outcome == "COMPLETED":
        return "READY"
    if overall_outcome:
        return "WARNING"
    return "MISSING"


def _build_rehearsal_section(
    rehearsal_path: Path | None,
    normalized_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    if rehearsal_path is None or not isinstance(normalized_summary, dict):
        return {
            "available": False,
            "path": None if rehearsal_path is None else str(rehearsal_path),
            "status_level": "MISSING",
            "overall_outcome": None,
            "overall_reason": None,
            "step_status_counts": None,
            "trading_session": {
                "status_level": "MISSING",
                "session_outcome": None,
                "polling_stop_reason": None,
                "timing2_setup_ready": None,
                "timing2_30s_verified": None,
                "attention_flags": [],
            },
        }

    trading_step = _find_ops_summary_step(normalized_summary, "Trading Session Preview")
    timing2_pipeline = None
    if isinstance(trading_step, dict):
        timing2_pipeline = trading_step.get("timing2_30s_pipeline")

    timing2_verified = None
    if isinstance(timing2_pipeline, dict) and timing2_pipeline.get("cycle_found"):
        timing2_verified = bool(timing2_pipeline.get("all_steps_completed"))

    trading_attention_flags = []
    if isinstance(trading_step, dict):
        trading_attention_flags = _coerce_attention_flags(trading_step.get("warning_flags"))

    return {
        "available": True,
        "path": str(rehearsal_path),
        "status_level": _build_rehearsal_status_level(normalized_summary),
        "overall_outcome": normalized_summary.get("overall_outcome"),
        "overall_reason": normalized_summary.get("overall_reason"),
        "step_status_counts": normalized_summary.get("step_status_counts"),
        "trading_session": {
            "status_level": (
                None if not isinstance(trading_step, dict) else trading_step.get("status_level")
            ),
            "session_outcome": (
                None if not isinstance(trading_step, dict) else trading_step.get("session_outcome")
            ),
            "polling_stop_reason": (
                None
                if not isinstance(trading_step, dict)
                else trading_step.get("polling_stop_reason")
            ),
            "timing2_setup_ready": (
                None
                if not isinstance(trading_step, dict)
                else trading_step.get("timing2_setup_ready")
            ),
            "timing2_30s_verified": timing2_verified,
            "attention_flags": trading_attention_flags,
        },
    }


def _build_scan_section(
    report: dict[str, Any] | None,
    rehearsal_section: dict[str, Any],
) -> dict[str, Any]:
    artifacts = {}
    if isinstance(report, dict):
        raw_artifacts = report.get("artifacts")
        if isinstance(raw_artifacts, dict):
            artifacts = raw_artifacts

    return {
        "live_preview": _with_card_key(
            _build_trading_session_row(
                _artifact_row(artifacts, "trading_session_preview")
            ),
            "scan-live-preview",
        ),
        "live_execute": _with_card_key(
            _build_trading_session_row(
                _artifact_row(artifacts, "trading_session_execute")
            ),
            "scan-live-execute",
        ),
        "rehearsal_validation": _with_card_key(
            rehearsal_section["trading_session"],
            "scan-rehearsal-validation",
        ),
    }


def _build_executions_section(report: dict[str, Any] | None) -> dict[str, Any]:
    artifacts = {}
    if isinstance(report, dict):
        raw_artifacts = report.get("artifacts")
        if isinstance(raw_artifacts, dict):
            artifacts = raw_artifacts

    return {
        "buy_preview": _with_card_key(
            _build_execution_row(
                _artifact_row(artifacts, "execute_buy_signals_preview")
            ),
            "execution-buy-preview",
        ),
        "buy_execute": _with_card_key(
            _build_execution_row(
                _artifact_row(artifacts, "execute_buy_signals_execute")
            ),
            "execution-buy-execute",
        ),
        "sell_preview": _with_card_key(
            _build_execution_row(
                _artifact_row(artifacts, "execute_sell_signals_preview")
            ),
            "execution-sell-preview",
        ),
        "sell_execute": _with_card_key(
            _build_execution_row(
                _artifact_row(artifacts, "execute_sell_signals_execute")
            ),
            "execution-sell-execute",
        ),
    }


def _build_recovery_section(report: dict[str, Any] | None) -> dict[str, Any]:
    artifacts = {}
    if isinstance(report, dict):
        raw_artifacts = report.get("artifacts")
        if isinstance(raw_artifacts, dict):
            artifacts = raw_artifacts

    review_row = _artifact_row(artifacts, "execution_recovery_review")
    return {
        "order_maintenance_preview": _with_card_key(
            _build_recovery_row(_artifact_row(artifacts, "order_maintenance_preview")),
            "recovery-maintenance-preview",
        ),
        "order_maintenance_execute": _with_card_key(
            _build_recovery_row(_artifact_row(artifacts, "order_maintenance_execute")),
            "recovery-maintenance-execute",
        ),
        "execution_recovery_review": _with_card_key(
            {
                "available": bool(review_row.get("exists")),
                "status_level": review_row.get("status_level"),
                "highest_severity": review_row.get("highest_severity"),
                "manual_recovery_required_count": review_row.get(
                    "manual_recovery_required_count"
                ),
                "attention_flags": _coerce_attention_flags(
                    review_row.get("attention_flags")
                ),
            },
            "recovery-execution-review",
        ),
    }


def _build_actions_section(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "required": False,
            "count": 0,
            "items": [],
            "top_action_codes": [],
        }

    action_items = report.get("action_items")
    if not isinstance(action_items, list):
        action_items = []

    top_action_codes: list[str] = []
    for row in action_items:
        if not isinstance(row, dict):
            continue
        action_code = _optional_text(row.get("action_code"))
        if action_code is None:
            continue
        top_action_codes.append(action_code)
        if len(top_action_codes) >= 5:
            break

    return {
        "required": bool(action_items),
        "count": len(action_items),
        "items": action_items,
        "top_action_codes": top_action_codes,
    }


def _build_operator_summary_from_daily_check(
    daily_check: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(daily_check, dict):
        return None

    operator_summary = daily_check.get("operator_summary")
    if not isinstance(operator_summary, dict):
        return None

    headline = _optional_text(operator_summary.get("headline"))
    detail = _optional_text(operator_summary.get("detail"))
    primary_attention_flag = _optional_text(
        operator_summary.get("primary_attention_flag")
    )
    if headline is None and detail is None and primary_attention_flag is None:
        return None

    affected_symbols = _optional_text(operator_summary.get("affected_symbols"))
    if affected_symbols is None:
        affected_symbols = _extract_symbol_hint(detail)

    status_level = _status_level_from_health_outcome(
        _optional_text(operator_summary.get("health_outcome"))
    )
    if status_level == "NO_DATA":
        if operator_summary.get("startup_open_entry_lot_position_mismatch") is True:
            status_level = "WARNING"
        elif _optional_text(operator_summary.get("primary_attention_flag")) is not None:
            status_level = "WARNING"
        else:
            status_level = "MISSING"

    return {
        "available": True,
        "source": "daily_ops_check",
        "status_level": status_level,
        "headline": headline,
        "detail": detail,
        "overall_outcome": daily_check.get("overall_outcome"),
        "overall_reason": daily_check.get("overall_reason"),
        "health_outcome": operator_summary.get("health_outcome"),
        "should_notify": daily_check.get("should_notify"),
        "dispatch_outcome": operator_summary.get("dispatch_outcome"),
        "primary_attention_flag": primary_attention_flag,
        "primary_action_code": _optional_text(
            operator_summary.get("primary_action_code")
        ),
        "startup_open_entry_lot_position_mismatch": (
            operator_summary.get("startup_open_entry_lot_position_mismatch") is True
        ),
        "affected_symbols": affected_symbols,
    }


def _build_operator_summary_from_daily_report(
    daily_report: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(daily_report, dict):
        return {
            "available": False,
            "source": "none",
            "status_level": "MISSING",
            "headline": None,
            "detail": None,
            "overall_outcome": None,
            "overall_reason": None,
            "health_outcome": None,
            "should_notify": None,
            "dispatch_outcome": None,
            "primary_attention_flag": None,
            "primary_action_code": None,
            "startup_open_entry_lot_position_mismatch": False,
            "affected_symbols": None,
        }

    alert = daily_report.get("alert")
    alert_summary = None
    alert_lines: list[str] = []
    alert_level = None
    if isinstance(alert, dict):
        alert_summary = _optional_text(alert.get("summary"))
        alert_lines = _coerce_string_list(alert.get("lines"))
        alert_level = _optional_text(alert.get("level"))

    action_items = daily_report.get("action_items")
    primary_action_code = None
    if isinstance(action_items, list):
        for row in action_items:
            if not isinstance(row, dict):
                continue
            primary_action_code = _optional_text(row.get("action_code"))
            if primary_action_code is not None:
                break

    startup = _build_startup_section(daily_report)
    startup_mismatch = (
        startup.get("reconcile_reason_code") == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    primary_attention_flag = None
    startup_flags = _coerce_attention_flags(startup.get("attention_flags"))
    if startup_flags:
        primary_attention_flag = startup_flags[0]
    else:
        report_flags = _coerce_attention_flags(daily_report.get("attention_flags"))
        if report_flags:
            primary_attention_flag = report_flags[0]

    detail = None
    if alert_lines:
        detail = " | ".join(alert_lines[:2])

    if startup_mismatch:
        headline = "Startup blocked by open entry lot position mismatch."
        detail = (
            _optional_text(startup.get("reconcile_reason_message"))
            or detail
            or "Review executions and lot state before rerunning startup."
        )
    elif alert_summary is not None:
        headline = alert_summary
    elif daily_report.get("health_outcome") == "READY":
        headline = "Daily ops looks ready."
        detail = detail or "No attention flags detected."
    else:
        headline = "Daily ops requires review."
        detail = detail or "Review the latest action items before live trading."

    return {
        "available": True,
        "source": "daily_ops_report_fallback",
        "status_level": _normalize_status_level(
            alert_level,
            fallback=_status_level_from_health_outcome(
                _optional_text(daily_report.get("health_outcome"))
            ),
        ),
        "headline": headline,
        "detail": detail,
        "overall_outcome": daily_report.get("report_outcome"),
        "overall_reason": None,
        "health_outcome": daily_report.get("health_outcome"),
        "should_notify": None,
        "dispatch_outcome": None,
        "primary_attention_flag": primary_attention_flag,
        "primary_action_code": primary_action_code,
        "startup_open_entry_lot_position_mismatch": startup_mismatch,
        "affected_symbols": _extract_symbol_hint(
            startup.get("reconcile_reason_message")
        ),
    }


def _build_operator_summary(
    *,
    daily_report: dict[str, Any] | None,
    daily_check: dict[str, Any] | None,
) -> dict[str, Any]:
    from_daily_check = _build_operator_summary_from_daily_check(daily_check)
    if from_daily_check is not None:
        return from_daily_check
    return _build_operator_summary_from_daily_report(daily_report)


def _normalize_rehearsal_summary(
    summary_path: Path | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if summary_path is None or payload is None:
        return None
    return ops_summary_script._build_normalized_payload(summary_path, payload)


def _build_snapshot_payload(
    *,
    trade_date: str,
    ops_dir: Path,
    daily_report_path: Path,
    daily_report: dict[str, Any] | None,
    daily_ops_check_path: Path,
    daily_ops_check: dict[str, Any] | None,
    strategy_selection_path: Path,
    strategy_selection: dict[str, Any] | None,
    rehearsal_path: Path | None,
    normalized_rehearsal: dict[str, Any] | None,
) -> dict[str, Any]:
    rehearsal_section = _build_rehearsal_section(
        rehearsal_path=rehearsal_path,
        normalized_summary=normalized_rehearsal,
    )
    return {
        "trade_date": trade_date,
        "generated_at": datetime.now(KST).isoformat(),
        "sources": {
            "ops_dir": str(ops_dir),
            "daily_report_path": str(daily_report_path),
            "daily_report_available": daily_report is not None,
            "daily_ops_check_path": str(daily_ops_check_path),
            "daily_ops_check_available": daily_ops_check is not None,
            "buy_strategy_selection_path": str(strategy_selection_path),
            "buy_strategy_selection_available": strategy_selection is not None,
            "rehearsal_summary_path": (
                None if rehearsal_path is None else str(rehearsal_path)
            ),
            "rehearsal_available": normalized_rehearsal is not None,
        },
        "operator_summary": _build_operator_summary(
            daily_report=daily_report,
            daily_check=daily_ops_check,
        ),
        "overview": _build_overview(daily_report),
        "startup": _build_startup_section(daily_report),
        "controls": _build_controls(daily_report),
        "strategy": _build_strategy_section(
            strategy_selection_path,
            strategy_selection,
        ),
        "scan": _build_scan_section(
            report=daily_report,
            rehearsal_section=rehearsal_section,
        ),
        "executions": _build_executions_section(daily_report),
        "recovery": _build_recovery_section(daily_report),
        "rehearsal": rehearsal_section,
        "actions": _build_actions_section(daily_report),
    }


def build_dashboard_snapshot_document(
    *,
    trade_date: str,
    ops_dir: str | Path | None = None,
    daily_report_input: str | Path | None = None,
    daily_check_input: str | Path | None = None,
    rehearsal_input: str | Path | None = None,
) -> tuple[dict[str, Any], Path, Path, Path | None]:
    resolved_ops_dir = (
        _resolve_optional_path(ops_dir)
        if ops_dir is not None
        else (PROJECT_ROOT / "data" / "ops" / trade_date).resolve()
    )
    resolved_daily_report_path = (
        _resolve_optional_path(daily_report_input)
        if daily_report_input is not None
        else resolved_ops_dir / "daily_ops_report.json"
    )
    resolved_daily_ops_check_path = (
        _resolve_optional_path(daily_check_input)
        if daily_check_input is not None
        else resolved_ops_dir / DAILY_OPS_CHECK_FILE
    )
    resolved_rehearsal_path = (
        _resolve_optional_path(rehearsal_input)
        if rehearsal_input is not None
        else _find_latest_rehearsal_summary(resolved_ops_dir)
    )

    daily_report = _load_optional_json(resolved_daily_report_path)
    daily_ops_check = _load_optional_json(resolved_daily_ops_check_path)
    resolved_strategy_selection_path = (
        resolved_ops_dir / BUY_STRATEGY_SELECTION_FILE
    )
    strategy_selection = _load_optional_json(resolved_strategy_selection_path)
    rehearsal_payload = (
        None
        if resolved_rehearsal_path is None
        else _load_optional_json(resolved_rehearsal_path)
    )
    normalized_rehearsal = _normalize_rehearsal_summary(
        summary_path=resolved_rehearsal_path,
        payload=rehearsal_payload,
    )
    payload = _build_snapshot_payload(
        trade_date=trade_date,
        ops_dir=resolved_ops_dir,
        daily_report_path=resolved_daily_report_path,
        daily_report=daily_report,
        daily_ops_check_path=resolved_daily_ops_check_path,
        daily_ops_check=daily_ops_check,
        strategy_selection_path=resolved_strategy_selection_path,
        strategy_selection=strategy_selection,
        rehearsal_path=resolved_rehearsal_path,
        normalized_rehearsal=normalized_rehearsal,
    )
    return (
        payload,
        resolved_ops_dir,
        resolved_daily_report_path,
        resolved_rehearsal_path,
    )


def main() -> int:
    args = _parse_args()

    try:
        payload, ops_dir, daily_report_path, rehearsal_path = (
            build_dashboard_snapshot_document(
                trade_date=args.trade_date,
                ops_dir=args.ops_dir,
                daily_report_input=args.daily_report_input,
                daily_check_input=args.daily_check_input,
                rehearsal_input=args.rehearsal_input,
            )
        )
        output_path = (
            _resolve_path(args.output)
            if args.output
            else ops_dir / "dashboard_snapshot.json"
        )
        _save_json(output_path, payload)
    except Exception as exc:
        _fail("dashboard_snapshot", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Dashboard Snapshot")
    _ok("trade_date", str(payload.get("trade_date")))
    _ok("ops_dir", str(ops_dir))
    _ok(
        "daily_report_available",
        str(payload["sources"]["daily_report_available"]),
    )
    _ok(
        "daily_ops_check_available",
        str(payload["sources"]["daily_ops_check_available"]),
    )
    _ok(
        "rehearsal_available",
        str(payload["sources"]["rehearsal_available"]),
    )
    overview = payload["overview"]
    _ok("status_level", str(overview.get("status_level")))
    if overview.get("action_required"):
        _warn("top_action_codes", ", ".join(payload["actions"]["top_action_codes"]))
    _ok("json_saved", str(output_path))

    if (
        not payload["sources"]["daily_report_available"]
        and not payload["sources"]["daily_ops_check_available"]
        and not payload["sources"]["rehearsal_available"]
    ):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
