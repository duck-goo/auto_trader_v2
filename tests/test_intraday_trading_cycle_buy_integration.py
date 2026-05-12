"""Integration coverage for buy-side cycle fallback behavior."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from broker.kis.models import Balance, PriceSnapshot
from services import (
    BuySignalExecutionService,
    BuySignalExecutionSettings,
    IntradayTradingCycleService,
    IntradayTriggerCombinedScanResult,
    IntradayTriggerStrategyStatus,
    OrderOutcome,
    SellSignalExecutionSettings,
    StaleBuyOrderCancelSettings,
    STRATEGY_NAME_BUY_EXECUTION_AUDIT,
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    TradingRiskGuardService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)
from strategy import (
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2ThirtySecondTriggerSettings,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, second))


def _record_buy_signal(
    conn,
    signal_repo: SignalRepository,
    *,
    strategy_name: str,
    symbol: str,
    scanned_at: str,
) -> int:
    with transaction(conn):
        row = signal_repo.record(
            symbol=symbol,
            strategy_name=strategy_name,
            scanned_at=scanned_at,
            payload={
                "trade_date": TRADE_DATE,
                "symbol": symbol,
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )
    return row.id


def _make_balance() -> Balance:
    observed_now = _kst_datetime(9, 1, 5)
    return Balance(
        cash=5_000_000,
        available_cash=5_000_000,
        total_eval=5_000_000,
        total_profit=0,
        holdings=(),
        timestamp=observed_now,
    )


def _make_price_snapshot(price: int = 70_000) -> PriceSnapshot:
    return PriceSnapshot(
        code="005930",
        name="Samsung Electronics",
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change=0,
        change_rate=0.0,
        volume=1,
        timestamp=_kst_datetime(9, 1, 5),
    )


class _NoopMaintenanceService:
    def run(
        self,
        *,
        trade_date: str,
        stale_cancel_settings,
        buy_signal_cleanup_settings=None,
        sell_signal_cleanup_settings=None,
        execute_changes: bool,
    ):
        return SimpleNamespace(
            trade_date=trade_date,
            execute_changes=execute_changes,
        )


class _NoopIntradayBarRefreshService:
    def refresh_live_positions(
        self,
        *,
        trade_date: str,
        bar_minutes: int,
        write: bool,
    ):
        return SimpleNamespace(
            position_count=0,
            candidate_count=0,
            preview_ready_count=0,
            refreshed_symbol_count=0,
            skipped_count=0,
            failed_count=0,
            refreshed_at=_kst_datetime(9, 0, 0).isoformat(),
        )


class _NoopSellExitScanService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        return SimpleNamespace(trade_date=trade_date, write_signals=write_signals)


class _NoopSellMacdScanService:
    def scan(self, *, trade_date: str, settings, history_limit: int, write_signals: bool):
        return SimpleNamespace(
            trade_date=trade_date,
            history_limit=history_limit,
            write_signals=write_signals,
        )


class _NoopTiming2LotExitScanService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        return SimpleNamespace(trade_date=trade_date, write_signals=write_signals)


class _NoopSellExecutionService:
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
                "signal_limit": signal_limit,
                "execute_orders": execute_orders,
            }
        )
        return SimpleNamespace(trade_date=trade_date)


class _NoopTiming2PriceSampleCaptureService:
    def capture(self, *, trade_date: str, write_samples: bool, max_symbols: int):
        return SimpleNamespace(
            trade_date=trade_date,
            write_samples=write_samples,
            max_symbols=max_symbols,
            failed_count=0,
        )


class _NoopTiming2ThirtySecondBarBuildService:
    def build(
        self,
        *,
        trade_date: str,
        min_samples_per_bar: int,
        write_bars: bool,
    ):
        return SimpleNamespace(
            trade_date=trade_date,
            min_samples_per_bar=min_samples_per_bar,
            write_bars=write_bars,
            failed_count=0,
        )


class _FailingBuyTriggerScanService:
    def scan(self, **kwargs):
        raise RuntimeError("buy trigger scan unavailable")


class _RecordingTiming1BuyTriggerScanService:
    def __init__(self, *, conn, signal_repo: SignalRepository) -> None:
        self._conn = conn
        self._signal_repo = signal_repo

    def scan(self, **kwargs):
        signal_id = _record_buy_signal(
            self._conn,
            self._signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="005930",
            scanned_at="2026-04-16T09:00:40+09:00",
        )
        return IntradayTriggerCombinedScanResult(
            trade_date=kwargs["trade_date"],
            timing1=IntradayTriggerStrategyStatus(
                outcome="COMPLETED",
                reason=None,
                result=SimpleNamespace(recorded_signal_id=signal_id),
            ),
            timing2=IntradayTriggerStrategyStatus(
                outcome="SKIPPED",
                reason="Timing2 scan is disabled in the combined step.",
                result=None,
            ),
        )


class _FailingTiming2ThirtySecondTriggerService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        raise RuntimeError("timing2 30-second trigger scan unavailable")


class _RecordingTiming2ThirtySecondTriggerService:
    def __init__(self, *, conn, signal_repo: SignalRepository) -> None:
        self._conn = conn
        self._signal_repo = signal_repo

    def scan(self, *, trade_date: str, settings, write_signals: bool):
        signal_id = _record_buy_signal(
            self._conn,
            self._signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            symbol="005930",
            scanned_at="2026-04-16T10:00:30+09:00",
        )
        return SimpleNamespace(
            trade_date=trade_date,
            write_signals=write_signals,
            recorded_signal_id=signal_id,
        )


def _make_real_buy_execution_service(
    conn,
    *,
    signal_repo: SignalRepository,
    order_repo: OrderRepository,
    position_repo: PositionRepository,
) -> tuple[BuySignalExecutionService, MagicMock]:
    broker = MagicMock()
    broker.get_balance.return_value = _make_balance()
    broker.get_current_price.return_value = _make_price_snapshot()

    order_service = MagicMock()
    order_service.place_order.return_value = SimpleNamespace(
        outcome=OrderOutcome.SUBMITTED,
        client_order_id="20260416090105-buy-005930",
        error_code=None,
        error_message=None,
    )

    service = BuySignalExecutionService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        order_repo=order_repo,
        position_repo=position_repo,
        order_service=order_service,
        risk_guard_service=TradingRiskGuardService(
            order_repo=order_repo,
            trading_control_repo=TradingControlRepository(conn),
            daily_stats_repo=DailyStatsRepository(conn),
            now_fn=lambda: _kst_datetime(9, 1, 5),
        ),
        now_fn=lambda: _kst_datetime(9, 1, 5),
    )
    return service, order_service


def _make_cycle_service(
    *,
    buy_trigger_scan_service,
    timing2_30s_trigger_service,
    buy_signal_execution_service: BuySignalExecutionService,
) -> tuple[IntradayTradingCycleService, _NoopSellExecutionService]:
    sell_execution_service = _NoopSellExecutionService()
    service = IntradayTradingCycleService(
        order_maintenance_service=_NoopMaintenanceService(),
        intraday_bar_refresh_service=_NoopIntradayBarRefreshService(),
        timing2_price_sample_capture_service=_NoopTiming2PriceSampleCaptureService(),
        timing2_30s_bar_build_service=_NoopTiming2ThirtySecondBarBuildService(),
        sell_exit_scan_service=_NoopSellExitScanService(),
        sell_macd_scan_service=_NoopSellMacdScanService(),
        timing2_lot_exit_scan_service=_NoopTiming2LotExitScanService(),
        sell_signal_execution_service=sell_execution_service,
        timing2_30s_trigger_service=timing2_30s_trigger_service,
        buy_trigger_scan_service=buy_trigger_scan_service,
        buy_signal_execution_service=buy_signal_execution_service,
    )
    return service, sell_execution_service


def _run_cycle(service: IntradayTradingCycleService):
    return service.run_cycle(
        trade_date=TRADE_DATE,
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


def test_cycle_executes_real_timing2_buy_signal_when_timing1_scan_fails(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        buy_execution_service, order_service = _make_real_buy_execution_service(
            conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, sell_execution_service = _make_cycle_service(
            buy_trigger_scan_service=_FailingBuyTriggerScanService(),
            timing2_30s_trigger_service=_RecordingTiming2ThirtySecondTriggerService(
                conn=conn,
                signal_repo=signal_repo,
            ),
            buy_signal_execution_service=buy_execution_service,
        )

        result = _run_cycle(cycle_service)

        assert result.buy_trigger_scan.outcome == "FAILED"
        assert result.timing2_30s_trigger_scan.outcome == "COMPLETED"
        assert result.buy_execution.outcome == "COMPLETED"
        assert len(sell_execution_service.calls) == 1
        assert order_service.place_order.call_count == 1

        timing2_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            limit=10,
        )
        assert len(timing2_rows) == 1
        assert signal_repo.get(timing2_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == timing2_rows[0].id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
    finally:
        conn.close()


def test_cycle_executes_real_timing1_buy_signal_when_timing2_trigger_fails(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        buy_execution_service, order_service = _make_real_buy_execution_service(
            conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, sell_execution_service = _make_cycle_service(
            buy_trigger_scan_service=_RecordingTiming1BuyTriggerScanService(
                conn=conn,
                signal_repo=signal_repo,
            ),
            timing2_30s_trigger_service=_FailingTiming2ThirtySecondTriggerService(),
            buy_signal_execution_service=buy_execution_service,
        )

        result = _run_cycle(cycle_service)

        assert result.buy_trigger_scan.outcome == "COMPLETED"
        assert result.timing2_30s_trigger_scan.outcome == "FAILED"
        assert result.buy_execution.outcome == "COMPLETED"
        assert len(sell_execution_service.calls) == 1
        assert order_service.place_order.call_count == 1

        timing1_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            limit=10,
        )
        assert len(timing1_rows) == 1
        assert signal_repo.get(timing1_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == timing1_rows[0].id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        )
    finally:
        conn.close()


def test_cycle_skips_buy_execution_when_all_buy_scans_fail_even_with_pending_signal(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        pending_signal_id = _record_buy_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            symbol="005930",
            scanned_at="2026-04-16T09:59:59+09:00",
        )

        buy_execution_service, order_service = _make_real_buy_execution_service(
            conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, sell_execution_service = _make_cycle_service(
            buy_trigger_scan_service=_FailingBuyTriggerScanService(),
            timing2_30s_trigger_service=_FailingTiming2ThirtySecondTriggerService(),
            buy_signal_execution_service=buy_execution_service,
        )

        result = _run_cycle(cycle_service)

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
        assert len(sell_execution_service.calls) == 1
        assert order_service.place_order.call_count == 0

        assert signal_repo.get(pending_signal_id).acted is False

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert audit_rows == []
    finally:
        conn.close()
