"""Tests for Timing1SetupScanService."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import Timing1SetupScanService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    SignalRepository,
    UniverseCandidate,
    UniverseCandidateRepository,
)
from strategy import Timing1SetupSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-03-10"


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


def _fixed_now():
    fixed = KST.localize(datetime(2026, 3, 10, 9, 5, 0))
    return lambda: fixed


def _daily_df(*, strong_day: bool) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2025, 12, 30)
    close_prices = [100] * 55 + [100, 100, 100, 100, 100, 120, 124, 127, 130, 136]
    volumes = [100] * 55 + [100, 100, 100, 100, 100, 250 if strong_day else 150, 100, 100, 100, 100]

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


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_scan_records_match_and_dedupes_second_run(conn):
    universe_repo = UniverseCandidateRepository(conn)
    signal_repo = SignalRepository(conn)

    with transaction(conn):
        universe_repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="035420",
                    name="NAVER",
                    market="KOSPI",
                    close_price=136000,
                    prev_day_trade_value=410_000_000_000,
                )
            ],
            refreshed_at="2026-03-10T08:30:00+09:00",
        )

    service = Timing1SetupScanService(
        broker=FakeBroker({"035420": _daily_df(strong_day=True)}),
        conn=conn,
        universe_repo=universe_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
    )

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing1SetupSettings(),
        write_signals=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing1SetupSettings(),
        write_signals=True,
    )

    assert first.matched_count == 1
    assert first.recorded_count == 1
    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1
    stored = signal_repo.list_by_symbol("035420")
    assert len(stored) == 1
    assert stored[0].payload["trade_date"] == TRADE_DATE


def test_scan_returns_zero_when_no_symbol_matches(conn):
    universe_repo = UniverseCandidateRepository(conn)
    signal_repo = SignalRepository(conn)

    with transaction(conn):
        universe_repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="035420",
                    name="NAVER",
                    market="KOSPI",
                    close_price=136000,
                    prev_day_trade_value=410_000_000_000,
                )
            ],
            refreshed_at="2026-03-10T08:30:00+09:00",
        )

    service = Timing1SetupScanService(
        broker=FakeBroker({"035420": _daily_df(strong_day=False)}),
        conn=conn,
        universe_repo=universe_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
    )

    result = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing1SetupSettings(),
        write_signals=True,
    )

    assert result.universe_count == 1
    assert result.matched_count == 0
    assert result.recorded_count == 0
    assert signal_repo.list_by_symbol("035420") == []
