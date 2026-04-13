"""Tests for PositionRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    NegativePositionError,
    PositionRepository,
    PositionRow,
    RepositoryError,
    RepositoryInvariantError,
)


AT1 = "2026-04-13T09:00:00+09:00"
AT2 = "2026-04-13T09:05:00+09:00"
AT3 = "2026-04-13T09:10:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------
# Transaction guard
# ---------------------------------------------------------------------
def test_write_methods_require_transaction(conn):
    repo = PositionRepository(conn)

    with pytest.raises(RepositoryError):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )
    with pytest.raises(RepositoryError):
        repo.upsert_from_broker(
            symbol="005930", qty=10, avg_price=70000, updated_at=AT1,
        )
    with pytest.raises(RepositoryError):
        repo.clear(symbol="005930", updated_at=AT1)


# ---------------------------------------------------------------------
# apply_execution: BUY
# ---------------------------------------------------------------------
def test_apply_execution_buy_creates_new_position(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        row = repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )

    assert row == PositionRow(
        symbol="005930", qty=10, avg_price=70000, updated_at=AT1,
    )


def test_apply_execution_buy_uses_weighted_average(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )
        row = repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=72000,
            executed_at=AT2,
        )

    # (10*70000 + 10*72000) / 20 = 71000
    assert row.qty == 20
    assert row.avg_price == 71000
    assert row.updated_at == AT2


def test_apply_execution_buy_rounds_fractional_average(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=3, price=10000,
            executed_at=AT1,
        )
        row = repo.apply_execution(
            symbol="005930", side="buy", qty=2, price=10001,
            executed_at=AT2,
        )

    # (3*10000 + 2*10001) / 5 = 50002/5 = 10000.4 -> 10000 (banker's round)
    assert row.qty == 5
    assert row.avg_price == 10000


# ---------------------------------------------------------------------
# apply_execution: SELL
# ---------------------------------------------------------------------
def test_apply_execution_sell_reduces_qty_keeps_avg(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )
        row = repo.apply_execution(
            symbol="005930", side="sell", qty=3, price=72000,
            executed_at=AT2,
        )

    assert row.qty == 7
    assert row.avg_price == 70000   # avg unchanged on sell


def test_apply_execution_sell_to_zero_resets_avg(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )
        row = repo.apply_execution(
            symbol="005930", side="sell", qty=10, price=72000,
            executed_at=AT2,
        )

    assert row.qty == 0
    assert row.avg_price == 0
    assert row.updated_at == AT2


def test_apply_execution_sell_raises_on_negative_position(conn):
    repo = PositionRepository(conn)

    with pytest.raises(NegativePositionError) as exc_info:
        with transaction(conn):
            repo.apply_execution(
                symbol="005930", side="buy", qty=5, price=70000,
                executed_at=AT1,
            )
            repo.apply_execution(
                symbol="005930", side="sell", qty=6, price=72000,
                executed_at=AT2,
            )

    err = exc_info.value
    assert err.symbol == "005930"
    assert err.current_qty == 5
    assert err.sell_qty == 6


def test_apply_execution_sell_without_holding_raises(conn):
    repo = PositionRepository(conn)

    with pytest.raises(NegativePositionError):
        with transaction(conn):
            repo.apply_execution(
                symbol="005930", side="sell", qty=1, price=70000,
                executed_at=AT1,
            )


# ---------------------------------------------------------------------
# upsert_from_broker
# ---------------------------------------------------------------------
def test_upsert_from_broker_overwrites_internal_ledger(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )

    # Broker snapshot disagrees: corporate action or untracked fill.
    with transaction(conn):
        row = repo.upsert_from_broker(
            symbol="005930", qty=12, avg_price=69000,
            updated_at=AT2,
        )

    assert row == PositionRow(
        symbol="005930", qty=12, avg_price=69000, updated_at=AT2,
    )


def test_upsert_from_broker_rejects_nonzero_avg_with_zero_qty(conn):
    repo = PositionRepository(conn)

    with pytest.raises(RepositoryInvariantError):
        with transaction(conn):
            repo.upsert_from_broker(
                symbol="005930", qty=0, avg_price=70000,
                updated_at=AT1,
            )


def test_upsert_from_broker_allows_zero_zero(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        row = repo.upsert_from_broker(
            symbol="005930", qty=0, avg_price=0, updated_at=AT1,
        )

    assert row.qty == 0
    assert row.avg_price == 0


# ---------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------
def test_clear_sets_qty_and_avg_to_zero(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )

    with transaction(conn):
        row = repo.clear(symbol="005930", updated_at=AT2)

    assert row.qty == 0
    assert row.avg_price == 0


# ---------------------------------------------------------------------
# Read filtering
# ---------------------------------------------------------------------
def test_list_all_excludes_zero_qty(conn):
    repo = PositionRepository(conn)

    with transaction(conn):
        repo.apply_execution(
            symbol="005930", side="buy", qty=10, price=70000,
            executed_at=AT1,
        )
        repo.apply_execution(
            symbol="000660", side="buy", qty=5, price=120000,
            executed_at=AT1,
        )
        # Close 000660 fully.
        repo.apply_execution(
            symbol="000660", side="sell", qty=5, price=125000,
            executed_at=AT2,
        )

    live = repo.list_all()
    full = repo.list_all_including_zero()

    assert [p.symbol for p in live] == ["005930"]
    assert {p.symbol for p in full} == {"005930", "000660"}


def test_get_returns_none_for_unknown_symbol(conn):
    repo = PositionRepository(conn)
    assert repo.get("005930") is None


# ---------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------
def test_apply_execution_rejects_bad_side(conn):
    repo = PositionRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.apply_execution(
                symbol="005930", side="long", qty=1, price=1,
                executed_at=AT1,
            )


def test_apply_execution_rejects_non_aware_timestamp(conn):
    repo = PositionRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.apply_execution(
                symbol="005930", side="buy", qty=1, price=1,
                executed_at="2026-04-13T09:00:00",  # no tz
            )


def test_apply_execution_rejects_zero_qty(conn):
    repo = PositionRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.apply_execution(
                symbol="005930", side="buy", qty=0, price=70000,
                executed_at=AT1,
            )