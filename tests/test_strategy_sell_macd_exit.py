"""Tests for sell MACD decrease evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz

from strategy import SellMacdExitEvaluator, SellMacdExitSettings


KST = pytz.timezone("Asia/Seoul")


def test_evaluate_matches_two_consecutive_histogram_declines():
    start = KST.localize(datetime(2026, 4, 16, 9, 0, 0))
    closes = list(range(100, 150)) + [149, 148, 147, 146, 145]
    rows = []
    for index, close_price in enumerate(closes):
        bar_start = start + timedelta(minutes=15 * index)
        rows.append(
            {
                "bar_start_at": bar_start.isoformat(),
                "bar_end_at": (bar_start + timedelta(minutes=15)).isoformat(),
                "close": close_price,
            }
        )

    evaluator = SellMacdExitEvaluator()
    match = evaluator.evaluate(
        symbol="005930",
        intraday_bars=pd.DataFrame(rows),
        settings=SellMacdExitSettings(
            fast_window=12,
            slow_window=26,
            signal_window=9,
            consecutive_decline_bars=2,
        ),
    )

    assert match is not None
    assert match.symbol == "005930"
    assert match.close_price == 145
    assert match.hist_t_minus_2 > match.hist_t_minus_1 > match.hist_t
