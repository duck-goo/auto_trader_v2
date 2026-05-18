"""Tests for KisDailyUniverseSource."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from market import KisDailyUniverseSource, UniverseMasterItem, UniverseSourceItem


KST = pytz.timezone("Asia/Seoul")


class FakeBroker(BrokerInterface):
    def __init__(self, candle_map: dict[str, pd.DataFrame]) -> None:
        self._candle_map = candle_map

    def get_access_token(self) -> str:
        raise NotImplementedError

    def get_current_price(self, code: str) -> PriceSnapshot:
        raise NotImplementedError

    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        return self._candle_map[code]

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> pd.DataFrame:
        raise NotImplementedError

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def place_order(
        self,
        code: str,
        side: str,
        quantity: int,
        price: int = 0,
    ) -> OrderInfo:
        raise NotImplementedError

    def cancel_order(
        self,
        order_no: str,
        code: str,
        quantity: int,
    ) -> OrderInfo:
        raise NotImplementedError

    def get_order_status(
        self,
        order_no: str | None = None,
        *,
        filled_only: bool = False,
    ) -> list[OrderInfo]:
        raise NotImplementedError


def _daily_df(
    *,
    start_day: int = 1,
    count: int = 20,
    include_trade_date_row: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, int | datetime]] = []
    for offset in range(count):
        day = start_day + offset
        rows.append(
            {
                "datetime": KST.localize(datetime(2026, 3, day, 0, 0, 0)),
                "open": 1000 + day,
                "high": 1100 + day,
                "low": 900 + day,
                "close": 2000 + day,
                "volume": 10000 + day,
                "trade_value": 1_000_000 * day,
            }
        )

    if include_trade_date_row:
        rows.append(
            {
                "datetime": KST.localize(datetime(2026, 4, 14, 0, 0, 0)),
                "open": 9999,
                "high": 9999,
                "low": 9999,
                "close": 9999,
                "volume": 9999,
                "trade_value": 999_999_999,
            }
        )

    return pd.DataFrame(rows)


def test_kis_daily_universe_source_builds_items_from_completed_candles():
    broker = FakeBroker({"005930": _daily_df(include_trade_date_row=True, count=30)})
    source = KisDailyUniverseSource(
        broker=broker,
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
                is_attention_issue=True,
            )
        ],
        trade_date="2026-04-14",
        daily_count=40,
    )

    items = source.load()

    assert len(items) == 1
    assert isinstance(items[0], UniverseSourceItem)
    assert items[0].symbol == "005930"
    assert items[0].close_price == 2030
    assert items[0].prev_day_trade_value == 30_000_000
    assert items[0].avg_trade_value_20 == 20_500_000
    assert items[0].is_attention_issue is True


def test_kis_daily_universe_source_rejects_not_enough_completed_candles():
    broker = FakeBroker({"005930": _daily_df(count=19)})
    source = KisDailyUniverseSource(
        broker=broker,
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
            )
        ],
        trade_date="2026-04-14",
        daily_count=40,
    )

    with pytest.raises(ValueError, match="Not enough completed daily candles"):
        source.load()


def test_kis_daily_universe_source_rejects_missing_trade_value_column():
    df = _daily_df()
    df = df.drop(columns=["trade_value"])
    broker = FakeBroker({"005930": df})
    source = KisDailyUniverseSource(
        broker=broker,
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
            )
        ],
        trade_date="2026-04-14",
        daily_count=40,
    )

    with pytest.raises(ValueError, match="missing required columns"):
        source.load()


def test_kis_daily_universe_source_can_skip_symbol_errors():
    df = _daily_df()
    df = df.drop(columns=["trade_value"])
    broker = FakeBroker(
        {
            "005930": _daily_df(count=30),
            "000020": df,
        }
    )
    source = KisDailyUniverseSource(
        broker=broker,
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
            ),
            UniverseMasterItem(
                symbol="000020",
                name="Bad Data",
                market="KOSPI",
            ),
        ],
        trade_date="2026-04-14",
        daily_count=40,
        skip_symbol_errors=True,
    )

    items = source.load()

    assert [item.symbol for item in items] == ["005930"]
    assert len(source.skipped_items) == 1
    assert source.skipped_items[0].symbol == "000020"
    assert source.skipped_items[0].error_type == "ValueError"
    assert "missing required columns" in source.skipped_items[0].error_message


def test_kis_daily_universe_source_rejects_bad_trade_date():
    broker = FakeBroker({"005930": _daily_df()})

    with pytest.raises(ValueError, match="trade_date must be YYYY-MM-DD"):
        KisDailyUniverseSource(
            broker=broker,
            master_items=[
                UniverseMasterItem(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                )
            ],
            trade_date="20260414",
            daily_count=40,
        )
