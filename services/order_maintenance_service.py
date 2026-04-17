"""Safely orchestrate unresolved-order sync before stale buy cancellation."""

from __future__ import annotations

from dataclasses import dataclass

from services.execution_recovery_finalize_service import (
    ExecutionRecoveryFinalizeResult,
    ExecutionRecoveryFinalizeService,
    ExecutionRecoveryFinalizeOutcome,
)
from logger import get_logger
from services.stale_buy_order_cancel_service import (
    StaleBuyOrderCancelResult,
    StaleBuyOrderCancelService,
    StaleBuyOrderCancelSettings,
)
from services.stale_sell_order_cancel_service import (
    StaleSellOrderCancelResult,
    StaleSellOrderCancelService,
    StaleSellOrderCancelSettings,
)
from services.unresolved_order_sync_service import (
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
    UnresolvedOrderSyncService,
)


_log = get_logger("order")


@dataclass(frozen=True)
class OrderMaintenanceResult:
    trade_date: str
    execute_changes: bool
    sync_result: UnresolvedOrderSyncResult
    execution_recovery_result: ExecutionRecoveryFinalizeResult
    stale_buy_cancel_result: StaleBuyOrderCancelResult
    stale_sell_cancel_result: StaleSellOrderCancelResult
    manual_recovery_required_client_order_ids: tuple[str, ...]


class OrderMaintenanceService:
    """Run safe order-maintenance steps in a conservative order."""

    def __init__(
        self,
        *,
        sync_service: UnresolvedOrderSyncService,
        execution_recovery_service: ExecutionRecoveryFinalizeService,
        stale_buy_cancel_service: StaleBuyOrderCancelService,
        stale_sell_cancel_service: StaleSellOrderCancelService,
    ) -> None:
        self._sync_service = sync_service
        self._execution_recovery_service = execution_recovery_service
        self._stale_buy_cancel_service = stale_buy_cancel_service
        self._stale_sell_cancel_service = stale_sell_cancel_service

    def run(
        self,
        *,
        trade_date: str,
        stale_cancel_settings: StaleBuyOrderCancelSettings,
        execute_changes: bool = False,
    ) -> OrderMaintenanceResult:
        _log.info(
            f"[order_maintenance:start] trade_date={trade_date} "
            f"execute_changes={execute_changes}"
        )

        sync_result = self._sync_service.sync_unresolved_orders(
            trade_date=trade_date,
            execute_sync=execute_changes,
        )

        execution_recovery_result = self._execution_recovery_service.finalize_recovery(
            trade_date=trade_date,
            execute_recovery=execute_changes,
            sync_result=sync_result,
        )
        manual_recovery_required_ids = tuple(
            sorted(
                {
                    item.client_order_id
                    for item in execution_recovery_result.candidates
                    if item.outcome
                    == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
                }
            )
        )

        stale_buy_cancel_result = self._stale_buy_cancel_service.cancel_stale_orders(
            trade_date=trade_date,
            settings=stale_cancel_settings,
            execute_cancels=execute_changes,
            skip_client_order_ids=set(manual_recovery_required_ids),
        )
        stale_sell_cancel_result = self._stale_sell_cancel_service.cancel_stale_orders(
            trade_date=trade_date,
            settings=StaleSellOrderCancelSettings(
                timeout_seconds=stale_cancel_settings.timeout_seconds
            ),
            execute_cancels=execute_changes,
            skip_client_order_ids=set(manual_recovery_required_ids),
        )

        _log.info(
            f"[order_maintenance:done] trade_date={trade_date} "
            f"initial_recovery_required_count="
            f"{sync_result.execution_recovery_required_count} "
            f"manual_recovery_required_count={len(manual_recovery_required_ids)} "
            f"synced_count={sync_result.synced_count} "
            f"recovered_count={execution_recovery_result.recovered_count} "
            f"buy_cancelled_count={stale_buy_cancel_result.cancelled_count} "
            f"sell_cancelled_count={stale_sell_cancel_result.cancelled_count}"
        )

        return OrderMaintenanceResult(
            trade_date=trade_date,
            execute_changes=execute_changes,
            sync_result=sync_result,
            execution_recovery_result=execution_recovery_result,
            stale_buy_cancel_result=stale_buy_cancel_result,
            stale_sell_cancel_result=stale_sell_cancel_result,
            manual_recovery_required_client_order_ids=manual_recovery_required_ids,
        )
