"""Integration coverage for sell-side cycle fallback behavior."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from broker.kis.models import PriceSnapshot
from services import (
    BuySignalExecutionSettings,
    IntradayTradingCycleService,
    IntradayTriggerCombinedScanResult,
    IntradayTriggerStrategyStatus,
    SellMacdExitScanService,
    SellSignalExecutionService,
    SellSignalExecutionSettings,
    StaleBuyOrderCancelSettings,
    STRATEGY_NAME_SELL_MACD_DECREASE,
    STRATEGY_NAME_SELL_EXECUTION_AUDIT,
    STRATEGY_NAME_SELL_STOP_LOSS,
    STRATEGY_NAME_SELL_TAKE_PROFIT,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    Timing2LotExitScanService,
    TradingRiskGuardService,
)
from services.order_service import OrderOutcome
from services.sell_exit_scan_service import SellExitScanService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    EntryLotRepository,
    ExecutionRepository,
    IntradayBar15m,
    IntradayBar15mRepository,
    IntradayBar30sRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)
from strategy import (
    SellMacdExitMatch,
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2ThirtySecondTriggerSettings,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 17, hour, minute, second))


def _make_price_snapshot(symbol: str, price: int) -> PriceSnapshot:
    return PriceSnapshot(
        code=symbol,
        name=f"Name-{symbol}",
        price=price,
        open=price,
        high=price,
        low=price,
        prev_close=price,
        change=0,
        change_rate=0.0,
        volume=1,
        timestamp=_kst_datetime(10, 10, 0),
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
            refreshed_at=_kst_datetime(10, 9, 0).isoformat(),
        )


class _UnusedTiming2PriceSampleCaptureService:
    def capture(self, *, trade_date: str, write_samples: bool, max_symbols: int):
        raise AssertionError("Timing2 price sample capture should not run here.")


class _UnusedTiming2ThirtySecondBarBuildService:
    def build(
        self,
        *,
        trade_date: str,
        min_samples_per_bar: int,
        write_bars: bool,
    ):
        raise AssertionError("Timing2 30-second bar build should not run here.")


class _NoopSellMacdScanService:
    def scan(self, *, trade_date: str, settings, history_limit: int, write_signals: bool):
        return SimpleNamespace(
            trade_date=trade_date,
            history_limit=history_limit,
            write_signals=write_signals,
        )


class _FailingSellMacdScanService:
    def scan(self, *, trade_date: str, settings, history_limit: int, write_signals: bool):
        raise RuntimeError("sell MACD scan unavailable")


class _FailingSellExitScanService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        raise RuntimeError("sell stop scan unavailable")


class _FailingTiming2LotExitScanService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        raise RuntimeError("timing2 lot scan unavailable")


class _StubSellMacdEvaluator:
    def evaluate(self, *, symbol, intraday_bars, settings):
        if symbol != "035420":
            return None
        return SellMacdExitMatch(
            symbol=symbol,
            bar_start_at="2026-04-17T10:00:00+09:00",
            bar_end_at="2026-04-17T10:15:00+09:00",
            close_price=98_000,
            macd_value=1.0,
            signal_value=0.8,
            hist_t_minus_2=0.5,
            hist_t_minus_1=0.2,
            hist_t=-0.1,
            consecutive_decline_bars=settings.consecutive_decline_bars,
        )


class _UnusedTiming2ThirtySecondTriggerService:
    def scan(self, *, trade_date: str, settings, write_signals: bool):
        raise AssertionError("Timing2 30-second trigger scan should not run here.")


class _NoopBuyTriggerScanService:
    def scan(self, **kwargs):
        return IntradayTriggerCombinedScanResult(
            trade_date=kwargs["trade_date"],
            timing1=IntradayTriggerStrategyStatus(
                outcome="SKIPPED",
                reason="Disabled for sell-cycle integration test.",
                result=None,
            ),
            timing2=IntradayTriggerStrategyStatus(
                outcome="SKIPPED",
                reason="Disabled for sell-cycle integration test.",
                result=None,
            ),
        )


class _NoopBuyExecutionService:
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


def _seed_live_position(
    conn,
    *,
    symbol: str,
    qty: int,
    avg_price: int,
) -> None:
    repo = PositionRepository(conn)
    with transaction(conn):
        repo.upsert_from_broker(
            symbol=symbol,
            qty=qty,
            avg_price=avg_price,
            updated_at="2026-04-17T09:00:00+09:00",
        )


def _record_sell_signal(
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
                "name": f"Name-{symbol}",
                "rule": "seeded_for_cycle_skip_check",
                "position_qty": 1,
                "avg_price": 100_000,
                "current_price": 97_000,
                "trigger_price": 97_000,
                "stop_loss_ratio": 0.03,
                "take_profit_ratio": 0.05,
            },
        )
    return row.id


def _seed_15m_bar(
    conn,
    *,
    trade_date: str,
    symbol: str,
) -> None:
    repo = IntradayBar15mRepository(conn)
    with transaction(conn):
        repo.replace_for_symbol_and_date(
            trade_date=trade_date,
            symbol=symbol,
            bars=[
                IntradayBar15m(
                    bar_start_at="2026-04-17T10:00:00+09:00",
                    bar_end_at="2026-04-17T10:15:00+09:00",
                    open=99_000,
                    high=100_000,
                    low=98_000,
                    close=98_000,
                    volume=100,
                )
            ],
            refreshed_at="2026-04-17T10:16:00+09:00",
        )


def _seed_open_timing2_lot(
    conn,
    *,
    symbol: str,
    qty: int,
    price: int,
    strategy_name: str,
) -> None:
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)
    position_repo = PositionRepository(conn)
    entry_lot_repo = EntryLotRepository(conn)

    executed_at = "2026-04-17T10:00:30+09:00"
    client_order_id = f"BUY-{symbol}-{qty}-{price}"
    with transaction(conn):
        order = order_repo.create(
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            qty=qty,
            price=0,
            order_type="MARKET",
            strategy_name=strategy_name,
            requested_at=executed_at,
        )
        execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no=f"EXEC-{client_order_id}",
            symbol=symbol,
            side="buy",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )
        order_repo.mark_filled(
            client_order_id=client_order_id,
            closed_at=executed_at,
        )
        position_repo.apply_execution(
            symbol=symbol,
            side="buy",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )
        entry_lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol=symbol,
            qty=qty,
            price=price,
            executed_at=executed_at,
            entry_strategy_name=strategy_name,
        )


def _make_real_sell_execution_service(
    conn,
    *,
    broker: MagicMock,
    signal_repo: SignalRepository,
    order_repo: OrderRepository,
    position_repo: PositionRepository,
    entry_lot_repo: EntryLotRepository | None = None,
) -> tuple[SellSignalExecutionService, MagicMock]:
    order_service = MagicMock()
    order_service.place_order.return_value = SimpleNamespace(
        outcome=OrderOutcome.SUBMITTED,
        client_order_id="20260417101000-sell-005930",
        error_code=None,
        error_message=None,
    )

    service = SellSignalExecutionService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        order_repo=order_repo,
        position_repo=position_repo,
        order_service=order_service,
        entry_lot_repo=entry_lot_repo,
        risk_guard_service=TradingRiskGuardService(
            order_repo=order_repo,
            trading_control_repo=TradingControlRepository(conn),
            daily_stats_repo=DailyStatsRepository(conn),
            now_fn=lambda: _kst_datetime(10, 10, 5),
        ),
        now_fn=lambda: _kst_datetime(10, 10, 5),
    )
    return service, order_service


def _make_cycle_service(
    *,
    sell_exit_scan_service,
    timing2_lot_exit_scan_service,
    sell_signal_execution_service: SellSignalExecutionService,
    sell_macd_scan_service=None,
) -> tuple[IntradayTradingCycleService, _NoopBuyExecutionService]:
    buy_execution_service = _NoopBuyExecutionService()
    service = IntradayTradingCycleService(
        order_maintenance_service=_NoopMaintenanceService(),
        intraday_bar_refresh_service=_NoopIntradayBarRefreshService(),
        timing2_price_sample_capture_service=_UnusedTiming2PriceSampleCaptureService(),
        timing2_30s_bar_build_service=_UnusedTiming2ThirtySecondBarBuildService(),
        sell_exit_scan_service=sell_exit_scan_service,
        sell_macd_scan_service=sell_macd_scan_service or _NoopSellMacdScanService(),
        timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
        sell_signal_execution_service=sell_signal_execution_service,
        timing2_30s_trigger_service=_UnusedTiming2ThirtySecondTriggerService(),
        buy_trigger_scan_service=_NoopBuyTriggerScanService(),
        buy_signal_execution_service=buy_execution_service,
    )
    return service, buy_execution_service


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
        run_timing1=False,
        run_timing2=False,
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


def test_cycle_executes_real_timing2_lot_sell_signal_when_sell_exit_scan_fails(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)

        _seed_open_timing2_lot(
            conn,
            symbol="005930",
            qty=2,
            price=11_000,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("005930", 10_850)

        timing2_lot_exit_scan_service = Timing2LotExitScanService(
            broker=broker,
            conn=conn,
            entry_lot_repo=entry_lot_repo,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 10, 0),
        )
        sell_signal_execution_service, order_service = _make_real_sell_execution_service(
            conn,
            broker=broker,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            entry_lot_repo=entry_lot_repo,
        )
        cycle_service, buy_execution_service = _make_cycle_service(
            sell_exit_scan_service=_FailingSellExitScanService(),
            timing2_lot_exit_scan_service=timing2_lot_exit_scan_service,
            sell_signal_execution_service=sell_signal_execution_service,
            sell_macd_scan_service=_FailingSellMacdScanService(),
        )

        result = _run_cycle(cycle_service)

        assert result.sell_exit_scan.outcome == "FAILED"
        assert result.sell_macd_scan.outcome == "FAILED"
        assert result.timing2_lot_exit_scan.outcome == "COMPLETED"
        assert result.sell_execution.outcome == "COMPLETED"
        assert order_service.place_order.call_count == 1
        assert buy_execution_service.calls == []

        lot_signal_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
            limit=10,
        )
        assert len(lot_signal_rows) == 1
        assert signal_repo.get(lot_signal_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
        )
    finally:
        conn.close()


def test_cycle_executes_real_sell_stop_signal_when_timing2_lot_scan_fails(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _seed_live_position(
            conn,
            symbol="035420",
            qty=7,
            avg_price=100_000,
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("035420", 96_000)

        sell_exit_scan_service = SellExitScanService(
            broker=broker,
            conn=conn,
            position_repo=position_repo,
            signal_repo=signal_repo,
            now_fn=lambda: _kst_datetime(10, 10, 0),
        )
        sell_signal_execution_service, order_service = _make_real_sell_execution_service(
            conn,
            broker=broker,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, buy_execution_service = _make_cycle_service(
            sell_exit_scan_service=sell_exit_scan_service,
            timing2_lot_exit_scan_service=_FailingTiming2LotExitScanService(),
            sell_signal_execution_service=sell_signal_execution_service,
            sell_macd_scan_service=_FailingSellMacdScanService(),
        )

        result = _run_cycle(cycle_service)

        assert result.sell_exit_scan.outcome == "COMPLETED"
        assert result.sell_macd_scan.outcome == "FAILED"
        assert result.timing2_lot_exit_scan.outcome == "FAILED"
        assert result.sell_execution.outcome == "COMPLETED"
        assert order_service.place_order.call_count == 1
        assert buy_execution_service.calls == []

        stop_signal_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_STOP_LOSS,
            limit=10,
        )
        assert len(stop_signal_rows) == 1
        assert signal_repo.get(stop_signal_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == stop_signal_rows[0].id
        assert audit_rows[0].payload["source_strategy_name"] == STRATEGY_NAME_SELL_STOP_LOSS
    finally:
        conn.close()


def test_cycle_executes_real_sell_take_profit_signal_when_other_sell_scans_fail(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _seed_live_position(
            conn,
            symbol="035420",
            qty=7,
            avg_price=100_000,
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("035420", 106_000)

        sell_exit_scan_service = SellExitScanService(
            broker=broker,
            conn=conn,
            position_repo=position_repo,
            signal_repo=signal_repo,
            now_fn=lambda: _kst_datetime(10, 10, 0),
        )
        sell_signal_execution_service, order_service = _make_real_sell_execution_service(
            conn,
            broker=broker,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, buy_execution_service = _make_cycle_service(
            sell_exit_scan_service=sell_exit_scan_service,
            timing2_lot_exit_scan_service=_FailingTiming2LotExitScanService(),
            sell_signal_execution_service=sell_signal_execution_service,
            sell_macd_scan_service=_FailingSellMacdScanService(),
        )

        result = _run_cycle(cycle_service)

        assert result.sell_exit_scan.outcome == "COMPLETED"
        assert result.sell_macd_scan.outcome == "FAILED"
        assert result.timing2_lot_exit_scan.outcome == "FAILED"
        assert result.sell_execution.outcome == "COMPLETED"
        assert order_service.place_order.call_count == 1
        assert buy_execution_service.calls == []

        take_profit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_TAKE_PROFIT,
            limit=10,
        )
        assert len(take_profit_rows) == 1
        assert signal_repo.get(take_profit_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == take_profit_rows[0].id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_SELL_TAKE_PROFIT
        )
    finally:
        conn.close()


def test_cycle_executes_real_sell_macd_signal_when_other_sell_scans_fail(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        intraday_bar_15m_repo = IntradayBar15mRepository(conn)

        _seed_live_position(
            conn,
            symbol="035420",
            qty=4,
            avg_price=100_000,
        )
        _seed_15m_bar(
            conn,
            trade_date=TRADE_DATE,
            symbol="035420",
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("035420", 98_000)

        sell_macd_scan_service = SellMacdExitScanService(
            conn=conn,
            position_repo=position_repo,
            intraday_bar_repo=intraday_bar_15m_repo,
            signal_repo=signal_repo,
            now_fn=lambda: _kst_datetime(10, 20, 0),
            evaluator=_StubSellMacdEvaluator(),
        )
        sell_signal_execution_service, order_service = _make_real_sell_execution_service(
            conn,
            broker=broker,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, buy_execution_service = _make_cycle_service(
            sell_exit_scan_service=_FailingSellExitScanService(),
            timing2_lot_exit_scan_service=_FailingTiming2LotExitScanService(),
            sell_signal_execution_service=sell_signal_execution_service,
            sell_macd_scan_service=sell_macd_scan_service,
        )

        result = _run_cycle(cycle_service)

        assert result.sell_exit_scan.outcome == "FAILED"
        assert result.sell_macd_scan.outcome == "COMPLETED"
        assert result.timing2_lot_exit_scan.outcome == "FAILED"
        assert result.sell_execution.outcome == "COMPLETED"
        assert order_service.place_order.call_count == 1
        assert buy_execution_service.calls == []

        macd_signal_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_MACD_DECREASE,
            limit=10,
        )
        assert len(macd_signal_rows) == 1
        assert signal_repo.get(macd_signal_rows[0].id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == macd_signal_rows[0].id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_SELL_MACD_DECREASE
        )
    finally:
        conn.close()


def test_cycle_skips_sell_execution_when_all_sell_scans_fail_even_with_pending_signal(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        pending_signal_id = _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
            symbol="035420",
            scanned_at="2026-04-17T10:09:59+09:00",
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("035420", 96_000)

        sell_signal_execution_service, order_service = _make_real_sell_execution_service(
            conn,
            broker=broker,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
        )
        cycle_service, buy_execution_service = _make_cycle_service(
            sell_exit_scan_service=_FailingSellExitScanService(),
            timing2_lot_exit_scan_service=_FailingTiming2LotExitScanService(),
            sell_signal_execution_service=sell_signal_execution_service,
            sell_macd_scan_service=_FailingSellMacdScanService(),
        )

        result = _run_cycle(cycle_service)

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
        assert order_service.place_order.call_count == 0
        assert buy_execution_service.calls == []

        assert signal_repo.get(pending_signal_id).acted is False

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert audit_rows == []
    finally:
        conn.close()
