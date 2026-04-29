"""Tests for run_daily_ops_check.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.run_daily_ops_check as target


TRADE_DATE = "2026-04-21"


def _set_cli_args(
    monkeypatch,
    *,
    ops_dir: Path,
    extra_args: list[str] | None = None,
) -> None:
    args = [
        "run_daily_ops_check.py",
        "--trade-date",
        TRADE_DATE,
        "--ops-dir",
        str(ops_dir),
    ]
    if extra_args:
        args.extend(extra_args)
    monkeypatch.setattr(sys, "argv", args)


def _write_child_output(command: list[str], payload: dict, *, flag: str = "--output") -> None:
    output_index = command.index(flag) + 1
    output_path = Path(command[output_index])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_main_runs_report_then_notification_and_returns_ready(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_check_ready")
    _set_cli_args(monkeypatch, ops_dir=ops_dir)

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "READY",
                    "health_outcome": "READY",
                    "highest_severity": "NONE",
                    "artifact_count": 5,
                    "attention_flags": [],
                    "action_items": [],
                    "alert": {
                        "title": f"[READY] Daily ops {TRADE_DATE}",
                        "summary": "Daily ops looks ready across 5 artifacts.",
                        "lines": ["No attention flags detected."],
                        "text": f"[READY] Daily ops {TRADE_DATE}\nNo attention flags detected.",
                    },
                },
            )
            Path(command[command.index("--alert-output") + 1]).write_text(
                f"[READY] Daily ops {TRADE_DATE}\nNo attention flags detected.\n",
                encoding="utf-8",
            )
            return 0
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "should_notify": False,
                    "notification_reason": "health_outcome=READY is below min_level=WARNING",
                },
            )
            _write_child_output(
                command,
                {
                    "text": f"[READY] Daily ops {TRADE_DATE}\nNo attention flags detected.",
                },
                flag="--text-output",
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert [Path(command[1]).name for command in commands] == [
        "show_daily_ops_report.py",
        "prepare_daily_ops_notification.py",
    ]
    report_command = commands[0]
    assert "--strict" not in report_command
    notification_command = commands[1]
    assert notification_command[notification_command.index("--min-level") + 1] == "WARNING"

    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "READY"
    assert payload["overall_reason"] is None
    assert payload["operator_summary"]["headline"] == (
        "Daily ops looks ready across 5 artifacts."
    )
    assert payload["operator_summary"]["detail"] == "No attention flags detected."
    assert payload["operator_summary"]["startup_open_entry_lot_position_mismatch"] is False
    assert [step["name"] for step in payload["steps"]] == [
        "Build Daily Ops Report",
        "Prepare Daily Ops Notification",
    ]


def test_main_returns_notification_required_and_forwards_threshold_flags(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_check_notify")
    _set_cli_args(
        monkeypatch,
        ops_dir=ops_dir,
        extra_args=[
            "--strict-report",
            "--notify-min-level",
            "CRITICAL",
        ],
    )

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "CRITICAL",
                    "highest_severity": "CRITICAL",
                    "artifact_count": 4,
                    "attention_flags": ["KILL_SWITCH_ENABLED"],
                    "action_items": [{"action_code": "REVIEW_KILL_SWITCH"}],
                    "alert": {
                        "title": f"[CRITICAL] Daily ops {TRADE_DATE}",
                        "summary": "1 attention flag detected (1 critical, 0 warning).",
                        "lines": ["Kill switch is enabled."],
                        "text": f"[CRITICAL] Daily ops {TRADE_DATE}\nKill switch is enabled.",
                    },
                },
            )
            return 5
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "should_notify": True,
                    "notification_reason": "health_outcome=CRITICAL meets min_level=CRITICAL",
                },
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    report_command = commands[0]
    assert "--strict" in report_command
    notification_command = commands[1]
    assert notification_command[notification_command.index("--min-level") + 1] == "CRITICAL"

    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_REQUIRED"
    assert payload["overall_reason"] == "health_outcome=CRITICAL meets min_level=CRITICAL"


def test_main_can_dispatch_notification_when_enabled(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_check_dispatch")
    _set_cli_args(
        monkeypatch,
        ops_dir=ops_dir,
        extra_args=[
            "--dispatch",
            "--force-dispatch",
        ],
    )

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "CRITICAL",
                    "highest_severity": "CRITICAL",
                    "artifact_count": 3,
                    "attention_flags": ["KILL_SWITCH_ENABLED"],
                    "action_items": [{"action_code": "REVIEW_KILL_SWITCH"}],
                    "alert": {
                        "title": f"[CRITICAL] Daily ops {TRADE_DATE}",
                        "summary": "1 attention flag detected.",
                        "lines": ["Kill switch is enabled."],
                        "text": f"[CRITICAL] Daily ops {TRADE_DATE}\nKill switch is enabled.",
                    },
                },
            )
            return 5
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "health_outcome": "CRITICAL",
                    "should_notify": True,
                    "notification_reason": "health_outcome=CRITICAL meets min_level=WARNING",
                },
            )
            return 4
        if script_name == "send_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "outcome": "FORCED_DISPATCHED",
                    "reason": "Notification dispatch recorded.",
                    "dispatched_at": "2026-04-21T19:00:00+09:00",
                },
                flag="--record-output",
            )
            Path(command[command.index("--dispatch-text-output") + 1]).write_text(
                f"[CRITICAL] Daily ops {TRADE_DATE}\nKill switch is enabled.\n",
                encoding="utf-8",
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "show_daily_ops_report.py",
        "prepare_daily_ops_notification.py",
        "send_daily_ops_notification.py",
    ]
    dispatch_command = commands[2]
    assert "--force" in dispatch_command

    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_DISPATCHED"
    assert payload["overall_reason"] == "Notification dispatch recorded."
    assert [step["name"] for step in payload["steps"]] == [
        "Build Daily Ops Report",
        "Prepare Daily Ops Notification",
        "Dispatch Daily Ops Notification",
    ]


def test_main_returns_0_when_duplicate_dispatch_is_skipped(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_check_duplicate")
    _set_cli_args(
        monkeypatch,
        ops_dir=ops_dir,
        extra_args=[
            "--dispatch",
        ],
    )

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "WARNING",
                    "highest_severity": "WARNING",
                    "artifact_count": 2,
                    "attention_flags": ["MANUAL_RECOVERY_REQUIRED"],
                    "action_items": [{"action_code": "REVIEW_EXECUTION_RECOVERY"}],
                    "alert": {
                        "title": f"[WARNING] Daily ops {TRADE_DATE}",
                        "summary": "Manual recovery required.",
                        "lines": ["Manual recovery required."],
                        "text": f"[WARNING] Daily ops {TRADE_DATE}\nManual recovery required.",
                    },
                },
            )
            return 4
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "health_outcome": "WARNING",
                    "should_notify": True,
                    "notification_reason": "health_outcome=WARNING meets min_level=WARNING",
                },
            )
            return 4
        if script_name == "send_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "outcome": "DUPLICATE_SKIPPED",
                    "reason": "An identical notification was already dispatched.",
                },
                flag="--record-output",
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_DUPLICATE_SKIPPED"
    assert payload["overall_reason"] == "An identical notification was already dispatched."


def test_main_stops_when_report_payload_is_missing(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_check_report_failed")
    _set_cli_args(monkeypatch, ops_dir=ops_dir)

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        return 5

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 5
    assert [Path(command[1]).name for command in commands] == ["show_daily_ops_report.py"]

    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "REPORT_FAILED"
    assert payload["steps"][0]["outcome"] == "FAILED"


def test_main_returns_notification_required_for_direct_buy_block_attention(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_daily_ops_check_buy_blocked"
    )
    _set_cli_args(monkeypatch, ops_dir=ops_dir)

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "WARNING",
                    "highest_severity": "WARNING",
                    "artifact_count": 1,
                    "attention_flags": ["EXECUTE_BUY_SIGNALS_PREVIEW_BLOCKED"],
                    "action_items": [
                        {"action_code": "REVIEW_BUY_EXECUTION_BLOCK"}
                    ],
                    "alert": {
                        "title": f"[WARNING] Daily ops {TRADE_DATE}",
                        "summary": "Direct buy execution is blocked by a risk guard.",
                        "lines": [
                            "WARNING: Buy execution direct run was blocked. Check MAX_DAILY_LOSS_REACHED before retrying.",
                        ],
                        "text": f"[WARNING] Daily ops {TRADE_DATE}\nDirect buy execution is blocked by a risk guard.",
                    },
                },
            )
            return 0
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "health_outcome": "WARNING",
                    "should_notify": True,
                    "notification_reason": "health_outcome=WARNING meets min_level=WARNING",
                    "top_action_codes": ["REVIEW_BUY_EXECUTION_BLOCK"],
                },
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "show_daily_ops_report.py",
        "prepare_daily_ops_notification.py",
    ]
    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_REQUIRED"
    assert payload["overall_reason"] == (
        "health_outcome=WARNING meets min_level=WARNING"
    )
    assert payload["steps"][1]["result"]["top_action_codes"] == [
        "REVIEW_BUY_EXECUTION_BLOCK"
    ]


def test_main_returns_notification_required_for_direct_sell_failure_attention(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_daily_ops_check_sell_failed"
    )
    _set_cli_args(monkeypatch, ops_dir=ops_dir)

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "CRITICAL",
                    "highest_severity": "CRITICAL",
                    "artifact_count": 1,
                    "attention_flags": ["EXECUTE_SELL_SIGNALS_EXECUTE_FAILED"],
                    "action_items": [
                        {"action_code": "REVIEW_SELL_EXECUTION_FAILURE"}
                    ],
                    "alert": {
                        "title": f"[CRITICAL] Daily ops {TRADE_DATE}",
                        "summary": "Direct sell execution failed and needs manual review.",
                        "lines": [
                            "CRITICAL: Sell execution direct run failed. Check BROKER_SELL_FAILED before any retry.",
                        ],
                        "text": f"[CRITICAL] Daily ops {TRADE_DATE}\nDirect sell execution failed and needs manual review.",
                    },
                },
            )
            return 0
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "health_outcome": "CRITICAL",
                    "should_notify": True,
                    "notification_reason": "health_outcome=CRITICAL meets min_level=WARNING",
                    "top_action_codes": ["REVIEW_SELL_EXECUTION_FAILURE"],
                },
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "show_daily_ops_report.py",
        "prepare_daily_ops_notification.py",
    ]
    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_REQUIRED"
    assert payload["overall_reason"] == (
        "health_outcome=CRITICAL meets min_level=WARNING"
    )
    assert payload["steps"][1]["result"]["top_action_codes"] == [
        "REVIEW_SELL_EXECUTION_FAILURE"
    ]


def test_main_builds_operator_summary_for_startup_open_entry_lot_mismatch(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_daily_ops_check_startup_mismatch"
    )
    _set_cli_args(monkeypatch, ops_dir=ops_dir)

    commands: list[list[str]] = []

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "show_daily_ops_report.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "report_outcome": "ATTENTION",
                    "health_outcome": "WARNING",
                    "highest_severity": "WARNING",
                    "artifact_count": 1,
                    "attention_flags": [
                        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
                    ],
                    "action_items": [
                        {
                            "action_code": "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
                        }
                    ],
                    "alert": {
                        "title": f"[WARNING] Daily ops {TRADE_DATE}",
                        "summary": "Startup blocked by open entry lot position mismatch.",
                        "lines": [
                            "Affected symbols: 005930",
                            "Review executions and lot state before rerunning startup.",
                        ],
                        "text": (
                            f"[WARNING] Daily ops {TRADE_DATE}\n"
                            "Startup blocked by open entry lot position mismatch.\n"
                            "Affected symbols: 005930\n"
                            "Review executions and lot state before rerunning startup."
                        ),
                    },
                },
            )
            return 0
        if script_name == "prepare_daily_ops_notification.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "health_outcome": "WARNING",
                    "should_notify": True,
                    "notification_reason": (
                        "health_outcome=WARNING meets min_level=WARNING"
                    ),
                    "summary": "Startup blocked by open entry lot position mismatch.",
                    "lines": [
                        "Affected symbols: 005930",
                        "Review executions and lot state before rerunning startup.",
                    ],
                    "primary_attention_flag": (
                        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
                    ),
                    "primary_action_code": (
                        "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
                    ),
                    "startup_context": {
                        "available": True,
                        "outcome": "BLOCKED",
                        "reconcile_reason_code": "OPEN_ENTRY_LOT_POSITION_MISMATCH",
                        "reconcile_reason_message": (
                            "Reconciliation would change positions for symbols "
                            "that still have open entry lots. Review executions "
                            "first: 005930"
                        ),
                        "open_entry_lot_position_mismatch": True,
                    },
                },
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads((ops_dir / "daily_ops_check.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "NOTIFICATION_REQUIRED"
    assert payload["operator_summary"]["headline"] == (
        "Startup blocked by open entry lot position mismatch."
    )
    assert payload["operator_summary"]["detail"] == (
        "Affected symbols: 005930 | "
        "Review executions and lot state before rerunning startup."
    )
    assert payload["operator_summary"]["primary_attention_flag"] == (
        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    assert payload["operator_summary"]["primary_action_code"] == (
        "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
    )
    assert payload["operator_summary"]["startup_open_entry_lot_position_mismatch"] is True
    assert payload["operator_summary"]["affected_symbols"] == "005930"
