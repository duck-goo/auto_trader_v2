"""Build a human-readable review report for manual execution recovery."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from logger import get_logger
from services.errors import ServiceError
from services.execution_recovery_finalize_service import (
    ExecutionRecoveryFinalizeOutcome,
    ExecutionRecoveryFinalizeResult,
    ExecutionRecoveryFinalizeService,
)
from storage.repositories import ExecutionRepository, OrderRepository, PositionRepository


_log = get_logger("order")


class ManualExecutionRecoveryRecommendation(str, enum.Enum):
    IMPORT_MISSING_EXECUTIONS = "IMPORT_MISSING_EXECUTIONS"
    RECONCILE_EXECUTION_QTY = "RECONCILE_EXECUTION_QTY"
    VERIFY_TERMINAL_STATUS = "VERIFY_TERMINAL_STATUS"
    REVIEW_ORDER_MANUALLY = "REVIEW_ORDER_MANUALLY"


@dataclass(frozen=True)
class ManualExecutionRecoveryExecutionDetail:
    kis_exec_no: str
    qty: int
    price: int
    executed_at: str


@dataclass(frozen=True)
class ManualExecutionRecoveryReviewItem:
    client_order_id: str
    symbol: str
    side: str
    order_qty: int
    order_price: int
    order_type: str
    order_status: str
    kis_order_no: str | None
    requested_at: str
    submitted_at: str | None
    closed_at: str | None
    broker_status: str | None
    broker_filled_qty: int | None
    local_execution_count: int
    local_filled_qty: int
    local_avg_fill_price: int
    current_position_qty: int
    current_position_avg_price: int
    recommendation: ManualExecutionRecoveryRecommendation
    reason_code: str | None
    reason_message: str | None
    executions: tuple[ManualExecutionRecoveryExecutionDetail, ...]


@dataclass(frozen=True)
class ManualExecutionRecoveryReviewResult:
    trade_date: str
    recovery_result: ExecutionRecoveryFinalizeResult
    review_item_count: int
    items: tuple[ManualExecutionRecoveryReviewItem, ...]


class ManualExecutionRecoveryReviewService:
    """Prepare review items for orders that still need manual recovery."""

    def __init__(
        self,
        *,
        order_repo: OrderRepository,
        execution_repo: ExecutionRepository,
        position_repo: PositionRepository,
        execution_recovery_service: ExecutionRecoveryFinalizeService,
    ) -> None:
        self._order_repo = order_repo
        self._execution_repo = execution_repo
        self._position_repo = position_repo
        self._execution_recovery_service = execution_recovery_service

    def build_review(
        self,
        *,
        trade_date: str,
        recovery_result: ExecutionRecoveryFinalizeResult | None = None,
    ) -> ManualExecutionRecoveryReviewResult:
        actual_recovery_result = (
            recovery_result
            or self._execution_recovery_service.finalize_recovery(
                trade_date=trade_date,
                execute_recovery=False,
            )
        )
        items: list[ManualExecutionRecoveryReviewItem] = []

        for candidate in actual_recovery_result.candidates:
            if (
                candidate.outcome
                != ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
            ):
                continue

            order_row = self._order_repo.get_by_client_order_id(
                candidate.client_order_id
            )
            if order_row is None:
                raise ServiceError(
                    "Manual recovery review order row not found: "
                    f"client_order_id={candidate.client_order_id}"
                )

            execution_rows = self._execution_repo.list_by_order(order_row.id)
            position_row = self._position_repo.get(order_row.symbol)
            recommendation = self._recommend(candidate.reason_code)

            items.append(
                ManualExecutionRecoveryReviewItem(
                    client_order_id=order_row.client_order_id,
                    symbol=order_row.symbol,
                    side=order_row.side,
                    order_qty=order_row.qty,
                    order_price=order_row.price,
                    order_type=order_row.order_type,
                    order_status=order_row.status.value,
                    kis_order_no=order_row.kis_order_no,
                    requested_at=order_row.requested_at,
                    submitted_at=order_row.submitted_at,
                    closed_at=order_row.closed_at,
                    broker_status=candidate.broker_status,
                    broker_filled_qty=candidate.broker_filled_qty,
                    local_execution_count=candidate.local_execution_count,
                    local_filled_qty=candidate.local_filled_qty,
                    local_avg_fill_price=candidate.local_avg_fill_price,
                    current_position_qty=0 if position_row is None else position_row.qty,
                    current_position_avg_price=(
                        0 if position_row is None else position_row.avg_price
                    ),
                    recommendation=recommendation,
                    reason_code=candidate.reason_code,
                    reason_message=candidate.reason_message,
                    executions=tuple(
                        ManualExecutionRecoveryExecutionDetail(
                            kis_exec_no=row.kis_exec_no,
                            qty=row.qty,
                            price=row.price,
                            executed_at=row.executed_at,
                        )
                        for row in execution_rows
                    ),
                )
            )

        _log.info(
            f"[manual_execution_recovery_review:done] trade_date={trade_date} "
            f"review_item_count={len(items)}"
        )

        return ManualExecutionRecoveryReviewResult(
            trade_date=trade_date,
            recovery_result=actual_recovery_result,
            review_item_count=len(items),
            items=tuple(items),
        )

    @staticmethod
    def _recommend(
        reason_code: str | None,
    ) -> ManualExecutionRecoveryRecommendation:
        if reason_code == "LOCAL_EXECUTIONS_MISSING":
            return ManualExecutionRecoveryRecommendation.IMPORT_MISSING_EXECUTIONS
        if reason_code == "LOCAL_BROKER_FILLED_QTY_MISMATCH":
            return ManualExecutionRecoveryRecommendation.RECONCILE_EXECUTION_QTY
        if reason_code in {
            "FILLED_QTY_DOES_NOT_MATCH_ORDER_QTY",
            "PARTIAL_QTY_IS_NOT_STRICTLY_BETWEEN_0_AND_ORDER_QTY",
            "CANCELLED_QTY_MUST_BE_BELOW_ORDER_QTY",
        }:
            return ManualExecutionRecoveryRecommendation.VERIFY_TERMINAL_STATUS
        return ManualExecutionRecoveryRecommendation.REVIEW_ORDER_MANUALLY
