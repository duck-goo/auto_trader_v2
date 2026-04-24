"""Tests for execute_sell_signals.py stop semantics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from services import SellSignalExecutionOutcome

import scripts.execute_sell_signals as target


TRADE_DATE = "2026-04-16"


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
        "execute_sell_signals.py",
        "--trade-date",
        TRADE_DATE,
        "--output",
        str(output_path),
    ]
    if extra_args:
        args.extend(extra_args)
    monkeypatch.setattr(sys, "argv", args)


def _candidate(*, outcome, reason_code):
    return SimpleNamespace(
        signal_id=1,
        symbol="005930",
        name="Samsung",
        source_strategy_name="sell_stop_loss",
        lot_id=None,
        requested_sell_qty=1,
        order_qty=1,
        sell_cost_rate=0.002140527,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_code,
        current_price=70_000,
        position_qty=1,
        avg_price=71_000,
        client_order_id=None,
        order_error_code=reason_code,
        order_error_message=reason_code,
        acted=False,
    )


def _result_with_candidate(candidate) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=TRADE_DATE,
        executed_at="2026-04-16T09:30:00+09:00",
        execute_orders=False,
        pending_signal_count=1,
        candidate_count=1,
        preview_ready_count=0,
        blocked_count=0,
        submitted_count=0,
        unknown_count=0,
        rejected_count=0,
        failed_count=0,
        acted_count=0,
        audit_record_count=0,
        acted_signal_ids=tuple(),
        candidates=(candidate,),
    )


class _FakeBroker:
    def __init__(self, settings) -> None:
        self.settings = settings

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeSellSignalExecutionService:
    result = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def execute_pending_signals(self, **kwargs):
        return self.result


def test_main_returns_5_when_sell_execution_contains_failed_candidate(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(f"{test_db_path.stem}_sell_failed.json")
    _set_cli_args(monkeypatch, output_path=output_path)

    _FakeSellSignalExecutionService.result = _result_with_candidate(
        _candidate(
            outcome=SellSignalExecutionOutcome.FAILED,
            reason_code="BROKER_SELL_FAILED",
        )
    )

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "KisBroker", _FakeBroker)
    monkeypatch.setattr(
        target,
        "SellSignalExecutionService",
        _FakeSellSignalExecutionService,
    )

    exit_code = target.main()

    assert exit_code == 5
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["stop_reason"] == "BROKER_SELL_FAILED"
    assert payload["result"]["candidates"][0]["reason_code"] == "BROKER_SELL_FAILED"


def test_main_keeps_zero_exit_for_blocked_sell_candidate(
    test_db_path,
    monkeypatch,
):
    output_path = test_db_path.with_name(f"{test_db_path.stem}_sell_blocked.json")
    _set_cli_args(monkeypatch, output_path=output_path)

    _FakeSellSignalExecutionService.result = _result_with_candidate(
        _candidate(
            outcome=SellSignalExecutionOutcome.BLOCKED,
            reason_code="UNRESOLVED_SELL_ORDER_EXISTS",
        )
    )

    monkeypatch.setattr(target, "load_settings", lambda: _make_settings(test_db_path))
    monkeypatch.setattr(target, "setup_logging", lambda settings: None)
    monkeypatch.setattr(target, "KisBroker", _FakeBroker)
    monkeypatch.setattr(
        target,
        "SellSignalExecutionService",
        _FakeSellSignalExecutionService,
    )

    exit_code = target.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["stop_reason"] is None
    assert payload["result"]["candidates"][0]["reason_code"] == (
        "UNRESOLVED_SELL_ORDER_EXISTS"
    )
