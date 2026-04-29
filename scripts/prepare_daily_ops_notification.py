"""
Prepare a notifier-friendly payload from one daily ops report.

Input:
- daily_ops_report.json created by show_daily_ops_report.py

Safety:
- read-only for source report
- optional JSON/text outputs for downstream notification automation
- exits with code 4 when notification is required
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

KST = pytz.timezone("Asia/Seoul")

MIN_LEVEL_TO_OUTCOMES = {
    "READY": {"READY", "WARNING", "CRITICAL", "NO_ARTIFACTS"},
    "WARNING": {"WARNING", "CRITICAL", "NO_ARTIFACTS"},
    "CRITICAL": {"CRITICAL", "NO_ARTIFACTS"},
    "NO_ARTIFACTS": {"NO_ARTIFACTS"},
}


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
        description="Prepare one notifier-friendly payload from a daily ops report."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Optional path to daily_ops_report.json.",
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Used when --input is omitted.",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>",
    )
    parser.add_argument(
        "--min-level",
        choices=sorted(MIN_LEVEL_TO_OUTCOMES.keys()),
        default="WARNING",
        help="Minimum health_outcome level that should trigger notification.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional normalized JSON output path.",
    )
    parser.add_argument(
        "--text-output",
        default=None,
        help="Optional plain-text notification output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _resolve_report_path(args: argparse.Namespace) -> Path:
    if args.input:
        return _resolve_path(args.input)
    if args.ops_dir:
        return _resolve_path(args.ops_dir) / "daily_ops_report.json"
    return (
        PROJECT_ROOT / "data" / "ops" / args.trade_date / "daily_ops_report.json"
    ).resolve()


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


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _coerce_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for row in value:
        text = _optional_text(row)
        if text is not None:
            lines.append(text)
    return lines


def _build_default_alert_text(*, title: str, summary: str, lines: list[str]) -> str:
    rows = [title, summary]
    rows.extend(lines)
    return "\n".join(row for row in rows if row)


def _extract_startup_symbol_hint(startup_context: dict[str, Any]) -> str | None:
    marker = "Review executions first:"
    for key in ("reconcile_reason_message", "reason"):
        text = _optional_text(startup_context.get(key))
        if text is None:
            continue
        marker_index = text.find(marker)
        if marker_index < 0:
            continue
        suffix = text[marker_index + len(marker) :].strip()
        suffix = suffix.rstrip(".")
        if suffix:
            return suffix
    return None


def _build_startup_context(report: dict[str, Any]) -> dict[str, Any]:
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        return {
            "available": False,
            "status_level": "MISSING",
            "highest_severity": "NONE",
            "outcome": None,
            "reason": None,
            "checked_at": None,
            "reconcile_reason_code": None,
            "reconcile_reason_message": None,
            "reconcile_changed_rows": None,
            "unresolved_order_count": None,
            "live_position_count": None,
            "attention_flags": [],
            "open_entry_lot_position_mismatch": False,
        }

    startup = artifacts.get("startup_check")
    if not isinstance(startup, dict):
        return {
            "available": False,
            "status_level": "MISSING",
            "highest_severity": "NONE",
            "outcome": None,
            "reason": None,
            "checked_at": None,
            "reconcile_reason_code": None,
            "reconcile_reason_message": None,
            "reconcile_changed_rows": None,
            "unresolved_order_count": None,
            "live_position_count": None,
            "attention_flags": [],
            "open_entry_lot_position_mismatch": False,
        }

    reconcile_reason_code = _optional_text(startup.get("reconcile_reason_code"))
    return {
        "available": bool(startup.get("exists")),
        "status_level": startup.get("status_level"),
        "highest_severity": startup.get("highest_severity"),
        "outcome": startup.get("outcome"),
        "reason": startup.get("reason"),
        "checked_at": startup.get("checked_at"),
        "reconcile_reason_code": reconcile_reason_code,
        "reconcile_reason_message": startup.get("reconcile_reason_message"),
        "reconcile_changed_rows": startup.get("reconcile_changed_rows"),
        "unresolved_order_count": startup.get("unresolved_order_count"),
        "live_position_count": startup.get("live_position_count"),
        "attention_flags": _coerce_lines(startup.get("attention_flags")),
        "open_entry_lot_position_mismatch": (
            reconcile_reason_code == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
        ),
    }


def _refine_notification_content(
    *,
    primary_attention_flag: str | None,
    startup_context: dict[str, Any],
    summary: str,
    lines: list[str],
) -> tuple[str, list[str], bool]:
    if (
        primary_attention_flag != "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
        or startup_context.get("open_entry_lot_position_mismatch") is not True
    ):
        return summary, lines, False

    refined_summary = "Startup blocked by open entry lot position mismatch."
    refined_lines: list[str] = []
    symbol_hint = _extract_startup_symbol_hint(startup_context)
    if symbol_hint is not None:
        refined_lines.append(f"Affected symbols: {symbol_hint}")
    else:
        reason_message = _optional_text(startup_context.get("reconcile_reason_message"))
        if reason_message is not None:
            refined_lines.append(reason_message)
    refined_lines.append("Review executions and lot state before rerunning startup.")
    return refined_summary, refined_lines, True


def _build_notification_payload(
    *,
    report_path: Path,
    report: dict[str, Any],
    min_level: str,
) -> dict[str, Any]:
    trade_date = _optional_text(report.get("trade_date")) or "UNKNOWN"
    health_outcome = _optional_text(report.get("health_outcome")) or "UNKNOWN"
    report_outcome = _optional_text(report.get("report_outcome")) or "UNKNOWN"
    highest_severity = _optional_text(report.get("highest_severity")) or "UNKNOWN"
    artifact_count = report.get("artifact_count")
    attention_flags = report.get("attention_flags")
    action_items = report.get("action_items")
    alert = report.get("alert")
    startup_context = _build_startup_context(report)

    if not isinstance(attention_flags, list):
        attention_flags = []
    if not isinstance(action_items, list):
        action_items = []
    if not isinstance(alert, dict):
        alert = {}

    primary_attention_flag = (
        None if not attention_flags else _optional_text(attention_flags[0])
    )
    title = _optional_text(alert.get("title")) or f"[{health_outcome}] Daily ops {trade_date}"
    summary = _optional_text(alert.get("summary")) or (
        f"health_outcome={health_outcome}, report_outcome={report_outcome}"
    )
    lines = _coerce_lines(alert.get("lines"))
    summary, lines, rebuild_text = _refine_notification_content(
        primary_attention_flag=primary_attention_flag,
        startup_context=startup_context,
        summary=summary,
        lines=lines,
    )
    if rebuild_text:
        text = _build_default_alert_text(
            title=title,
            summary=summary,
            lines=lines,
        )
    else:
        text = _optional_text(alert.get("text")) or _build_default_alert_text(
            title=title,
            summary=summary,
            lines=lines,
        )

    eligible_outcomes = MIN_LEVEL_TO_OUTCOMES[min_level]
    should_notify = health_outcome in eligible_outcomes
    if should_notify:
        notification_reason = (
            f"health_outcome={health_outcome} meets min_level={min_level}"
        )
    else:
        notification_reason = (
            f"health_outcome={health_outcome} is below min_level={min_level}"
        )

    top_action_codes: list[str] = []
    for row in action_items:
        if not isinstance(row, dict):
            continue
        action_code = _optional_text(row.get("action_code"))
        if action_code is None:
            continue
        top_action_codes.append(action_code)
        if len(top_action_codes) >= 3:
            break

    return {
        "trade_date": trade_date,
        "report_path": str(report_path),
        "ops_dir": str(report_path.parent),
        "report_outcome": report_outcome,
        "health_outcome": health_outcome,
        "highest_severity": highest_severity,
        "artifact_count": artifact_count,
        "attention_flags": attention_flags,
        "action_items": action_items,
        "primary_attention_flag": primary_attention_flag,
        "top_action_codes": top_action_codes,
        "primary_action_code": (
            None if not top_action_codes else top_action_codes[0]
        ),
        "startup_context": startup_context,
        "min_level": min_level,
        "should_notify": should_notify,
        "notification_reason": notification_reason,
        "title": title,
        "summary": summary,
        "lines": lines,
        "text": text,
    }


def main() -> int:
    args = _parse_args()

    try:
        report_path = _resolve_report_path(args)
        output_path = _resolve_path(args.output) if args.output else None
        text_output_path = _resolve_path(args.text_output) if args.text_output else None
        report = _load_json(report_path)
        payload = _build_notification_payload(
            report_path=report_path,
            report=report,
            min_level=args.min_level,
        )
    except Exception as exc:
        _fail("notification", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Daily Ops Notification")
    _ok("report_path", str(report_path))
    _ok("trade_date", str(payload.get("trade_date")))
    _ok("health_outcome", str(payload.get("health_outcome")))
    _ok("min_level", str(payload.get("min_level")))
    if payload["should_notify"]:
        _warn("should_notify", "True")
        _warn("notification_reason", str(payload["notification_reason"]))
    else:
        _ok("should_notify", "False")
        _ok("notification_reason", str(payload["notification_reason"]))
    _ok("title", str(payload["title"]))
    if payload["should_notify"]:
        _warn("summary", str(payload["summary"]))
    else:
        _ok("summary", str(payload["summary"]))
    for line in payload["lines"]:
        if payload["should_notify"]:
            _warn("line", line)
        else:
            _ok("line", line)

    if output_path is not None:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))
    if text_output_path is not None:
        _save_text(text_output_path, f"{payload['text']}\n")
        _ok("text_saved", str(text_output_path))

    if payload["should_notify"]:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
