"""Tests for prepare_daily_ops_notification.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.prepare_daily_ops_notification as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["prepare_daily_ops_notification.py", *args])


def test_main_writes_payload_and_text_without_notification_for_ready_report(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_ready")
    report_path = ops_dir / "daily_ops_report.json"
    output_path = ops_dir / "daily_ops_notification.json"
    text_path = ops_dir / "daily_ops_notification.txt"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 5,
            "report_outcome": "READY",
            "health_outcome": "READY",
            "highest_severity": "NONE",
            "attention_flags": [],
            "action_items": [],
            "alert": {
                "level": "READY",
                "title": "[READY] Daily ops 2026-04-20",
                "summary": "Daily ops looks ready across 5 artifacts.",
                "lines": [
                    "No attention flags detected.",
                ],
                "text": "[READY] Daily ops 2026-04-20\nNo attention flags detected.",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
            "--text-output",
            str(text_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["should_notify"] is False
    assert payload["notification_reason"] == "health_outcome=READY is below min_level=WARNING"
    assert payload["title"] == "[READY] Daily ops 2026-04-20"
    assert payload["primary_attention_flag"] is None
    assert payload["primary_action_code"] is None
    assert payload["top_action_codes"] == []
    assert payload["startup_context"]["available"] is False
    text = text_path.read_text(encoding="utf-8")
    assert "[READY] Daily ops 2026-04-20" in text
    assert "No attention flags detected." in text


def test_main_returns_4_for_critical_report_and_keeps_top_action_codes(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_critical")
    report_path = ops_dir / "daily_ops_report.json"
    output_path = ops_dir / "daily_ops_notification.json"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 4,
            "report_outcome": "ATTENTION",
            "health_outcome": "CRITICAL",
            "highest_severity": "CRITICAL",
            "attention_flags": [
                "KILL_SWITCH_ENABLED",
                "MANUAL_RECOVERY_REQUIRED",
            ],
            "action_items": [
                {
                    "action_code": "REVIEW_KILL_SWITCH",
                    "severity": "CRITICAL",
                },
                {
                    "action_code": "REVIEW_EXECUTION_RECOVERY",
                    "severity": "WARNING",
                },
            ],
            "alert": {
                "level": "CRITICAL",
                "title": "[CRITICAL] Daily ops 2026-04-20",
                "summary": "2 attention flags detected (1 critical, 1 warning).",
                "lines": [
                    "Kill switch is enabled. note=manual emergency stop",
                    "CRITICAL: Review kill switch state before allowing any new automation run.",
                ],
                "text": "[CRITICAL] Daily ops 2026-04-20\nKill switch is enabled. note=manual emergency stop",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(report_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["should_notify"] is True
    assert payload["notification_reason"] == "health_outcome=CRITICAL meets min_level=WARNING"
    assert payload["primary_attention_flag"] == "KILL_SWITCH_ENABLED"
    assert payload["primary_action_code"] == "REVIEW_KILL_SWITCH"
    assert payload["top_action_codes"] == [
        "REVIEW_KILL_SWITCH",
        "REVIEW_EXECUTION_RECOVERY",
    ]
    assert payload["summary"] == "2 attention flags detected (1 critical, 1 warning)."


def test_main_respects_min_level_ready(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_ready_threshold")
    report_path = ops_dir / "daily_ops_report.json"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 2,
            "report_outcome": "READY",
            "health_outcome": "READY",
            "highest_severity": "NONE",
            "attention_flags": [],
            "action_items": [],
            "alert": {
                "level": "READY",
                "title": "[READY] Daily ops 2026-04-20",
                "summary": "Daily ops looks ready across 2 artifacts.",
                "lines": [],
                "text": "[READY] Daily ops 2026-04-20",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(report_path),
            "--min-level",
            "READY",
        ],
    )

    exit_code = target.main()

    assert exit_code == 4


def test_main_returns_4_for_direct_buy_block_warning_report(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_buy_blocked")
    report_path = ops_dir / "daily_ops_report.json"
    output_path = ops_dir / "daily_ops_notification.json"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "ATTENTION",
            "health_outcome": "WARNING",
            "highest_severity": "WARNING",
            "attention_flags": [
                "EXECUTE_BUY_SIGNALS_PREVIEW_BLOCKED",
            ],
            "action_items": [
                {
                    "action_code": "REVIEW_BUY_EXECUTION_BLOCK",
                    "severity": "WARNING",
                },
            ],
            "alert": {
                "level": "WARNING",
                "title": "[WARNING] Daily ops 2026-04-20",
                "summary": "Direct buy execution is blocked by a risk guard.",
                "lines": [
                    "WARNING: Buy execution direct run was blocked. Check MAX_DAILY_LOSS_REACHED before retrying.",
                ],
                "text": "[WARNING] Daily ops 2026-04-20\nDirect buy execution is blocked by a risk guard.",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(report_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["should_notify"] is True
    assert payload["notification_reason"] == (
        "health_outcome=WARNING meets min_level=WARNING"
    )
    assert payload["top_action_codes"] == ["REVIEW_BUY_EXECUTION_BLOCK"]
    assert payload["summary"] == "Direct buy execution is blocked by a risk guard."


def test_main_returns_4_for_direct_sell_failure_critical_report(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_sell_failed")
    report_path = ops_dir / "daily_ops_report.json"
    output_path = ops_dir / "daily_ops_notification.json"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "ATTENTION",
            "health_outcome": "CRITICAL",
            "highest_severity": "CRITICAL",
            "attention_flags": [
                "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
            ],
            "action_items": [
                {
                    "action_code": "REVIEW_SELL_EXECUTION_FAILURE",
                    "severity": "CRITICAL",
                },
            ],
            "alert": {
                "level": "CRITICAL",
                "title": "[CRITICAL] Daily ops 2026-04-20",
                "summary": "Direct sell execution failed and needs manual review.",
                "lines": [
                    "CRITICAL: Sell execution direct run failed. Check BROKER_SELL_FAILED before any retry.",
                ],
                "text": "[CRITICAL] Daily ops 2026-04-20\nDirect sell execution failed and needs manual review.",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(report_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["should_notify"] is True
    assert payload["notification_reason"] == (
        "health_outcome=CRITICAL meets min_level=WARNING"
    )
    assert payload["top_action_codes"] == ["REVIEW_SELL_EXECUTION_FAILURE"]
    assert payload["summary"] == "Direct sell execution failed and needs manual review."


def test_main_includes_structured_startup_context_for_open_entry_lot_block(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_notify_startup_block")
    report_path = ops_dir / "daily_ops_report.json"
    output_path = ops_dir / "daily_ops_notification.json"

    _write_json(
        report_path,
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "ATTENTION",
            "health_outcome": "WARNING",
            "highest_severity": "WARNING",
            "attention_flags": [
                "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
            ],
            "action_items": [
                {
                    "action_code": "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
                    "severity": "WARNING",
                },
            ],
            "alert": {
                "level": "WARNING",
                "title": "[WARNING] Daily ops 2026-04-20",
                "summary": "Startup reconcile block requires review.",
                "lines": [
                    "Review executions first: 005930",
                ],
                "text": (
                    "[WARNING] Daily ops 2026-04-20\n"
                    "Startup reconcile block requires review.\n"
                    "Review executions first: 005930"
                ),
            },
            "artifacts": {
                "startup_check": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "outcome": "BLOCKED",
                    "reason": (
                        "Reconciliation would change positions for symbols that "
                        "still have open entry lots."
                    ),
                    "checked_at": "2026-04-20T08:59:00+09:00",
                    "reconcile_reason_code": "OPEN_ENTRY_LOT_POSITION_MISMATCH",
                    "reconcile_reason_message": (
                        "Reconciliation would change positions for symbols that "
                        "still have open entry lots. Review executions first: 005930"
                    ),
                    "reconcile_changed_rows": 1,
                    "unresolved_order_count": 0,
                    "live_position_count": 1,
                    "attention_flags": [
                        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
                    ],
                }
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(report_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["primary_attention_flag"] == (
        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    assert payload["primary_action_code"] == "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
    assert payload["summary"] == "Startup blocked by open entry lot position mismatch."
    assert payload["lines"] == [
        "Affected symbols: 005930",
        "Review executions and lot state before rerunning startup.",
    ]
    assert payload["text"] == (
        "[WARNING] Daily ops 2026-04-20\n"
        "Startup blocked by open entry lot position mismatch.\n"
        "Affected symbols: 005930\n"
        "Review executions and lot state before rerunning startup."
    )
    assert payload["startup_context"]["available"] is True
    assert payload["startup_context"]["outcome"] == "BLOCKED"
    assert payload["startup_context"]["reconcile_reason_code"] == (
        "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    assert payload["startup_context"]["reconcile_reason_message"].endswith("005930")
    assert payload["startup_context"]["reconcile_changed_rows"] == 1
    assert payload["startup_context"]["live_position_count"] == 1
    assert payload["startup_context"]["open_entry_lot_position_mismatch"] is True
