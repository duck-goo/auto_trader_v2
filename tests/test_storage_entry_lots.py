"""Tests for EntryLotRepository."""

from __future__ import annotations

import pytest

from services import (
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    ENTRY_SLOT_TIMING2_MORNING,
    ENTRY_SLOT_TIMING2_RANGE,
    EntryLotRepository,
    NegativePositionError,
    OrderRepository,
    RepositoryError,
    RepositoryInvariantError,
)


REQUESTED_AT = "2026-04-16T09:00:00+09:00"
SUBMITTED_AT = "2026-04-16T09:00:01+09:00"
EXECUTED_AT1 = "2026-04-16T09:01:00+09:00"
EXECUTED_AT2 = "2026-04-16T09:01:10+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _create_buy_order(
    conn,
    *,
    symbol: str = "005930",
    strategy_name: str = STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
) -> int:
    order_repo = OrderRepository(conn)
    with transaction(conn):
        order = order_repo.create(
            client_order_id=f"ORDER-{symbol}-{strategy_name}",
            symbol=symbol,
            side="buy",
            qty=10,
            price=0,
            order_type="MARKET",
            strategy_name=strategy_name,
            requested_at=REQUESTED_AT,
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no=f"KIS-{symbol}",
            submitted_at=SUBMITTED_AT,
        )
    return order.id


def test_write_methods_require_transaction(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with pytest.raises(RepositoryError):
        repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=70_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )


def test_apply_buy_execution_creates_lot_from_actual_fill_quantity(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        lot = repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=70_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            entry_signal_id=None,
        )

    assert lot.entry_order_id == order_id
    assert lot.entry_slot == ENTRY_SLOT_TIMING2_MORNING
    assert lot.total_buy_qty == 3
    assert lot.remaining_qty == 3
    assert lot.avg_buy_price == 70_000
    assert lot.status == "OPEN"
    assert lot.opened_at == EXECUTED_AT1
    assert lot.closed_at is None


def test_apply_split_buy_execution_aggregates_same_order_lot(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=70_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        lot = repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=2,
            price=71_500,
            executed_at=EXECUTED_AT2,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )

    assert lot.total_buy_qty == 5
    assert lot.remaining_qty == 5
    assert lot.avg_buy_price == 70_600
    assert lot.opened_at == EXECUTED_AT1
    assert lot.updated_at == EXECUTED_AT2


def test_apply_buy_execution_rejects_identity_mismatch(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=70_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        with pytest.raises(RepositoryInvariantError, match="strategy mismatch"):
            repo.apply_buy_execution(
                entry_order_id=order_id,
                symbol="005930",
                qty=1,
                price=71_000,
                executed_at=EXECUTED_AT2,
                entry_strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            )


def test_apply_sell_to_lot_reduces_remaining_qty_and_closes(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        lot = repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=5,
            price=10_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        partial = repo.apply_sell_to_lot(
            lot_id=lot.id,
            qty=3,
            price=11_000,
            executed_at="2026-04-16T09:10:00+09:00",
        )
        closed = repo.apply_sell_to_lot(
            lot_id=lot.id,
            qty=2,
            price=9_000,
            executed_at="2026-04-16T09:20:00+09:00",
        )

    assert partial.remaining_qty == 2
    assert partial.realized_sell_qty == 3
    assert partial.realized_pnl == 3_000
    assert partial.status == "OPEN"
    assert closed.remaining_qty == 0
    assert closed.realized_sell_qty == 5
    assert closed.realized_pnl == 1_000
    assert closed.status == "CLOSED"
    assert closed.closed_at == "2026-04-16T09:20:00+09:00"


def test_apply_sell_to_lot_includes_sell_cost_rate(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        lot = repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=10_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        sold = repo.apply_sell_to_lot(
            lot_id=lot.id,
            qty=3,
            price=10_000,
            executed_at="2026-04-16T09:10:00+09:00",
            sell_cost_rate=0.002,
        )

    assert sold.realized_pnl == -60
    assert sold.status == "CLOSED"


def test_apply_sell_to_lot_rejects_oversell_and_rolls_back(conn):
    order_id = _create_buy_order(conn)
    repo = EntryLotRepository(conn)

    with transaction(conn):
        lot = repo.apply_buy_execution(
            entry_order_id=order_id,
            symbol="005930",
            qty=3,
            price=10_000,
            executed_at=EXECUTED_AT1,
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )

    with pytest.raises(NegativePositionError):
        with transaction(conn):
            repo.apply_sell_to_lot(
                lot_id=lot.id,
                qty=4,
                price=11_000,
                executed_at="2026-04-16T09:10:00+09:00",
            )

    unchanged = repo.get(lot.id)
    assert unchanged is not None
    assert unchanged.remaining_qty == 3
    assert unchanged.realized_sell_qty == 0
    assert unchanged.realized_pnl == 0
    assert unchanged.status == "OPEN"


def test_list_open_by_symbol_returns_only_open_lots_in_order(conn):
    morning_order_id = _create_buy_order(
        conn,
        symbol="005930",
        strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    )
    range_order_id = _create_buy_order(
        conn,
        symbol="005931",
        strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    )
    repo = EntryLotRepository(conn)

    with transaction(conn):
        morning = repo.apply_buy_execution(
            entry_order_id=morning_order_id,
            symbol="005930",
            qty=3,
            price=10_000,
            executed_at="2026-04-16T09:01:00+09:00",
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )
        range_lot = repo.apply_buy_execution(
            entry_order_id=range_order_id,
            symbol="005931",
            qty=2,
            price=20_000,
            executed_at="2026-04-16T10:01:00+09:00",
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
        )
        repo.apply_sell_to_lot(
            lot_id=morning.id,
            qty=3,
            price=10_500,
            executed_at="2026-04-16T09:30:00+09:00",
        )

    assert repo.list_open_by_symbol(symbol="005930") == []
    open_range_lots = repo.list_open_by_symbol(symbol="005931")
    assert [lot.id for lot in open_range_lots] == [range_lot.id]
    assert open_range_lots[0].entry_slot == ENTRY_SLOT_TIMING2_RANGE
