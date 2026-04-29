"""Tests for send_daily_ops_notification.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.send_daily_ops_notification as target


TRADE_DATE = "2026-04-21"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["send_daily_ops_notification.py", *args])


def test_main_returns_0_when_notification_is_not_required(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_send_notify_ready")
    notification_path = ops_dir / "daily_ops_notification.json"
    record_path = ops_dir / "daily_ops_dispatch.json"
    dispatch_text_path = ops_dir / "daily_ops_dispatched.txt"

    _write_json(
        notification_path,
        {
            "trade_date": TRADE_DATE,
            "health_outcome": "READY",
            "should_notify": False,
            "summary": "Daily ops looks ready.",
            "text": "[READY] Daily ops",
            "top_action_codes": [],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(notification_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "NO_NOTIFICATION"
    assert payload["reason"] == "Notification payload does not require dispatch."
    assert dispatch_text_path.exists() is False


def test_main_dispatches_once_and_writes_text(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_send_notify_critical")
    notification_path = ops_dir / "daily_ops_notification.json"
    record_path = ops_dir / "daily_ops_dispatch.json"
    dispatch_text_path = ops_dir / "daily_ops_dispatched.txt"

    _write_json(
        notification_path,
        {
            "trade_date": TRADE_DATE,
            "health_outcome": "CRITICAL",
            "should_notify": True,
            "summary": "Kill switch is enabled.",
            "text": "[CRITICAL] Daily ops 2026-04-21\nKill switch is enabled.",
            "top_action_codes": ["REVIEW_KILL_SWITCH"],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(notification_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "DISPATCHED"
    assert payload["reason"] == "Notification dispatch recorded."
    assert payload["dispatched_at"] is not None
    text = dispatch_text_path.read_text(encoding="utf-8")
    assert "[CRITICAL] Daily ops 2026-04-21" in text
    assert "Kill switch is enabled." in text


def test_main_skips_duplicate_dispatch_without_force(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_send_notify_duplicate")
    notification_path = ops_dir / "daily_ops_notification.json"
    record_path = ops_dir / "daily_ops_dispatch.json"

    notification_payload = {
        "trade_date": TRADE_DATE,
        "health_outcome": "WARNING",
        "should_notify": True,
        "summary": "Manual recovery required.",
        "text": "[WARNING] Daily ops 2026-04-21\nManual recovery required.",
        "top_action_codes": ["REVIEW_EXECUTION_RECOVERY"],
    }
    dispatch_key = target._build_dispatch_key(notification_payload)

    _write_json(notification_path, notification_payload)
    _write_json(
        record_path,
        {
            "dispatch_key": dispatch_key,
            "outcome": "DISPATCHED",
            "dispatched_at": "2026-04-21T18:00:00+09:00",
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(notification_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "DUPLICATE_SKIPPED"
    assert payload["reason"] == "An identical notification was already dispatched."
    assert payload["previous_outcome"] == "DISPATCHED"
    assert payload["previous_dispatched_at"] == "2026-04-21T18:00:00+09:00"


def test_main_dispatches_again_when_text_changed_even_if_summary_matches(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_send_notify_text_changed")
    notification_path = ops_dir / "daily_ops_notification.json"
    record_path = ops_dir / "daily_ops_dispatch.json"
    dispatch_text_path = ops_dir / "daily_ops_dispatched.txt"

    previous_payload = {
        "trade_date": TRADE_DATE,
        "health_outcome": "WARNING",
        "title": f"[WARNING] Daily ops {TRADE_DATE}",
        "should_notify": True,
        "summary": "Startup reconcile block requires review.",
        "text": (
            f"[WARNING] Daily ops {TRADE_DATE}\n"
            "Startup reconcile block requires review.\n"
            "Review executions first: 005930"
        ),
        "top_action_codes": ["REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"],
    }
    current_payload = {
        "trade_date": TRADE_DATE,
        "health_outcome": "WARNING",
        "title": f"[WARNING] Daily ops {TRADE_DATE}",
        "should_notify": True,
        "summary": "Startup reconcile block requires review.",
        "text": (
            f"[WARNING] Daily ops {TRADE_DATE}\n"
            "Startup reconcile block requires review.\n"
            "Review executions first: 035420"
        ),
        "top_action_codes": ["REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"],
    }

    _write_json(notification_path, current_payload)
    _write_json(
        record_path,
        {
            "dispatch_key": target._build_dispatch_key(previous_payload),
            "dispatch_key_version": target.DISPATCH_KEY_VERSION,
            "outcome": "DISPATCHED",
            "dispatched_at": "2026-04-21T18:00:00+09:00",
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(notification_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "DISPATCHED"
    assert payload["reason"] == "Notification dispatch recorded."
    assert payload["dispatch_key_version"] == target.DISPATCH_KEY_VERSION
    assert payload["previous_outcome"] == "DISPATCHED"
    text = dispatch_text_path.read_text(encoding="utf-8")
    assert "Review executions first: 035420" in text
