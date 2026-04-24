"""Tests for run_intraday_trading_cycle.py helpers."""

from __future__ import annotations

from types import SimpleNamespace

from services import BuySignalExecutionOutcome

import scripts.run_intraday_trading_cycle as target


def _candidate(*, outcome, reason_code):
    return SimpleNamespace(
        outcome=outcome,
        reason_code=reason_code,
    )


def _cycle_result_with_buy_candidates(*candidates):
    return SimpleNamespace(
        buy_execution=SimpleNamespace(
            result=SimpleNamespace(
                candidates=tuple(candidates),
            )
        )
    )


def test_resolve_buy_execution_terminal_stop_reason_returns_daily_loss_code():
    cycle_result = _cycle_result_with_buy_candidates(
        _candidate(
            outcome=BuySignalExecutionOutcome.BLOCKED,
            reason_code="MAX_DAILY_LOSS_REACHED",
        )
    )

    assert (
        target._resolve_buy_execution_terminal_stop_reason(cycle_result)
        == "MAX_DAILY_LOSS_REACHED"
    )


def test_resolve_buy_execution_terminal_stop_reason_ignores_non_terminal_blocks():
    cycle_result = _cycle_result_with_buy_candidates(
        _candidate(
            outcome=BuySignalExecutionOutcome.BLOCKED,
            reason_code="MAX_HOLDINGS_REACHED",
        ),
        _candidate(
            outcome=BuySignalExecutionOutcome.PREVIEW_READY,
            reason_code=None,
        ),
    )

    assert target._resolve_buy_execution_terminal_stop_reason(cycle_result) is None


def test_resolve_buy_execution_terminal_stop_reason_handles_missing_candidates():
    cycle_result = SimpleNamespace(
        buy_execution=SimpleNamespace(
            result=SimpleNamespace(candidates=None)
        )
    )

    assert target._resolve_buy_execution_terminal_stop_reason(cycle_result) is None
