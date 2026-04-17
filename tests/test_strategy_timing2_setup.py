"""Tests for timing2 daily setup evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz

from strategy import Timing2SetupEvaluator, Timing2SetupSettings


KST = pytz.timezone("Asia/Seoul")


def _daily_df(
    *,
    latest_close: int = 168_300,
    prior_high_override: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2026, 1, 1)

    prior_closes = [100_000 + (index * 500) for index in range(60)]
    for index, close_price in enumerate(prior_closes):
        day = start + timedelta(days=index)
        high_price = close_price + 200
        if prior_high_override is not None and index == 20:
            high_price = prior_high_override
        rows.append(
            {
                "datetime": KST.localize(datetime(day.year, day.month, day.day)),
                "open": close_price - 100,
                "high": high_price,
                "low": close_price - 200,
                "close": close_price,
                "volume": 100_000,
            }
        )

    latest_day = start + timedelta(days=60)
    rows.append(
        {
            "datetime": KST.localize(
                datetime(latest_day.year, latest_day.month, latest_day.day)
            ),
            "open": 140_000,
            "high": latest_close,
            "low": 139_000,
            "close": latest_close,
            "volume": 5_000_000,
        }
    )

    rows.append(
        {
            "datetime": KST.localize(datetime(2026, 3, 3)),
            "open": 999_999,
            "high": 999_999,
            "low": 999_999,
            "close": 999_999,
            "volume": 999_999,
        }
    )
    return pd.DataFrame(rows)


def test_evaluator_returns_match_for_valid_timing2_setup():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(),
        settings=Timing2SetupSettings(),
    )

    assert match is not None
    assert match.symbol == "005930"
    assert match.market == "KOSPI"
    assert match.latest_daily_date == "2026-03-02"
    assert match.latest_close == 168_300
    assert match.previous_close == 129_500
    assert match.official_upper_limit_price == 168_300
    assert match.prior_lookback_high < match.latest_close


def test_evaluator_returns_none_when_latest_close_is_not_official_upper_limit():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(latest_close=167_800),
        settings=Timing2SetupSettings(),
    )

    assert match is None


def test_evaluator_returns_none_when_latest_close_is_not_new_high():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(prior_high_override=170_000),
        settings=Timing2SetupSettings(),
    )

    assert match is None
