"""Tests for DailyStatsRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    ExecutionRepository,
    OrderRepository,
    RepositoryError,
)


DAY = "2026-04-13"
NEXT_DAY = "2026-04-14"

T_0900 = "2026-04-13T09:00:00+09:00"
T_0901 = "2026-04-13T09:01:00+09:00"
T_0902 = "2026-04-13T09:02:00+09:00"
T_1000 = "2026-04-13T10:00:00+09:00"
T_1500 = "2026-04-13T15:00:00+09:00"
T_NEXT = "2026-04-14T09:00:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _create_order(
    order_repo: OrderRepository,
    *,
    coid: str,
    symbol: str = "005930",
    side: str = "buy",
    qty: int = 10,
    price: int = 70000,
    requested_at: str = T_0900,
):
    return order_repo.create(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        order_type="LIMIT",
        strategy_name="stats-test",
        requested_at=requested_at,
    )


# ---------------------------------------------------------------------
# Transaction guard
# ---------------------------------------------------------------------
def test_recompute_day_requires_transaction(conn):
    repo = DailyStatsRepository(conn)
    with pytest.raises(RepositoryError):
        repo.recompute_day(DAY)


# ---------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------
def test_recompute_day_rejects_bad_date_format(conn):
    repo = DailyStatsRepository(conn)
    for bad in ("2026/04/13", "26-04-13", "2026-4-13", "not-a-date", ""):
        with pytest.raises(ValueError):
            with transaction(conn):
                repo.recompute_day(bad)


def test_get_rejects_bad_date_format(conn):
    repo = DailyStatsRepository(conn)
    with pytest.raises(ValueError):
        repo.get("2026/04/13")


# ---------------------------------------------------------------------
# Empty day
# ---------------------------------------------------------------------
def test_recompute_empty_day_returns_zeros(conn):
    stats_repo = DailyStatsRepository(conn)
    with transaction(conn):
        row = stats_repo.recompute_day(DAY)

    assert row.trade_date == DAY
    assert row.realized_pnl == 0
    assert row.order_count == 0
    assert row.fill_count == 0
    assert row.error_count == 0


# ---------------------------------------------------------------------
# Order / execution counting
# ---------------------------------------------------------------------
def test_recompute_counts_orders_within_day(conn):
    order_repo = OrderRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    with transaction(conn):
        _create_order(order_repo, coid="COID_A", requested_at=T_0900)
        _create_order(order_repo, coid="COID_B", requested_at=T_1500)
        # Next-day order should NOT be counted.
        _create_order(order_repo, coid="COID_C", requested_at=T_NEXT)

    with transaction(conn):
        row = stats_repo.recompute_day(DAY)

    assert row.order_count == 2
    assert row.fill_count == 0
    assert row.error_count == 0


def test_recompute_counts_errors_by_requested_at(conn):
    order_repo = OrderRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    with transaction(conn):
        a = _create_order(order_repo, coid="COID_A", requested_at=T_0900)
        b = _create_order(order_repo, coid="COID_B", requested_at=T_0901)
        _create_order(order_repo, coid="COID_C", requested_at=T_0902)

        order_repo.mark_rejected(
            client_order_id=a.client_order_id,
            error_code="E001",
            error_message="rejected",
            closed_at=T_1000,
        )
        order_repo.mark_failed(
            client_order_id=b.client_order_id,
            error_code="E500",
            error_message="failed",
            closed_at=T_1000,
        )

    with transaction(conn):
        row = stats_repo.recompute_day(DAY)

    assert row.order_count == 3
    assert row.error_count == 2


def test_recompute_counts_fills_within_day(conn):
    order_repo = OrderRepository(conn)
    exec_repo = ExecutionRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    with transaction(conn):
        o = _create_order(order_repo, coid="COID_F", qty=10)
        order_repo.mark_submitted(
            client_order_id=o.client_order_id,
            kis_order_no="KIS_F",
            submitted_at=T_0901,
        )
        exec_repo.insert_if_new(
            order_id=o.id, kis_exec_no="E1",
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at=T_1000,
        )
        exec_repo.insert_if_new(
            order_id=o.id, kis_exec_no="E2",
            symbol="005930", side="buy", qty=5, price=70100,
            executed_at=T_1500,
        )
        # Fill that happens after midnight should NOT be counted.
        o2 = _create_order(order_repo, coid="COID_G", qty=3, requested_at=T_0902)
        order_repo.mark_submitted(
            client_order_id=o2.client_order_id,
            kis_order_no="KIS_G",
            submitted_at=T_0902,
        )
        exec_repo.insert_if_new(
            order_id=o2.id, kis_exec_no="E3",
            symbol="005930", side="buy", qty=3, price=70000,
            executed_at=T_NEXT,
        )

    with transaction(conn):
        row = stats_repo.recompute_day(DAY)

    assert row.fill_count == 2   # E1, E2 on DAY; E3 on NEXT_DAY


# ---------------------------------------------------------------------
# UPSERT idempotency
# ---------------------------------------------------------------------
def test_recompute_is_idempotent(conn):
    order_repo = OrderRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    with transaction(conn):
        _create_order(order_repo, coid="COID_I", requested_at=T_0900)
    with transaction(conn):
        first = stats_repo.recompute_day(DAY)
    with transaction(conn):
        second = stats_repo.recompute_day(DAY)

    assert first == second


def test_recompute_reflects_newly_added_rows(conn):
    order_repo = OrderRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    with transaction(conn):
        _create_order(order_repo, coid="COID_1", requested_at=T_0900)
    with transaction(conn):
        first = stats_repo.recompute_day(DAY)
    assert first.order_count == 1

    with transaction(conn):
        _create_order(order_repo, coid="COID_2", requested_at=T_1000)
    with transaction(conn):
        second = stats_repo.recompute_day(DAY)
    assert second.order_count == 2


# ---------------------------------------------------------------------
# list_between
# ---------------------------------------------------------------------
def test_list_between_returns_sorted(conn):
    stats_repo = DailyStatsRepository(conn)
    with transaction(conn):
        stats_repo.recompute_day("2026-04-13")
        stats_repo.recompute_day("2026-04-14")
        stats_repo.recompute_day("2026-04-15")

    result = stats_repo.list_between(
        start_date="2026-04-13", end_date="2026-04-14",
    )
    dates = [r.trade_date for r in result]
    assert dates == ["2026-04-13", "2026-04-14"]


def test_list_between_rejects_inverted_range(conn):
    stats_repo = DailyStatsRepository(conn)
    with pytest.raises(ValueError):
        stats_repo.list_between(
            start_date="2026-04-15", end_date="2026-04-13",
        )


def test_get_returns_none_for_unknown_date(conn):
    stats_repo = DailyStatsRepository(conn)
    assert stats_repo.get(DAY) is None