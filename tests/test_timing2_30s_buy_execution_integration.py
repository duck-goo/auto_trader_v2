"""Integration test for Timing2 30-second trigger -> buy execution flow."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from broker.kis.models import Balance, PriceSnapshot
from services import (
    BuySignalExecutionService,
    BuySignalExecutionSettings,
    OrderOutcome,
    STRATEGY_NAME_BUY_EXECUTION_AUDIT,
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    STRATEGY_NAME_TIMING2_SETUP,
    Timing2ThirtySecondBarBuildService,
    Timing2ThirtySecondTriggerService,
    TradingRiskGuardService,
)
from services.order_service import OrderService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    CurrentPriceSample,
    CurrentPriceSampleRepository,
    DailyStatsRepository,
    EntryLotRepository,
    ExecutionRepository,
    IntradayBar30sRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)
from strategy import Timing2ThirtySecondTriggerSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, second))


def _seed_setup_signal(conn) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            scanned_at="2026-04-16T08:55:00+09:00",
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )


def _record_trigger_signal(
    conn,
    *,
    strategy_name: str,
    scanned_at: str,
) -> int:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        row = signal_repo.record(
            symbol="005930",
            strategy_name=strategy_name,
            scanned_at=scanned_at,
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )
    return row.id


def _seed_open_timing2_lot(
    conn,
    *,
    strategy_name: str,
    client_order_id: str,
    qty: int = 3,
    price: int = 1000,
    executed_at: str = "2026-04-16T09:02:00+09:00",
) -> None:
    order_repo = OrderRepository(conn)
    execution_repo = ExecutionRepository(conn)
    position_repo = PositionRepository(conn)
    entry_lot_repo = EntryLotRepository(conn)

    with transaction(conn):
        order = order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
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
            symbol="005930",
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
            symbol="005930",
            side="buy",
            qty=qty,
            price=price,
            executed_at=executed_at,
        )
        entry_lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol="005930",
            qty=qty,
            price=price,
            executed_at=executed_at,
            entry_strategy_name=strategy_name,
        )


def _upsert_samples(
    conn,
    *samples: CurrentPriceSample,
    captured_at: str,
) -> None:
    repo = CurrentPriceSampleRepository(conn)
    with transaction(conn):
        repo.upsert_many(
            samples=list(samples),
            captured_at=captured_at,
        )


def _sample(
    *,
    observed_at: str,
    price: int,
    volume: int,
) -> CurrentPriceSample:
    return CurrentPriceSample(
        trade_date=TRADE_DATE,
        symbol="005930",
        observed_at=observed_at,
        price=price,
        open=1000,
        high=max(1000, price),
        low=min(1000, price),
        prev_close=950,
        change=price - 950,
        change_rate=((price / 950) - 1.0) * 100,
        volume=volume,
        source="kis_current_price",
    )


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


def _make_price_snapshot(price: int = 1001) -> PriceSnapshot:
    return PriceSnapshot(
        code="005930",
        name="Samsung Electronics",
        price=price,
        open=1000,
        high=price,
        low=990,
        prev_close=950,
        change=price - 950,
        change_rate=((price / 950) - 1.0) * 100,
        volume=1,
        timestamp=_kst_datetime(9, 1, 5),
    )


def test_timing2_30s_morning_trigger_flows_into_buy_execution(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        sample_repo = CurrentPriceSampleRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _seed_setup_signal(conn)

        _upsert_samples(
            conn,
            _sample(
                observed_at="2026-04-16T09:00:00+09:00",
                price=1000,
                volume=100,
            ),
            _sample(
                observed_at="2026-04-16T09:00:20+09:00",
                price=990,
                volume=130,
            ),
            captured_at="2026-04-16T09:00:21+09:00",
        )

        bar_build_service = Timing2ThirtySecondBarBuildService(
            conn=conn,
            signal_repo=signal_repo,
            sample_repo=sample_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 0, 31),
        )
        trigger_service = Timing2ThirtySecondTriggerService(
            conn=conn,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 0, 31),
        )

        first_build_result = bar_build_service.build(
            trade_date=TRADE_DATE,
            min_samples_per_bar=2,
            write_bars=True,
        )
        first_trigger_result = trigger_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=True,
        )

        assert first_build_result.built_symbol_count == 1
        assert first_trigger_result.recorded_count == 1
        assert (
            first_trigger_result.candidates[0].transition_strategy_name
            == STRATEGY_NAME_TIMING2_30S_MORNING_DIP
        )

        _upsert_samples(
            conn,
            _sample(
                observed_at="2026-04-16T09:00:30+09:00",
                price=990,
                volume=140,
            ),
            _sample(
                observed_at="2026-04-16T09:00:50+09:00",
                price=1001,
                volume=180,
            ),
            captured_at="2026-04-16T09:00:51+09:00",
        )

        second_build_service = Timing2ThirtySecondBarBuildService(
            conn=conn,
            signal_repo=signal_repo,
            sample_repo=sample_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 1, 1),
        )
        second_trigger_service = Timing2ThirtySecondTriggerService(
            conn=conn,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 1, 1),
        )

        second_build_result = second_build_service.build(
            trade_date=TRADE_DATE,
            min_samples_per_bar=2,
            write_bars=True,
        )
        second_trigger_result = second_trigger_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=True,
        )

        assert second_build_result.built_symbol_count == 1
        assert second_trigger_result.recorded_count == 1
        assert second_trigger_result.buy_triggered_count == 1
        assert (
            second_trigger_result.candidates[0].transition_strategy_name
            == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        )

        morning_trigger_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            limit=10,
        )
        assert len(morning_trigger_rows) == 1
        morning_trigger_signal = morning_trigger_rows[0]
        assert morning_trigger_signal.payload is not None
        assert morning_trigger_signal.payload["trade_date"] == TRADE_DATE
        assert morning_trigger_signal.payload["symbol"] == "005930"
        assert morning_trigger_signal.payload["name"] == "Samsung Electronics"
        assert morning_trigger_signal.payload["market"] == "KOSPI"
        assert morning_trigger_signal.payload["buy_triggered"] is True

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot()

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260416090105-buy-005930",
            error_code=None,
            error_message=None,
        )

        buy_execution_service = BuySignalExecutionService(
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

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert (
            execution_result.candidates[0].source_strategy_name
            == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        )
        assert execution_result.candidates[0].symbol == "005930"
        assert signal_repo.get(morning_trigger_signal.id).acted is True

        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        )

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == morning_trigger_signal.id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        )
    finally:
        conn.close()


def test_timing2_30s_range_breakout_flows_into_buy_execution_without_morning_entry(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        sample_repo = CurrentPriceSampleRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _seed_setup_signal(conn)

        _upsert_samples(
            conn,
            _sample(
                observed_at="2026-04-16T09:00:00+09:00",
                price=1000,
                volume=100,
            ),
            _sample(
                observed_at="2026-04-16T09:00:20+09:00",
                price=1000,
                volume=120,
            ),
            _sample(
                observed_at="2026-04-16T09:59:30+09:00",
                price=1090,
                volume=150,
            ),
            _sample(
                observed_at="2026-04-16T09:59:50+09:00",
                price=1100,
                volume=190,
            ),
            _sample(
                observed_at="2026-04-16T10:00:00+09:00",
                price=1100,
                volume=210,
            ),
            _sample(
                observed_at="2026-04-16T10:00:20+09:00",
                price=1101,
                volume=240,
            ),
            captured_at="2026-04-16T10:00:21+09:00",
        )

        bar_build_service = Timing2ThirtySecondBarBuildService(
            conn=conn,
            signal_repo=signal_repo,
            sample_repo=sample_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 0, 31),
        )
        trigger_service = Timing2ThirtySecondTriggerService(
            conn=conn,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(10, 0, 31),
        )

        build_result = bar_build_service.build(
            trade_date=TRADE_DATE,
            min_samples_per_bar=2,
            write_bars=True,
        )
        trigger_result = trigger_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=True,
        )

        assert build_result.built_symbol_count == 1
        assert trigger_result.recorded_count == 1
        assert trigger_result.buy_triggered_count == 1
        assert (
            trigger_result.candidates[0].transition_strategy_name
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
        assert trigger_result.candidates[0].decision is not None
        assert (
            trigger_result.candidates[0].decision.state_before.morning_triggered
            is False
        )
        assert trigger_result.candidates[0].decision.morning_high_close == 1100

        range_trigger_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            limit=10,
        )
        assert len(range_trigger_rows) == 1
        range_trigger_signal = range_trigger_rows[0]
        assert range_trigger_signal.payload is not None
        assert range_trigger_signal.payload["trade_date"] == TRADE_DATE
        assert range_trigger_signal.payload["symbol"] == "005930"
        assert range_trigger_signal.payload["name"] == "Samsung Electronics"
        assert range_trigger_signal.payload["market"] == "KOSPI"
        assert range_trigger_signal.payload["buy_triggered"] is True
        assert range_trigger_signal.payload["morning_high_close"] == 1100

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot(price=1101)

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260416100031-buy-005930-range",
            error_code=None,
            error_message=None,
        )

        buy_execution_service = BuySignalExecutionService(
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
                now_fn=lambda: _kst_datetime(10, 0, 35),
            ),
            now_fn=lambda: _kst_datetime(10, 0, 35),
        )

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert (
            execution_result.candidates[0].source_strategy_name
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
        assert signal_repo.get(range_trigger_signal.id).acted is True

        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == range_trigger_signal.id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
    finally:
        conn.close()


def test_buy_execution_blocks_repeated_timing2_morning_slot_with_open_morning_lot(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)

        _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="20260416090200-buy-005930-morning",
        )
        repeated_signal_id = _record_trigger_signal(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            scanned_at="2026-04-16T09:05:00+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot()

        order_service = MagicMock(spec=OrderService)

        buy_execution_service = BuySignalExecutionService(
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
                now_fn=lambda: _kst_datetime(9, 5, 5),
            ),
            entry_lot_repo=entry_lot_repo,
            now_fn=lambda: _kst_datetime(9, 5, 5),
        )

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.blocked_count == 1
        assert execution_result.submitted_count == 0
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert (
            execution_result.candidates[0].reason_code
            == "TIMING2_ENTRY_SLOT_ALREADY_USED"
        )
        assert signal_repo.get(repeated_signal_id).acted is True

        broker.get_current_price.assert_not_called()
        order_service.place_order.assert_not_called()

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == repeated_signal_id
        assert audit_rows[0].payload["reason_code"] == "TIMING2_ENTRY_SLOT_ALREADY_USED"
    finally:
        conn.close()


def test_buy_execution_allows_timing2_range_slot_with_open_morning_lot(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        entry_lot_repo = EntryLotRepository(conn)

        _seed_open_timing2_lot(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            client_order_id="20260416090200-buy-005930-morning",
        )
        range_signal_id = _record_trigger_signal(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            scanned_at="2026-04-16T10:00:30+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot(price=1101)

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260416100031-buy-005930-range",
            error_code=None,
            error_message=None,
        )

        buy_execution_service = BuySignalExecutionService(
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
                now_fn=lambda: _kst_datetime(10, 0, 35),
            ),
            entry_lot_repo=entry_lot_repo,
            now_fn=lambda: _kst_datetime(10, 0, 35),
        )

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 1
        assert execution_result.blocked_count == 0
        assert execution_result.submitted_count == 1
        assert execution_result.acted_count == 1
        assert execution_result.audit_record_count == 1
        assert (
            execution_result.candidates[0].source_strategy_name
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
        assert signal_repo.get(range_signal_id).acted is True

        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].payload is not None
        assert audit_rows[0].payload["source_signal_id"] == range_signal_id
        assert (
            audit_rows[0].payload["source_strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        )
    finally:
        conn.close()


def test_buy_execution_prefers_timing1_over_timing2_30s_same_symbol_signal(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        timing2_signal_id = _record_trigger_signal(
            conn,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            scanned_at="2026-04-16T10:00:30+09:00",
        )
        timing1_signal_id = _record_trigger_signal(
            conn,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            scanned_at="2026-04-16T10:00:31+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot(price=1101)

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260416100035-buy-005930-timing1",
            error_code=None,
            error_message=None,
        )

        buy_execution_service = BuySignalExecutionService(
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
                now_fn=lambda: _kst_datetime(10, 0, 35),
            ),
            now_fn=lambda: _kst_datetime(10, 0, 35),
        )

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 2
        assert execution_result.candidate_count == 2
        assert execution_result.submitted_count == 1
        assert execution_result.blocked_count == 1
        assert execution_result.acted_count == 2
        assert execution_result.audit_record_count == 2

        submitted = next(
            item
            for item in execution_result.candidates
            if item.outcome.value == "SUBMITTED"
        )
        blocked = next(
            item
            for item in execution_result.candidates
            if item.outcome.value == "BLOCKED"
        )

        assert submitted.source_strategy_name == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        assert blocked.source_strategy_name == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        assert blocked.reason_code == "SUPERSEDED_BY_HIGHER_PRIORITY"

        assert signal_repo.get(timing1_signal_id).acted is True
        assert signal_repo.get(timing2_signal_id).acted is True

        broker.get_balance.assert_called_once()
        broker.get_current_price.assert_called_once_with("005930")
        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        )

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 2
        audit_by_signal_id = {
            row.payload["source_signal_id"]: row.payload for row in audit_rows
        }
        assert audit_by_signal_id[timing1_signal_id]["execution_outcome"] == "SUBMITTED"
        assert audit_by_signal_id[timing2_signal_id]["execution_outcome"] == "BLOCKED"
        assert (
            audit_by_signal_id[timing2_signal_id]["reason_code"]
            == "SUPERSEDED_BY_HIGHER_PRIORITY"
        )
    finally:
        conn.close()


def test_buy_execution_prefers_timing1_over_timing2_morning_trigger_same_symbol(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        sample_repo = CurrentPriceSampleRepository(conn)
        intraday_bar_repo = IntradayBar30sRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _seed_setup_signal(conn)

        _upsert_samples(
            conn,
            _sample(
                observed_at="2026-04-16T09:00:00+09:00",
                price=1000,
                volume=100,
            ),
            _sample(
                observed_at="2026-04-16T09:00:20+09:00",
                price=990,
                volume=130,
            ),
            captured_at="2026-04-16T09:00:21+09:00",
        )

        first_build_service = Timing2ThirtySecondBarBuildService(
            conn=conn,
            signal_repo=signal_repo,
            sample_repo=sample_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 0, 31),
        )
        first_trigger_service = Timing2ThirtySecondTriggerService(
            conn=conn,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 0, 31),
        )

        first_build_service.build(
            trade_date=TRADE_DATE,
            min_samples_per_bar=2,
            write_bars=True,
        )
        first_trigger_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=True,
        )

        _upsert_samples(
            conn,
            _sample(
                observed_at="2026-04-16T09:00:30+09:00",
                price=990,
                volume=140,
            ),
            _sample(
                observed_at="2026-04-16T09:00:50+09:00",
                price=1001,
                volume=180,
            ),
            captured_at="2026-04-16T09:00:51+09:00",
        )

        second_build_service = Timing2ThirtySecondBarBuildService(
            conn=conn,
            signal_repo=signal_repo,
            sample_repo=sample_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 1, 1),
        )
        second_trigger_service = Timing2ThirtySecondTriggerService(
            conn=conn,
            signal_repo=signal_repo,
            intraday_bar_repo=intraday_bar_repo,
            now_fn=lambda: _kst_datetime(9, 1, 1),
        )

        second_build_service.build(
            trade_date=TRADE_DATE,
            min_samples_per_bar=2,
            write_bars=True,
        )
        second_trigger_service.scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=True,
        )

        morning_trigger_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            limit=10,
        )
        assert len(morning_trigger_rows) == 1
        morning_trigger_signal = morning_trigger_rows[0]

        timing1_signal_id = _record_trigger_signal(
            conn,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            scanned_at="2026-04-16T09:01:02+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot()

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = SimpleNamespace(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id="20260416090105-buy-005930-timing1",
            error_code=None,
            error_message=None,
        )

        buy_execution_service = BuySignalExecutionService(
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

        execution_result = buy_execution_service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
            ),
            execute_orders=True,
        )

        assert execution_result.pending_signal_count == 2
        assert execution_result.candidate_count == 2
        assert execution_result.submitted_count == 1
        assert execution_result.blocked_count == 1
        assert execution_result.acted_count == 2
        assert execution_result.audit_record_count == 2

        submitted = next(
            item
            for item in execution_result.candidates
            if item.outcome.value == "SUBMITTED"
        )
        blocked = next(
            item
            for item in execution_result.candidates
            if item.outcome.value == "BLOCKED"
        )

        assert submitted.source_strategy_name == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        assert blocked.source_strategy_name == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        assert blocked.reason_code == "SUPERSEDED_BY_HIGHER_PRIORITY"

        assert signal_repo.get(timing1_signal_id).acted is True
        assert signal_repo.get(morning_trigger_signal.id).acted is True

        broker.get_balance.assert_called_once()
        broker.get_current_price.assert_called_once_with("005930")
        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        )

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 2
        audit_by_signal_id = {
            row.payload["source_signal_id"]: row.payload for row in audit_rows
        }
        assert (
            audit_by_signal_id[timing1_signal_id]["execution_outcome"] == "SUBMITTED"
        )
        assert (
            audit_by_signal_id[morning_trigger_signal.id]["execution_outcome"]
            == "BLOCKED"
        )
        assert (
            audit_by_signal_id[morning_trigger_signal.id]["reason_code"]
            == "SUPERSEDED_BY_HIGHER_PRIORITY"
        )
    finally:
        conn.close()
