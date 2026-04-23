"""Mock end-to-end tests for the trading session launcher."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import scripts.run_trading_session as target


TRADE_DATE = "2026-04-20"


def _make_settings(test_db_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mode="mock",
        db_path=str(test_db_path),
        db_busy_timeout_ms=5000,
    )


def _set_cli_args(
    monkeypatch,
    *,
    output_path: Path,
    extra_args: list[str] | None = None,
) -> None:
    args = [
        "run_trading_session.py",
        "--trade-date",
        TRADE_DATE,
        "--use-db-master",
        "--per-order-budget",
        "1000000",
        "--max-holdings",
        "3",
        "--output",
        str(output_path),
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


class _FixedTemporaryDirectory:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> str:
        self._path.mkdir(parents=True, exist_ok=True)
        return str(self._path)

    def __exit__(self, exc_type, exc, tb) -> bool:
        shutil.rmtree(self._path, ignore_errors=True)
        return False


def _install_fixed_tempdir(monkeypatch, *, test_db_path: Path, suffix: str) -> None:
    fixed_path = test_db_path.with_name(f"{test_db_path.stem}_{suffix}")
    monkeypatch.setattr(
        target.tempfile,
        "TemporaryDirectory",
        lambda prefix="": _FixedTemporaryDirectory(fixed_path),
    )


def test_main_execute_success_runs_preopen_then_polling_and_passes_risk_flags(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(f"{test_db_path.stem}_session_success.json")
    _set_cli_args(
        monkeypatch,
        output_path=output_path,
        extra_args=[
            "--execute",
            "--max-daily-order-count",
            "7",
            "--max-daily-loss",
            "500000",
        ],
    )

    commands: list[list[str]] = []
    precheck_calls: list[dict[str, object]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="session_temp_success",
    )
    monkeypatch.setattr(
        target,
        "_precheck_polling_lock",
        lambda **kwargs: precheck_calls.append(kwargs),
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "prepare_preopen_universe.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "readiness_outcome": "READY",
                    "readiness_reason": None,
                },
            )
            return 0
        if script_name == "run_intraday_trading_polling.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "stop_reason": "CUTOFF_REACHED",
                    "cycle_count": 1,
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert len(precheck_calls) == 1
    assert precheck_calls[0]["execute_mode"] is True
    assert [Path(command[1]).name for command in commands] == [
        "prepare_preopen_universe.py",
        "run_intraday_trading_polling.py",
    ]

    polling_command = commands[1]
    assert "--execute" in polling_command
    assert "--max-daily-order-count" in polling_command
    assert polling_command[polling_command.index("--max-daily-order-count") + 1] == "7"
    assert "--max-daily-loss" in polling_command
    assert polling_command[polling_command.index("--max-daily-loss") + 1] == "500000"
    assert "--timing2-lot-stop-loss-percent" in polling_command
    assert (
        polling_command[polling_command.index("--timing2-lot-stop-loss-percent") + 1]
        == "1.5"
    )
    assert "--timing2-lot-take-profit-percent" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-lot-take-profit-percent") + 1
        ]
        == "5.0"
    )
    assert "--timing2-lot-partial-take-profit-percent" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-lot-partial-take-profit-percent") + 1
        ]
        == "50.0"
    )
    assert "--timing2-lot-sell-cost-rate" in polling_command
    assert (
        polling_command[polling_command.index("--timing2-lot-sell-cost-rate") + 1]
        == str(target.DEFAULT_TIMING2_SELL_COST_RATE)
    )
    assert "--timing2-30s-min-samples-per-bar" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-30s-min-samples-per-bar") + 1
        ]
        == "2"
    )
    assert "--timing2-max-sample-symbols-per-cycle" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-max-sample-symbols-per-cycle") + 1
        ]
        == "30"
    )
    assert "--timing2-30s-morning-start-time" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-30s-morning-start-time") + 1
        ]
        == "09:00:00"
    )
    assert "--timing2-30s-morning-end-time" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-30s-morning-end-time") + 1
        ]
        == "10:00:00"
    )
    assert "--timing2-30s-range-breakout-start-time" in polling_command
    assert (
        polling_command[
            polling_command.index("--timing2-30s-range-breakout-start-time") + 1
        ]
        == "10:00:00"
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["trade_date"] == TRADE_DATE
    assert payload["session_outcome"] == "COMPLETED"
    assert payload["session_reason"] is None
    assert payload["polling_started"] is True
    assert payload["preopen_exit_code"] == 0
    assert payload["polling_exit_code"] == 0


def test_main_stops_before_polling_when_preopen_is_blocked(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_session_preopen_blocked.json"
    )
    _set_cli_args(monkeypatch, output_path=output_path)

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="session_temp_preopen_blocked",
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        _write_child_output(
            command,
            {
                "trade_date": TRADE_DATE,
                "readiness_outcome": "BLOCKED",
                "readiness_reason": "startup gate rejected the session",
            },
        )
        return 4

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "prepare_preopen_universe.py"
    ]

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["session_outcome"] == "PREOPEN_BLOCKED"
    assert payload["session_reason"] == "startup gate rejected the session"
    assert payload["polling_started"] is False
    assert payload["polling_exit_code"] is None


def test_main_surfaces_polling_block_reason_in_session_payload(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_session_polling_blocked.json"
    )
    _set_cli_args(monkeypatch, output_path=output_path)

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="session_temp_polling_blocked",
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "prepare_preopen_universe.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "readiness_outcome": "READY",
                    "readiness_reason": None,
                },
            )
            return 0
        if script_name == "run_intraday_trading_polling.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "stop_reason": "MAX_DAILY_LOSS_REACHED",
                },
            )
            return 4
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "prepare_preopen_universe.py",
        "run_intraday_trading_polling.py",
    ]

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["session_outcome"] == "POLLING_BLOCKED"
    assert payload["session_reason"] == "MAX_DAILY_LOSS_REACHED"
    assert payload["polling_started"] is True
    assert payload["polling_exit_code"] == 4


def test_main_forwards_buy_strategy_to_polling_and_payload(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_session_buy_strategy.json"
    )
    _set_cli_args(
        monkeypatch,
        output_path=output_path,
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
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="session_temp_buy_strategy",
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "prepare_preopen_universe.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "readiness_outcome": "READY",
                    "readiness_reason": None,
                },
            )
            return 0
        if script_name == "run_intraday_trading_polling.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "stop_reason": "MAX_CYCLES_REACHED",
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    polling_command = commands[1]
    assert "--buy-strategy" in polling_command
    assert polling_command[polling_command.index("--buy-strategy") + 1] == "timing2"
    assert "--scan-timing1" not in polling_command
    assert "--scan-timing2" not in polling_command

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["buy_strategy"] == "timing2"
    assert payload["run_timing1"] is False
    assert payload["run_timing2"] is True


def test_main_rejects_explicit_timing2_without_preopen_setup_signals(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_session_buy_strategy_missing_setup.json"
    )
    _set_cli_args(
        monkeypatch,
        output_path=output_path,
        extra_args=["--buy-strategy", "timing2"],
    )

    exit_code = target.main()

    assert exit_code == 5
    assert not output_path.exists()
