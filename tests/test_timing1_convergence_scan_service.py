"""Tests for Timing1ConvergenceScanService."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    STRATEGY_NAME_TIMING1_CONVERGENCE,
    STRATEGY_NAME_TIMING1_SETUP,
    Timing1ConvergenceScanService,
)
from services.errors import ServiceError
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar15m,
    IntradayBar15mRepository,
    SignalRepository,
)
from strategy import Timing1ConvergenceSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


class FakeBroker(BrokerInterface):
    def __init__(self, minute_map: dict[str, pd.DataFrame]) -> None:
        self._minute_map = minute_map

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
        raise NotImplementedError

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ) -> pd.DataFrame:
        raise NotImplementedError

    def get_same_day_minute_candles(
        self,
        code: str,
        *,
        end_time: str | None = None,
    ) -> pd.DataFrame:
        return self._minute_map[code]

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


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _fixed_now(hour: int, minute: int):
    fixed = KST.localize(datetime(2026, 4, 16, hour, minute, 0))
    return lambda: fixed


def _add_bar_rows(
    rows: list[dict[str, object]],
    *,
    start_hour: int,
    start_minute: int,
    open_price: int,
    high_price: int,
    low_price: int,
    close_price: int,
) -> None:
    start_dt = KST.localize(datetime(2026, 4, 16, start_hour, start_minute, 0))
    prices = [open_price, high_price, low_price] + [open_price] * 10 + [
        close_price,
        close_price,
    ]
    for index, price in enumerate(prices):
        dt = start_dt + timedelta(minutes=index)
        rows.append(
            {
                "datetime": dt,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 10,
                "trade_value": 0,
            }
        )


def _same_day_minute_df() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    _add_bar_rows(
        rows,
        start_hour=9,
        start_minute=0,
        open_price=100,
        high_price=101,
        low_price=99,
        close_price=100,
    )
    _add_bar_rows(
        rows,
        start_hour=9,
        start_minute=15,
        open_price=101,
        high_price=102,
        low_price=100,
        close_price=101,
    )
    _add_bar_rows(
        rows,
        start_hour=9,
        start_minute=30,
        open_price=102,
        high_price=104,
        low_price=101,
        close_price=102,
    )
    return pd.DataFrame(rows)


def _seed_timing1_setup_signal(conn) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING1_SETUP,
            scanned_at="2026-04-16T08:55:00+09:00",
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "strong_day": {
                    "date": "2026-04-15",
                    "open_price": 90,
                    "close_price": 110,
                    "prev_close": 89,
                    "gain_rate": 0.235,
                    "volume": 1_000_000,
                    "avg_volume_before": 300_000,
                    "volume_ratio": 3.3,
                },
            },
        )


def _seed_previous_day_intraday_bar(conn) -> None:
    repo = IntradayBar15mRepository(conn)
    with transaction(conn):
        repo.replace_for_symbol_and_date(
            trade_date="2026-04-15",
            symbol="005930",
            bars=[
                IntradayBar15m(
                    bar_start_at="2026-04-15T15:00:00+09:00",
                    bar_end_at="2026-04-15T15:15:00+09:00",
                    open=99,
                    high=100,
                    low=98,
                    close=99,
                    volume=150,
                )
            ],
            refreshed_at="2026-04-15T15:31:00+09:00",
        )


def test_scan_records_convergence_and_stores_same_day_bars(conn):
    _seed_timing1_setup_signal(conn)
    _seed_previous_day_intraday_bar(conn)
    signal_repo = SignalRepository(conn)
    intraday_repo = IntradayBar15mRepository(conn)

    service = Timing1ConvergenceScanService(
        broker=FakeBroker({"005930": _same_day_minute_df()}),
        conn=conn,
        signal_repo=signal_repo,
        intraday_bar_repo=intraday_repo,
        now_fn=_fixed_now(15, 31),
    )

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing1ConvergenceSettings(
            ma_short_window=2,
            ma_long_window=4,
            convergence_threshold_rate=0.02,
        ),
        history_limit=10,
        write=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=Timing1ConvergenceSettings(
            ma_short_window=2,
            ma_long_window=4,
            convergence_threshold_rate=0.02,
        ),
        history_limit=10,
        write=True,
    )

    assert first.setup_signal_count == 1
    assert first.processed_count == 1
    assert first.stored_symbol_count == 1
    assert first.matched_count == 1
    assert first.recorded_count == 1
    assert first.candidates[0].history_bar_count == 4
    assert first.candidates[0].match is not None
    assert first.candidates[0].match.day_high == 104

    stored_bars = intraday_repo.list_for_symbol_and_date(
        trade_date=TRADE_DATE,
        symbol="005930",
    )
    assert [row.bar_start_at for row in stored_bars] == [
        "2026-04-16T09:00:00+09:00",
        "2026-04-16T09:15:00+09:00",
        "2026-04-16T09:30:00+09:00",
    ]

    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1
    stored_signals = signal_repo.list_by_symbol("005930")
    convergence_signals = [
        row
        for row in stored_signals
        if row.strategy_name == STRATEGY_NAME_TIMING1_CONVERGENCE
    ]
    assert len(convergence_signals) == 1
    assert convergence_signals[0].payload["trade_date"] == TRADE_DATE


def test_scan_requires_after_close_runtime(conn):
    _seed_timing1_setup_signal(conn)
    service = Timing1ConvergenceScanService(
        broker=FakeBroker({"005930": _same_day_minute_df()}),
        conn=conn,
        signal_repo=SignalRepository(conn),
        intraday_bar_repo=IntradayBar15mRepository(conn),
        now_fn=_fixed_now(15, 0),
    )

    with pytest.raises(ServiceError, match="after 15:30 KST"):
        service.scan(
            trade_date=TRADE_DATE,
            settings=Timing1ConvergenceSettings(
                ma_short_window=2,
                ma_long_window=4,
                convergence_threshold_rate=0.02,
            ),
            history_limit=10,
            write=False,
        )
