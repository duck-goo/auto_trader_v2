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
from services.stale_execution_signal_cleanup_service import (
    STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
    STRATEGY_NAME_STALE_SELL_SIGNAL_CLEANUP_AUDIT,
    StaleExecutionSignalCleanupResult,
    StaleExecutionSignalCleanupService,
    StaleExecutionSignalCleanupSettings,
)
from services.timing1_intraday_trigger_service import (
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
)
from services.timing2_30s_trigger_service import (
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
)
from services.timing2_intraday_trigger_service import (
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
)
from services.sell_exit_scan_service import (
    STRATEGY_NAME_SELL_STOP_LOSS,
    STRATEGY_NAME_SELL_TAKE_PROFIT,
)
from services.sell_macd_exit_scan_service import (
    STRATEGY_NAME_SELL_MACD_DECREASE,
)
from services.timing2_lot_exit_scan_service import (
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
)
from services.unresolved_order_sync_service import (
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
    UnresolvedOrderSyncService,
)


_log = get_logger("order")

_BUY_SIGNAL_CLEANUP_STRATEGIES = frozenset(
    {
        STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
        STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
        STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    }
)
_SELL_SIGNAL_CLEANUP_STRATEGIES = frozenset(
    {
        STRATEGY_NAME_SELL_STOP_LOSS,
        STRATEGY_NAME_SELL_TAKE_PROFIT,
        STRATEGY_NAME_SELL_MACD_DECREASE,
        STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
        STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
        STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
    }
)


@dataclass(frozen=True)
class OrderMaintenanceResult:
    trade_date: str
    execute_changes: bool
    sync_result: UnresolvedOrderSyncResult
    execution_recovery_result: ExecutionRecoveryFinalizeResult
    stale_buy_cancel_result: StaleBuyOrderCancelResult
    stale_sell_cancel_result: StaleSellOrderCancelResult
    stale_buy_signal_cleanup_result: StaleExecutionSignalCleanupResult | None
    stale_sell_signal_cleanup_result: StaleExecutionSignalCleanupResult | None
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
        stale_signal_cleanup_service: StaleExecutionSignalCleanupService | None = None,
    ) -> None:
        self._sync_service = sync_service
        self._execution_recovery_service = execution_recovery_service
        self._stale_buy_cancel_service = stale_buy_cancel_service
        self._stale_sell_cancel_service = stale_sell_cancel_service
        self._stale_signal_cleanup_service = stale_signal_cleanup_service

    def run(
        self,
        *,
        trade_date: str,
        stale_cancel_settings: StaleBuyOrderCancelSettings,
        buy_signal_cleanup_settings: StaleExecutionSignalCleanupSettings | None = None,
        sell_signal_cleanup_settings: StaleExecutionSignalCleanupSettings | None = None,
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
        stale_buy_signal_cleanup_result = self._cleanup_stale_signals(
            trade_date=trade_date,
            strategy_names=_BUY_SIGNAL_CLEANUP_STRATEGIES,
            audit_strategy_name=STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
            settings=buy_signal_cleanup_settings,
            execute_changes=execute_changes,
        )
        stale_sell_signal_cleanup_result = self._cleanup_stale_signals(
            trade_date=trade_date,
            strategy_names=_SELL_SIGNAL_CLEANUP_STRATEGIES,
            audit_strategy_name=STRATEGY_NAME_STALE_SELL_SIGNAL_CLEANUP_AUDIT,
            settings=sell_signal_cleanup_settings,
            execute_changes=execute_changes,
        )

        _log.info(
            f"[order_maintenance:done] trade_date={trade_date} "
            f"initial_recovery_required_count="
            f"{sync_result.execution_recovery_required_count} "
            f"manual_recovery_required_count={len(manual_recovery_required_ids)} "
            f"synced_count={sync_result.synced_count} "
            f"recovered_count={execution_recovery_result.recovered_count} "
            f"buy_cancelled_count={stale_buy_cancel_result.cancelled_count} "
            f"sell_cancelled_count={stale_sell_cancel_result.cancelled_count} "
            f"buy_signal_cleaned_count="
            f"{0 if stale_buy_signal_cleanup_result is None else stale_buy_signal_cleanup_result.cleaned_count} "
            f"sell_signal_cleaned_count="
            f"{0 if stale_sell_signal_cleanup_result is None else stale_sell_signal_cleanup_result.cleaned_count}"
        )

        return OrderMaintenanceResult(
            trade_date=trade_date,
            execute_changes=execute_changes,
            sync_result=sync_result,
            execution_recovery_result=execution_recovery_result,
            stale_buy_cancel_result=stale_buy_cancel_result,
            stale_sell_cancel_result=stale_sell_cancel_result,
            stale_buy_signal_cleanup_result=stale_buy_signal_cleanup_result,
            stale_sell_signal_cleanup_result=stale_sell_signal_cleanup_result,
            manual_recovery_required_client_order_ids=manual_recovery_required_ids,
        )

    def _cleanup_stale_signals(
        self,
        *,
        trade_date: str,
        strategy_names: frozenset[str],
        audit_strategy_name: str,
        settings: StaleExecutionSignalCleanupSettings | None,
        execute_changes: bool,
    ) -> StaleExecutionSignalCleanupResult | None:
        if settings is None or self._stale_signal_cleanup_service is None:
            return None

        return self._stale_signal_cleanup_service.cleanup_stale_signals(
            trade_date=trade_date,
            strategy_names=strategy_names,
            audit_strategy_name=audit_strategy_name,
            settings=settings,
            execute_cleanup=execute_changes,
        )
