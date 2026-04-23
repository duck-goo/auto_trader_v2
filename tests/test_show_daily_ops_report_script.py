"""Tests for show_daily_ops_report.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.show_daily_ops_report as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["show_daily_ops_report.py", "--trade-date", "2026-04-20", *args],
    )


def test_main_builds_ready_report_with_known_artifacts(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_ready")
    output_path = ops_dir / "daily_ops_report.json"
    alert_path = ops_dir / "daily_ops_alert.txt"

    _write_json(
        ops_dir / "startup_check.json",
        {
            "trade_date": "2026-04-20",
            "checked_at": "2026-04-20T08:59:00+09:00",
            "outcome": "READY",
            "reason": None,
            "universe_snapshot": {
                "exists": True,
                "candidate_count": 25,
                "refreshed_at": "2026-04-20T08:30:00+09:00",
            },
            "unresolved_orders": [],
            "live_positions": [],
        },
    )
    _write_json(
        ops_dir / "run_trading_session.preview.json",
        {
            "trade_date": "2026-04-20",
            "execute_mode": False,
            "session_outcome": "COMPLETED",
            "session_reason": None,
            "preopen_exit_code": 0,
            "preopen_result": {
                "readiness_outcome": "READY",
                "readiness_reason": None,
            },
            "polling_started": True,
            "polling_exit_code": 0,
            "polling_result": {
                "stop_reason": "MAX_CYCLES_REACHED",
            },
        },
    )
    _write_json(
        ops_dir / "after_close.preview.json",
        {
            "trade_date": "2026-04-20",
            "write_mode": False,
            "session_outcome": "COMPLETED",
            "session_reason": None,
            "lock_acquired": False,
            "lock_released": False,
            "steps": [],
        },
    )
    _write_json(
        ops_dir / "kill_switch.status.json",
        {
            "action": "STATUS",
            "enabled": False,
            "note": None,
            "updated_at": "2026-04-20T08:40:00+09:00",
        },
    )
    _write_json(
        ops_dir / "rehearsal" / "rehearsal_summary.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
            "include_after_close": False,
            "scan_settings": {
                "scan_timing1": False,
                "scan_timing2": True,
                "timing2_30s_min_samples_per_bar": 2,
                "timing2_max_sample_symbols_per_cycle": 30,
            },
            "steps": [
                {
                    "name": "Trading Session Preview",
                    "outcome": "COMPLETED",
                    "result": {
                        "polling_result": {
                            "cycles": [
                                {
                                    "timing2_price_sample_capture": {
                                        "outcome": "COMPLETED",
                                    },
                                    "timing2_30s_bar_build": {
                                        "outcome": "COMPLETED",
                                    },
                                    "timing2_30s_trigger_scan": {
                                        "outcome": "COMPLETED",
                                    },
                                }
                            ],
                        },
                    },
                },
            ],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
            "--alert-output",
            str(alert_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact_count"] == 5
    assert payload["report_outcome"] == "READY"
    assert payload["health_outcome"] == "READY"
    assert payload["highest_severity"] == "NONE"
    assert payload["attention_flags"] == []
    assert payload["action_items"] == []
    assert payload["alert"]["level"] == "READY"
    assert payload["alert"]["summary"] == "Daily ops looks ready across 5 artifacts."
    assert payload["alert"]["lines"][0] == "No attention flags detected."
    alert_text = alert_path.read_text(encoding="utf-8")
    assert "[READY] Daily ops 2026-04-20" in alert_text
    assert "No attention flags detected." in alert_text
    assert payload["artifacts"]["startup_check"]["outcome"] == "READY"
    assert payload["artifacts"]["trading_session_preview"]["polling_stop_reason"] == "MAX_CYCLES_REACHED"
    assert payload["latest_kill_switch"]["enabled"] is False
    assert payload["rehearsals"][0]["overall_outcome"] == "COMPLETED"
    assert payload["rehearsals"][0]["scan_settings"]["scan_timing2"] is True
    assert payload["rehearsals"][0]["timing2_30s_verified"] is True


def test_main_marks_attention_when_blocked_or_manual_actions_exist(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_attention")
    output_path = ops_dir / "daily_ops_report.json"
    alert_path = ops_dir / "daily_ops_alert.txt"

    _write_json(
        ops_dir / "run_trading_session.execute.json",
        {
            "trade_date": "2026-04-20",
            "execute_mode": True,
            "session_outcome": "POLLING_BLOCKED",
            "session_reason": "MAX_DAILY_LOSS_REACHED",
            "preopen_exit_code": 0,
            "preopen_result": {
                "readiness_outcome": "READY",
                "readiness_reason": None,
            },
            "polling_started": True,
            "polling_exit_code": 4,
            "polling_result": {
                "stop_reason": "MAX_DAILY_LOSS_REACHED",
            },
        },
    )
    _write_json(
        ops_dir / "order_maintenance.preview.json",
        {
            "trade_date": "2026-04-20",
            "execute_mode": False,
            "error_type": None,
            "error_message": None,
            "result": {
                "manual_recovery_required_client_order_ids": ["COID_1", "COID_2"],
                "sync_result": {
                    "candidate_count": 3,
                    "synced_count": 1,
                    "execution_recovery_required_count": 2,
                },
                "execution_recovery_result": {
                    "preview_ready_count": 1,
                    "recovered_count": 0,
                    "manual_recovery_required_count": 2,
                },
                "stale_buy_cancel_result": {
                    "cancelled_count": 0,
                },
                "stale_sell_cancel_result": {
                    "cancelled_count": 0,
                },
            },
        },
    )
    _write_json(
        ops_dir / "kill_switch.enable.json",
        {
            "action": "ENABLE",
            "enabled": True,
            "note": "manual emergency stop",
            "updated_at": "2026-04-20T11:10:00+09:00",
        },
    )
    _write_json(
        ops_dir / "rehearsal_090500" / "rehearsal_summary.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "STARTUP_BLOCKED",
            "overall_reason": "Unresolved orders exist. Startup is blocked.",
            "include_after_close": False,
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
            "--alert-output",
            str(alert_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["report_outcome"] == "ATTENTION"
    assert payload["health_outcome"] == "CRITICAL"
    assert payload["highest_severity"] == "CRITICAL"
    assert "KILL_SWITCH_ENABLED" in payload["attention_flags"]
    assert "TRADING_SESSION_EXECUTE_BLOCKED" in payload["attention_flags"]
    assert "MANUAL_RECOVERY_REQUIRED" in payload["attention_flags"]
    assert "REHEARSAL_BLOCKED" in payload["attention_flags"]
    flag_details = {row["flag"]: row for row in payload["flag_details"]}
    assert flag_details["KILL_SWITCH_ENABLED"]["severity"] == "CRITICAL"
    assert flag_details["MANUAL_RECOVERY_REQUIRED"]["severity"] == "WARNING"
    action_items = {row["flag"]: row for row in payload["action_items"]}
    assert action_items["KILL_SWITCH_ENABLED"]["action_code"] == "REVIEW_KILL_SWITCH"
    assert action_items["KILL_SWITCH_ENABLED"]["reference_path"].endswith(
        "kill_switch.enable.json"
    )
    assert action_items["MANUAL_RECOVERY_REQUIRED"]["action_code"] == "REVIEW_EXECUTION_RECOVERY"
    assert action_items["TRADING_SESSION_EXECUTE_BLOCKED"]["action_code"] == "REVIEW_TRADING_SESSION_BLOCK"
    assert action_items["REHEARSAL_BLOCKED"]["action_code"] == "REVIEW_REHEARSAL"
    assert payload["alert"]["level"] == "CRITICAL"
    assert payload["alert"]["critical_count"] == 1
    assert payload["alert"]["warning_count"] == 3
    assert "Kill switch is enabled. note=manual emergency stop" in payload["alert"]["lines"]
    assert any(
        line.startswith("CRITICAL:")
        for line in payload["alert"]["lines"]
    )
    alert_text = alert_path.read_text(encoding="utf-8")
    assert "[CRITICAL] Daily ops 2026-04-20" in alert_text
    assert "Kill switch is enabled. note=manual emergency stop" in alert_text
    assert payload["latest_kill_switch"]["enabled"] is True
    assert payload["artifacts"]["order_maintenance_preview"]["manual_recovery_required_count"] == 2


def test_main_strict_returns_4_for_warning_only(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_strict_warning")
    output_path = ops_dir / "daily_ops_report.json"

    _write_json(
        ops_dir / "order_maintenance.preview.json",
        {
            "trade_date": "2026-04-20",
            "execute_mode": False,
            "error_type": None,
            "error_message": None,
            "result": {
                "manual_recovery_required_client_order_ids": ["COID_1"],
                "sync_result": {
                    "candidate_count": 1,
                    "synced_count": 0,
                    "execution_recovery_required_count": 1,
                },
                "execution_recovery_result": {
                    "preview_ready_count": 0,
                    "recovered_count": 0,
                    "manual_recovery_required_count": 1,
                },
                "stale_buy_cancel_result": {
                    "cancelled_count": 0,
                },
                "stale_sell_cancel_result": {
                    "cancelled_count": 0,
                },
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--strict",
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["report_outcome"] == "ATTENTION"
    assert payload["health_outcome"] == "WARNING"
    assert payload["highest_severity"] == "WARNING"
    assert "MANUAL_RECOVERY_REQUIRED" in payload["attention_flags"]
    assert payload["action_items"][0]["action_code"] == "REVIEW_EXECUTION_RECOVERY"


def test_main_warns_when_timing2_rehearsal_did_not_verify_30s_pipeline(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_timing2_gap")
    output_path = ops_dir / "daily_ops_report.json"

    _write_json(
        ops_dir / "rehearsal_timing2" / "rehearsal_summary.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
            "include_after_close": False,
            "scan_settings": {
                "scan_timing1": False,
                "scan_timing2": True,
                "timing2_30s_min_samples_per_bar": 2,
                "timing2_max_sample_symbols_per_cycle": 30,
            },
            "steps": [
                {
                    "name": "Trading Session Preview",
                    "outcome": "COMPLETED",
                    "result": {
                        "polling_result": {
                            "cycles": [
                                {
                                    "timing2_price_sample_capture": {
                                        "outcome": "COMPLETED",
                                    },
                                }
                            ],
                        },
                    },
                },
            ],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["health_outcome"] == "WARNING"
    assert "REHEARSAL_TIMING2_30S_NOT_VERIFIED" in payload["attention_flags"]
    action_items = {row["flag"]: row for row in payload["action_items"]}
    assert (
        action_items["REHEARSAL_TIMING2_30S_NOT_VERIFIED"]["action_code"]
        == "RERUN_TIMING2_REHEARSAL"
    )
    assert payload["rehearsals"][0]["timing2_30s_verified"] is False


def test_main_warns_when_trading_session_timing2_setup_is_not_ready(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_timing2_setup")
    output_path = ops_dir / "daily_ops_report.json"

    _write_json(
        ops_dir / "run_trading_session.preview.json",
        {
            "trade_date": "2026-04-20",
            "execute_mode": False,
            "session_outcome": "COMPLETED",
            "session_reason": None,
            "preopen_exit_code": 0,
            "preopen_result": {
                "readiness_outcome": "READY",
                "readiness_reason": None,
            },
            "polling_started": True,
            "polling_exit_code": 0,
            "polling_result": {
                "stop_reason": "MAX_CYCLES_REACHED",
                "timing2_setup_readiness": {
                    "trade_date": "2026-04-20",
                    "required": True,
                    "setup_signal_count": 0,
                    "ready": False,
                    "reason": "Timing2 setup signals are missing for this trade date.",
                },
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
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    flag = "TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY"
    assert payload["health_outcome"] == "WARNING"
    assert flag in payload["attention_flags"]
    assert payload["artifacts"]["trading_session_preview"]["timing2_setup_ready"] is False
    assert (
        payload["artifacts"]["trading_session_preview"]["timing2_setup_signal_count"]
        == 0
    )
    action_items = {row["flag"]: row for row in payload["action_items"]}
    assert (
        action_items[flag]["action_code"]
        == "RERUN_TRADING_SESSION_WITH_TIMING2_SETUP"
    )
    assert "--preopen-scan-timing2-setup" in action_items[flag]["suggested_command"]
    assert "--preopen-write-timing2-signals" in action_items[flag]["suggested_command"]


def test_main_strict_returns_5_for_critical_attention(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_strict_critical")
    output_path = ops_dir / "daily_ops_report.json"

    _write_json(
        ops_dir / "kill_switch.enable.json",
        {
            "action": "ENABLE",
            "enabled": True,
            "note": "manual emergency stop",
            "updated_at": "2026-04-20T11:10:00+09:00",
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--ops-dir",
            str(ops_dir),
            "--strict",
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 5
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["report_outcome"] == "ATTENTION"
    assert payload["health_outcome"] == "CRITICAL"
    assert payload["highest_severity"] == "CRITICAL"
    assert "KILL_SWITCH_ENABLED" in payload["attention_flags"]
    assert payload["action_items"][0]["action_code"] == "REVIEW_KILL_SWITCH"


def test_main_returns_4_when_no_artifacts_exist(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_daily_ops_empty")
    ops_dir.mkdir(parents=True, exist_ok=True)

    _set_cli_args(monkeypatch, ["--ops-dir", str(ops_dir)])

    exit_code = target.main()

    assert exit_code == 4
