"""Tests for ManualExecutionImportService."""

from __future__ import annotations

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import DbOrderStatus, ExecutionRepository, OrderRepository, PositionRepository
from services import (
    ManualExecutionImportItem,
    ManualExecutionImportOutcome,
    ManualExecutionImportService,
)


def _seed_submitted_order(conn, order_repo: OrderRepository, *, client_order_id: str):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side="buy",
            qty=2,
            price=71_000,
            order_type="LIMIT",
            strategy_name="seed",
            requested_at="2026-04-17T09:01:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no="KIS-005930",
            submitted_at="2026-04-17T09:01:01+09:00",
        )


def test_execute_import_inserts_execution_and_updates_order_and_position(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_IMPORT_OK")

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_OK",
                    kis_exec_no="EXEC-1",
                    qty=2,
                    price=70_500,
                    executed_at="2026-04-17T09:05:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.imported_count == 1
        assert result.candidates[0].outcome == ManualExecutionImportOutcome.IMPORTED

        order_row = order_repo.get_by_client_order_id("COID_IMPORT_OK")
        assert order_row.status == DbOrderStatus.FILLED
        assert order_row.filled_qty == 2
        assert order_row.avg_fill_price == 70_500

        executions = execution_repo.list_by_order(order_row.id)
        assert len(executions) == 1
        assert executions[0].kis_exec_no == "EXEC-1"

        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 2
        assert position.avg_price == 70_500
    finally:
        conn.close()


def test_duplicate_exec_no_is_skipped_without_reapplying_position(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_IMPORT_DUP")
        order_row = order_repo.get_by_client_order_id("COID_IMPORT_DUP")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="EXEC-DUP",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:03:00+09:00",
            ) is True
            position_repo.apply_execution(
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:03:00+09:00",
            )
            order_repo.sync_execution_summary(
                client_order_id="COID_IMPORT_DUP",
                closed_at="2026-04-17T09:03:00+09:00",
            )

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
        )

        result = service.import_items(
            items=[
                ManualExecutionImportItem(
                    client_order_id="COID_IMPORT_DUP",
                    kis_exec_no="EXEC-DUP",
                    qty=1,
                    price=70_000,
                    executed_at="2026-04-17T09:03:00+09:00",
                )
            ],
            execute_import=True,
        )

        assert result.skipped_count == 1
        assert result.candidates[0].outcome == ManualExecutionImportOutcome.SKIPPED

        executions = execution_repo.list_by_order(order_row.id)
        assert len(executions) == 1
        position = position_repo.get("005930")
        assert position is not None
        assert position.qty == 1
    finally:
        conn.close()
