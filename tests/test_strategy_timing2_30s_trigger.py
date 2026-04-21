"""Tests for timing2 30-second candle trigger evaluator."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from strategy import (
    Timing2ThirtySecondTransition,
    Timing2ThirtySecondTriggerEvaluator,
    Timing2ThirtySecondTriggerSettings,
    Timing2ThirtySecondTriggerState,
    Timing2ThirtySecondTriggerType,
)


KST = pytz.timezone("Asia/Seoul")


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 15, hour, minute, second))


def test_morning_dip_then_open_reclaim_triggers_once():
    evaluator = Timing2ThirtySecondTriggerEvaluator()
    settings = Timing2ThirtySecondTriggerSettings()
    state = Timing2ThirtySecondTriggerState()

    dip = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(9, 5, 30),
        session_open_price=1000,
        bar_close_price=990,
        morning_high_close=None,
        state_before=state,
        settings=settings,
    )

    assert dip.buy_triggered is False
    assert dip.transition == Timing2ThirtySecondTransition.MORNING_DIP_CONFIRMED
    assert dip.state_after == Timing2ThirtySecondTriggerState(
        morning_dipped_below_open=True,
        morning_triggered=False,
        range_triggered=False,
    )

    reclaim = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(9, 6, 0),
        session_open_price=1000,
        bar_close_price=1001,
        morning_high_close=None,
        state_before=dip.state_after,
        settings=settings,
    )

    assert reclaim.buy_triggered is True
    assert (
        reclaim.transition
        == Timing2ThirtySecondTransition.MORNING_OPEN_RECLAIM_TRIGGERED
    )
    assert reclaim.trigger_type == Timing2ThirtySecondTriggerType.MORNING_OPEN_RECLAIM

    repeated = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(9, 7, 0),
        session_open_price=1000,
        bar_close_price=1002,
        morning_high_close=None,
        state_before=reclaim.state_after,
        settings=settings,
    )

    assert repeated.buy_triggered is False
    assert repeated.transition == Timing2ThirtySecondTransition.NONE
    assert repeated.state_after == reclaim.state_after


def test_before_10_does_not_use_range_breakout_condition():
    evaluator = Timing2ThirtySecondTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(9, 59, 30),
        session_open_price=1000,
        bar_close_price=1200,
        morning_high_close=1100,
        state_before=Timing2ThirtySecondTriggerState(),
        settings=Timing2ThirtySecondTriggerSettings(),
    )

    assert decision.buy_triggered is False
    assert decision.transition == Timing2ThirtySecondTransition.NONE


def test_after_10_breaks_morning_high_close_even_after_morning_trigger():
    evaluator = Timing2ThirtySecondTriggerEvaluator()
    state = Timing2ThirtySecondTriggerState(
        morning_dipped_below_open=True,
        morning_triggered=True,
        range_triggered=False,
    )

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(10, 5, 0),
        session_open_price=1000,
        bar_close_price=1101,
        morning_high_close=1100,
        state_before=state,
        settings=Timing2ThirtySecondTriggerSettings(),
    )

    assert decision.buy_triggered is True
    assert decision.transition == Timing2ThirtySecondTransition.RANGE_HIGH_BREAKOUT_TRIGGERED
    assert decision.trigger_type == Timing2ThirtySecondTriggerType.RANGE_HIGH_BREAKOUT
    assert decision.state_after == Timing2ThirtySecondTriggerState(
        morning_dipped_below_open=True,
        morning_triggered=True,
        range_triggered=True,
    )


def test_after_10_allows_range_breakout_without_morning_trigger():
    evaluator = Timing2ThirtySecondTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(10, 1, 0),
        session_open_price=1000,
        bar_close_price=1101,
        morning_high_close=1100,
        state_before=Timing2ThirtySecondTriggerState(),
        settings=Timing2ThirtySecondTriggerSettings(),
    )

    assert decision.buy_triggered is True
    assert decision.trigger_type == Timing2ThirtySecondTriggerType.RANGE_HIGH_BREAKOUT


def test_after_10_requires_morning_high_close_reference():
    evaluator = Timing2ThirtySecondTriggerEvaluator()

    with pytest.raises(ValueError, match="morning_high_close is required"):
        evaluator.evaluate(
            symbol="005930",
            trade_date="2026-04-15",
            bar_end_at=_kst_datetime(10, 1, 0),
            session_open_price=1000,
            bar_close_price=1101,
            morning_high_close=None,
            state_before=Timing2ThirtySecondTriggerState(),
            settings=Timing2ThirtySecondTriggerSettings(),
        )


def test_equal_prices_do_not_count_as_upward_breakouts():
    evaluator = Timing2ThirtySecondTriggerEvaluator()

    reclaim = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(9, 6, 0),
        session_open_price=1000,
        bar_close_price=1000,
        morning_high_close=None,
        state_before=Timing2ThirtySecondTriggerState(
            morning_dipped_below_open=True,
        ),
        settings=Timing2ThirtySecondTriggerSettings(),
    )
    assert reclaim.buy_triggered is False

    range_breakout = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        bar_end_at=_kst_datetime(10, 1, 0),
        session_open_price=1000,
        bar_close_price=1100,
        morning_high_close=1100,
        state_before=Timing2ThirtySecondTriggerState(),
        settings=Timing2ThirtySecondTriggerSettings(),
    )
    assert range_breakout.buy_triggered is False


def test_bar_date_must_match_trade_date_in_kst():
    evaluator = Timing2ThirtySecondTriggerEvaluator()

    with pytest.raises(ValueError, match="date must match trade_date"):
        evaluator.evaluate(
            symbol="005930",
            trade_date="2026-04-14",
            bar_end_at=_kst_datetime(9, 1, 0),
            session_open_price=1000,
            bar_close_price=990,
            morning_high_close=None,
            state_before=Timing2ThirtySecondTriggerState(),
            settings=Timing2ThirtySecondTriggerSettings(),
        )
