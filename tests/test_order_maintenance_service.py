"""Tests for OrderMaintenanceService."""

from __future__ import annotations

from services import (
    ExecutionRecoveryFinalizeAction,
    ExecutionRecoveryFinalizeCandidate,
    ExecutionRecoveryFinalizeOutcome,
    ExecutionRecoveryFinalizeResult,
    OrderMaintenanceService,
    StaleBuyOrderCancelOutcome,
    StaleBuyOrderCancelResult,
    StaleBuyOrderCancelSettings,
    StaleSellOrderCancelOutcome,
    StaleSellOrderCancelResult,
    StaleSellOrderCancelSettings,
    UnresolvedOrderSyncAction,
    UnresolvedOrderSyncCandidate,
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
)


class _FakeSyncService:
    def __init__(self, result: UnresolvedOrderSyncResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def sync_unresolved_orders(self, *, trade_date: str, execute_sync: bool):
        self.calls.append(
            {
                "trade_date": trade_date,
                "execute_sync": execute_sync,
            }
        )
        return self.result


class _FakeStaleCancelService:
    def __init__(self, result: StaleBuyOrderCancelResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def cancel_stale_orders(
        self,
        *,
        trade_date: str,
        settings: StaleBuyOrderCancelSettings,
        execute_cancels: bool,
        skip_client_order_ids,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "execute_cancels": execute_cancels,
                "skip_client_order_ids": set(skip_client_order_ids),
            }
        )
        return self.result


class _FakeStaleSellCancelService:
    def __init__(self, result: StaleSellOrderCancelResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def cancel_stale_orders(
        self,
        *,
        trade_date: str,
        settings,
        execute_cancels: bool,
        skip_client_order_ids,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "execute_cancels": execute_cancels,
                "skip_client_order_ids": set(skip_client_order_ids),
            }
        )
        return self.result


class _FakeExecutionRecoveryService:
    def __init__(self, result: ExecutionRecoveryFinalizeResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def finalize_recovery(
        self,
        *,
        trade_date: str,
        execute_recovery: bool,
        sync_result: UnresolvedOrderSyncResult,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "execute_recovery": execute_recovery,
                "sync_result": sync_result,
            }
        )
        return self.result


def test_order_maintenance_passes_only_manual_recovery_ids_to_stale_cancel():
    sync_result = UnresolvedOrderSyncResult(
        trade_date="2026-04-16",
        scanned_at="2026-04-16T10:00:00+09:00",
        execute_sync=False,
        unresolved_order_count=2,
        candidate_count=2,
        preview_ready_count=0,
        skipped_count=0,
        synced_count=0,
        execution_recovery_required_count=2,
        acted_count=0,
        candidates=(
            UnresolvedOrderSyncCandidate(
                client_order_id="B-2",
                symbol="000660",
                status_before="SUBMITTED",
                status_after=None,
                kis_order_no="K2",
                action=UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED,
                outcome=UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message="recover first",
                broker_status="FILLED",
                broker_filled_qty=1,
                acted=False,
            ),
            UnresolvedOrderSyncCandidate(
                client_order_id="A-1",
                symbol="005930",
                status_before="SUBMITTED",
                status_after=None,
                kis_order_no="K1",
                action=UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED,
                outcome=UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message="recover first",
                broker_status="PARTIAL",
                broker_filled_qty=1,
                acted=False,
            ),
        ),
    )
    cancel_result = StaleBuyOrderCancelResult(
        trade_date="2026-04-16",
        scanned_at="2026-04-16T10:00:01+09:00",
        execute_cancels=False,
        unresolved_order_count=2,
        candidate_count=2,
        preview_ready_count=0,
        skipped_count=2,
        cancelled_count=0,
        rejected_count=0,
        unknown_count=0,
        blocked_count=0,
        acted_count=0,
        candidates=(),
    )
    sell_cancel_result = StaleSellOrderCancelResult(
        trade_date="2026-04-16",
        scanned_at="2026-04-16T10:00:01+09:00",
        execute_cancels=False,
        unresolved_order_count=2,
        candidate_count=2,
        preview_ready_count=0,
        skipped_count=2,
        cancelled_count=0,
        rejected_count=0,
        unknown_count=0,
        blocked_count=0,
        acted_count=0,
        candidates=(),
    )
    recovery_result = ExecutionRecoveryFinalizeResult(
        trade_date="2026-04-16",
        scanned_at="2026-04-16T10:00:01+09:00",
        execute_recovery=False,
        sync_result=sync_result,
        candidate_count=2,
        preview_ready_count=1,
        recovered_count=0,
        manual_recovery_required_count=1,
        skipped_count=0,
        acted_count=0,
        candidates=(
            ExecutionRecoveryFinalizeCandidate(
                client_order_id="A-1",
                symbol="005930",
                status_before="SUBMITTED",
                status_after="FILLED",
                broker_status="filled",
                broker_filled_qty=1,
                local_execution_count=1,
                local_filled_qty=1,
                local_avg_fill_price=70_000,
                action=ExecutionRecoveryFinalizeAction.FINALIZE_FROM_LOCAL_EXECUTIONS,
                outcome=ExecutionRecoveryFinalizeOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                acted=False,
            ),
            ExecutionRecoveryFinalizeCandidate(
                client_order_id="B-2",
                symbol="000660",
                status_before="SUBMITTED",
                status_after=None,
                broker_status="partial",
                broker_filled_qty=1,
                local_execution_count=0,
                local_filled_qty=0,
                local_avg_fill_price=0,
                action=ExecutionRecoveryFinalizeAction.NONE,
                outcome=ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED,
                reason_code="LOCAL_EXECUTIONS_MISSING",
                reason_message="recover first",
                acted=False,
            ),
        ),
    )
    sync_service = _FakeSyncService(sync_result)
    execution_recovery_service = _FakeExecutionRecoveryService(recovery_result)
    stale_cancel_service = _FakeStaleCancelService(cancel_result)
    stale_sell_cancel_service = _FakeStaleSellCancelService(sell_cancel_result)
    service = OrderMaintenanceService(
        sync_service=sync_service,
        execution_recovery_service=execution_recovery_service,
        stale_buy_cancel_service=stale_cancel_service,
        stale_sell_cancel_service=stale_sell_cancel_service,
    )
    settings = StaleBuyOrderCancelSettings(timeout_seconds=300)

    result = service.run(
        trade_date="2026-04-16",
        stale_cancel_settings=settings,
        execute_changes=False,
    )

    assert sync_service.calls == [
        {
            "trade_date": "2026-04-16",
            "execute_sync": False,
        }
    ]
    assert execution_recovery_service.calls == [
        {
            "trade_date": "2026-04-16",
            "execute_recovery": False,
            "sync_result": sync_result,
        }
    ]
    assert stale_cancel_service.calls == [
        {
            "trade_date": "2026-04-16",
            "settings": settings,
            "execute_cancels": False,
            "skip_client_order_ids": {"B-2"},
        }
    ]
    assert stale_sell_cancel_service.calls == [
        {
            "trade_date": "2026-04-16",
            "settings": StaleSellOrderCancelSettings(timeout_seconds=300),
            "execute_cancels": False,
            "skip_client_order_ids": {"B-2"},
        }
    ]
    assert result.manual_recovery_required_client_order_ids == ("B-2",)
    assert result.execution_recovery_result.manual_recovery_required_count == 1
    assert result.stale_buy_cancel_result.skipped_count == 2
    assert result.stale_sell_cancel_result.skipped_count == 2
    assert result.sync_result.execution_recovery_required_count == 2
