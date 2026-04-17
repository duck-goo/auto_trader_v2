"""Tests for timing1 15-minute convergence evaluator."""

from __future__ import annotations

import pandas as pd

from strategy import Timing1ConvergenceEvaluator, Timing1ConvergenceSettings


def _bars(*, closes: list[int], highs: list[int], trade_date: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for index, close_price in enumerate(closes):
        hour = 9 + ((index * 15) // 60)
        minute = (index * 15) % 60
        next_hour = 9 + (((index + 1) * 15) // 60)
        next_minute = ((index + 1) * 15) % 60
        rows.append(
            {
                "bar_start_at": (
                    f"{trade_date}T{hour:02d}:{minute:02d}:00+09:00"
                ),
                "bar_end_at": (
                    f"{trade_date}T{next_hour:02d}:{next_minute:02d}:00+09:00"
                ),
                "high": highs[index],
                "close": close_price,
            }
        )
    return pd.DataFrame(rows)


def test_evaluator_returns_none_when_history_is_insufficient():
    evaluator = Timing1ConvergenceEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        strong_day_date="2026-04-15",
        intraday_bars=_bars(
            closes=[100, 101],
            highs=[101, 102],
            trade_date="2026-04-16",
        ),
        settings=Timing1ConvergenceSettings(
            ma_short_window=2,
            ma_long_window=3,
            convergence_threshold_rate=0.02,
        ),
    )

    assert match is None


def test_evaluator_returns_match_when_close_and_mas_converge():
    evaluator = Timing1ConvergenceEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        strong_day_date="2026-04-15",
        intraday_bars=_bars(
            closes=[100, 101, 102],
            highs=[101, 103, 104],
            trade_date="2026-04-16",
        ),
        settings=Timing1ConvergenceSettings(
            ma_short_window=2,
            ma_long_window=3,
            convergence_threshold_rate=0.02,
        ),
    )

    assert match is not None
    assert match.symbol == "005930"
    assert match.trade_date == "2026-04-16"
    assert match.strong_day_date == "2026-04-15"
    assert match.convergence_trade_date == "2026-04-16"
    assert match.close_price == 102
    assert match.day_high == 104
    assert match.bar_end_at == "2026-04-16T09:45:00+09:00"


def test_evaluator_returns_none_when_spread_is_too_wide():
    evaluator = Timing1ConvergenceEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        trade_date="2026-04-16",
        strong_day_date="2026-04-15",
        intraday_bars=_bars(
            closes=[100, 105, 120],
            highs=[101, 106, 121],
            trade_date="2026-04-16",
        ),
        settings=Timing1ConvergenceSettings(
            ma_short_window=2,
            ma_long_window=3,
            convergence_threshold_rate=0.02,
        ),
    )

    assert match is None
