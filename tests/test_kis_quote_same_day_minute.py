"""Tests for KIS same-day minute candle backfill behavior."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytz

from broker.kis.quote import Quote


KST = pytz.timezone("Asia/Seoul")


def _row(dt: datetime, close: int) -> dict[str, object]:
    return {
        "datetime": dt,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
        "trade_value": close,
    }


def test_same_day_minute_backfill_drops_previous_day_rows(monkeypatch):
    quote = Quote(client=object())  # type: ignore[arg-type]
    today = datetime.now(KST).date()
    today_open = KST.localize(datetime.combine(today, datetime.min.time())).replace(
        hour=9,
        minute=0,
    )
    previous_day = today_open - timedelta(days=1)
    calls: list[str] = []

    def fake_fetch_minute_window(*, code, end_time, include_past_data):
        calls.append(end_time)
        return pd.DataFrame(
            [
                _row(previous_day.replace(hour=15, minute=29), 900),
                _row(today_open, 1000),
            ]
        )

    monkeypatch.setattr(quote, "_fetch_minute_window", fake_fetch_minute_window)

    df = quote.get_same_day_minute_candles("005930", end_time="090500")

    assert calls == ["090500"]
    assert len(df) == 1
    assert df.iloc[0]["datetime"].astimezone(KST).date() == today
    assert df.iloc[0]["close"] == 1000


def test_same_day_minute_backfill_stops_when_only_previous_day_rows(monkeypatch):
    quote = Quote(client=object())  # type: ignore[arg-type]
    today = datetime.now(KST).date()
    previous_day = KST.localize(
        datetime.combine(today - timedelta(days=1), datetime.min.time())
    )
    calls: list[str] = []

    def fake_fetch_minute_window(*, code, end_time, include_past_data):
        calls.append(end_time)
        return pd.DataFrame([_row(previous_day.replace(hour=15, minute=29), 900)])

    monkeypatch.setattr(quote, "_fetch_minute_window", fake_fetch_minute_window)

    df = quote.get_same_day_minute_candles("005930", end_time="090500")

    assert calls == ["090500"]
    assert df.empty
