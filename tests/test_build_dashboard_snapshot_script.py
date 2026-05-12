"""Tests for build_dashboard_snapshot.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.build_dashboard_snapshot as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["build_dashboard_snapshot.py", *args])


def test_main_builds_dashboard_snapshot_from_daily_report_and_latest_rehearsal(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_dashboard_snapshot")
    output_path = ops_dir / "dashboard_snapshot.json"

    _write_json(
        ops_dir / "daily_ops_report.json",
        {
            "trade_date": "2026-04-20",
            "artifact_count": 5,
            "report_outcome": "ATTENTION",
            "health_outcome": "CRITICAL",
            "highest_severity": "CRITICAL",
            "attention_flags": [
                "KILL_SWITCH_ENABLED",
                "TRADING_SESSION_EXECUTE_BLOCKED",
                "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
            ],
            "action_items": [
                {"action_code": "REVIEW_KILL_SWITCH"},
                {"action_code": "REVIEW_SELL_EXECUTION_FAILURE"},
            ],
            "alert": {
                "critical_count": 2,
                "warning_count": 1,
            },
            "latest_kill_switch": {
                "enabled": True,
                "note": "manual emergency stop",
                "updated_at": "2026-04-20T11:10:00+09:00",
            },
            "artifacts": {
                "startup_check": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "outcome": "BLOCKED",
                    "reason": "Startup is blocked because reconcile found an open entry lot mismatch.",
                    "checked_at": "2026-04-20T08:59:00+09:00",
                    "universe_exists": True,
                    "universe_candidate_count": 12,
                    "reconcile_changed_rows": 1,
                    "unresolved_order_count": 0,
                    "live_position_count": 1,
                    "reconcile_reason_code": "OPEN_ENTRY_LOT_POSITION_MISMATCH",
                    "reconcile_reason_message": (
                        "Reconciliation would change positions for symbols that still have "
                        "open entry lots. Review executions first: 005930"
                    ),
                    "attention_flags": [
                        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
                    ],
                },
                "trading_session_preview": {
                    "exists": True,
                    "status_level": "READY",
                    "highest_severity": "NONE",
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "preopen_readiness_outcome": "READY",
                    "preopen_readiness_reason": None,
                    "polling_started": True,
                    "polling_exit_code": 0,
                    "polling_stop_reason": "MAX_CYCLES_REACHED",
                    "timing2_setup_required": True,
                    "timing2_setup_ready": True,
                    "timing2_setup_signal_count": 12,
                    "attention_flags": [],
                },
                "trading_session_execute": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "session_outcome": "POLLING_BLOCKED",
                    "session_reason": "MAX_DAILY_LOSS_REACHED",
                    "preopen_readiness_outcome": "READY",
                    "preopen_readiness_reason": None,
                    "polling_started": True,
                    "polling_exit_code": 4,
                    "polling_stop_reason": "MAX_DAILY_LOSS_REACHED",
                    "timing2_setup_required": True,
                    "timing2_setup_ready": True,
                    "timing2_setup_signal_count": 12,
                    "attention_flags": [
                        "TRADING_SESSION_EXECUTE_BLOCKED",
                    ],
                },
                "execute_sell_signals_execute": {
                    "exists": True,
                    "status_level": "CRITICAL",
                    "highest_severity": "CRITICAL",
                    "stop_reason": "BROKER_SELL_FAILED",
                    "blocked_count": 0,
                    "preview_ready_count": 0,
                    "submitted_count": 0,
                    "acted_count": 1,
                    "attention_flags": [
                        "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
                    ],
                },
                "order_maintenance_preview": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "manual_recovery_required_count": 2,
                    "stale_signal_preview_ready_count": 3,
                    "stale_signal_cleaned_count": 0,
                    "stale_signal_blocked_count": 0,
                    "stale_signal_symbol_hint": "005930, 000660",
                    "stale_signal_blocked_reason_codes": None,
                    "attention_flags": [
                        "MANUAL_RECOVERY_REQUIRED",
                    ],
                },
                "stale_signal_cleanup_review": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "path": (
                        "C:/python/auto_trader_v2/data/ops/test/"
                        "stale_signal_cleanup.review.json"
                    ),
                    "source_label": "order_maintenance.execute",
                    "source_path": (
                        "C:/python/auto_trader_v2/data/ops/test/"
                        "order_maintenance.execute.json"
                    ),
                    "source_file_name": "order_maintenance.execute.json",
                    "review_item_count": 3,
                    "blocked_item_count": 1,
                    "preview_ready_item_count": 1,
                    "cleaned_item_count": 1,
                    "top_symbols": "005930, 000660",
                    "preview_items": [
                        {
                            "scope": "buy",
                            "symbol": "005930",
                            "strategy_name": "buy_timing2_30s_morning_reclaim",
                            "scanned_at": "2026-04-20T09:07:01+09:00",
                            "outcome": "BLOCKED",
                            "reason_code": "INVALID_SIGNAL_SCANNED_AT",
                            "age_seconds": 421,
                        },
                        {
                            "scope": "sell",
                            "symbol": "000660",
                            "strategy_name": "sell_stop_loss",
                            "scanned_at": "2026-04-20T09:12:44+09:00",
                            "outcome": "PREVIEW_READY",
                            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                            "age_seconds": 366,
                        },
                    ],
                    "attention_flags": [
                        "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
                    ],
                },
            },
        },
    )

    _write_json(
        ops_dir / "buy_strategy.selection.json",
        {
            "action": "SET",
            "buy_strategy": "timing2",
            "effective_buy_strategy": "timing2",
            "run_timing1": False,
            "run_timing2": True,
            "updated_at": "2026-04-20T11:12:00+09:00",
            "note": "focus on timing2",
            "applies_to_next_run": True,
        },
    )

    _write_json(
        ops_dir / "daily_ops_check.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "NOTIFICATION_REQUIRED",
            "overall_reason": "Notification should be sent for startup mismatch.",
            "should_notify": True,
            "operator_summary": {
                "headline": "Startup blocked by open entry lot position mismatch.",
                "detail": (
                    "Affected symbols: 005930 | "
                    "Review executions and lot state before rerunning startup."
                ),
                "health_outcome": "WARNING",
                "dispatch_outcome": "DISPATCHED",
                "primary_attention_flag": (
                    "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"
                ),
                "primary_action_code": "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
                "startup_open_entry_lot_position_mismatch": True,
                "affected_symbols": "005930",
            },
        },
    )

    _write_json(
        ops_dir / "rehearsal_old" / "rehearsal_summary.json",
        {
            "trade_date": "2026-04-20",
            "mode": "mock",
            "started_at": "2026-04-20T09:00:00+09:00",
            "finished_at": "2026-04-20T09:00:10+09:00",
            "overall_outcome": "TRADING_SESSION_FAILED",
            "overall_reason": "Old failure.",
            "include_after_close": False,
            "intraday_window": {},
            "steps": [],
        },
    )
    _write_json(
        ops_dir / "rehearsal_latest" / "rehearsal_summary.json",
        {
            "trade_date": "2026-04-20",
            "mode": "mock",
            "started_at": "2026-04-20T09:05:00+09:00",
            "finished_at": "2026-04-20T09:05:12+09:00",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
            "include_after_close": False,
            "scan_settings": {
                "scan_timing1": False,
                "scan_timing2": True,
                "timing2_30s_min_samples_per_bar": 2,
                "timing2_max_sample_symbols_per_cycle": 30,
            },
            "intraday_window": {},
            "steps": [
                {
                    "name": "Trading Session Preview",
                    "exit_code": 0,
                    "outcome": "COMPLETED",
                    "reason": None,
                    "result": {
                        "trade_date": "2026-04-20",
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
                                "setup_signal_count": 12,
                                "ready": True,
                                "reason": None,
                            },
                            "cycles": [
                                {
                                    "cycle_no": 1,
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
                }
            ],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--trade-date",
            "2026-04-20",
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["overview"]["status_level"] == "CRITICAL"
    assert payload["overview"]["top_action_codes"] == [
        "REVIEW_KILL_SWITCH",
        "REVIEW_SELL_EXECUTION_FAILURE",
    ]
    assert payload["sources"]["daily_ops_check_available"] is True
    assert payload["sources"]["daily_ops_check_path"].endswith("daily_ops_check.json")
    assert payload["operator_summary"]["available"] is True
    assert payload["operator_summary"]["source"] == "daily_ops_check"
    assert payload["operator_summary"]["status_level"] == "WARNING"
    assert payload["operator_summary"]["headline"] == (
        "Startup blocked by open entry lot position mismatch."
    )
    assert payload["operator_summary"]["primary_action_code"] == (
        "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
    )
    assert payload["operator_summary"]["affected_symbols"] == "005930"
    assert payload["startup"]["available"] is True
    assert payload["startup"]["status_level"] == "WARNING"
    assert payload["startup"]["reconcile_reason_code"] == (
        "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    assert payload["startup"]["reconcile_reason_message"].endswith("005930")
    assert payload["controls"]["kill_switch_enabled"] is True
    assert payload["controls"]["kill_switch_status_level"] == "CRITICAL"
    assert payload["strategy"]["selection_available"] is True
    assert payload["strategy"]["buy_strategy"] == "timing2"
    assert payload["strategy"]["run_timing1"] is False
    assert payload["strategy"]["run_timing2"] is True
    assert payload["scan"]["live_preview"]["status_level"] == "READY"
    assert payload["scan"]["live_preview"]["card_key"] == "scan-live-preview"
    assert payload["scan"]["live_execute"]["status_level"] == "WARNING"
    assert payload["scan"]["live_execute"]["card_key"] == "scan-live-execute"
    assert payload["scan"]["live_execute"]["polling_stop_reason"] == (
        "MAX_DAILY_LOSS_REACHED"
    )
    assert (
        payload["scan"]["rehearsal_validation"]["card_key"]
        == "scan-rehearsal-validation"
    )
    assert payload["executions"]["sell_execute"]["status_level"] == "CRITICAL"
    assert (
        payload["executions"]["sell_execute"]["card_key"]
        == "execution-sell-execute"
    )
    assert payload["executions"]["sell_execute"]["stop_reason"] == (
        "BROKER_SELL_FAILED"
    )
    assert (
        payload["recovery"]["order_maintenance_preview"][
            "manual_recovery_required_count"
        ]
        == 2
    )
    assert (
        payload["recovery"]["order_maintenance_preview"][
            "stale_signal_preview_ready_count"
        ]
        == 3
    )
    assert (
        payload["recovery"]["order_maintenance_preview"]["stale_signal_symbol_hint"]
        == "005930, 000660"
    )
    assert (
        payload["recovery"]["order_maintenance_preview"]["card_key"]
        == "recovery-maintenance-preview"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["card_key"]
        == "recovery-stale-signal-review"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["blocked_item_count"]
        == 1
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["source_label"]
        == "order_maintenance.execute"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["path"]
        == "C:/python/auto_trader_v2/data/ops/test/stale_signal_cleanup.review.json"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["review_file_name"]
        == "stale_signal_cleanup.review.json"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["source_file_name"]
        == "order_maintenance.execute.json"
    )
    assert payload["recovery"]["stale_signal_cleanup_review"]["preview_items"] == [
        {
            "scope": "buy",
            "symbol": "005930",
            "strategy_name": "buy_timing2_30s_morning_reclaim",
            "scanned_at": "2026-04-20T09:07:01+09:00",
            "outcome": "BLOCKED",
            "reason_code": "INVALID_SIGNAL_SCANNED_AT",
            "age_seconds": 421,
        },
        {
            "scope": "sell",
            "symbol": "000660",
            "strategy_name": "sell_stop_loss",
            "scanned_at": "2026-04-20T09:12:44+09:00",
            "outcome": "PREVIEW_READY",
            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
            "age_seconds": 366,
        },
    ]
    assert (
        payload["recovery"]["execution_recovery_review"]["card_key"]
        == "recovery-execution-review"
    )
    assert payload["rehearsal"]["available"] is True
    assert payload["rehearsal"]["status_level"] == "READY"
    assert payload["rehearsal"]["trading_session"]["timing2_setup_ready"] is True
    assert payload["rehearsal"]["trading_session"]["timing2_30s_verified"] is True
    assert payload["sources"]["rehearsal_summary_path"].endswith(
        "rehearsal_latest\\rehearsal_summary.json"
    )
    assert payload["actions"]["required"] is True
    assert payload["actions"]["top_action_codes"] == [
        "REVIEW_KILL_SWITCH",
        "REVIEW_SELL_EXECUTION_FAILURE",
    ]


def test_main_writes_no_data_snapshot_when_sources_are_missing(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_dashboard_snapshot_empty")
    output_path = ops_dir / "dashboard_snapshot.json"

    _set_cli_args(
        monkeypatch,
        [
            "--trade-date",
            "2026-04-20",
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["overview"]["daily_report_available"] is False
    assert payload["overview"]["status_level"] == "NO_DATA"
    assert payload["sources"]["daily_ops_check_available"] is False
    assert payload["operator_summary"]["available"] is False
    assert payload["operator_summary"]["source"] == "none"
    assert payload["operator_summary"]["status_level"] == "MISSING"
    assert payload["startup"]["available"] is False
    assert payload["startup"]["status_level"] == "MISSING"
    assert payload["controls"]["kill_switch_status_level"] == "MISSING"
    assert payload["strategy"]["selection_available"] is False
    assert payload["strategy"]["effective_buy_strategy"] == "both"
    assert payload["strategy"]["run_timing1"] is True
    assert payload["strategy"]["run_timing2"] is True
    assert payload["rehearsal"]["available"] is False
    assert payload["scan"]["live_preview"]["available"] is False
    assert payload["scan"]["live_preview"]["card_key"] == "scan-live-preview"
    assert payload["executions"]["buy_preview"]["card_key"] == "execution-buy-preview"
    assert (
        payload["recovery"]["execution_recovery_review"]["card_key"]
        == "recovery-execution-review"
    )
    assert (
        payload["recovery"]["stale_signal_cleanup_review"]["card_key"]
        == "recovery-stale-signal-review"
    )
    assert payload["actions"]["required"] is False
    assert payload["actions"]["count"] == 0


def test_main_uses_preview_daily_ops_check_when_default_daily_ops_check_is_missing(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_snapshot_preview_daily_ops"
    )
    output_path = ops_dir / "dashboard_snapshot.json"

    _write_json(
        ops_dir / "daily_ops_report.json",
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "READY",
            "health_outcome": "READY",
            "highest_severity": "NONE",
            "attention_flags": [],
            "action_items": [],
            "alert": {
                "critical_count": 0,
                "warning_count": 0,
            },
            "artifacts": {
                "trading_session_preview": {
                    "exists": True,
                    "status_level": "READY",
                    "highest_severity": "NONE",
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "attention_flags": [],
                }
            },
        },
    )

    _write_json(
        ops_dir / "daily_ops_check.preview.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "READY",
            "overall_reason": None,
            "should_notify": False,
            "operator_summary": {
                "headline": "Preview looks ready.",
                "detail": "No attention flags detected.",
                "health_outcome": "READY",
                "dispatch_outcome": "NOT_REQUIRED",
                "primary_attention_flag": None,
                "primary_action_code": None,
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--trade-date",
            "2026-04-20",
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["sources"]["daily_ops_check_available"] is True
    assert payload["sources"]["daily_ops_check_path"].endswith(
        "daily_ops_check.preview.json"
    )
    assert payload["operator_summary"]["available"] is True
    assert payload["operator_summary"]["source"] == "daily_ops_check"
    assert payload["operator_summary"]["headline"] == "Preview looks ready."


def test_main_preserves_timing2_zero_match_action_from_preview_daily_ops_check(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_snapshot_timing2_zero_match"
    )
    output_path = ops_dir / "dashboard_snapshot.json"

    _write_json(
        ops_dir / "daily_ops_report.json",
        {
            "trade_date": "2026-04-20",
            "artifact_count": 1,
            "report_outcome": "ATTENTION",
            "health_outcome": "WARNING",
            "highest_severity": "WARNING",
            "attention_flags": ["TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY"],
            "action_items": [
                {
                    "action_code": "REVIEW_TIMING2_SETUP_ZERO_MATCH",
                }
            ],
            "alert": {
                "critical_count": 0,
                "warning_count": 1,
            },
            "artifacts": {
                "trading_session_preview": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "attention_flags": [
                        "TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY"
                    ],
                }
            },
        },
    )

    _write_json(
        ops_dir / "daily_ops_check.preview.json",
        {
            "trade_date": "2026-04-20",
            "overall_outcome": "NOTIFICATION_REQUIRED",
            "overall_reason": "health_outcome=WARNING meets min_level=WARNING",
            "should_notify": True,
            "operator_summary": {
                "headline": "1 attention flags detected (0 critical, 1 warning).",
                "detail": (
                    "Timing2 setup scan ran normally, but no matching symbols were "
                    "found for this trade date. Timing2 buys stayed disabled."
                ),
                "health_outcome": "WARNING",
                "dispatch_outcome": "NOT_REQUIRED",
                "primary_attention_flag": (
                    "TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY"
                ),
                "primary_action_code": "REVIEW_TIMING2_SETUP_ZERO_MATCH",
            },
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--trade-date",
            "2026-04-20",
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["sources"]["daily_ops_check_available"] is True
    assert payload["operator_summary"]["source"] == "daily_ops_check"
    assert (
        payload["operator_summary"]["primary_action_code"]
        == "REVIEW_TIMING2_SETUP_ZERO_MATCH"
    )
    assert payload["actions"]["top_action_codes"] == [
        "REVIEW_TIMING2_SETUP_ZERO_MATCH"
    ]


def test_main_builds_operator_summary_from_daily_report_fallback_when_daily_check_missing(
    test_db_path,
    monkeypatch,
):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_dashboard_snapshot_operator_fallback"
    )
    output_path = ops_dir / "dashboard_snapshot.json"

    _write_json(
        ops_dir / "daily_ops_report.json",
        {
            "trade_date": "2026-04-20",
            "artifact_count": 5,
            "report_outcome": "ATTENTION",
            "health_outcome": "WARNING",
            "highest_severity": "WARNING",
            "attention_flags": ["STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH"],
            "action_items": [
                {
                    "action_code": "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
                }
            ],
            "alert": {
                "level": "WARNING",
                "summary": "Startup blocked by open entry lot position mismatch.",
                "lines": [
                    "Affected symbols: 005930",
                    "Review executions and lot state before rerunning startup.",
                ],
                "critical_count": 0,
                "warning_count": 1,
            },
            "artifacts": {
                "startup_check": {
                    "exists": True,
                    "status_level": "WARNING",
                    "highest_severity": "WARNING",
                    "outcome": "BLOCKED",
                    "reason": "Startup blocked by reconcile safety gate.",
                    "checked_at": "2026-04-20T08:59:00+09:00",
                    "universe_exists": True,
                    "universe_candidate_count": 12,
                    "reconcile_changed_rows": 1,
                    "unresolved_order_count": 0,
                    "live_position_count": 1,
                    "reconcile_reason_code": "OPEN_ENTRY_LOT_POSITION_MISMATCH",
                    "reconcile_reason_message": (
                        "Reconciliation would change positions for symbols that still have "
                        "open entry lots. Review executions first: 005930"
                    ),
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
            "--trade-date",
            "2026-04-20",
            "--ops-dir",
            str(ops_dir),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["sources"]["daily_ops_check_available"] is False
    assert payload["operator_summary"]["available"] is True
    assert payload["operator_summary"]["source"] == "daily_ops_report_fallback"
    assert payload["operator_summary"]["status_level"] == "WARNING"
    assert payload["operator_summary"]["headline"] == (
        "Startup blocked by open entry lot position mismatch."
    )
    assert payload["operator_summary"]["detail"] == (
        "Reconciliation would change positions for symbols that still have open "
        "entry lots. Review executions first: 005930"
    )
    assert payload["operator_summary"]["primary_action_code"] == (
        "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK"
    )
    assert payload["operator_summary"]["affected_symbols"] == "005930"
