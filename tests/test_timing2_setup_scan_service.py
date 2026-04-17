"""Tests for Timing2SetupScanService."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import Timing2SetupScanService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    SignalRepository,
    UniverseCandidate,
    UniverseCandidateRepository,
)
from strategy import Timing2SetupSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-03-03"


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
    fixed = KST.localize(datetime(2026, 3, 3, 8, 55, 0))
    return lambda: fixed


def _daily_df(*, match: bool) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2026, 1, 1)

    prior_closes = [100_000 + (index * 500) for index in range(60)]
    for index, close_price in enumerate(prior_closes):
        day = start + timedelta(days=index)
        rows.append(
            {
                "datetime": KST.localize(datetime(day.year, day.month, day.day)),
                "open": close_price - 100,
                "high": close_price + (200 if match or index != 10 else 40_000),
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
            "high": 168_300 if match else 167_800,
            "low": 139_000,
            "close": 168_300 if match else 167_800,
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
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=168_300,
                    prev_day_trade_value=950_000_000_000,
                )
            ],
            refreshed_at="2026-03-03T08:30:00+09:00",
        )

    service = Timing2SetupScanService(
        broker=FakeBroker({"005930": _daily_df(match=True)}),
        conn=conn,
        universe_repo=universe_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
    )

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing2SetupSettings(),
        write_signals=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing2SetupSettings(),
        write_signals=True,
    )

    assert first.matched_count == 1
    assert first.recorded_count == 1
    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1
    stored = signal_repo.list_by_symbol("005930")
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
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=167_800,
                    prev_day_trade_value=950_000_000_000,
                )
            ],
            refreshed_at="2026-03-03T08:30:00+09:00",
        )

    service = Timing2SetupScanService(
        broker=FakeBroker({"005930": _daily_df(match=False)}),
        conn=conn,
        universe_repo=universe_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
    )

    result = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing2SetupSettings(),
        write_signals=True,
    )

    assert result.universe_count == 1
    assert result.matched_count == 0
    assert result.recorded_count == 0
    assert signal_repo.list_by_symbol("005930") == []
