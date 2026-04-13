"""Tests for storage repositories."""

from __future__ import annotations

import pytest

from broker.kis.models import OrderStatus as BrokerOrderStatus
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    ExecutionRepository,
    IllegalStateTransition,
    OrderRepository,
    RepositoryError,
    RepositoryInvariantError,
    broker_status_to_db,
)


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_broker_status_mapping_uses_submitted_for_accepted():
    assert broker_status_to_db(BrokerOrderStatus.ACCEPTED) == DbOrderStatus.SUBMITTED
    assert broker_status_to_db(BrokerOrderStatus.UNKNOWN) == DbOrderStatus.UNKNOWN


def test_repository_write_requires_explicit_transaction(conn):
    repo = OrderRepository(conn)

    with pytest.raises(RepositoryError):
        repo.create(
            client_order_id="COID_TX",
            symbol="005930",
            side="buy",
            qty=1,
            price=70000,
            order_type="LIMIT",
            strategy_name="tx-check",
            requested_at="2026-04-13T09:00:00+09:00",
        )


def test_execution_repository_ignores_duplicate_execution(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id="COID_EXEC_DUP",
            symbol="005930",
            side="buy",
            qty=10,
            price=70000,
            order_type="LIMIT",
            strategy_name="dup",
            requested_at="2026-04-13T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS_EXEC_DUP",
            submitted_at="2026-04-13T09:00:01+09:00",
        )
        inserted_first = execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-1",
            symbol="005930",
            side="buy",
            qty=5,
            price=70000,
            executed_at="2026-04-13T09:01:00+09:00",
        )
        inserted_second = execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-1",
            symbol="005930",
            side="buy",
            qty=5,
            price=70000,
            executed_at="2026-04-13T09:01:00+09:00",
        )

    assert inserted_first is True
    assert inserted_second is False


def test_sync_execution_summary_uses_execution_rows_as_single_source_of_truth(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id="COID_PARTIAL",
            symbol="005930",
            side="buy",
            qty=10,
            price=70000,
            order_type="LIMIT",
            strategy_name="partial",
            requested_at="2026-04-13T09:05:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS_PARTIAL",
            submitted_at="2026-04-13T09:05:01+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-1",
            symbol="005930",
            side="buy",
            qty=2,
            price=100,
            executed_at="2026-04-13T09:06:00+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-2",
            symbol="005930",
            side="buy",
            qty=2,
            price=110,
            executed_at="2026-04-13T09:06:10+09:00",
        )
        synced = order_repo.sync_execution_summary(
            client_order_id=order.client_order_id
        )

    assert synced.status == DbOrderStatus.PARTIAL
    assert synced.filled_qty == 4
    assert synced.avg_fill_price == 105


def test_mark_filled_computes_weighted_average_from_executions(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id="COID_FILLED",
            symbol="005930",
            side="buy",
            qty=5,
            price=70000,
            order_type="LIMIT",
            strategy_name="filled",
            requested_at="2026-04-13T09:10:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS_FILLED",
            submitted_at="2026-04-13T09:10:01+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-A",
            symbol="005930",
            side="buy",
            qty=2,
            price=100,
            executed_at="2026-04-13T09:11:00+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-B",
            symbol="005930",
            side="buy",
            qty=3,
            price=110,
            executed_at="2026-04-13T09:11:10+09:00",
        )
        filled = order_repo.mark_filled(
            client_order_id=order.client_order_id,
            closed_at="2026-04-13T09:12:00+09:00",
        )

    assert filled.status == DbOrderStatus.FILLED
    assert filled.filled_qty == 5
    assert filled.avg_fill_price == 106


def test_mark_cancelled_keeps_execution_summary_for_partial_fill(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id="COID_CANCEL_PARTIAL",
            symbol="005930",
            side="buy",
            qty=10,
            price=70000,
            order_type="LIMIT",
            strategy_name="cancel",
            requested_at="2026-04-13T09:20:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS_CANCEL_PARTIAL",
            submitted_at="2026-04-13T09:20:01+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-C1",
            symbol="005930",
            side="buy",
            qty=3,
            price=100,
            executed_at="2026-04-13T09:21:00+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-C2",
            symbol="005930",
            side="buy",
            qty=2,
            price=110,
            executed_at="2026-04-13T09:21:10+09:00",
        )
        order_repo.sync_execution_summary(client_order_id=order.client_order_id)
        cancelled = order_repo.mark_cancelled(
            client_order_id=order.client_order_id,
            closed_at="2026-04-13T09:22:00+09:00",
        )

    assert cancelled.status == DbOrderStatus.CANCELLED
    assert cancelled.filled_qty == 5
    assert cancelled.avg_fill_price == 104


def test_terminal_state_transition_is_blocked(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id="COID_TERMINAL",
            symbol="005930",
            side="buy",
            qty=5,
            price=70000,
            order_type="LIMIT",
            strategy_name="terminal",
            requested_at="2026-04-13T09:30:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no="KIS_TERMINAL",
            submitted_at="2026-04-13T09:30:01+09:00",
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no="EXEC-T1",
            symbol="005930",
            side="buy",
            qty=5,
            price=100,
            executed_at="2026-04-13T09:31:00+09:00",
        )
        order_repo.mark_filled(
            client_order_id=order.client_order_id,
            closed_at="2026-04-13T09:31:30+09:00",
        )

    with pytest.raises(IllegalStateTransition):
        with transaction(conn):
            order_repo.mark_cancelled(
                client_order_id="COID_TERMINAL",
                closed_at="2026-04-13T09:32:00+09:00",
            )


def test_find_unresolved_includes_partial_orders(conn):
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)

    with transaction(conn):
        pending = order_repo.create(
            client_order_id="COID_PENDING",
            symbol="005930",
            side="buy",
            qty=10,
            price=70000,
            order_type="LIMIT",
            strategy_name="pending",
            requested_at="2026-04-13T09:40:00+09:00",
        )
        submitted = order_repo.create(
            client_order_id="COID_SUBMITTED",
            symbol="000660",
            side="buy",
            qty=10,
            price=80000,
            order_type="LIMIT",
            strategy_name="submitted",
            requested_at="2026-04-13T09:40:01+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=submitted.client_order_id,
            kis_order_no="KIS_SUBMITTED",
            submitted_at="2026-04-13T09:40:02+09:00",
        )
        unknown = order_repo.create(
            client_order_id="COID_UNKNOWN",
            symbol="035420",
            side="buy",
            qty=10,
            price=90000,
            order_type="LIMIT",
            strategy_name="unknown",
            requested_at="2026-04-13T09:40:03+09:00",
        )
        order_repo.mark_unknown(client_order_id=unknown.client_order_id)
        partial = order_repo.create(
            client_order_id="COID_PARTIAL_ONLY",
            symbol="051910",
            side="buy",
            qty=10,
            price=100000,
            order_type="LIMIT",
            strategy_name="partial",
            requested_at="2026-04-13T09:40:04+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=partial.client_order_id,
            kis_order_no="KIS_PARTIAL_ONLY",
            submitted_at="2026-04-13T09:40:05+09:00",
        )
        execution_repo.insert_if_new(
            order_id=partial.id,
            kis_exec_no="EXEC-PARTIAL",
            symbol="051910",
            side="buy",
            qty=2,
            price=100000,
            executed_at="2026-04-13T09:41:00+09:00",
        )
        order_repo.sync_execution_summary(client_order_id=partial.client_order_id)

        filled = order_repo.create(
            client_order_id="COID_RESOLVED",
            symbol="068270",
            side="buy",
            qty=2,
            price=120000,
            order_type="LIMIT",
            strategy_name="filled",
            requested_at="2026-04-13T09:40:06+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=filled.client_order_id,
            kis_order_no="KIS_RESOLVED",
            submitted_at="2026-04-13T09:40:07+09:00",
        )
        execution_repo.insert_if_new(
            order_id=filled.id,
            kis_exec_no="EXEC-FILLED",
            symbol="068270",
            side="buy",
            qty=2,
            price=120000,
            executed_at="2026-04-13T09:41:10+09:00",
        )
        order_repo.mark_filled(
            client_order_id=filled.client_order_id,
            closed_at="2026-04-13T09:41:30+09:00",
        )

    unresolved_ids = {
        row.client_order_id for row in order_repo.find_unresolved()
    }
    assert pending.client_order_id in unresolved_ids
    assert submitted.client_order_id in unresolved_ids
    assert unknown.client_order_id in unresolved_ids
    assert partial.client_order_id in unresolved_ids
    assert filled.client_order_id not in unresolved_ids


def test_get_by_kis_order_no_raises_when_duplicates_exist(conn):
    order_repo = OrderRepository(conn)

    with transaction(conn):
        first = order_repo.create(
            client_order_id="COID_DUP_A",
            symbol="005930",
            side="buy",
            qty=1,
            price=70000,
            order_type="LIMIT",
            strategy_name="dup",
            requested_at="2026-04-13T10:00:00+09:00",
        )
        second = order_repo.create(
            client_order_id="COID_DUP_B",
            symbol="000660",
            side="buy",
            qty=1,
            price=80000,
            order_type="LIMIT",
            strategy_name="dup",
            requested_at="2026-04-13T10:00:01+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=first.client_order_id,
            kis_order_no="KIS-DUPLICATED",
            submitted_at="2026-04-13T10:00:02+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=second.client_order_id,
            kis_order_no="KIS-DUPLICATED",
            submitted_at="2026-04-13T10:00:03+09:00",
        )

    duplicate_rows = order_repo.list_by_kis_order_no("KIS-DUPLICATED")
    assert len(duplicate_rows) == 2
    with pytest.raises(RepositoryInvariantError):
        order_repo.get_by_kis_order_no("KIS-DUPLICATED")
