"""Tests for startup_check.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import scripts.startup_check as target


def _set_cli_args(monkeypatch, args: list[str]) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["startup_check.py", "--trade-date", "2026-04-20", *args],
    )


class _DummyConn:
    def close(self) -> None:
        return None


class _DummyBroker:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_main_writes_reconcile_reason_fields_into_output(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(f"{test_db_path.stem}_startup_check.json")
    _set_cli_args(
        monkeypatch,
        [
            "--db-path",
            str(test_db_path),
            "--output",
            str(output_path),
        ],
    )

    settings = SimpleNamespace(
        mode="mock",
        db_path=str(test_db_path),
        db_busy_timeout_ms=5000,
    )
    reconcile_result = SimpleNamespace(
        changed_rows=1,
        reason_code="OPEN_ENTRY_LOT_POSITION_MISMATCH",
        reason_message=(
            "Reconciliation would change positions for symbols that still have "
            "open entry lots. Review executions first: 005930"
        ),
        unresolved_orders=[],
    )
    startup_result = SimpleNamespace(
        outcome=target.StartupOutcome.BLOCKED,
        checked_at="2026-04-20T08:59:00+09:00",
        trade_date="2026-04-20",
        reason=(
            "Reconciliation would change positions for symbols that still have "
            "open entry lots. Review executions first: 005930"
        ),
        universe_snapshot=SimpleNamespace(
            exists=True,
            candidate_count=12,
            refreshed_at="2026-04-20T08:30:00+09:00",
        ),
        reconcile_result=reconcile_result,
        live_positions=[
            SimpleNamespace(
                symbol="005930",
                qty=2,
                avg_price=71000,
                updated_at="2026-04-20T08:58:00+09:00",
            )
        ],
    )

    class _FakeStartupService:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run_startup_check(self, *, trade_date: str, allow_unresolved_orders: bool):
            assert trade_date == "2026-04-20"
            assert allow_unresolved_orders is False
            return startup_result

    monkeypatch.setattr(target, "load_settings", lambda: settings)
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "run_migrations", lambda db_path: None)
    monkeypatch.setattr(target, "get_connection", lambda *args, **kwargs: _DummyConn())
    monkeypatch.setattr(target, "OrderRepository", lambda conn: object())
    monkeypatch.setattr(target, "PositionRepository", lambda conn: object())
    monkeypatch.setattr(target, "UniverseCandidateRepository", lambda conn: object())
    monkeypatch.setattr(target, "KisBroker", lambda settings: _DummyBroker())
    monkeypatch.setattr(target, "StartupService", _FakeStartupService)

    exit_code = target.main()

    assert exit_code == 4
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "BLOCKED"
    assert payload["reconcile_reason_code"] == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    assert payload["reconcile_reason_message"].endswith("005930")
    assert payload["reconcile_changed_rows"] == 1
    assert payload["unresolved_orders"] == []
    assert payload["live_positions"][0]["symbol"] == "005930"
