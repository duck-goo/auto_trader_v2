"""Tests for SellExitScanService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import SellExitScanService, STRATEGY_NAME_SELL_STOP_LOSS
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import PositionRepository, SignalRepository
from strategy import SellExitSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class FakeBroker(BrokerInterface):
    def __init__(self, snapshot_map: dict[str, PriceSnapshot]) -> None:
        self._snapshot_map = snapshot_map

    def get_access_token(self) -> str:
        raise NotImplementedError

    def get_current_price(self, code: str) -> PriceSnapshot:
        return self._snapshot_map[code]

    def get_daily_candles(self, code: str, count: int = 30, end_date: str | None = None):
        raise NotImplementedError

    def get_minute_candles(self, code: str, interval: str = "1"):
        raise NotImplementedError

    def get_balance(self) -> Balance:
        raise NotImplementedError

    def place_order(self, code: str, side: str, quantity: int, price: int = 0) -> OrderInfo:
        raise NotImplementedError

    def cancel_order(self, order_no: str, code: str, quantity: int) -> OrderInfo:
        raise NotImplementedError

    def get_order_status(self, order_no: str | None = None, *, filled_only: bool = False):
        raise NotImplementedError


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 17, 10, 5, 0))
    return lambda: fixed


def _make_snapshot(symbol: str, price: int) -> PriceSnapshot:
    return PriceSnapshot(
        code=symbol,
        name=f"Name-{symbol}",
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change=0,
        change_rate=0.0,
        volume=1,
        timestamp=_fixed_now()(),
    )


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_scan_records_stop_loss_signal_and_dedupes_second_run(conn):
    position_repo = PositionRepository(conn)
    signal_repo = SignalRepository(conn)

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=10,
            avg_price=100_000,
            updated_at="2026-04-17T09:00:00+09:00",
        )

    service = SellExitScanService(
        broker=FakeBroker({"005930": _make_snapshot("005930", 97_000)}),
        conn=conn,
        position_repo=position_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
    )

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=SellExitSettings(stop_loss_ratio=0.03, take_profit_ratio=0.05),
        write_signals=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=SellExitSettings(stop_loss_ratio=0.03, take_profit_ratio=0.05),
        write_signals=True,
    )

    assert first.position_count == 1
    assert first.matched_count == 1
    assert first.stop_loss_count == 1
    assert first.recorded_count == 1
    assert first.candidates[0].strategy_name == STRATEGY_NAME_SELL_STOP_LOSS

    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1

    stored = signal_repo.list_by_symbol("005930", limit=10)
    assert len(stored) == 1
    assert stored[0].strategy_name == STRATEGY_NAME_SELL_STOP_LOSS
    assert stored[0].payload["trade_date"] == TRADE_DATE
