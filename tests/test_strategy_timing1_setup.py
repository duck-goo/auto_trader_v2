"""Tests for timing1 daily setup evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytz

from strategy import Timing1SetupEvaluator, Timing1SetupSettings


KST = pytz.timezone("Asia/Seoul")


def _daily_df() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2025, 12, 30)
    close_prices = [100] * 55 + [100, 100, 100, 100, 100, 120, 124, 127, 130, 136]
    volumes = [100] * 55 + [100, 100, 100, 100, 100, 250, 100, 100, 100, 100]

    for index, close_price in enumerate(close_prices):
        day = start + timedelta(days=index)
        open_price = close_price - 1
        if index == 60:
            open_price = 104
        rows.append(
            {
                "datetime": KST.localize(datetime(day.year, day.month, day.day)),
                "open": open_price,
                "high": close_price + 2,
                "low": close_price - 2,
                "close": close_price,
                "volume": volumes[index],
            }
        )

    rows.append(
        {
            "datetime": KST.localize(datetime(2026, 3, 10)),
            "open": 999,
            "high": 999,
            "low": 999,
            "close": 999,
            "volume": 999,
        }
    )
    return pd.DataFrame(rows)


def test_evaluator_returns_match_for_valid_setup():
    evaluator = Timing1SetupEvaluator()

    match = evaluator.evaluate(
        symbol="035420",
        trade_date="2026-03-10",
        daily_candles=_daily_df(),
        settings=Timing1SetupSettings(),
    )

    assert match is not None
    assert match.symbol == "035420"
    assert match.latest_daily_date == "2026-03-04"
    assert match.strong_day.date == "2026-02-28"
    assert match.strong_day.gain_rate >= 0.15
    assert match.ma_short_now > match.ma_short_past
    assert match.ma_long_now > match.ma_long_past


def test_evaluator_returns_none_when_strong_day_is_missing():
    evaluator = Timing1SetupEvaluator()
    df = _daily_df()
    df.loc[df["datetime"].dt.strftime("%Y-%m-%d") == "2026-02-28", "volume"] = 150

    match = evaluator.evaluate(
        symbol="035420",
        trade_date="2026-03-10",
        daily_candles=df,
        settings=Timing1SetupSettings(),
    )

    assert match is None


def test_evaluator_rejects_not_enough_completed_rows():
    evaluator = Timing1SetupEvaluator()
    df = _daily_df().head(30)

    with pytest.raises(ValueError, match="Not enough completed daily candles"):
        evaluator.evaluate(
            symbol="035420",
            trade_date="2026-03-10",
            daily_candles=df,
            settings=Timing1SetupSettings(),
        )
