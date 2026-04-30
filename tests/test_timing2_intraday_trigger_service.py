"""Tests for Timing2IntradayTriggerService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    STRATEGY_NAME_TIMING2_INTRADAY_BREAKOUT,
    STRATEGY_NAME_TIMING2_INTRADAY_EXPIRED,
    STRATEGY_NAME_TIMING2_INTRADAY_PULLBACK,
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_SETUP,
    Timing2IntradayTriggerService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository
from strategy import Timing2IntradayStage, Timing2IntradayTriggerSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-15"


class FakeBroker(BrokerInterface):
    def __init__(self, snapshot_map: dict[str, PriceSnapshot]) -> None:
        self._snapshot_map = snapshot_map
        self.current_price_calls: list[str] = []

    def set_snapshot(self, code: str, snapshot: PriceSnapshot) -> None:
        self._snapshot_map[code] = snapshot

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
    ):
        raise NotImplementedError

    def get_minute_candles(
        self,
        code: str,
        interval: str = "1",
    ):
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
    return KST.localize(datetime(2026, 4, 15, hour, minute, 0))


def _price_snapshot(
    *,
    price: int,
    open_price: int,
    hour: int,
    minute: int,
) -> PriceSnapshot:
    timestamp = _kst_datetime(hour, minute)
    return PriceSnapshot(
        code="005930",
        name="Samsung Electronics",
        price=price,
        open=open_price,
        high=max(price, open_price),
        low=min(price, open_price),
        prev_close=900,
        change=price - 900,
        change_rate=((price - 900) / 900) * 100,
        volume=1_000_000,
        timestamp=timestamp,
    )


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _seed_timing2_setup_signal(conn) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            scanned_at="2026-04-15T08:55:00+09:00",
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )


def test_scan_advances_breakout_pullback_trigger_sequence(conn):
    _seed_timing2_setup_signal(conn)
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        {"005930": _price_snapshot(price=1004, open_price=1000, hour=9, minute=5)}
    )

    first = Timing2IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 5),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing2IntradayTriggerSettings(),
        write_signals=True,
    )
    assert first.transition_count == 1
    assert first.recorded_count == 1
    assert first.candidates[0].decision.stage_after == Timing2IntradayStage.WAIT_PULLBACK

    broker.set_snapshot(
        "005930",
        _price_snapshot(price=997, open_price=1000, hour=9, minute=20),
    )
    second = Timing2IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 20),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing2IntradayTriggerSettings(),
        write_signals=True,
    )
    assert second.transition_count == 1
    assert second.recorded_count == 1
    assert second.candidates[0].decision.stage_after == Timing2IntradayStage.WAIT_REBOUND

    broker.set_snapshot(
        "005930",
        _price_snapshot(price=1000, open_price=1000, hour=9, minute=35),
    )
    third = Timing2IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 35),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing2IntradayTriggerSettings(),
        write_signals=True,
    )
    assert third.transition_count == 1
    assert third.recorded_count == 1
    assert third.candidates[0].decision.stage_after == Timing2IntradayStage.TRIGGERED

    by_symbol = signal_repo.list_by_symbol("005930")
    strategy_names = {row.strategy_name for row in by_symbol}
    assert STRATEGY_NAME_TIMING2_SETUP in strategy_names
    assert STRATEGY_NAME_TIMING2_INTRADAY_BREAKOUT in strategy_names
    assert STRATEGY_NAME_TIMING2_INTRADAY_PULLBACK in strategy_names
    assert STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER in strategy_names


def test_scan_records_expired_after_cutoff(conn):
    _seed_timing2_setup_signal(conn)
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        {"005930": _price_snapshot(price=1001, open_price=1000, hour=12, minute=0)}
    )

    result = Timing2IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(12, 0),
    ).scan(
        trade_date=TRADE_DATE,
        settings=Timing2IntradayTriggerSettings(),
        write_signals=True,
    )

    assert result.expired_count == 1
    assert result.recorded_count == 1
    assert result.candidates[0].decision.stage_after == Timing2IntradayStage.EXPIRED
    assert any(
        row.strategy_name == STRATEGY_NAME_TIMING2_INTRADAY_EXPIRED
        for row in signal_repo.list_by_symbol("005930")
    )


def test_scan_raises_when_timing2_setup_signals_are_missing(conn):
    service = Timing2IntradayTriggerService(
        broker=FakeBroker({}),
        conn=conn,
        signal_repo=SignalRepository(conn),
        now_fn=lambda: _kst_datetime(9, 5),
    )

    with pytest.raises(Exception, match="Timing2 setup signals are missing"):
        service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2IntradayTriggerSettings(),
            write_signals=False,
        )


def test_scan_rejects_non_current_runtime_trade_date_before_broker_calls(conn):
    _seed_timing2_setup_signal(conn)
    signal_repo = SignalRepository(conn)
    broker = FakeBroker(
        {"005930": _price_snapshot(price=1004, open_price=1000, hour=9, minute=5)}
    )

    service = Timing2IntradayTriggerService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: KST.localize(datetime(2026, 4, 16, 9, 5, 0)),
    )

    with pytest.raises(Exception, match="supports only the current KST trade_date"):
        service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2IntradayTriggerSettings(),
            write_signals=False,
        )

    assert broker.current_price_calls == []
