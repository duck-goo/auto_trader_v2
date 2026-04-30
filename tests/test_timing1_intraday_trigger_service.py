"""Tests for Timing1IntradayTriggerService."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    STRATEGY_NAME_TIMING1_CONVERGENCE,
    STRATEGY_NAME_TIMING1_INTRADAY_EXPIRED,
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
    Timing1IntradayTriggerService,
)
from services.errors import ServiceError
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository
from strategy import Timing1IntradayStage, Timing1IntradayTriggerSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


class FakeBroker(BrokerInterface):
    def __init__(
        self,
        *,
        snapshot_map: dict[str, PriceSnapshot],
        daily_map: dict[str, pd.DataFrame],
    ) -> None:
        self._snapshot_map = snapshot_map
        self._daily_map = daily_map
        self.current_price_calls: list[str] = []
        self.daily_candle_calls: list[str] = []

    def get_access_token(self) -> str:
        raise NotImplementedError

    def get_current_price(self, code: str) -> PriceSnapshot:
        self.current_price_calls.append(code)
        return self._snapshot_map[code]

    def get_daily_candles(
        self,
        code: str,
        count: int = 30,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        self.daily_candle_calls.append(code)
        return self._daily_map[code]

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


def _kst_datetime(hour: int, minute: int) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, 0))


def _price_snapshot(*, price: int, hour: int, minute: int) -> PriceSnapshot:
    timestamp = _kst_datetime(hour, minute)
    return PriceSnapshot(
        code="005930",
        name="Samsung Electronics",
        price=price,
        open=100,
        high=max(price, 100),
        low=min(price, 100),
        prev_close=99,
        change=price - 99,
        change_rate=((price - 99) / 99) * 100,
        volume=1_000_000,
        timestamp=timestamp,
    )


def _daily_df(*, latest_completed_date: str) -> pd.DataFrame:
    rows = [
        {
            "datetime": f"{latest_completed_date}T15:30:00+09:00",
            "open": 100,
            "high": 104,
            "low": 99,
            "close": 103,
            "volume": 1_000_000,
        },
        {
            "datetime": f"{TRADE_DATE}T09:05:00+09:00",
            "open": 100,
            "high": 105,
            "low": 100,
            "close": 105,
            "volume": 10_000,
        },
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _seed_convergence_signal(
    conn,
    *,
    convergence_trade_date: str,
) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING1_CONVERGENCE,
            scanned_at="2026-04-15T15:31:00+09:00",
            payload={
                "trade_date": convergence_trade_date,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "strong_day_date": "2026-04-14",
                "convergence_trade_date": convergence_trade_date,
                "convergence_day_high": 104,
            },
        )


def test_scan_records_trigger_and_dedupes_second_run(conn):
    _seed_convergence_signal(conn, convergence_trade_date="2026-04-15")
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        snapshot_map={"005930": _price_snapshot(price=104, hour=9, minute=5)},
        daily_map={"005930": _daily_df(latest_completed_date="2026-04-15")},
    )

    first = Timing1IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 5),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing1IntradayTriggerSettings(),
        write_signals=True,
    )
    second = Timing1IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 6),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing1IntradayTriggerSettings(),
        write_signals=True,
    )

    assert first.candidate_count == 1
    assert first.transition_count == 1
    assert first.recorded_count == 1
    assert first.triggered_count == 1
    assert first.candidates[0].decision.stage_after == Timing1IntradayStage.TRIGGERED

    assert second.candidate_count == 1
    assert second.transition_count == 0
    assert second.recorded_count == 0
    assert second.candidates[0].decision.stage_after == Timing1IntradayStage.TRIGGERED

    stored = signal_repo.list_by_symbol("005930")
    trigger_signals = [
        row
        for row in stored
        if row.strategy_name == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
    ]
    assert len(trigger_signals) == 1
    assert trigger_signals[0].payload["trade_date"] == TRADE_DATE


def test_scan_records_expired_after_cutoff(conn):
    _seed_convergence_signal(conn, convergence_trade_date="2026-04-15")
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        snapshot_map={"005930": _price_snapshot(price=103, hour=12, minute=0)},
        daily_map={"005930": _daily_df(latest_completed_date="2026-04-15")},
    )

    result = Timing1IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(12, 0),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing1IntradayTriggerSettings(),
        write_signals=True,
    )

    assert result.candidate_count == 1
    assert result.expired_count == 1
    assert result.recorded_count == 1
    assert result.candidates[0].decision.stage_after == Timing1IntradayStage.EXPIRED
    assert any(
        row.strategy_name == STRATEGY_NAME_TIMING1_INTRADAY_EXPIRED
        for row in signal_repo.list_by_symbol("005930")
    )


def test_scan_skips_when_convergence_is_not_for_immediate_previous_trading_day(conn):
    _seed_convergence_signal(conn, convergence_trade_date="2026-04-14")
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        snapshot_map={"005930": _price_snapshot(price=104, hour=9, minute=5)},
        daily_map={"005930": _daily_df(latest_completed_date="2026-04-15")},
    )

    result = Timing1IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 5),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing1IntradayTriggerSettings(),
        write_signals=True,
    )

    assert result.convergence_signal_count == 1
    assert result.skipped_not_next_trading_day_count == 1
    assert result.candidate_count == 0
    assert result.recorded_count == 0


def test_scan_raises_when_convergence_signals_are_missing(conn):
    service = Timing1IntradayTriggerService(
        broker=FakeBroker(snapshot_map={}, daily_map={}),
        conn=conn,
        signal_repo=SignalRepository(conn),
        now_fn=lambda: _kst_datetime(9, 5),
    )

    with pytest.raises(ServiceError, match="Timing1 convergence signals are missing"):
        service.scan(
            trade_date=TRADE_DATE,
            settings=Timing1IntradayTriggerSettings(),
            write_signals=False,
        )


def test_scan_rejects_non_current_runtime_trade_date_before_broker_calls(conn):
    _seed_convergence_signal(conn, convergence_trade_date="2026-04-15")
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        snapshot_map={"005930": _price_snapshot(price=104, hour=9, minute=5)},
        daily_map={"005930": _daily_df(latest_completed_date="2026-04-15")},
    )

    service = Timing1IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: KST.localize(datetime(2026, 4, 17, 9, 5, 0)),
    )

    with pytest.raises(ServiceError, match="supports only the current KST trade_date"):
        service.scan(
            trade_date=TRADE_DATE,
            settings=Timing1IntradayTriggerSettings(),
            write_signals=False,
        )

    assert broker.current_price_calls == []
    assert broker.daily_candle_calls == []
