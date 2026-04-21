"""Tests for show_ops_summary.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.show_ops_summary as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["show_ops_summary.py", *args])


def test_main_reads_summary_input_and_writes_normalized_output(
    test_db_path,
    monkeypatch,
):
    summary_dir = test_db_path.with_name(f"{test_db_path.stem}_ops_summary")
    summary_path = summary_dir / "rehearsal_summary.json"
    output_path = summary_dir / "normalized.json"

    _write_json(
        summary_path,
        {
            "trade_date": "2026-04-20",
            "mode": "mock",
            "started_at": "2026-04-20T09:05:00+09:00",
            "finished_at": "2026-04-20T09:05:02+09:00",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
            "include_after_close": False,
            "intraday_window": {
                "reference_at": "2026-04-20T09:05:00+09:00",
                "start_time": "09:04:00",
                "cutoff_time": "09:06:00",
            },
            "steps": [
                {
                    "name": "Startup Check",
                    "exit_code": 0,
                    "outcome": "READY",
                    "reason": None,
                    "output_path": str(summary_dir / "startup_check.json"),
                    "result": {
                        "trade_date": "2026-04-20",
                        "checked_at": "2026-04-20T08:59:00+09:00",
                        "outcome": "READY",
                        "reason": None,
                        "universe_snapshot": {
                            "exists": True,
                            "candidate_count": 12,
                            "refreshed_at": "2026-04-20T08:30:00+09:00",
                        },
                        "unresolved_orders": [],
                        "live_positions": [],
                    },
                },
                {
                    "name": "Trading Session Preview",
                    "exit_code": 0,
                    "outcome": "COMPLETED",
                    "reason": None,
                    "output_path": str(summary_dir / "trading_session_preview.json"),
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
                        },
                    },
                },
            ],
        },
    )

    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(summary_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "COMPLETED"
    assert payload["steps"][0]["startup_outcome"] == "READY"
    assert payload["steps"][0]["universe_candidate_count"] == 12
    assert payload["steps"][1]["session_outcome"] == "COMPLETED"
    assert payload["steps"][1]["polling_stop_reason"] == "MAX_CYCLES_REACHED"


def test_main_reads_from_output_dir_and_returns_blocked(
    test_db_path,
    monkeypatch,
):
    summary_dir = test_db_path.with_name(f"{test_db_path.stem}_ops_summary_blocked")
    summary_path = summary_dir / "rehearsal_summary.json"

    _write_json(
        summary_path,
        {
            "trade_date": "2026-04-20",
            "mode": "mock",
            "started_at": "2026-04-20T09:05:00+09:00",
            "finished_at": "2026-04-20T09:05:01+09:00",
            "overall_outcome": "STARTUP_BLOCKED",
            "overall_reason": "Unresolved orders exist. Startup is blocked.",
            "include_after_close": False,
            "intraday_window": {},
            "steps": [
                {
                    "name": "Startup Check",
                    "exit_code": 4,
                    "outcome": "BLOCKED",
                    "reason": "Unresolved orders exist. Startup is blocked.",
                    "output_path": str(summary_dir / "startup_check.json"),
                    "result": {
                        "trade_date": "2026-04-20",
                        "checked_at": "2026-04-20T08:59:00+09:00",
                        "outcome": "BLOCKED",
                        "reason": "Unresolved orders exist. Startup is blocked.",
                        "universe_snapshot": {
                            "exists": True,
                            "candidate_count": 5,
                            "refreshed_at": "2026-04-20T08:30:00+09:00",
                        },
                        "unresolved_orders": [{"client_order_id": "COID_1"}],
                        "live_positions": [],
                    },
                }
            ],
        },
    )

    _set_cli_args(monkeypatch, ["--output-dir", str(summary_dir)])

    exit_code = target.main()

    assert exit_code == 4


def test_main_falls_back_to_child_output_when_step_result_is_missing(
    test_db_path,
    monkeypatch,
):
    summary_dir = test_db_path.with_name(f"{test_db_path.stem}_ops_summary_fallback")
    summary_path = summary_dir / "rehearsal_summary.json"
    after_close_path = summary_dir / "after_close_preview.json"

    _write_json(
        after_close_path,
        {
            "trade_date": "2026-04-20",
            "write_mode": False,
            "session_outcome": "COMPLETED",
            "session_reason": None,
            "lock_acquired": False,
            "lock_released": False,
            "steps": [
                {
                    "name": "Refresh Intraday Bars 15m",
                    "outcome": "COMPLETED",
                    "reason": None,
                    "exit_code": 0,
                },
                {
                    "name": "Scan Sell MACD Exit Signals",
                    "outcome": "COMPLETED",
                    "reason": None,
                    "exit_code": 0,
                },
            ],
        },
    )
    _write_json(
        summary_path,
        {
            "trade_date": "2026-04-20",
            "mode": "mock",
            "started_at": "2026-04-20T15:35:00+09:00",
            "finished_at": "2026-04-20T15:35:02+09:00",
            "overall_outcome": "COMPLETED",
            "overall_reason": None,
            "include_after_close": True,
            "intraday_window": {},
            "steps": [
                {
                    "name": "After Close Preview",
                    "exit_code": 0,
                    "outcome": "COMPLETED",
                    "reason": None,
                    "output_path": str(after_close_path),
                    "result": None,
                }
            ],
        },
    )

    normalized_path = summary_dir / "fallback_normalized.json"
    _set_cli_args(
        monkeypatch,
        [
            "--input",
            str(summary_path),
            "--output",
            str(normalized_path),
        ],
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert payload["steps"][0]["session_outcome"] == "COMPLETED"
    assert payload["steps"][0]["steps"][0]["name"] == "Refresh Intraday Bars 15m"
    assert payload["steps"][0]["steps"][1]["name"] == "Scan Sell MACD Exit Signals"
