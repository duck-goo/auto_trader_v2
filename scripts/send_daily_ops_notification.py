"""
Dispatch one prepared daily ops notification with duplicate protection.

Input:
- daily_ops_notification.json created by prepare_daily_ops_notification.py

Safety:
- does not dispatch when should_notify is false
- skips duplicate dispatches unless --force is used
- writes a local dispatch record for audit and recovery
"""

from __future__ import annotations

import argparse
import hashlib
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
        description="Dispatch one prepared daily ops notification with duplicate protection."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Optional path to daily_ops_notification.json.",
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
        "--record-output",
        default=None,
        help="Optional dispatch record JSON output path.",
    )
    parser.add_argument(
        "--dispatch-text-output",
        default=None,
        help="Optional dispatched text output path.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Dispatch even if an identical dispatch record already exists.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _resolve_notification_path(args: argparse.Namespace) -> Path:
    if args.input:
        return _resolve_path(args.input)
    if args.ops_dir:
        return _resolve_path(args.ops_dir) / "daily_ops_notification.json"
    return (
        PROJECT_ROOT / "data" / "ops" / args.trade_date / "daily_ops_notification.json"
    ).resolve()


def _resolve_output_paths(
    *,
    args: argparse.Namespace,
    notification_path: Path,
) -> tuple[Path, Path]:
    ops_dir = notification_path.parent
    record_output = (
        _resolve_path(args.record_output)
        if args.record_output
        else ops_dir / "daily_ops_dispatch.json"
    )
    dispatch_text_output = (
        _resolve_path(args.dispatch_text_output)
        if args.dispatch_text_output
        else ops_dir / "daily_ops_dispatched.txt"
    )
    return record_output, dispatch_text_output


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load_json(path)


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


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            rows.append(text)
    return rows


def _build_dispatch_key(payload: dict[str, Any]) -> str:
    trade_date = _optional_text(payload.get("trade_date")) or "UNKNOWN"
    health_outcome = _optional_text(payload.get("health_outcome")) or "UNKNOWN"
    summary = _optional_text(payload.get("summary")) or ""
    top_action_codes = _coerce_string_list(payload.get("top_action_codes"))
    source = "|".join(
        [trade_date, health_outcome, summary, ",".join(top_action_codes)]
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _build_dispatch_payload(
    *,
    notification_path: Path,
    record_output: Path,
    dispatch_text_output: Path,
    notification_payload: dict[str, Any],
    existing_record: dict[str, Any] | None,
    force: bool,
) -> dict[str, Any]:
    trade_date = _optional_text(notification_payload.get("trade_date")) or "UNKNOWN"
    should_notify = notification_payload.get("should_notify") is True
    title = _optional_text(notification_payload.get("title")) or f"Daily ops {trade_date}"
    summary = _optional_text(notification_payload.get("summary")) or ""
    text = _optional_text(notification_payload.get("text")) or title
    health_outcome = _optional_text(notification_payload.get("health_outcome")) or "UNKNOWN"
    top_action_codes = _coerce_string_list(notification_payload.get("top_action_codes"))
    dispatch_key = _build_dispatch_key(notification_payload)
    now = datetime.now(KST).isoformat()

    payload = {
        "trade_date": trade_date,
        "notification_path": str(notification_path),
        "record_output": str(record_output),
        "dispatch_text_output": str(dispatch_text_output),
        "health_outcome": health_outcome,
        "should_notify": should_notify,
        "force": force,
        "dispatch_key": dispatch_key,
        "title": title,
        "summary": summary,
        "text": text,
        "top_action_codes": top_action_codes,
        "previous_dispatch_key": None,
        "previous_outcome": None,
        "previous_dispatched_at": None,
        "outcome": "UNKNOWN",
        "reason": None,
        "dispatched_at": None,
    }

    if isinstance(existing_record, dict):
        payload["previous_dispatch_key"] = _optional_text(
            existing_record.get("dispatch_key")
        )
        payload["previous_outcome"] = _optional_text(existing_record.get("outcome"))
        payload["previous_dispatched_at"] = _optional_text(
            existing_record.get("dispatched_at")
        )

    if not should_notify:
        payload["outcome"] = "NO_NOTIFICATION"
        payload["reason"] = "Notification payload does not require dispatch."
        return payload

    previous_key = payload["previous_dispatch_key"]
    previous_outcome = payload["previous_outcome"]
    if (
        not force
        and previous_key == dispatch_key
        and previous_outcome in ("DISPATCHED", "FORCED_DISPATCHED")
    ):
        payload["outcome"] = "DUPLICATE_SKIPPED"
        payload["reason"] = "An identical notification was already dispatched."
        return payload

    payload["outcome"] = "FORCED_DISPATCHED" if force else "DISPATCHED"
    payload["reason"] = "Notification dispatch recorded."
    payload["dispatched_at"] = now
    return payload


def main() -> int:
    args = _parse_args()

    try:
        notification_path = _resolve_notification_path(args)
        record_output, dispatch_text_output = _resolve_output_paths(
            args=args,
            notification_path=notification_path,
        )
        notification_payload = _load_json(notification_path)
        existing_record = _load_optional_json(record_output)
        payload = _build_dispatch_payload(
            notification_path=notification_path,
            record_output=record_output,
            dispatch_text_output=dispatch_text_output,
            notification_payload=notification_payload,
            existing_record=existing_record,
            force=args.force,
        )
    except Exception as exc:
        _fail("dispatch", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Send Daily Ops Notification")
    _ok("notification_path", str(notification_path))
    _ok("trade_date", str(payload.get("trade_date")))
    _ok("health_outcome", str(payload.get("health_outcome")))
    _ok("dispatch_key", str(payload.get("dispatch_key")))

    outcome = payload["outcome"]
    reason = _optional_text(payload.get("reason")) or ""
    if outcome in ("DISPATCHED", "FORCED_DISPATCHED"):
        _warn("outcome", outcome)
        _warn("reason", reason)
        print(payload["text"])
        _save_text(dispatch_text_output, f"{payload['text']}\n")
    elif outcome == "DUPLICATE_SKIPPED":
        _ok("outcome", outcome)
        _ok("reason", reason)
    elif outcome == "NO_NOTIFICATION":
        _ok("outcome", outcome)
        _ok("reason", reason)
    else:
        _fail("outcome", outcome)
        _fail("reason", reason)
        return 5

    _save_json(record_output, payload)
    _ok("record_saved", str(record_output))
    if outcome in ("DISPATCHED", "FORCED_DISPATCHED"):
        _ok("dispatch_text_saved", str(dispatch_text_output))
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
