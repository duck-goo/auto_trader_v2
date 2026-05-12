from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import scripts.run_operational_preview_check as target


TRADE_DATE = "2026-05-11"


def _make_settings(test_db_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mode="live",
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
        "run_operational_preview_check.py",
        "--trade-date",
        TRADE_DATE,
        "--use-db-master",
        "--per-order-budget",
        "1000000",
        "--max-holdings",
        "3",
        "--ops-dir",
        str(output_path.parent),
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


def test_main_runs_preview_then_daily_ops_then_dashboard(
    test_db_path,
    monkeypatch,
):
    summary_path = test_db_path.with_name(
        f"{test_db_path.stem}_operational_preview_success.json"
    )
    _set_cli_args(monkeypatch, output_path=summary_path)

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                },
            )
            return 0
        if script_name == "run_daily_ops_check.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "overall_outcome": "READY",
                    "overall_reason": None,
                },
            )
            return 0
        if script_name == "build_dashboard_snapshot.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "operator_summary": {
                        "status_level": "READY",
                    },
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert [Path(command[1]).name for command in commands] == [
        "run_trading_session.py",
        "run_daily_ops_check.py",
        "build_dashboard_snapshot.py",
    ]

    trading_command = commands[0]
    assert "--use-db-master" in trading_command
    assert "--max-cycles" in trading_command
    assert trading_command[trading_command.index("--max-cycles") + 1] == "1"
    assert "--interval-seconds" in trading_command
    assert trading_command[trading_command.index("--interval-seconds") + 1] == "1"
    assert "--polling-lock-name" in trading_command
    assert (
        trading_command[trading_command.index("--polling-lock-name") + 1]
        == f"intraday_trading_polling:{TRADE_DATE}:preview-generic"
    )

    daily_ops_command = commands[1]
    assert "--notify-min-level" in daily_ops_command
    assert daily_ops_command[daily_ops_command.index("--notify-min-level") + 1] == "WARNING"

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "COMPLETED"
    assert payload["overall_reason"] is None
    assert payload["polling_lock_name"] == f"intraday_trading_polling:{TRADE_DATE}:preview-generic"
    assert payload["preopen_scan_timing2_setup"] is False
    assert payload["preopen_write_timing2_signals"] is False
    assert [step["outcome"] for step in payload["steps"]] == [
        "COMPLETED",
        "COMPLETED",
        "COMPLETED",
    ]


def test_main_returns_blocked_when_trading_session_is_blocked(
    test_db_path,
    monkeypatch,
):
    summary_path = test_db_path.with_name(
        f"{test_db_path.stem}_operational_preview_blocked.json"
    )
    _set_cli_args(
        monkeypatch,
        output_path=summary_path,
        extra_args=[
            "--buy-strategy",
            "timing2",
            "--preopen-scan-timing2-setup",
            "--preopen-write-timing2-signals",
        ],
    )

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "session_outcome": "PREOPEN_BLOCKED",
                    "session_reason": "startup blocked",
                },
            )
            return 4
        if script_name == "run_daily_ops_check.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "overall_outcome": "NOTIFICATION_REQUIRED",
                    "overall_reason": "health_outcome=WARNING meets min_level=WARNING",
                },
            )
            return 0
        if script_name == "build_dashboard_snapshot.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "operator_summary": {
                        "status_level": "WARNING",
                    },
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 4
    assert [Path(command[1]).name for command in commands] == [
        "run_trading_session.py",
        "run_daily_ops_check.py",
        "build_dashboard_snapshot.py",
    ]

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["overall_outcome"] == "TRADING_SESSION_BLOCKED"
    assert payload["overall_reason"] == "startup blocked"


def test_resolve_ops_dir_defaults_to_strategy_scoped_preview_directory():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        ops_dir=None,
        run_label=None,
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    resolved = target._resolve_ops_dir(args, run_label="run-1")

    assert resolved == (
        target.PROJECT_ROOT / "data" / "ops" / f"{TRADE_DATE}_preview_timing1" / "run-1"
    ).resolve()


def test_resolve_ops_dir_defaults_to_timing2_preview_directory_when_setup_scan_enabled():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        ops_dir=None,
        run_label=None,
        buy_strategy=None,
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=True,
        preopen_write_timing2_signals=True,
    )

    resolved = target._resolve_ops_dir(args, run_label="run-2")

    assert resolved == (
        target.PROJECT_ROOT / "data" / "ops" / f"{TRADE_DATE}_preview_timing2" / "run-2"
    ).resolve()


def test_resolve_run_label_defaults_to_kst_timestamp_when_ops_dir_is_not_overridden():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        ops_dir=None,
        run_label=None,
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    resolved = target._resolve_run_label(
        args,
        current_time=datetime(2026, 5, 11, 9, 15, 30, 123456),
    )

    assert resolved == "20260511_091530_123456"


def test_resolve_run_label_uses_explicit_ops_dir_name_when_run_label_is_missing():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        ops_dir=r".\data\ops\custom_preview_folder",
        run_label=None,
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    resolved = target._resolve_run_label(args)

    assert resolved == "custom_preview_folder"


def test_resolve_run_label_rejects_unsafe_characters():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        ops_dir=None,
        run_label="preview:timing1",
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    try:
        target._resolve_run_label(args)
    except ValueError as exc:
        assert "run-label" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsafe run label")


def test_resolve_polling_lock_name_defaults_to_strategy_scoped_preview_lock():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        polling_lock_name=None,
        run_label=None,
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    resolved = target._resolve_polling_lock_name(args)

    assert resolved == f"intraday_trading_polling:{TRADE_DATE}:preview-timing1"


def test_resolve_polling_lock_name_keeps_explicit_override():
    args = SimpleNamespace(
        trade_date=TRADE_DATE,
        polling_lock_name="intraday_trading_polling:custom-lock",
        run_label=None,
        buy_strategy="timing1",
        scan_timing1=False,
        scan_timing2=False,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    resolved = target._resolve_polling_lock_name(args)

    assert resolved == "intraday_trading_polling:custom-lock"


def test_resolve_effective_timing2_preopen_options_enables_setup_for_timing2_preview():
    args = SimpleNamespace(
        buy_strategy="timing2",
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=False,
    )

    scan_enabled, write_enabled = target._resolve_effective_timing2_preopen_options(
        args
    )

    assert scan_enabled is True
    assert write_enabled is True


def test_resolve_effective_timing2_preopen_options_promotes_scan_when_write_is_requested():
    args = SimpleNamespace(
        buy_strategy=None,
        preopen_scan_timing2_setup=False,
        preopen_write_timing2_signals=True,
    )

    scan_enabled, write_enabled = target._resolve_effective_timing2_preopen_options(
        args
    )

    assert scan_enabled is True
    assert write_enabled is True


def test_main_auto_enables_timing2_preopen_setup_for_timing2_strategy(
    test_db_path,
    monkeypatch,
):
    summary_path = test_db_path.with_name(
        f"{test_db_path.stem}_operational_preview_timing2_auto_setup.json"
    )
    _set_cli_args(
        monkeypatch,
        output_path=summary_path,
        extra_args=[
            "--buy-strategy",
            "timing2",
        ],
    )

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "run_trading_session.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "session_outcome": "COMPLETED",
                    "session_reason": None,
                },
            )
            return 0
        if script_name == "run_daily_ops_check.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "overall_outcome": "READY",
                    "overall_reason": None,
                },
            )
            return 0
        if script_name == "build_dashboard_snapshot.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "operator_summary": {
                        "status_level": "READY",
                    },
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    trading_command = commands[0]
    assert "--preopen-scan-timing2-setup" in trading_command
    assert "--preopen-write-timing2-signals" in trading_command

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["preopen_scan_timing2_setup"] is True
    assert payload["preopen_write_timing2_signals"] is True
