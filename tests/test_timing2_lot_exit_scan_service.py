"""Tests for Timing2 lot-level sell exit scan service."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz

from broker.base import BrokerInterface
from broker.kis.models import Balance, OrderInfo, PriceSnapshot
from services import (
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
    Timing2LotExitScanService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    EntryLotRepository,
    IntradayBar30s,
    IntradayBar30sRepository,
    OrderRepository,
    SignalRepository,
)
from strategy import Timing2LotExitSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-21"


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


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 21, 10, 30, 0))


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
        timestamp=_fixed_now(),
    )


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _create_timing2_lot(
    conn,
    *,
    symbol: str,
    qty: int,
    price: int,
) -> int:
    order_repo = OrderRepository(conn)
    lot_repo = EntryLotRepository(conn)
    with transaction(conn):
        order = order_repo.create(
            client_order_id=f"BUY-{symbol}-{qty}-{price}",
            symbol=symbol,
            side="buy",
            qty=qty,
            price=0,
            order_type="MARKET",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            requested_at="2026-04-21T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no=f"KIS-{symbol}",
            submitted_at="2026-04-21T09:00:01+09:00",
        )
        lot = lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol=symbol,
            qty=qty,
            price=price,
            executed_at="2026-04-21T09:01:00+09:00",
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
    return lot.id


def _make_service(conn, *, price_map: dict[str, int]) -> Timing2LotExitScanService:
    return Timing2LotExitScanService(
        broker=FakeBroker(
            {
                symbol: _make_snapshot(symbol, price)
                for symbol, price in price_map.items()
            }
        ),
        conn=conn,
        entry_lot_repo=EntryLotRepository(conn),
        signal_repo=SignalRepository(conn),
        intraday_bar_repo=IntradayBar30sRepository(conn),
        now_fn=_fixed_now,
    )


def _settings() -> Timing2LotExitSettings:
    return Timing2LotExitSettings()


def _store_complete_3m_closes(conn, *, symbol: str, closes: list[int]) -> None:
    repo = IntradayBar30sRepository(conn)
    bars: list[IntradayBar30s] = []
    session_start = KST.localize(datetime(2026, 4, 21, 9, 0, 0))

    for bucket_index, close in enumerate(closes):
        bucket_start = session_start + timedelta(minutes=3 * bucket_index)
        for offset in range(6):
            bar_start = bucket_start + timedelta(seconds=30 * offset)
            bar_end = bar_start + timedelta(seconds=30)
            bar_close = close if offset == 5 else close + 10
            bars.append(
                IntradayBar30s(
                    bar_start_at=bar_start.isoformat(),
                    bar_end_at=bar_end.isoformat(),
                    open=bar_close,
                    high=bar_close,
                    low=bar_close,
                    close=bar_close,
                    volume=10,
                )
            )

    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date=TRADE_DATE,
            symbol=symbol,
            bars=bars,
            refreshed_at="2026-04-21T10:30:00+09:00",
        )


def test_scan_records_stop_loss_signal_and_dedupes(conn):
    lot_id = _create_timing2_lot(
        conn,
        symbol="005930",
        qty=5,
        price=100_000,
    )
    service = _make_service(conn, price_map={"005930": 98_711})

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=_settings(),
        write_signals=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=_settings(),
        write_signals=True,
    )

    assert first.lot_count == 1
    assert first.matched_count == 1
    assert first.stop_loss_count == 1
    assert first.recorded_count == 1
    assert first.candidates[0].lot_id == lot_id
    assert first.candidates[0].strategy_name == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
    assert first.recorded_signals[0].payload["sell_qty"] == 5

    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1


def test_scan_records_rounded_up_partial_take_profit_qty(conn):
    _create_timing2_lot(
        conn,
        symbol="035420",
        qty=5,
        price=10_000,
    )
    service = _make_service(conn, price_map={"035420": 10_500})

    result = service.scan(
        trade_date=TRADE_DATE,
        settings=_settings(),
        write_signals=True,
    )

    assert result.partial_take_profit_count == 1
    assert result.recorded_signals[0].strategy_name == (
        STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL
    )
    assert result.recorded_signals[0].payload["sell_qty"] == 3
    assert result.recorded_signals[0].payload["lot_id"] == result.candidates[0].lot_id


def test_scan_records_three_minute_ma_break_before_partial_take_profit(conn):
    _create_timing2_lot(
        conn,
        symbol="000660",
        qty=5,
        price=10_000,
    )
    _store_complete_3m_closes(
        conn,
        symbol="000660",
        closes=[10_500, 10_400, 10_300, 10_200, 10_000],
    )
    service = _make_service(conn, price_map={"000660": 10_600})

    result = service.scan(
        trade_date=TRADE_DATE,
        settings=_settings(),
        write_signals=True,
    )

    assert result.ma_break_count == 1
    assert result.partial_take_profit_count == 0
    assert result.recorded_signals[0].strategy_name == (
        STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK
    )
    assert result.recorded_signals[0].payload["sell_qty"] == 5
    assert result.recorded_signals[0].payload["latest_3m_close"] == 10_000
    assert result.recorded_signals[0].payload["ma5_3m"] == 10_280
