"""Tests for ExecutionRecoveryFinalizeService."""

from __future__ import annotations

from datetime import datetime

import pytz

from services import (
    ExecutionRecoveryFinalizeOutcome,
    ExecutionRecoveryFinalizeService,
    UnresolvedOrderSyncAction,
    UnresolvedOrderSyncCandidate,
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    ExecutionRepository,
    OrderRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class _FakeSyncService:
    def __init__(self, result: UnresolvedOrderSyncResult) -> None:
        self.result = result

    def sync_unresolved_orders(self, *, trade_date: str, execute_sync: bool):
        assert trade_date == TRADE_DATE
        assert execute_sync is False
        return self.result


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 10, 15, 0))


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
            requested_at="2026-04-17T09:05:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no="KIS-005930",
            submitted_at="2026-04-17T09:05:01+09:00",
        )


def _sync_result(client_order_id: str) -> UnresolvedOrderSyncResult:
    return UnresolvedOrderSyncResult(
        trade_date=TRADE_DATE,
        scanned_at="2026-04-17T10:10:00+09:00",
        execute_sync=False,
        unresolved_order_count=1,
        candidate_count=1,
        preview_ready_count=0,
        skipped_count=0,
        synced_count=0,
        execution_recovery_required_count=1,
        acted_count=0,
        candidates=(
            UnresolvedOrderSyncCandidate(
                client_order_id=client_order_id,
                symbol="005930",
                status_before="SUBMITTED",
                status_after=None,
                kis_order_no="KIS-005930",
                action=UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED,
                outcome=UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message="recover first",
                broker_status="filled",
                broker_filled_qty=2,
                acted=False,
            ),
        ),
    )


def test_execute_finalizes_filled_order_from_local_execution_rows(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_RECOVER_OK")
        order_row = order_repo.get_by_client_order_id("COID_RECOVER_OK")

        with transaction(conn):
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E1",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:06:00+09:00",
            ) is True
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E2",
                symbol="005930",
                side="buy",
                qty=1,
                price=71_000,
                executed_at="2026-04-17T09:07:00+09:00",
            ) is True

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_RECOVER_OK")),
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=True,
        )

        assert result.recovered_count == 1
        assert result.candidates[0].outcome == ExecutionRecoveryFinalizeOutcome.RECOVERED
        updated = order_repo.get_by_client_order_id("COID_RECOVER_OK")
        assert updated.status == DbOrderStatus.FILLED
        assert updated.filled_qty == 2
        assert updated.avg_fill_price == 70_500
    finally:
        conn.close()


def test_missing_local_execution_rows_stays_manual_recovery_required(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        _seed_submitted_order(conn, order_repo, client_order_id="COID_RECOVER_MANUAL")

        service = ExecutionRecoveryFinalizeService(
            conn=conn,
            order_repo=order_repo,
            execution_repo=execution_repo,
            sync_service=_FakeSyncService(_sync_result("COID_RECOVER_MANUAL")),
            now_fn=_fixed_now,
        )

        result = service.finalize_recovery(
            trade_date=TRADE_DATE,
            execute_recovery=False,
        )

        assert result.manual_recovery_required_count == 1
        assert (
            result.candidates[0].outcome
            == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
        )
        assert result.candidates[0].reason_code == "LOCAL_EXECUTIONS_MISSING"
        assert (
            order_repo.get_by_client_order_id("COID_RECOVER_MANUAL").status
            == DbOrderStatus.SUBMITTED
        )
    finally:
        conn.close()
