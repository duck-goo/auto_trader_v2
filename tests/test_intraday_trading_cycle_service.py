"""Tests for IntradayTradingCycleService."""

from __future__ import annotations

from dataclasses import dataclass

from services import (
    BuySignalExecutionSettings,
    IntradayTradingCycleService,
    IntradayTriggerCombinedScanResult,
    IntradayTriggerStrategyStatus,
    SellSignalExecutionSettings,
    StaleBuyOrderCancelSettings,
)
from strategy import (
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
    Timing2LotExitSettings,
)


@dataclass(frozen=True)
class _FakeResult:
    name: str


class _FakeMaintenanceService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def run(self, *, trade_date: str, stale_cancel_settings, execute_changes: bool):
        self.calls.append(
            {
                "trade_date": trade_date,
                "stale_cancel_settings": stale_cancel_settings,
                "execute_changes": execute_changes,
            }
        )
        if self.error is not None:
            raise self.error
        return _FakeResult("maintenance")


class _FakeIntradayBarRefreshService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def refresh_live_positions(
        self,
        *,
        trade_date: str,
        bar_minutes: int,
        write: bool,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "bar_minutes": bar_minutes,
                "write": write,
            }
        )
        if self.error is not None:
            raise self.error
        return type(
            "_RefreshResult",
            (),
            {
                "position_count": 1,
                "candidate_count": 1,
                "preview_ready_count": 0,
                "refreshed_symbol_count": 1 if write else 0,
                "skipped_count": 0,
                "failed_count": 0,
                "refreshed_at": "2026-04-17T09:20:00+09:00",
            },
        )()


class _FakeSellScanService:
    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.calls: list[dict[str, object]] = []

    def scan(self, *, trade_date: str, settings, write_signals: bool, **kwargs):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "write_signals": write_signals,
                **kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return _FakeResult(self.name)


class _FakeSellExecutionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute_pending_signals(
        self,
        *,
        trade_date: str,
        settings,
        signal_limit: int,
        execute_orders: bool,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "signal_limit": signal_limit,
                "execute_orders": execute_orders,
            }
        )
        return _FakeResult("sell_execution")


class _FakeBuyTriggerScanService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return IntradayTriggerCombinedScanResult(
            trade_date=kwargs["trade_date"],
            timing1=IntradayTriggerStrategyStatus(
                outcome="COMPLETED",
                reason=None,
                result=_FakeResult("timing1"),
            ),
            timing2=IntradayTriggerStrategyStatus(
                outcome="COMPLETED",
                reason=None,
                result=_FakeResult("timing2"),
            ),
        )


class _FakeBuyExecutionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute_pending_signals(
        self,
        *,
        trade_date: str,
        settings,
        signal_limit: int,
        execute_orders: bool,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "signal_limit": signal_limit,
                "execute_orders": execute_orders,
            }
        )
        return _FakeResult("buy_execution")


def test_run_cycle_skips_order_execution_when_maintenance_fails():
    maintenance_service = _FakeMaintenanceService(error=RuntimeError("maintenance down"))
    refresh_service = _FakeIntradayBarRefreshService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        buy_trigger_scan_service=buy_trigger_scan_service,
        buy_signal_execution_service=buy_execution_service,
    )

    result = service.run_cycle(
        trade_date="2026-04-17",
        execute_actions=True,
        maintenance_settings=StaleBuyOrderCancelSettings(timeout_seconds=300),
        sell_exit_settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
        sell_macd_settings=SellMacdExitSettings(),
        sell_macd_history_limit=300,
        timing2_lot_exit_settings=Timing2LotExitSettings(),
        sell_execution_settings=SellSignalExecutionSettings(),
        sell_signal_limit=200,
        run_timing1=True,
        run_timing2=True,
        timing1_settings=Timing1IntradayTriggerSettings(),
        timing1_daily_count=5,
        timing2_settings=Timing2IntradayTriggerSettings(),
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.maintenance.outcome == "FAILED"
    assert result.intraday_bar_refresh.outcome == "COMPLETED"
    assert refresh_service.calls[0]["write"] is True
    assert sell_exit_scan_service.calls[0]["write_signals"] is False
    assert sell_macd_scan_service.calls[0]["write_signals"] is False
    assert timing2_lot_exit_scan_service.calls[0]["write_signals"] is False
    assert buy_trigger_scan_service.calls[0]["write_timing1_signals"] is False
    assert buy_trigger_scan_service.calls[0]["write_timing2_signals"] is False
    assert result.sell_execution.outcome == "SKIPPED"
    assert result.buy_execution.outcome == "SKIPPED"
    assert sell_execution_service.calls == []
    assert buy_execution_service.calls == []


def test_run_cycle_allows_sell_execution_when_macd_scan_fails():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService(
        "sell_macd_scan",
        error=RuntimeError("macd unavailable"),
    )
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        buy_trigger_scan_service=buy_trigger_scan_service,
        buy_signal_execution_service=buy_execution_service,
    )

    result = service.run_cycle(
        trade_date="2026-04-17",
        execute_actions=True,
        maintenance_settings=StaleBuyOrderCancelSettings(timeout_seconds=300),
        sell_exit_settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
        sell_macd_settings=SellMacdExitSettings(),
        sell_macd_history_limit=300,
        timing2_lot_exit_settings=Timing2LotExitSettings(),
        sell_execution_settings=SellSignalExecutionSettings(),
        sell_signal_limit=200,
        run_timing1=True,
        run_timing2=True,
        timing1_settings=Timing1IntradayTriggerSettings(),
        timing1_daily_count=5,
        timing2_settings=Timing2IntradayTriggerSettings(),
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_macd_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert len(sell_execution_service.calls) == 1


def test_run_cycle_skips_order_execution_when_timing2_lot_exit_scan_fails():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService(
        "timing2_lot_exit_scan",
        error=RuntimeError("lot scan unavailable"),
    )
    sell_execution_service = _FakeSellExecutionService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        buy_trigger_scan_service=buy_trigger_scan_service,
        buy_signal_execution_service=buy_execution_service,
    )

    result = service.run_cycle(
        trade_date="2026-04-17",
        execute_actions=True,
        maintenance_settings=StaleBuyOrderCancelSettings(timeout_seconds=300),
        sell_exit_settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
        sell_macd_settings=SellMacdExitSettings(),
        sell_macd_history_limit=300,
        timing2_lot_exit_settings=Timing2LotExitSettings(),
        sell_execution_settings=SellSignalExecutionSettings(),
        sell_signal_limit=200,
        run_timing1=True,
        run_timing2=True,
        timing1_settings=Timing1IntradayTriggerSettings(),
        timing1_daily_count=5,
        timing2_settings=Timing2IntradayTriggerSettings(),
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.timing2_lot_exit_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "SKIPPED"
    assert result.buy_execution.outcome == "SKIPPED"
    assert sell_execution_service.calls == []
    assert buy_execution_service.calls == []
