"""Tests for ExecutionRecoveryWorkflowService."""

from __future__ import annotations

from dataclasses import dataclass

from services import ExecutionRecoveryWorkflowService


@dataclass(frozen=True)
class _DummyReviewResult:
    trade_date: str
    review_item_count: int


@dataclass(frozen=True)
class _DummyDraftResult:
    exported_item_count: int


class _FakeReviewService:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def build_review(self, *, trade_date: str):
        self.calls.append({"trade_date": trade_date})
        return self.result


class _FakeDraftService:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def build_from_review_result(self, *, review_result, include_reconcile_qty: bool):
        self.calls.append(
            {
                "review_result": review_result,
                "include_reconcile_qty": include_reconcile_qty,
            }
        )
        return self.result


def test_workflow_calls_draft_service_only_when_requested():
    review_result = _DummyReviewResult(trade_date="2026-04-17", review_item_count=2)
    draft_result = _DummyDraftResult(exported_item_count=1)
    review_service = _FakeReviewService(review_result)
    draft_service = _FakeDraftService(draft_result)
    service = ExecutionRecoveryWorkflowService(
        review_service=review_service,
        draft_service=draft_service,
    )

    result = service.run(
        trade_date="2026-04-17",
        create_draft=True,
        include_reconcile_qty=True,
    )

    assert review_service.calls == [{"trade_date": "2026-04-17"}]
    assert draft_service.calls == [
        {
            "review_result": review_result,
            "include_reconcile_qty": True,
        }
    ]
    assert result.review_result is review_result
    assert result.draft_result is draft_result
