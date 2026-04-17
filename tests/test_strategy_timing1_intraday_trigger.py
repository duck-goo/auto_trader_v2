"""Tests for timing1 intraday trigger evaluator."""

from __future__ import annotations

from datetime import datetime

import pytz

from strategy import (
    Timing1IntradayStage,
    Timing1IntradayTransition,
    Timing1IntradayTriggerEvaluator,
    Timing1IntradayTriggerSettings,
)


KST = pytz.timezone("Asia/Seoul")


def _kst_datetime(hour: int, minute: int) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, 0))


def test_evaluator_triggers_when_price_breaks_target_during_window():
    evaluator = Timing1IntradayTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        observed_at=_kst_datetime(9, 5),
        target_price=104,
        current_price=104,
        stage_before=Timing1IntradayStage.WAIT_BREAKOUT,
        settings=Timing1IntradayTriggerSettings(),
    )

    assert decision.stage_after == Timing1IntradayStage.TRIGGERED
    assert decision.transition == Timing1IntradayTransition.BREAKOUT_TRIGGERED


def test_evaluator_does_not_trigger_before_start_time():
    evaluator = Timing1IntradayTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        observed_at=_kst_datetime(8, 50),
        target_price=104,
        current_price=110,
        stage_before=Timing1IntradayStage.WAIT_BREAKOUT,
        settings=Timing1IntradayTriggerSettings(),
    )

    assert decision.stage_after == Timing1IntradayStage.WAIT_BREAKOUT
    assert decision.transition == Timing1IntradayTransition.NONE


def test_evaluator_expires_after_cutoff():
    evaluator = Timing1IntradayTriggerEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        observed_at=_kst_datetime(12, 0),
        target_price=104,
        current_price=103,
        stage_before=Timing1IntradayStage.WAIT_BREAKOUT,
        settings=Timing1IntradayTriggerSettings(),
    )

    assert decision.stage_after == Timing1IntradayStage.EXPIRED
    assert decision.transition == Timing1IntradayTransition.EXPIRED
