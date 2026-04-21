"""Mock end-to-end tests for the after-close session launcher."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import scripts.run_after_close_session as target
from services import RuntimeLockService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import RuntimeLockRepository


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
        "run_after_close_session.py",
        "--trade-date",
        TRADE_DATE,
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


def _count_runtime_locks(test_db_path: Path) -> int:
    conn = sqlite3.connect(str(test_db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM runtime_locks").fetchone()
        assert row is not None
        return int(row[0])
    finally:
        conn.close()


def _seed_live_lock(test_db_path: Path, *, lock_name: str) -> None:
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        lock_service = RuntimeLockService(
            conn=conn,
            lock_repo=RuntimeLockRepository(conn),
            owner_id="existing-owner",
        )
        lock_service.acquire(lock_name=lock_name, lease_seconds=900)
    finally:
        conn.close()


def test_main_write_success_runs_all_steps_and_releases_runtime_lock(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_after_close_success.json"
    )
    _set_cli_args(monkeypatch, output_path=output_path, extra_args=["--write"])

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="after_close_temp_success",
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "refresh_intraday_bars_15m.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "refresh_outcome": "COMPLETED",
                },
            )
            return 0
        if script_name == "scan_buy_timing1_convergence.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "scan_outcome": "COMPLETED",
                },
            )
            return 0
        if script_name == "scan_sell_macd_exit_signals.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "scan_outcome": "COMPLETED",
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 0
    assert [Path(command[1]).name for command in commands] == [
        "refresh_intraday_bars_15m.py",
        "scan_buy_timing1_convergence.py",
        "scan_sell_macd_exit_signals.py",
    ]
    assert all("--write" in command for command in commands)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["trade_date"] == TRADE_DATE
    assert payload["session_outcome"] == "COMPLETED"
    assert payload["session_reason"] is None
    assert payload["lock_name"] == f"after_close_session:{TRADE_DATE}"
    assert payload["lock_acquired"] is True
    assert payload["lock_released"] is True
    assert [step["outcome"] for step in payload["steps"]] == [
        "COMPLETED",
        "COMPLETED",
        "COMPLETED",
    ]
    assert _count_runtime_locks(test_db_path) == 0


def test_main_write_skips_sell_macd_when_refresh_fails(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_after_close_refresh_failed.json"
    )
    _set_cli_args(monkeypatch, output_path=output_path, extra_args=["--write"])

    commands: list[list[str]] = []

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    _install_fixed_tempdir(
        monkeypatch,
        test_db_path=test_db_path,
        suffix="after_close_temp_refresh_failed",
    )

    def fake_run_child(command: list[str]) -> int:
        commands.append(command)
        script_name = Path(command[1]).name
        if script_name == "refresh_intraday_bars_15m.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "error_message": "refresh failed in mock child",
                },
            )
            return 5
        if script_name == "scan_buy_timing1_convergence.py":
            _write_child_output(
                command,
                {
                    "trade_date": TRADE_DATE,
                    "scan_outcome": "COMPLETED",
                },
            )
            return 0
        raise AssertionError(f"Unexpected child script: {command}")

    monkeypatch.setattr(target, "_run_child", fake_run_child)

    exit_code = target.main()

    assert exit_code == 5
    assert [Path(command[1]).name for command in commands] == [
        "refresh_intraday_bars_15m.py",
        "scan_buy_timing1_convergence.py",
    ]

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["session_outcome"] == "FAILED"
    assert payload["session_reason"] == (
        "Refresh Intraday Bars 15m: refresh failed in mock child"
    )
    assert payload["lock_acquired"] is True
    assert payload["lock_released"] is True
    assert [step["outcome"] for step in payload["steps"]] == [
        "FAILED",
        "COMPLETED",
        "SKIPPED",
    ]
    assert payload["steps"][2]["reason"] == (
        "Skipped because 15-minute bar refresh failed in write mode."
    )
    assert _count_runtime_locks(test_db_path) == 0


def test_main_returns_lock_busy_and_writes_payload(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(
        f"{test_db_path.stem}_after_close_lock_busy.json"
    )
    _set_cli_args(monkeypatch, output_path=output_path, extra_args=["--write"])

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)

    _seed_live_lock(
        test_db_path,
        lock_name=f"after_close_session:{TRADE_DATE}",
    )

    exit_code = target.main()

    assert exit_code == 4

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["session_outcome"] == "LOCK_BUSY"
    assert payload["lock_acquired"] is False
    assert payload["steps"] == []
    assert "Runtime lock is already held by another process" in payload["session_reason"]
    assert _count_runtime_locks(test_db_path) == 1
