"""Tests for ManualExecutionRecoveryReviewService."""

from __future__ import annotations

from datetime import datetime

import pytz

from services import (
    ExecutionRecoveryFinalizeAction,
    ExecutionRecoveryFinalizeCandidate,
    ExecutionRecoveryFinalizeOutcome,
    ExecutionRecoveryFinalizeResult,
    ManualExecutionRecoveryRecommendation,
    ManualExecutionRecoveryReviewService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class _FakeExecutionRecoveryService:
    def __init__(self, result: ExecutionRecoveryFinalizeResult) -> None:
        self.result = result

    def finalize_recovery(self, *, trade_date: str, execute_recovery: bool):
        assert trade_date == TRADE_DATE
        assert execute_recovery is False
        return self.result


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 11, 0, 0))


def test_build_review_includes_order_execution_and_position_context(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        execution_repo = ExecutionRepository(conn)
        position_repo = PositionRepository(conn)

        with transaction(conn):
            order_repo.create(
                client_order_id="COID_REVIEW",
                symbol="005930",
                side="buy",
                qty=2,
                price=71_000,
                order_type="LIMIT",
                strategy_name="seed",
                requested_at="2026-04-17T09:01:00+09:00",
            )
            order_repo.mark_submitted(
                client_order_id="COID_REVIEW",
                kis_order_no="KIS-REVIEW",
                submitted_at="2026-04-17T09:01:01+09:00",
            )
            order_row = order_repo.get_by_client_order_id("COID_REVIEW")
            assert execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no="E-REVIEW-1",
                symbol="005930",
                side="buy",
                qty=1,
                price=70_000,
                executed_at="2026-04-17T09:02:00+09:00",
            ) is True
            position_repo.upsert_from_broker(
                symbol="005930",
                qty=1,
                avg_price=70_000,
                updated_at=_fixed_now().isoformat(),
            )

        recovery_result = ExecutionRecoveryFinalizeResult(
            trade_date=TRADE_DATE,
            scanned_at=_fixed_now().isoformat(),
            execute_recovery=False,
            sync_result=None,  # type: ignore[arg-type]
            candidate_count=1,
            preview_ready_count=0,
            recovered_count=0,
            manual_recovery_required_count=1,
            skipped_count=0,
            acted_count=0,
            candidates=(
                ExecutionRecoveryFinalizeCandidate(
                    client_order_id="COID_REVIEW",
                    symbol="005930",
                    status_before=DbOrderStatus.SUBMITTED.value,
                    status_after=None,
                    broker_status="filled",
                    broker_filled_qty=2,
                    local_execution_count=1,
                    local_filled_qty=1,
                    local_avg_fill_price=70_000,
                    action=ExecutionRecoveryFinalizeAction.NONE,
                    outcome=ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED,
                    reason_code="LOCAL_BROKER_FILLED_QTY_MISMATCH",
                    reason_message="recover first",
                    acted=False,
                ),
            ),
        )

        service = ManualExecutionRecoveryReviewService(
            order_repo=order_repo,
            execution_repo=execution_repo,
            position_repo=position_repo,
            execution_recovery_service=_FakeExecutionRecoveryService(
                recovery_result
            ),
        )

        result = service.build_review(trade_date=TRADE_DATE)

        assert result.review_item_count == 1
        item = result.items[0]
        assert item.client_order_id == "COID_REVIEW"
        assert item.current_position_qty == 1
        assert item.local_execution_count == 1
        assert len(item.executions) == 1
        assert (
            item.recommendation
            == ManualExecutionRecoveryRecommendation.RECONCILE_EXECUTION_QTY
        )
    finally:
        conn.close()
