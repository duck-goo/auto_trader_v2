"""Tests for timing2 intraday trigger evaluator."""

from __future__ import annotations

from datetime import datetime

import pytz

from strategy import (
    Timing2IntradayStage,
    Timing2IntradayTransition,
    Timing2IntradayTriggerEvaluator,
    Timing2IntradayTriggerSettings,
)


KST = pytz.timezone("Asia/Seoul")


def _kst_datetime(hour: int, minute: int) -> datetime:
    return KST.localize(datetime(2026, 4, 15, hour, minute, 0))


def test_evaluator_advances_breakout_pullback_trigger_sequence():
    evaluator = Timing2IntradayTriggerEvaluator()
    settings = Timing2IntradayTriggerSettings(tolerance_rate=0.003)

    breakout = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        observed_at=_kst_datetime(9, 5),
        base_open_price=1000,
        current_price=1004,
        stage_before=Timing2IntradayStage.WAIT_BREAKOUT,
        settings=settings,
    )
    assert breakout.stage_after == Timing2IntradayStage.WAIT_PULLBACK
    assert breakout.transition == Timing2IntradayTransition.BREAKOUT_CONFIRMED
    assert breakout.breakout_trigger_price == 1003
    assert breakout.pullback_trigger_price == 997

    pullback = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        observed_at=_kst_datetime(9, 20),
        base_open_price=1000,
        current_price=997,
        stage_before=Timing2IntradayStage.WAIT_PULLBACK,
        settings=settings,
    )
    assert pullback.stage_after == Timing2IntradayStage.WAIT_REBOUND
    assert pullback.transition == Timing2IntradayTransition.PULLBACK_CONFIRMED

    trigger = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        observed_at=_kst_datetime(9, 35),
        base_open_price=1000,
        current_price=1000,
        stage_before=Timing2IntradayStage.WAIT_REBOUND,
        settings=settings,
    )
    assert trigger.stage_after == Timing2IntradayStage.TRIGGERED
    assert trigger.transition == Timing2IntradayTransition.REBOUND_TRIGGERED


def test_evaluator_does_not_transition_before_start_time():
    evaluator = Timing2IntradayTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        observed_at=_kst_datetime(8, 50),
        base_open_price=1000,
        current_price=1004,
        stage_before=Timing2IntradayStage.WAIT_BREAKOUT,
        settings=Timing2IntradayTriggerSettings(),
    )

    assert decision.stage_after == Timing2IntradayStage.WAIT_BREAKOUT
    assert decision.transition == Timing2IntradayTransition.NONE


def test_evaluator_expires_after_cutoff_time():
    evaluator = Timing2IntradayTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-15",
        observed_at=_kst_datetime(12, 0),
        base_open_price=1000,
        current_price=1002,
        stage_before=Timing2IntradayStage.WAIT_PULLBACK,
        settings=Timing2IntradayTriggerSettings(),
    )

    assert decision.stage_after == Timing2IntradayStage.EXPIRED
    assert decision.transition == Timing2IntradayTransition.EXPIRED
