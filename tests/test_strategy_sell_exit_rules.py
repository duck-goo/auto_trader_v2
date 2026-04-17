"""Tests for sell stop-loss / take-profit evaluator."""

from __future__ import annotations

from strategy import SellExitEvaluator, SellExitRule, SellExitSettings


def test_evaluate_matches_stop_loss_first():
    evaluator = SellExitEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        avg_price=100_000,
        current_price=97_000,
        settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
    )

    assert match is not None
    assert match.rule == SellExitRule.STOP_LOSS
    assert match.trigger_price == 97_000


def test_evaluate_uses_ceiling_for_take_profit_trigger():
    evaluator = SellExitEvaluator()

    no_match = evaluator.evaluate(
        symbol="035420",
        avg_price=10_001,
        current_price=10_501,
        settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
    )
    match = evaluator.evaluate(
        symbol="035420",
        avg_price=10_001,
        current_price=10_502,
        settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
    )

    assert no_match is None
    assert match is not None
    assert match.rule == SellExitRule.TAKE_PROFIT
    assert match.trigger_price == 10_502
