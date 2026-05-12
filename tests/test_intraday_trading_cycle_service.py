"""Tests for IntradayTradingCycleService."""

from __future__ import annotations

from dataclasses import dataclass

from services import (
    BuySignalExecutionSettings,
    IntradayTradingCycleService,
    IntradayTradingCycleStepStatus,
    IntradayTriggerCombinedScanResult,
    IntradayTriggerStrategyStatus,
    MissingTiming2SetupSignalsError,
    SellSignalExecutionSettings,
    StaleBuyOrderCancelSettings,
    StaleExecutionSignalCleanupSettings,
)
from strategy import (
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2ThirtySecondTriggerSettings,
)


@dataclass(frozen=True)
class _FakeResult:
    name: str


class _FakeMaintenanceService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        *,
        trade_date: str,
        stale_cancel_settings,
        buy_signal_cleanup_settings=None,
        sell_signal_cleanup_settings=None,
        execute_changes: bool,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "stale_cancel_settings": stale_cancel_settings,
                "buy_signal_cleanup_settings": buy_signal_cleanup_settings,
                "sell_signal_cleanup_settings": sell_signal_cleanup_settings,
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


class _FakeTiming2PriceSampleCaptureService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def capture(self, *, trade_date: str, write_samples: bool, max_symbols: int):
        self.calls.append(
            {
                "trade_date": trade_date,
                "write_samples": write_samples,
                "max_symbols": max_symbols,
            }
        )
        if self.error is not None:
            raise self.error
        return type(
            "_CaptureResult",
            (),
            {
                "failed_count": 0,
                "setup_signal_count": max_symbols,
                "candidate_count": max_symbols,
                "skipped_by_limit_count": 0,
                "preview_ready_count": 0,
                "captured_count": max_symbols,
                "captured_at": "2026-04-17T09:00:00+09:00",
            },
        )()


class _FakeTiming2ThirtySecondBarBuildService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        *,
        trade_date: str,
        min_samples_per_bar: int,
        write_bars: bool,
    ):
        self.calls.append(
            {
                "trade_date": trade_date,
                "min_samples_per_bar": min_samples_per_bar,
                "write_bars": write_bars,
            }
        )
        if self.error is not None:
            raise self.error
        return type("_BarBuildResult", (), {"failed_count": 0})()


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
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
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


class _FakeTiming2ThirtySecondTriggerService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def scan(self, *, trade_date: str, settings, write_signals: bool):
        self.calls.append(
            {
                "trade_date": trade_date,
                "settings": settings,
                "write_signals": write_signals,
            }
        )
        if self.error is not None:
            raise self.error
        return _FakeResult("timing2_30s_trigger_scan")


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
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.maintenance.outcome == "FAILED"
    assert result.intraday_bar_refresh.outcome == "COMPLETED"
    assert refresh_service.calls[0]["write"] is True
    assert price_sample_capture_service.calls[0]["write_samples"] is True
    assert price_sample_capture_service.calls[0]["max_symbols"] == 30
    assert bar_build_service.calls[0]["write_bars"] is True
    assert sell_exit_scan_service.calls[0]["write_signals"] is False
    assert sell_macd_scan_service.calls[0]["write_signals"] is False
    assert timing2_lot_exit_scan_service.calls[0]["write_signals"] is False
    assert timing2_30s_trigger_service.calls[0]["write_signals"] is False
    assert buy_trigger_scan_service.calls[0]["write_timing1_signals"] is False
    assert buy_trigger_scan_service.calls[0]["write_timing2_signals"] is False
    assert buy_trigger_scan_service.calls[0]["run_timing2"] is False
    assert result.sell_execution.outcome == "SKIPPED"
    assert result.buy_execution.outcome == "SKIPPED"
    assert sell_execution_service.calls == []
    assert buy_execution_service.calls == []


def test_run_cycle_passes_signal_cleanup_settings_into_maintenance():
    maintenance_service = _FakeMaintenanceService()
    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=_FakeIntradayBarRefreshService(),
        timing2_price_sample_capture_service=_FakeTiming2PriceSampleCaptureService(),
        timing2_30s_bar_build_service=_FakeTiming2ThirtySecondBarBuildService(),
        sell_exit_scan_service=_FakeSellScanService("sell_exit_scan"),
        sell_macd_scan_service=_FakeSellScanService("sell_macd_scan"),
        timing2_lot_exit_scan_service=_FakeSellScanService("timing2_lot_exit_scan"),
        sell_signal_execution_service=_FakeSellExecutionService(),
        timing2_30s_trigger_service=_FakeTiming2ThirtySecondTriggerService(),
        buy_trigger_scan_service=_FakeBuyTriggerScanService(),
        buy_signal_execution_service=_FakeBuyExecutionService(),
    )

    service.run_cycle(
        trade_date="2026-04-17",
        execute_actions=False,
        maintenance_settings=StaleBuyOrderCancelSettings(timeout_seconds=300),
        sell_exit_settings=SellExitSettings(
            stop_loss_ratio=0.03,
            take_profit_ratio=0.05,
        ),
        sell_macd_settings=SellMacdExitSettings(),
        sell_macd_history_limit=300,
        timing2_lot_exit_settings=Timing2LotExitSettings(),
        sell_execution_settings=SellSignalExecutionSettings(
            max_signal_age_seconds=600
        ),
        sell_signal_limit=70,
        run_timing1=True,
        run_timing2=True,
        timing1_settings=Timing1IntradayTriggerSettings(),
        timing1_daily_count=5,
        timing2_settings=Timing2IntradayTriggerSettings(),
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
            max_signal_age_seconds=300,
        ),
        buy_signal_limit=40,
    )

    assert maintenance_service.calls == [
        {
            "trade_date": "2026-04-17",
            "stale_cancel_settings": StaleBuyOrderCancelSettings(
                timeout_seconds=300
            ),
            "buy_signal_cleanup_settings": StaleExecutionSignalCleanupSettings(
                max_signal_age_seconds=300,
                signal_limit=40,
            ),
            "sell_signal_cleanup_settings": StaleExecutionSignalCleanupSettings(
                max_signal_age_seconds=600,
                signal_limit=70,
            ),
            "execute_changes": False,
        }
    ]


def test_run_cycle_allows_sell_execution_when_macd_scan_fails():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-17")
    )
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService(
        "sell_macd_scan",
        error=RuntimeError("macd unavailable"),
    )
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-17")
    )
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_macd_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert len(sell_execution_service.calls) == 1


def test_run_cycle_allows_sell_execution_when_sell_exit_scan_fails_but_timing2_lot_scan_completes():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService(
        "sell_exit_scan",
        error=RuntimeError("sell stop scan unavailable"),
    )
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_exit_scan.outcome == "FAILED"
    assert result.timing2_lot_exit_scan.outcome == "COMPLETED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert len(sell_execution_service.calls) == 1


def test_run_cycle_allows_sell_execution_when_timing2_lot_scan_fails_but_sell_exit_scan_completes():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService(
        "timing2_lot_exit_scan",
        error=RuntimeError("timing2 lot scan unavailable"),
    )
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_exit_scan.outcome == "COMPLETED"
    assert result.timing2_lot_exit_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert len(sell_execution_service.calls) == 1


def test_run_cycle_allows_sell_execution_when_sell_stop_and_timing2_lot_scans_fail_but_macd_completes():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService(
        "sell_exit_scan",
        error=RuntimeError("sell stop scan unavailable"),
    )
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService(
        "timing2_lot_exit_scan",
        error=RuntimeError("timing2 lot scan unavailable"),
    )
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_exit_scan.outcome == "FAILED"
    assert result.sell_macd_scan.outcome == "COMPLETED"
    assert result.timing2_lot_exit_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert len(sell_execution_service.calls) == 1


def test_run_cycle_executes_timing2_30s_buy_path_and_buy_execution():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.timing2_price_sample_capture.outcome == "COMPLETED"
    assert result.timing2_30s_bar_build.outcome == "COMPLETED"
    assert result.timing2_30s_trigger_scan.outcome == "COMPLETED"
    assert result.sell_execution.outcome == "COMPLETED"
    assert result.buy_execution.outcome == "COMPLETED"

    assert price_sample_capture_service.calls[0]["write_samples"] is True
    assert price_sample_capture_service.calls[0]["max_symbols"] == 30
    assert bar_build_service.calls[0]["write_bars"] is True
    assert bar_build_service.calls[0]["min_samples_per_bar"] == 2
    assert timing2_30s_trigger_service.calls[0]["write_signals"] is True

    assert buy_trigger_scan_service.calls[0]["write_timing1_signals"] is True
    assert buy_trigger_scan_service.calls[0]["write_timing2_signals"] is False
    assert buy_trigger_scan_service.calls[0]["run_timing2"] is False

    assert len(sell_execution_service.calls) == 1
    assert sell_execution_service.calls[0]["execute_orders"] is True
    assert len(buy_execution_service.calls) == 1
    assert buy_execution_service.calls[0]["trade_date"] == "2026-04-17"
    assert buy_execution_service.calls[0]["signal_limit"] == 200
    assert buy_execution_service.calls[0]["execute_orders"] is True


def test_run_cycle_skips_sell_execution_when_all_sell_scans_fail():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService(
        "sell_exit_scan",
        error=RuntimeError("sell stop scan unavailable"),
    )
    sell_macd_scan_service = _FakeSellScanService(
        "sell_macd_scan",
        error=RuntimeError("sell macd unavailable"),
    )
    timing2_lot_exit_scan_service = _FakeSellScanService(
        "timing2_lot_exit_scan",
        error=RuntimeError("lot scan unavailable"),
    )
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.sell_exit_scan.outcome == "FAILED"
    assert result.sell_macd_scan.outcome == "FAILED"
    assert result.timing2_lot_exit_scan.outcome == "FAILED"
    assert result.sell_execution.outcome == "SKIPPED"
    assert (
        result.sell_execution.reason
        == "Skipped because all sell scans failed: "
        "sell stop-loss/take-profit, sell MACD, Timing2 lot-level sell. "
        "Details: sell stop-loss/take-profit=unavailable; "
        "sell MACD=unavailable; "
        "Timing2 lot-level sell=unavailable"
    )
    assert result.buy_execution.outcome == "SKIPPED"
    assert sell_execution_service.calls == []
    assert buy_execution_service.calls == []


def test_run_cycle_skips_timing2_steps_when_setup_signals_are_missing():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-17")
    )
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-17")
    )
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-17")
    )
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.timing2_price_sample_capture.outcome == "SKIPPED"
    assert "MissingTiming2SetupSignalsError" in str(
        result.timing2_price_sample_capture.reason
    )
    assert result.timing2_30s_bar_build.outcome == "SKIPPED"
    assert result.timing2_30s_trigger_scan.outcome == "SKIPPED"


def test_run_cycle_allows_buy_execution_when_timing1_scan_fails_but_timing2_30s_completes():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService()
    buy_trigger_scan_service = _FakeBuyTriggerScanService(
        error=RuntimeError("timing1 unavailable")
    )
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.buy_trigger_scan.outcome == "FAILED"
    assert result.timing2_30s_trigger_scan.outcome == "COMPLETED"
    assert result.buy_execution.outcome == "COMPLETED"
    assert len(buy_execution_service.calls) == 1
    assert buy_execution_service.calls[0]["execute_orders"] is True


def test_run_cycle_allows_buy_execution_when_timing2_30s_scan_fails_but_timing1_completes():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService(
        error=RuntimeError("timing2 30s unavailable")
    )
    buy_trigger_scan_service = _FakeBuyTriggerScanService()
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.buy_trigger_scan.outcome == "COMPLETED"
    assert result.timing2_30s_trigger_scan.outcome == "FAILED"
    assert result.buy_execution.outcome == "COMPLETED"
    assert len(buy_execution_service.calls) == 1
    assert buy_execution_service.calls[0]["execute_orders"] is True


def test_run_cycle_skips_buy_execution_when_timing1_and_timing2_buy_scans_both_fail():
    maintenance_service = _FakeMaintenanceService()
    refresh_service = _FakeIntradayBarRefreshService()
    price_sample_capture_service = _FakeTiming2PriceSampleCaptureService()
    bar_build_service = _FakeTiming2ThirtySecondBarBuildService()
    sell_exit_scan_service = _FakeSellScanService("sell_exit_scan")
    sell_macd_scan_service = _FakeSellScanService("sell_macd_scan")
    timing2_lot_exit_scan_service = _FakeSellScanService("timing2_lot_exit_scan")
    sell_execution_service = _FakeSellExecutionService()
    timing2_30s_trigger_service = _FakeTiming2ThirtySecondTriggerService(
        error=RuntimeError("timing2 30s unavailable")
    )
    buy_trigger_scan_service = _FakeBuyTriggerScanService(
        error=RuntimeError("timing1 unavailable")
    )
    buy_execution_service = _FakeBuyExecutionService()

    service = IntradayTradingCycleService(
        order_maintenance_service=maintenance_service,
        intraday_bar_refresh_service=refresh_service,
        timing2_price_sample_capture_service=price_sample_capture_service,
        timing2_30s_bar_build_service=bar_build_service,
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service,
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
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
        timing2_30s_trigger_settings=Timing2ThirtySecondTriggerSettings(),
        timing2_30s_min_samples_per_bar=2,
        timing2_max_sample_symbols_per_cycle=30,
        buy_execution_settings=BuySignalExecutionSettings(
            per_order_budget=1_000_000,
            max_holdings=3,
        ),
        buy_signal_limit=200,
    )

    assert result.buy_trigger_scan.outcome == "FAILED"
    assert result.timing2_30s_trigger_scan.outcome == "FAILED"
    assert result.buy_execution.outcome == "SKIPPED"
    assert (
        result.buy_execution.reason
        == "Skipped because all buy scans failed: "
        "buy trigger scan, Timing2 30-second trigger scan. "
        "Details: buy trigger scan=unavailable; "
        "Timing2 30-second trigger scan=unavailable"
    )
    assert buy_execution_service.calls == []


def test_format_failed_step_reasons_truncates_long_multiline_messages():
    formatted = IntradayTradingCycleService._format_failed_step_reasons(
        (
            (
                "buy trigger scan",
                IntradayTradingCycleStepStatus(
                    outcome="FAILED",
                    reason=(
                        "RuntimeError: timing1 unavailable because the upstream "
                        "provider returned an unexpectedly long diagnostic message "
                        "with repeated detail segments\nthat should still be shown "
                        "compactly in one line for operators."
                    ),
                    result=None,
                ),
            ),
            (
                "Timing2 30-second trigger scan",
                IntradayTradingCycleStepStatus(
                    outcome="FAILED",
                    reason="RuntimeError: timing2 30s unavailable",
                    result=None,
                ),
            ),
        )
    )

    first_part, second_part = formatted.split("; ", maxsplit=1)

    assert "\n" not in formatted
    assert first_part.startswith(
        "buy trigger scan=RuntimeError: timing1 unavailable because the upstream "
    )
    assert first_part.endswith("...")
    assert second_part == "Timing2 30-second trigger scan=unavailable"


def test_summarize_failed_step_reason_maps_common_operator_messages():
    assert (
        IntradayTradingCycleService._summarize_failed_step_reason(
            "RuntimeError: sell stop scan unavailable"
        )
        == "unavailable"
    )
    assert (
        IntradayTradingCycleService._summarize_failed_step_reason(
            "MissingTiming2SetupSignalsError: trade_date=2026-04-17"
        )
        == "setup signals missing"
    )
