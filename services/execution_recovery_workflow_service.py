"""Read-only workflow for manual execution recovery review and draft export."""

from __future__ import annotations

from dataclasses import dataclass

from logger import get_logger
from services.manual_execution_import_draft_service import (
    ManualExecutionImportDraftResult,
    ManualExecutionImportDraftService,
)
from services.manual_execution_recovery_review_service import (
    ManualExecutionRecoveryReviewResult,
    ManualExecutionRecoveryReviewService,
)


_log = get_logger("order")


@dataclass(frozen=True)
class ExecutionRecoveryWorkflowResult:
    trade_date: str
    include_reconcile_qty: bool
    draft_requested: bool
    review_result: ManualExecutionRecoveryReviewResult
    draft_result: ManualExecutionImportDraftResult | None


class ExecutionRecoveryWorkflowService:
    """Run review first, then optionally build an import draft from it."""

    def __init__(
        self,
        *,
        review_service: ManualExecutionRecoveryReviewService,
        draft_service: ManualExecutionImportDraftService,
    ) -> None:
        self._review_service = review_service
        self._draft_service = draft_service

    def run(
        self,
        *,
        trade_date: str,
        create_draft: bool = False,
        include_reconcile_qty: bool = False,
    ) -> ExecutionRecoveryWorkflowResult:
        _log.info(
            f"[execution_recovery_workflow:start] trade_date={trade_date} "
            f"create_draft={create_draft} "
            f"include_reconcile_qty={include_reconcile_qty}"
        )

        review_result = self._review_service.build_review(trade_date=trade_date)
        draft_result = None
        if create_draft:
            draft_result = self._draft_service.build_from_review_result(
                review_result=review_result,
                include_reconcile_qty=include_reconcile_qty,
            )

        _log.info(
            f"[execution_recovery_workflow:done] trade_date={trade_date} "
            f"review_item_count={review_result.review_item_count} "
            f"draft_item_count={0 if draft_result is None else draft_result.exported_item_count}"
        )

        return ExecutionRecoveryWorkflowResult(
            trade_date=trade_date,
            include_reconcile_qty=include_reconcile_qty,
            draft_requested=create_draft,
            review_result=review_result,
            draft_result=draft_result,
        )
