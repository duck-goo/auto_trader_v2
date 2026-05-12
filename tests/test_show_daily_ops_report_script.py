from __future__ import annotations

import json
from pathlib import Path

import scripts.show_daily_ops_report as target


def _base_summaries() -> dict[str, dict]:
    return {
        key: {
            "exists": False,
            "path": str(Path(f"C:/python/auto_trader_v2/data/ops/test/{key}.json")),
        }
        for key in target.KNOWN_ARTIFACT_FILES
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_summarize_order_maintenance_includes_stale_signal_cleanup_summary():
    payload = {
        "trade_date": "2026-05-06",
        "execute_mode": False,
        "result": {
            "manual_recovery_required_client_order_ids": ["A-1"],
            "sync_result": {
                "candidate_count": 2,
                "synced_count": 0,
                "execution_recovery_required_count": 1,
            },
            "execution_recovery_result": {
                "preview_ready_count": 1,
                "recovered_count": 0,
                "manual_recovery_required_count": 1,
            },
            "stale_buy_cancel_result": {
                "cancelled_count": 0,
            },
            "stale_sell_cancel_result": {
                "cancelled_count": 0,
            },
            "stale_buy_signal_cleanup_result": {
                "preview_ready_count": 2,
                "cleaned_count": 0,
                "blocked_count": 1,
                "candidates": [
                    {
                        "symbol": "005930",
                        "outcome": "PREVIEW_READY",
                        "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                    },
                    {
                        "symbol": "000660",
                        "outcome": "BLOCKED",
                        "reason_code": "INVALID_SIGNAL_SCANNED_AT",
                    },
                ],
            },
            "stale_sell_signal_cleanup_result": {
                "preview_ready_count": 0,
                "cleaned_count": 1,
                "blocked_count": 1,
                "candidates": [
                    {
                        "symbol": "035420",
                        "outcome": "CLEANED",
                        "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                    },
                    {
                        "symbol": "005930",
                        "outcome": "BLOCKED",
                        "reason_code": "SIGNAL_TIMESTAMP_IN_FUTURE",
                    },
                ],
            },
        },
    }

    summary = target._summarize_order_maintenance(
        label="order_maintenance_preview",
        path=Path("C:/python/auto_trader_v2/data/ops/test/order_maintenance.preview.json"),
        payload=payload,
    )

    assert summary["manual_recovery_required_count"] == 1
    assert summary["stale_signal_preview_ready_count"] == 2
    assert summary["stale_signal_cleaned_count"] == 1
    assert summary["stale_signal_blocked_count"] == 2
    assert summary["stale_signal_symbol_hint"] == "005930, 000660, 035420"
    assert summary["stale_signal_blocked_reason_codes"] == (
        "INVALID_SIGNAL_SCANNED_AT, SIGNAL_TIMESTAMP_IN_FUTURE"
    )


def test_summarize_stale_signal_cleanup_review_includes_counts_and_symbols():
    payload = {
        "trade_date": "2026-05-06",
        "source_label": "order_maintenance.execute",
        "source_path": "C:/python/auto_trader_v2/data/ops/test/order_maintenance.execute.json",
        "review_item_count": 3,
        "blocked_item_count": 1,
        "preview_ready_item_count": 1,
        "cleaned_item_count": 1,
        "top_symbols": "005930, 000660",
        "items": [
            {
                "signal_id": 1,
                "scope": "buy",
                "symbol": "005930",
                "strategy_name": "buy_timing2_30s_morning_reclaim",
                "scanned_at": "2026-05-06T09:07:01+09:00",
                "outcome": "BLOCKED",
                "reason_code": "INVALID_SIGNAL_SCANNED_AT",
                "age_seconds": 421,
            },
            {
                "signal_id": 2,
                "scope": "sell",
                "symbol": "000660",
                "strategy_name": "sell_stop_loss",
                "scanned_at": "2026-05-06T09:12:44+09:00",
                "outcome": "PREVIEW_READY",
                "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                "age_seconds": 366,
            },
            {
                "signal_id": 3,
                "scope": "sell",
                "symbol": "035420",
                "strategy_name": "sell_take_profit",
                "scanned_at": "2026-05-06T09:03:05+09:00",
                "outcome": "CLEANED",
                "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                "age_seconds": 605,
            },
        ],
    }

    summary = target._summarize_stale_signal_cleanup_review(
        path=Path(
            "C:/python/auto_trader_v2/data/ops/test/stale_signal_cleanup.review.json"
        ),
        payload=payload,
    )

    assert summary["exists"] is True
    assert summary["source_label"] == "order_maintenance.execute"
    assert summary["source_path"].endswith("order_maintenance.execute.json")
    assert summary["source_file_name"] == "order_maintenance.execute.json"
    assert summary["review_item_count"] == 3
    assert summary["blocked_item_count"] == 1
    assert summary["top_symbols"] == "005930, 000660"
    assert summary["item_count"] == 3
    assert summary["preview_items"] == [
        {
            "scope": "buy",
            "symbol": "005930",
            "strategy_name": "buy_timing2_30s_morning_reclaim",
            "scanned_at": "2026-05-06T09:07:01+09:00",
            "outcome": "BLOCKED",
            "reason_code": "INVALID_SIGNAL_SCANNED_AT",
            "age_seconds": 421,
        },
        {
            "scope": "sell",
            "symbol": "000660",
            "strategy_name": "sell_stop_loss",
            "scanned_at": "2026-05-06T09:12:44+09:00",
            "outcome": "PREVIEW_READY",
            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
            "age_seconds": 366,
        },
        {
            "scope": "sell",
            "symbol": "035420",
            "strategy_name": "sell_take_profit",
            "scanned_at": "2026-05-06T09:03:05+09:00",
            "outcome": "CLEANED",
            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
            "age_seconds": 605,
        },
    ]


def test_collect_attention_flags_adds_stale_signal_cleanup_blocked_items():
    summaries = _base_summaries()
    summaries["order_maintenance_execute"].update(
        {
            "exists": True,
            "stale_signal_blocked_count": 1,
            "stale_signal_symbol_hint": "005930",
            "stale_signal_blocked_reason_codes": "INVALID_SIGNAL_SCANNED_AT",
        }
    )

    flags = target._collect_attention_flags(summaries=summaries, rehearsals=[])

    assert "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS" in flags


def test_build_attention_message_for_stale_signal_cleanup_blocked_items():
    summaries = _base_summaries()
    summaries["order_maintenance_preview"].update(
        {
            "exists": True,
            "stale_signal_blocked_count": 2,
            "stale_signal_symbol_hint": "005930, 000660",
            "stale_signal_blocked_reason_codes": (
                "INVALID_SIGNAL_SCANNED_AT, SIGNAL_TIMESTAMP_IN_FUTURE"
            ),
        }
    )

    message = target._build_attention_message(
        flag="STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
        summaries=summaries,
        rehearsals=[],
    )

    assert message == (
        "Stale signal cleanup blocked count=2 "
        "reason_codes=INVALID_SIGNAL_SCANNED_AT, SIGNAL_TIMESTAMP_IN_FUTURE "
        "symbols=005930, 000660"
    )


def test_build_action_item_for_stale_signal_cleanup_blocked_items():
    item = target._build_action_item_for_flag(
        flag="STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
        trade_date="2026-05-06",
        severity=target.SEVERITY_WARNING,
        message="Stale signal cleanup blocked count=1 reason_codes=INVALID_SIGNAL_SCANNED_AT",
        reference_path="C:/python/auto_trader_v2/data/ops/2026-05-06/order_maintenance.execute.json",
        summaries=_base_summaries(),
    )

    assert item["action_code"] == "REVIEW_STALE_SIGNAL_CLEANUP"
    assert item["severity"] == target.SEVERITY_WARNING
    assert "show_stale_signal_cleanup_review.py" in item["suggested_command"]


def test_summarize_trading_session_includes_timing2_setup_scan_details():
    payload = {
        "trade_date": "2026-05-11",
        "execute_mode": False,
        "session_outcome": "COMPLETED",
        "session_reason": None,
        "preopen_exit_code": 0,
        "preopen_result": {
            "readiness_outcome": "READY",
            "readiness_reason": None,
            "timing2_setup_scan_outcome": "SCANNED",
            "timing2_setup_scan_reason": None,
            "timing2_setup_scan_result": {
                "matched_count": 0,
                "recorded_count": 0,
            },
        },
        "polling_started": True,
        "polling_exit_code": 0,
        "polling_result": {
            "stop_reason": "MAX_CYCLES_REACHED",
            "timing2_setup_readiness": {
                "trade_date": "2026-05-11",
                "required": True,
                "setup_signal_count": 0,
                "ready": False,
                "reason": (
                    "Timing2 setup signals are missing for this trade date. "
                    "Timing2 intraday buy scans will be skipped, but sell/maintenance "
                    "flows should continue."
                ),
            },
        },
    }

    summary = target._summarize_trading_session(
        label="trading_session_preview",
        path=Path("C:/python/auto_trader_v2/data/ops/test/run_trading_session.preview.json"),
        payload=payload,
    )

    assert summary["timing2_setup_scan_outcome"] == "SCANNED"
    assert summary["timing2_setup_scan_matched_count"] == 0
    assert summary["timing2_setup_scan_recorded_count"] == 0


def test_summarize_trading_session_includes_intraday_bar_refresh_failure_details():
    payload = {
        "trade_date": "2026-05-12",
        "execute_mode": False,
        "session_outcome": "COMPLETED",
        "session_reason": None,
        "preopen_exit_code": 0,
        "polling_started": True,
        "polling_exit_code": 0,
        "polling_result": {
            "stop_reason": "MAX_CYCLES_REACHED",
            "cycles": [
                {
                    "intraday_bar_refresh": {
                        "outcome": "COMPLETED",
                        "reason": (
                            "Some symbols failed 15-minute bar refresh. "
                            "failed_count=1"
                        ),
                        "summary": {
                            "candidate_count": 1,
                            "failed_count": 1,
                        },
                    }
                }
            ],
        },
    }

    summary = target._summarize_trading_session(
        label="trading_session_preview",
        path=Path("C:/python/auto_trader_v2/data/ops/test/run_trading_session.preview.json"),
        payload=payload,
    )

    assert summary["intraday_bar_refresh_cycle_count"] == 1
    assert summary["intraday_bar_refresh_failed_count"] == 1
    assert "failed_count=1" in summary["intraday_bar_refresh_reason"]


def test_build_action_item_for_timing2_setup_not_ready_uses_zero_match_action_when_scan_ran():
    summaries = _base_summaries()
    summaries["trading_session_preview"].update(
        {
            "exists": True,
            "timing2_setup_scan_outcome": "SCANNED",
            "timing2_setup_scan_matched_count": 0,
            "timing2_setup_ready": False,
            "timing2_setup_reason": (
                "Timing2 setup signals are missing for this trade date. "
                "Timing2 intraday buy scans will be skipped, but sell/maintenance "
                "flows should continue."
            ),
        }
    )

    item = target._build_action_item_for_flag(
        flag="TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY",
        trade_date="2026-05-11",
        severity=target.SEVERITY_WARNING,
        message=summaries["trading_session_preview"]["timing2_setup_reason"],
        reference_path="C:/python/auto_trader_v2/data/ops/2026-05-11/run_trading_session.preview.json",
        summaries=summaries,
    )

    assert item["action_code"] == "REVIEW_TIMING2_SETUP_ZERO_MATCH"
    assert "no matching symbols" in item["summary"]
    assert item["suggested_command"] is None


def test_build_action_item_for_timing2_setup_not_ready_keeps_rerun_action_when_scan_was_not_run():
    summaries = _base_summaries()
    summaries["trading_session_preview"].update(
        {
            "exists": True,
            "timing2_setup_scan_outcome": None,
            "timing2_setup_scan_matched_count": None,
            "timing2_setup_ready": False,
            "timing2_setup_reason": "Timing2 setup signals are missing for the trading session.",
        }
    )

    item = target._build_action_item_for_flag(
        flag="TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY",
        trade_date="2026-05-11",
        severity=target.SEVERITY_WARNING,
        message=summaries["trading_session_preview"]["timing2_setup_reason"],
        reference_path="C:/python/auto_trader_v2/data/ops/2026-05-11/run_trading_session.preview.json",
        summaries=summaries,
    )

    assert item["action_code"] == "RERUN_TRADING_SESSION_WITH_TIMING2_SETUP"
    assert "--preopen-scan-timing2-setup" in item["suggested_command"]


def test_collect_attention_flags_adds_intraday_bar_refresh_failure_flag():
    summaries = _base_summaries()
    summaries["trading_session_preview"].update(
        {
            "exists": True,
            "session_outcome": "COMPLETED",
            "intraday_bar_refresh_failed_count": 1,
            "intraday_bar_refresh_reason": (
                "Some symbols failed 15-minute bar refresh. failed_count=1"
            ),
        }
    )

    flags = target._collect_attention_flags(summaries=summaries, rehearsals=[])

    assert "TRADING_SESSION_PREVIEW_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE" in flags


def test_build_action_item_for_intraday_bar_refresh_failure():
    item = target._build_action_item_for_flag(
        flag="TRADING_SESSION_PREVIEW_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE",
        trade_date="2026-05-12",
        severity=target.SEVERITY_WARNING,
        message="Some symbols failed 15-minute bar refresh. failed_count=1",
        reference_path="C:/python/auto_trader_v2/data/ops/2026-05-12/run_trading_session.preview.json",
        summaries=_base_summaries(),
    )

    assert item["action_code"] == "REVIEW_INTRADAY_BAR_REFRESH_FAILURE"
    assert "15-minute bar refresh" in item["summary"]
    assert item["suggested_command"] is None


def test_stale_signal_cleanup_review_preferred_for_flag_reference_and_message():
    summaries = _base_summaries()
    summaries["stale_signal_cleanup_review"].update(
        {
            "exists": True,
            "path": (
                "C:/python/auto_trader_v2/data/ops/test/"
                "stale_signal_cleanup.review.json"
            ),
            "blocked_item_count": 2,
            "top_symbols": "005930, 000660",
        }
    )
    summaries["order_maintenance_execute"].update(
        {
            "exists": True,
            "stale_signal_blocked_count": 2,
            "stale_signal_symbol_hint": "fallback",
            "stale_signal_blocked_reason_codes": "INVALID_SIGNAL_SCANNED_AT",
        }
    )

    reference_path = target._resolve_reference_path_for_flag(
        flag="STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
        summaries=summaries,
        rehearsals=[],
    )
    message = target._build_attention_message(
        flag="STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
        summaries=summaries,
        rehearsals=[],
    )
    summary_key = target._resolve_summary_key_for_flag(
        flag="STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
        summaries=summaries,
    )

    assert reference_path.endswith("stale_signal_cleanup.review.json")
    assert message == "Stale signal cleanup blocked count=2 symbols=005930, 000660"
    assert summary_key == "stale_signal_cleanup_review"


def test_scan_rehearsals_sorts_latest_finished_first(tmp_path: Path):
    old_dir = tmp_path / "rehearsal_old"
    latest_dir = tmp_path / "rehearsal_latest"

    _write_json(
        old_dir / "rehearsal_summary.json",
        {
            "trade_date": "2026-05-11",
            "started_at": "2026-05-11T09:00:00+09:00",
            "finished_at": "2026-05-11T09:05:00+09:00",
            "overall_outcome": "STARTUP_BLOCKED",
            "overall_reason": "old blocked",
        },
    )
    _write_json(
        latest_dir / "rehearsal_summary.json",
        {
            "trade_date": "2026-05-11",
            "started_at": "2026-05-11T10:00:00+09:00",
            "finished_at": "2026-05-11T10:05:00+09:00",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
        },
    )

    rehearsals = target._scan_rehearsals(tmp_path)

    assert [row["name"] for row in rehearsals] == [
        "rehearsal_latest",
        "rehearsal_old",
    ]


def test_collect_attention_flags_prefers_latest_rehearsal_over_old_blocked_result():
    flags = target._collect_attention_flags(
        summaries=_base_summaries(),
        rehearsals=[
            {
                "name": "rehearsal_old",
                "path": "C:/python/auto_trader_v2/data/ops/test/rehearsal_old/rehearsal_summary.json",
                "started_at": "2026-05-11T09:00:00+09:00",
                "finished_at": "2026-05-11T09:05:00+09:00",
                "overall_outcome": "STARTUP_BLOCKED",
                "overall_reason": "old blocked",
                "scan_settings": {},
                "timing2_30s_verified": None,
            },
            {
                "name": "rehearsal_latest",
                "path": "C:/python/auto_trader_v2/data/ops/test/rehearsal_latest/rehearsal_summary.json",
                "started_at": "2026-05-11T10:00:00+09:00",
                "finished_at": "2026-05-11T10:05:00+09:00",
                "overall_outcome": "COMPLETED",
                "overall_reason": None,
                "scan_settings": {},
                "timing2_30s_verified": None,
            },
        ],
    )

    assert "REHEARSAL_BLOCKED" not in flags
