"""Mock end-to-end tests for the mock operational rehearsal launcher."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytz

import scripts.run_mock_operational_rehearsal as target


TRADE_DATE = "2026-04-20"
KST = pytz.timezone("Asia/Seoul")
FIXED_NOW = KST.localize(datetime(2026, 4, 20, 9, 5, 0))


def _make_settings(test_db_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mode="mock",
        db_path=str(test_db_path),
        db_busy_timeout_ms=5000,
    )


def _set_cli_args(
    monkeypatch,
    *,
    output_dir: Path,
    extra_args: list[str] | None = None,
) -> None:
    args = [
        "run_mock_operational_rehearsal.py",
        "--trade-date",
        TRADE_DATE,
        "--per-order-budget",
        "1000000",
        "--max-holdings",
        "3",
        "--output-dir",
        str(output_dir),
    ]
    if extra_args:
        args.extend(extra_args)
    monkeypatch.setattr(sys, "argv", args)


def _write_child_output(command: list[str], payload: dict) -> None:
    output_index = command.index("--output") + 1
    output_path = Path(command[output_index])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_main_runs_startup_then_one_cycle_trading_preview(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_success")
    _set_cli_args(monkeypatch, output_dir=output_dir)

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "_now", lambda: FIXED_NOW)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "startup_check.py":
            _write_child_output(
                command,
                {
                    "outcome": "READY",
                    "reason": None,
                },
            )
            return 0
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert [Path(command[1]).name for command in commands] == [
        "startup_check.py",
        "run_trading_session.py",
    ]

    trading_command = commands[1]
    assert "--use-db-master" in trading_command
    assert "--max-cycles" in trading_command
    assert trading_command[trading_command.index("--max-cycles") + 1] == "1"
    assert "--interval-seconds" in trading_command
    assert trading_command[trading_command.index("--interval-seconds") + 1] == "1"
    assert "--timing2-30s-min-samples-per-bar" in trading_command
    assert (
        trading_command[
            trading_command.index("--timing2-30s-min-samples-per-bar") + 1
        ]
        == "2"
    )
    assert "--timing2-max-sample-symbols-per-cycle" in trading_command
    assert (
        trading_command[
            trading_command.index("--timing2-max-sample-symbols-per-cycle") + 1
        ]
        == "30"
    )
    assert trading_command[trading_command.index("--buy-start-time") + 1] == "09:04:00"
    assert trading_command[trading_command.index("--buy-cutoff-time") + 1] == "09:06:00"

    summary_path = output_dir / "rehearsal_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["trade_date"] == TRADE_DATE
    assert payload["overall_outcome"] == "COMPLETED"
    assert payload["overall_reason"] is None
    assert payload["include_after_close"] is False
    assert payload["intraday_window"]["start_time"] == "09:04:00"
    assert payload["intraday_window"]["cutoff_time"] == "09:06:00"
    assert payload["scan_settings"] == {
        "buy_strategy": None,
        "effective_buy_strategy": "both",
        "scan_timing1": False,
        "scan_timing2": False,
        "preopen_scan_timing2_setup": False,
        "preopen_write_timing2_signals": False,
        "preopen_timing2_daily_count": 90,
        "preopen_timing2_new_high_lookback_days": 60,
        "timing2_30s_min_samples_per_bar": 2,
        "timing2_max_sample_symbols_per_cycle": 30,
    }
    assert [step["outcome"] for step in payload["steps"]] == [
        "READY",
        "COMPLETED",
    ]


def test_main_stops_when_startup_is_blocked(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_blocked")
    _set_cli_args(monkeypatch, output_dir=output_dir)

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "_now", lambda: FIXED_NOW)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        _write_child_output(
            command,
            {
                "outcome": "BLOCKED",
                "reason": "Unresolved orders exist. Startup is blocked.",
            },
        )
        return 4

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == ["startup_check.py"]

    payload = json.loads((output_dir / "rehearsal_summary.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "STARTUP_BLOCKED"
    assert payload["overall_reason"] == "Unresolved orders exist. Startup is blocked."
    assert [step["outcome"] for step in payload["steps"]] == ["BLOCKED"]


def test_main_can_include_after_close_and_forward_risk_flags(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_after_close")
    _set_cli_args(
        monkeypatch,
        output_dir=output_dir,
        extra_args=[
            "--master-input",
            ".\\data\\debug\\universe_master_sample.json",
            "--master-format",
            "json",
            "--include-after-close",
            "--max-daily-order-count",
            "7",
            "--max-daily-loss",
            "500000",
            "--preopen-scan-timing2-setup",
            "--preopen-write-timing2-signals",
            "--preopen-timing2-daily-count",
            "90",
            "--preopen-timing2-new-high-lookback-days",
            "60",
            "--scan-timing1",
            "--scan-timing2",
        ],
    )

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "_now", lambda: FIXED_NOW)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "startup_check.py":
            _write_child_output(command, {"outcome": "READY", "reason": None})
            return 0
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "polling_result": {
                        "cycles": [
                            {
                                "timing2_price_sample_capture": {
                                    "outcome": "COMPLETED"
                                },
                                "timing2_30s_bar_build": {
                                    "outcome": "COMPLETED"
                                },
                                "timing2_30s_trigger_scan": {
                                    "outcome": "COMPLETED"
                                },
                            }
                        ]
                    },
                },
            )
            return 0
        if script_name == "run_after_close_session.py":
            _write_child_output(
                command,
                {
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert [Path(command[1]).name for command in commands] == [
        "startup_check.py",
        "run_trading_session.py",
        "run_after_close_session.py",
    ]

    trading_command = commands[1]
    assert "--master-input" in trading_command
    assert trading_command[trading_command.index("--master-input") + 1] == ".\\data\\debug\\universe_master_sample.json"
    assert "--use-db-master" not in trading_command
    assert "--max-daily-order-count" in trading_command
    assert trading_command[trading_command.index("--max-daily-order-count") + 1] == "7"
    assert "--max-daily-loss" in trading_command
    assert trading_command[trading_command.index("--max-daily-loss") + 1] == "500000"
    assert "--preopen-scan-timing2-setup" in trading_command
    assert "--preopen-write-timing2-signals" in trading_command
    assert "--preopen-timing2-daily-count" in trading_command
    assert trading_command[trading_command.index("--preopen-timing2-daily-count") + 1] == "90"
    assert "--preopen-timing2-new-high-lookback-days" in trading_command
    assert (
        trading_command[
            trading_command.index("--preopen-timing2-new-high-lookback-days") + 1
        ]
        == "60"
    )
    assert "--scan-timing1" in trading_command
    assert "--scan-timing2" in trading_command

    payload = json.loads((output_dir / "rehearsal_summary.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "COMPLETED"
    assert payload["include_after_close"] is True
    assert payload["scan_settings"]["buy_strategy"] is None
    assert payload["scan_settings"]["effective_buy_strategy"] == "both"
    assert payload["scan_settings"]["scan_timing1"] is True
    assert payload["scan_settings"]["scan_timing2"] is True
    assert payload["scan_settings"]["preopen_scan_timing2_setup"] is True
    assert payload["scan_settings"]["preopen_write_timing2_signals"] is True
    assert [step["name"] for step in payload["steps"]] == [
        "Startup Check",
        "Trading Session Preview",
        "After Close Preview",
    ]


def test_main_fails_when_timing2_rehearsal_omits_30s_steps(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_timing2_missing")
    _set_cli_args(
        monkeypatch,
        output_dir=output_dir,
        extra_args=[
            "--preopen-scan-timing2-setup",
            "--preopen-write-timing2-signals",
            "--scan-timing2",
        ],
    )

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "_now", lambda: FIXED_NOW)

    def fake_run_child(command: list[str]) -> int:
        script_name = Path(command[1]).name
        if script_name == "startup_check.py":
            _write_child_output(command, {"outcome": "READY", "reason": None})
            return 0
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 5
    payload = json.loads((output_dir / "rehearsal_summary.json").read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "TRADING_SESSION_FAILED"
    assert payload["steps"][1]["outcome"] == "FAILED"
    assert payload["steps"][1]["reason"] == (
        "Timing2 rehearsal result is missing polling_result."
    )


def test_main_rejects_timing2_setup_write_without_setup_scan(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_bad_setup_flags")
    _set_cli_args(
        monkeypatch,
        output_dir=output_dir,
        extra_args=["--preopen-write-timing2-signals"],
    )

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)

    exit_code = target.main()

    assert exit_code == 5
    assert not (output_dir / "rehearsal_summary.json").exists()


def test_main_forwards_buy_strategy_timing2(
    test_db_path,
    monkeypatch,
):
    output_dir = test_db_path.with_name(f"{test_db_path.stem}_rehearsal_buy_strategy")
    _set_cli_args(
        monkeypatch,
        output_dir=output_dir,
        extra_args=[
            "--preopen-scan-timing2-setup",
            "--preopen-write-timing2-signals",
            "--buy-strategy",
            "timing2",
        ],
    )

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "_now", lambda: FIXED_NOW)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "startup_check.py":
            _write_child_output(command, {"outcome": "READY", "reason": None})
            return 0
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                    "polling_result": {
                        "cycles": [
                            {
                                "timing2_price_sample_capture": {
                                    "outcome": "COMPLETED"
                                },
                                "timing2_30s_bar_build": {
                                    "outcome": "COMPLETED"
                                },
                                "timing2_30s_trigger_scan": {
                                    "outcome": "COMPLETED"
                                },
                            }
                        ]
                    },
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    trading_command = commands[1]
    assert "--buy-strategy" in trading_command
    assert trading_command[trading_command.index("--buy-strategy") + 1] == "timing2"
    payload = json.loads((output_dir / "rehearsal_summary.json").read_text(encoding="utf-8"))
    assert payload["scan_settings"]["buy_strategy"] == "timing2"
    assert payload["scan_settings"]["effective_buy_strategy"] == "timing2"
