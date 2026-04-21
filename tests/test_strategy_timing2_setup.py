"""Tests for timing2 daily setup evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz

from strategy import Timing2SetupEvaluator, Timing2SetupSettings


KST = pytz.timezone("Asia/Seoul")


def _daily_df(
    *,
    latest_close: int = 150_000,
    latest_volume: int = 500_000,
    previous_close: int = 129_500,
    previous_volume: int = 100_000,
    prior_close_override: int | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2026, 1, 1)

    prior_closes = [100_000 + (index * 500) for index in range(59)]
    if prior_close_override is not None:
        prior_closes[20] = prior_close_override

    for index, close_price in enumerate(prior_closes):
        day = start + timedelta(days=index)
        rows.append(
            {
                "datetime": KST.localize(datetime(day.year, day.month, day.day)),
                "open": close_price - 100,
                "high": close_price + 200,
                "low": close_price - 200,
                "close": close_price,
                "volume": 100_000,
            }
        )

    previous_day = start + timedelta(days=59)
    rows.append(
        {
            "datetime": KST.localize(
                datetime(previous_day.year, previous_day.month, previous_day.day)
            ),
            "open": previous_close - 100,
            "high": previous_close + 200,
            "low": previous_close - 200,
            "close": previous_close,
            "volume": previous_volume,
        }
    )

    latest_day = start + timedelta(days=60)
    rows.append(
        {
            "datetime": KST.localize(
                datetime(latest_day.year, latest_day.month, latest_day.day)
            ),
            "open": latest_close - 1_000,
            "high": latest_close + 200,
            "low": latest_close - 2_000,
            "close": latest_close,
            "volume": latest_volume,
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
    assert match.latest_close == 150_000
    assert match.previous_close == 129_500
    assert match.latest_volume == 500_000
    assert match.previous_volume == 100_000
    assert match.close_gain_rate >= 0.15
    assert match.volume_ratio == 5.0
    assert match.lookback_highest_close == 150_000


def test_evaluator_returns_none_when_latest_close_is_not_lookback_high():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(prior_close_override=151_000),
        settings=Timing2SetupSettings(),
    )

    assert match is None


def test_evaluator_returns_none_when_latest_gain_is_too_small():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(latest_close=140_000),
        settings=Timing2SetupSettings(),
    )

    assert match is None


def test_evaluator_returns_none_when_latest_volume_ratio_is_too_small():
    evaluator = Timing2SetupEvaluator()

    match = evaluator.evaluate(
        symbol="005930",
        market="KOSPI",
        trade_date="2026-03-03",
        daily_candles=_daily_df(latest_volume=499_999),
        settings=Timing2SetupSettings(),
    )

    assert match is None
