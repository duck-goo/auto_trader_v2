"""Tests for Timing2 lot-level sell exit evaluator."""

from __future__ import annotations

import pytest

from strategy import (
    Timing2LotExitEvaluator,
    Timing2LotExitRule,
    Timing2LotExitSettings,
)


def test_stop_loss_includes_sell_fee_and_tax():
    evaluator = Timing2LotExitEvaluator()

    no_match = evaluator.evaluate(
        symbol="005930",
        lot_id=1,
        remaining_qty=5,
        total_buy_qty=5,
        avg_buy_price=100_000,
        current_price=98_712,
        partial_take_profit_done=False,
    )
    match = evaluator.evaluate(
        symbol="005930",
        lot_id=1,
        remaining_qty=5,
        total_buy_qty=5,
        avg_buy_price=100_000,
        current_price=98_711,
        partial_take_profit_done=False,
    )

    assert no_match is None
    assert match is not None
    assert match.rule == Timing2LotExitRule.STOP_LOSS
    assert match.sell_qty == 5
    assert match.trigger_price == 98_711
    assert match.net_return_rate <= -0.015


def test_three_minute_ma_break_sells_full_lot_before_take_profit():
    evaluator = Timing2LotExitEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        lot_id=7,
        remaining_qty=4,
        total_buy_qty=4,
        avg_buy_price=10_000,
        current_price=10_200,
        partial_take_profit_done=False,
        latest_3m_close=10_050,
        ma5_3m=10_100,
    )

    assert decision is not None
    assert decision.rule == Timing2LotExitRule.THREE_MINUTE_MA_BREAK
    assert decision.sell_qty == 4


def test_three_minute_ma_break_has_priority_over_partial_take_profit():
    evaluator = Timing2LotExitEvaluator()

    decision = evaluator.evaluate(
        symbol="005930",
        lot_id=7,
        remaining_qty=5,
        total_buy_qty=5,
        avg_buy_price=10_000,
        current_price=10_600,
        partial_take_profit_done=False,
        latest_3m_close=10_050,
        ma5_3m=10_100,
    )

    assert decision is not None
    assert decision.rule == Timing2LotExitRule.THREE_MINUTE_MA_BREAK
    assert decision.sell_qty == 5


def test_partial_take_profit_sells_rounded_up_half():
    evaluator = Timing2LotExitEvaluator()

    decision = evaluator.evaluate(
        symbol="035420",
        lot_id=2,
        remaining_qty=5,
        total_buy_qty=5,
        avg_buy_price=10_000,
        current_price=10_500,
        partial_take_profit_done=False,
    )

    assert decision is not None
    assert decision.rule == Timing2LotExitRule.TAKE_PROFIT_PARTIAL
    assert decision.sell_qty == 3
    assert decision.trigger_price == 10_500


def test_partial_take_profit_is_skipped_after_it_is_done():
    evaluator = Timing2LotExitEvaluator()

    decision = evaluator.evaluate(
        symbol="035420",
        lot_id=2,
        remaining_qty=2,
        total_buy_qty=5,
        avg_buy_price=10_000,
        current_price=10_500,
        partial_take_profit_done=True,
    )

    assert decision is None


def test_custom_settings_are_supported():
    evaluator = Timing2LotExitEvaluator()

    decision = evaluator.evaluate(
        symbol="035420",
        lot_id=2,
        remaining_qty=10,
        total_buy_qty=10,
        avg_buy_price=10_000,
        current_price=10_300,
        partial_take_profit_done=False,
        settings=Timing2LotExitSettings(
            stop_loss_ratio=0.02,
            take_profit_ratio=0.03,
            partial_take_profit_ratio=0.4,
            sell_cost_rate=0.0,
        ),
    )

    assert decision is not None
    assert decision.rule == Timing2LotExitRule.TAKE_PROFIT_PARTIAL
    assert decision.sell_qty == 4
    assert decision.take_profit_ratio == 0.03
    assert decision.sell_cost_rate == 0.0


def test_ma_inputs_must_be_provided_together():
    evaluator = Timing2LotExitEvaluator()

    with pytest.raises(
        ValueError,
        match="latest_3m_close and ma5_3m must be provided together",
    ):
        evaluator.evaluate(
            symbol="005930",
            lot_id=1,
            remaining_qty=5,
            total_buy_qty=5,
            avg_buy_price=10_000,
            current_price=10_100,
            partial_take_profit_done=False,
            latest_3m_close=10_000,
            ma5_3m=None,
        )


def test_remaining_qty_cannot_exceed_total_buy_qty():
    evaluator = Timing2LotExitEvaluator()

    with pytest.raises(ValueError, match="remaining_qty cannot exceed total_buy_qty"):
        evaluator.evaluate(
            symbol="005930",
            lot_id=1,
            remaining_qty=6,
            total_buy_qty=5,
            avg_buy_price=10_000,
            current_price=10_100,
            partial_take_profit_done=False,
        )
