"""Conservative orchestration for one intraday trading cycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.buy_signal_execution_service import (
    BuySignalExecutionService,
    BuySignalExecutionSettings,
)
from services.intraday_bar_15m_refresh_service import (
    IntradayBar15mRefreshService,
)
from services.intraday_trigger_combo_service import (
    IntradayTriggerCombinedScanResult,
    IntradayTriggerCombinedScanService,
)
from services.order_maintenance_service import OrderMaintenanceService
from services.sell_exit_scan_service import SellExitScanService
from services.sell_macd_exit_scan_service import SellMacdExitScanService
from services.sell_signal_execution_service import (
    SellSignalExecutionService,
    SellSignalExecutionSettings,
)
from services.stale_buy_order_cancel_service import StaleBuyOrderCancelSettings
from services.timing2_lot_exit_scan_service import Timing2LotExitScanService
from strategy import (
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2IntradayTriggerSettings,
)


@dataclass(frozen=True)
class IntradayTradingCycleStepStatus:
    outcome: str
    reason: str | None
    result: Any | None


@dataclass(frozen=True)
class IntradayTradingCycleResult:
    trade_date: str
    execute_actions: bool
    record_scan_signals: bool
    maintenance: IntradayTradingCycleStepStatus
    intraday_bar_refresh: IntradayTradingCycleStepStatus
    sell_exit_scan: IntradayTradingCycleStepStatus
    sell_macd_scan: IntradayTradingCycleStepStatus
    timing2_lot_exit_scan: IntradayTradingCycleStepStatus
    sell_execution: IntradayTradingCycleStepStatus
    buy_trigger_scan: IntradayTradingCycleStepStatus
    buy_execution: IntradayTradingCycleStepStatus


class IntradayTradingCycleService:
    """Run one conservative trading cycle using existing scan/execute services."""

    def __init__(
        self,
        *,
        order_maintenance_service: OrderMaintenanceService,
        intraday_bar_refresh_service: IntradayBar15mRefreshService,
        sell_exit_scan_service: SellExitScanService,
        sell_macd_scan_service: SellMacdExitScanService,
        timing2_lot_exit_scan_service: Timing2LotExitScanService,
        sell_signal_execution_service: SellSignalExecutionService,
        buy_trigger_scan_service: IntradayTriggerCombinedScanService,
        buy_signal_execution_service: BuySignalExecutionService,
    ) -> None:
        self._order_maintenance_service = order_maintenance_service
        self._intraday_bar_refresh_service = intraday_bar_refresh_service
        self._sell_exit_scan_service = sell_exit_scan_service
        self._sell_macd_scan_service = sell_macd_scan_service
        self._timing2_lot_exit_scan_service = timing2_lot_exit_scan_service
        self._sell_signal_execution_service = sell_signal_execution_service
        self._buy_trigger_scan_service = buy_trigger_scan_service
        self._buy_signal_execution_service = buy_signal_execution_service

    def run_cycle(
        self,
        *,
        trade_date: str,
        execute_actions: bool,
        maintenance_settings: StaleBuyOrderCancelSettings,
        sell_exit_settings: SellExitSettings,
        sell_macd_settings: SellMacdExitSettings,
        sell_macd_history_limit: int,
        timing2_lot_exit_settings: Timing2LotExitSettings,
        sell_execution_settings: SellSignalExecutionSettings,
        sell_signal_limit: int,
        run_timing1: bool,
        run_timing2: bool,
        timing1_settings: Timing1IntradayTriggerSettings,
        timing1_daily_count: int,
        timing2_settings: Timing2IntradayTriggerSettings,
        buy_execution_settings: BuySignalExecutionSettings,
        buy_signal_limit: int,
        record_scan_signals: bool | None = None,
    ) -> IntradayTradingCycleResult:
        normalized_record_scan_signals = (
            execute_actions
            if record_scan_signals is None
            else bool(record_scan_signals)
        )

        maintenance_status = self._run_maintenance(
            trade_date=trade_date,
            execute_actions=execute_actions,
            maintenance_settings=maintenance_settings,
        )
        intraday_bar_refresh_status = self._run_intraday_bar_refresh(
            trade_date=trade_date,
            write_bars=execute_actions,
        )
        allow_signal_writes = (
            normalized_record_scan_signals
            and maintenance_status.outcome != "FAILED"
        )

        sell_exit_scan_status = self._run_sell_exit_scan(
            trade_date=trade_date,
            settings=sell_exit_settings,
            write_signals=allow_signal_writes,
        )
        sell_macd_scan_status = self._run_sell_macd_scan(
            trade_date=trade_date,
            settings=sell_macd_settings,
            history_limit=sell_macd_history_limit,
            write_signals=allow_signal_writes,
            intraday_bar_refresh_status=intraday_bar_refresh_status,
        )
        timing2_lot_exit_scan_status = self._run_timing2_lot_exit_scan(
            trade_date=trade_date,
            settings=timing2_lot_exit_settings,
            write_signals=allow_signal_writes,
        )
        sell_execution_status = self._run_sell_execution(
            trade_date=trade_date,
            settings=sell_execution_settings,
            signal_limit=sell_signal_limit,
            execute_actions=execute_actions,
            maintenance_status=maintenance_status,
            sell_exit_scan_status=sell_exit_scan_status,
            timing2_lot_exit_scan_status=timing2_lot_exit_scan_status,
        )

        buy_trigger_scan_status = self._run_buy_trigger_scan(
            trade_date=trade_date,
            run_timing1=run_timing1,
            run_timing2=run_timing2,
            timing1_settings=timing1_settings,
            timing1_daily_count=timing1_daily_count,
            timing2_settings=timing2_settings,
            write_signals=allow_signal_writes,
        )
        buy_execution_status = self._run_buy_execution(
            trade_date=trade_date,
            settings=buy_execution_settings,
            signal_limit=buy_signal_limit,
            execute_actions=execute_actions,
            maintenance_status=maintenance_status,
            sell_exit_scan_status=sell_exit_scan_status,
            sell_macd_scan_status=sell_macd_scan_status,
            timing2_lot_exit_scan_status=timing2_lot_exit_scan_status,
            sell_execution_status=sell_execution_status,
            buy_trigger_scan_status=buy_trigger_scan_status,
        )

        return IntradayTradingCycleResult(
            trade_date=trade_date,
            execute_actions=execute_actions,
            record_scan_signals=allow_signal_writes,
            maintenance=maintenance_status,
            intraday_bar_refresh=intraday_bar_refresh_status,
            sell_exit_scan=sell_exit_scan_status,
            sell_macd_scan=sell_macd_scan_status,
            timing2_lot_exit_scan=timing2_lot_exit_scan_status,
            sell_execution=sell_execution_status,
            buy_trigger_scan=buy_trigger_scan_status,
            buy_execution=buy_execution_status,
        )

    def _run_maintenance(
        self,
        *,
        trade_date: str,
        execute_actions: bool,
        maintenance_settings: StaleBuyOrderCancelSettings,
    ) -> IntradayTradingCycleStepStatus:
        try:
            result = self._order_maintenance_service.run(
                trade_date=trade_date,
                stale_cancel_settings=maintenance_settings,
                execute_changes=execute_actions,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    def _run_timing2_lot_exit_scan(
        self,
        *,
        trade_date: str,
        settings: Timing2LotExitSettings,
        write_signals: bool,
    ) -> IntradayTradingCycleStepStatus:
        try:
            result = self._timing2_lot_exit_scan_service.scan(
                trade_date=trade_date,
                settings=settings,
                write_signals=write_signals,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    def _run_sell_exit_scan(
        self,
        *,
        trade_date: str,
        settings: SellExitSettings,
        write_signals: bool,
    ) -> IntradayTradingCycleStepStatus:
        try:
            result = self._sell_exit_scan_service.scan(
                trade_date=trade_date,
                settings=settings,
                write_signals=write_signals,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    def _run_intraday_bar_refresh(
        self,
        *,
        trade_date: str,
        write_bars: bool,
    ) -> IntradayTradingCycleStepStatus:
        try:
            result = self._intraday_bar_refresh_service.refresh_live_positions(
                trade_date=trade_date,
                bar_minutes=15,
                write=write_bars,
            )
        except Exception as exc:
            return self._failed(exc)

        reason = None
        if result.failed_count > 0:
            reason = (
                "Some symbols failed 15-minute bar refresh. "
                f"failed_count={result.failed_count}"
            )
        return IntradayTradingCycleStepStatus(
            outcome="COMPLETED",
            reason=reason,
            result=result,
        )

    def _run_sell_macd_scan(
        self,
        *,
        trade_date: str,
        settings: SellMacdExitSettings,
        history_limit: int,
        write_signals: bool,
        intraday_bar_refresh_status: IntradayTradingCycleStepStatus,
    ) -> IntradayTradingCycleStepStatus:
        if intraday_bar_refresh_status.outcome == "FAILED":
            return IntradayTradingCycleStepStatus(
                outcome="FAILED",
                reason="Skipped because 15-minute bar refresh failed.",
                result=None,
            )
        try:
            result = self._sell_macd_scan_service.scan(
                trade_date=trade_date,
                settings=settings,
                history_limit=history_limit,
                write_signals=write_signals,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    def _run_sell_execution(
        self,
        *,
        trade_date: str,
        settings: SellSignalExecutionSettings,
        signal_limit: int,
        execute_actions: bool,
        maintenance_status: IntradayTradingCycleStepStatus,
        sell_exit_scan_status: IntradayTradingCycleStepStatus,
        timing2_lot_exit_scan_status: IntradayTradingCycleStepStatus,
    ) -> IntradayTradingCycleStepStatus:
        if execute_actions and maintenance_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because order maintenance failed before order execution."
            )
        if sell_exit_scan_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because sell stop-loss/take-profit scan failed."
            )
        if timing2_lot_exit_scan_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because Timing2 lot-level sell scan failed."
            )

        try:
            result = self._sell_signal_execution_service.execute_pending_signals(
                trade_date=trade_date,
                settings=settings,
                signal_limit=signal_limit,
                execute_orders=execute_actions,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    def _run_buy_trigger_scan(
        self,
        *,
        trade_date: str,
        run_timing1: bool,
        run_timing2: bool,
        timing1_settings: Timing1IntradayTriggerSettings,
        timing1_daily_count: int,
        timing2_settings: Timing2IntradayTriggerSettings,
        write_signals: bool,
    ) -> IntradayTradingCycleStepStatus:
        try:
            result = self._buy_trigger_scan_service.scan(
                trade_date=trade_date,
                run_timing1=run_timing1,
                run_timing2=run_timing2,
                timing1_settings=timing1_settings,
                timing1_daily_count=timing1_daily_count,
                write_timing1_signals=write_signals,
                timing2_settings=timing2_settings,
                write_timing2_signals=write_signals,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._summarize_buy_trigger_scan(
            result=result,
            run_timing1=run_timing1,
            run_timing2=run_timing2,
        )

    def _run_buy_execution(
        self,
        *,
        trade_date: str,
        settings: BuySignalExecutionSettings,
        signal_limit: int,
        execute_actions: bool,
        maintenance_status: IntradayTradingCycleStepStatus,
        sell_exit_scan_status: IntradayTradingCycleStepStatus,
        sell_macd_scan_status: IntradayTradingCycleStepStatus,
        timing2_lot_exit_scan_status: IntradayTradingCycleStepStatus,
        sell_execution_status: IntradayTradingCycleStepStatus,
        buy_trigger_scan_status: IntradayTradingCycleStepStatus,
    ) -> IntradayTradingCycleStepStatus:
        if execute_actions and maintenance_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because order maintenance failed before order execution."
            )
        if execute_actions and sell_exit_scan_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because sell stop-loss/take-profit scan failed."
            )
        if execute_actions and sell_macd_scan_status.outcome == "FAILED":
            return self._skipped("Skipped because sell MACD scan failed.")
        if execute_actions and timing2_lot_exit_scan_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because Timing2 lot-level sell scan failed."
            )
        if execute_actions and sell_execution_status.outcome == "FAILED":
            return self._skipped(
                "Skipped because sell execution step failed in this cycle."
            )
        if buy_trigger_scan_status.outcome == "FAILED":
            return self._skipped("Skipped because buy trigger scan failed.")

        try:
            result = self._buy_signal_execution_service.execute_pending_signals(
                trade_date=trade_date,
                settings=settings,
                signal_limit=signal_limit,
                execute_orders=execute_actions,
            )
        except Exception as exc:
            return self._failed(exc)
        return self._completed(result)

    @staticmethod
    def _summarize_buy_trigger_scan(
        *,
        result: IntradayTriggerCombinedScanResult,
        run_timing1: bool,
        run_timing2: bool,
    ) -> IntradayTradingCycleStepStatus:
        requested_statuses = []
        if run_timing1:
            requested_statuses.append(result.timing1)
        if run_timing2:
            requested_statuses.append(result.timing2)

        if not requested_statuses:
            return IntradayTradingCycleStepStatus(
                outcome="SKIPPED",
                reason="No buy trigger scan strategy was enabled.",
                result=result,
            )

        if all(item.outcome == "SKIPPED" for item in requested_statuses):
            return IntradayTradingCycleStepStatus(
                outcome="SKIPPED",
                reason="All requested buy trigger scans were skipped.",
                result=result,
            )

        failure_messages = [
            item.reason
            for item in requested_statuses
            if item.outcome == "FAILED" and item.reason
        ]
        if failure_messages:
            return IntradayTradingCycleStepStatus(
                outcome="FAILED",
                reason="; ".join(failure_messages),
                result=result,
            )

        return IntradayTradingCycleStepStatus(
            outcome="COMPLETED",
            reason=None,
            result=result,
        )

    @staticmethod
    def _completed(result: object) -> IntradayTradingCycleStepStatus:
        return IntradayTradingCycleStepStatus(
            outcome="COMPLETED",
            reason=None,
            result=result,
        )

    @staticmethod
    def _skipped(reason: str) -> IntradayTradingCycleStepStatus:
        return IntradayTradingCycleStepStatus(
            outcome="SKIPPED",
            reason=reason,
            result=None,
        )

    @staticmethod
    def _failed(exc: Exception) -> IntradayTradingCycleStepStatus:
        return IntradayTradingCycleStepStatus(
            outcome="FAILED",
            reason=f"{type(exc).__name__}: {exc}",
            result=None,
        )
