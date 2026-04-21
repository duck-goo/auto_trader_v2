"""Tests for BuySignalExecutionService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from broker.kis.models import Balance, OrderInfo, OrderSide, OrderStatus, OrderType, PriceSnapshot
from services import (
    BuySignalExecutionOutcome,
    BuySignalExecutionService,
    BuySignalExecutionSettings,
    OrderOutcome,
    OrderResult,
    OrderService,
    STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
    TradingRiskGuardService,
)
from services.buy_signal_execution_service import STRATEGY_NAME_BUY_EXECUTION_AUDIT
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    ExecutionRepository,
    DbOrderStatus,
    OrderRepository,
    OrderRow,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 16, 9, 30, 0))


def _make_balance() -> Balance:
    return Balance(
        cash=5_000_000,
        available_cash=5_000_000,
        total_eval=5_000_000,
        total_profit=0,
        holdings=(),
        timestamp=_fixed_now(),
    )


def _make_price_snapshot(symbol: str, price: int = 70_000) -> PriceSnapshot:
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
        timestamp=_fixed_now(),
    )


def _make_order_result(symbol: str) -> OrderResult:
    return OrderResult(
        outcome=OrderOutcome.SUBMITTED,
        client_order_id=f"20260416093000-buy-{symbol}",
        order_row=OrderRow(
            id=1,
            client_order_id=f"20260416093000-buy-{symbol}",
            kis_order_no=f"KIS-{symbol}",
            symbol=symbol,
            side="buy",
            qty=1,
            price=0,
            order_type="MARKET",
            status=DbOrderStatus.SUBMITTED,
            filled_qty=0,
            avg_fill_price=0,
            requested_at="2026-04-16T09:30:00+09:00",
            submitted_at="2026-04-16T09:30:01+09:00",
            closed_at=None,
            error_code=None,
            error_message=None,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
        ),
        broker_info=OrderInfo(
            code=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            price=0,
            status=OrderStatus.ACCEPTED,
            order_no=f"KIS-{symbol}",
            filled_qty=0,
            timestamp=_fixed_now(),
            raw_response={"odno": f"KIS-{symbol}"},
        ),
        error_code=None,
        error_message=None,
    )


def _record_trigger_signal(
    conn,
    signal_repo: SignalRepository,
    *,
    strategy_name: str,
    symbol: str,
    signal_at: str,
) -> int:
    with transaction(conn):
        row = signal_repo.record(
            symbol=symbol,
            strategy_name=strategy_name,
            scanned_at=signal_at,
            payload={
                "trade_date": TRADE_DATE,
                "symbol": symbol,
                "name": f"Name-{symbol}",
                "market": "KOSPI",
            },
        )
    return row.id


def _make_service(
    *,
    conn,
    signal_repo: SignalRepository,
    order_repo: OrderRepository,
    position_repo: PositionRepository,
    broker: MagicMock,
    order_service: MagicMock,
) -> BuySignalExecutionService:
    return BuySignalExecutionService(
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
            now_fn=_fixed_now,
        ),
        now_fn=_fixed_now,
    )


def _settings() -> BuySignalExecutionSettings:
    return BuySignalExecutionSettings(
        per_order_budget=1_000_000,
        max_holdings=3,
        start_time="09:00:00",
        cutoff_time="12:00:00",
    )


def test_preview_prefers_timing1_and_keeps_signals_unacted(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        timing2_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T09:10:00+09:00",
        )
        timing1_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T09:11:00+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot("005930")
        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=False,
        )

        assert result.pending_signal_count == 2
        assert result.candidate_count == 2
        assert result.preview_ready_count == 1
        assert result.blocked_count == 1
        assert result.acted_count == 0
        assert result.audit_record_count == 0
        winner = next(
            item
            for item in result.candidates
            if item.outcome == BuySignalExecutionOutcome.PREVIEW_READY
        )
        superseded = next(
            item
            for item in result.candidates
            if item.outcome == BuySignalExecutionOutcome.BLOCKED
        )
        assert winner.source_strategy_name == STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        assert superseded.source_strategy_name == STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER
        assert superseded.reason_code == "SUPERSEDED_BY_HIGHER_PRIORITY"
        assert signal_repo.get(timing1_id).acted is False
        assert signal_repo.get(timing2_id).acted is False
        order_service.place_order.assert_not_called()
    finally:
        conn.close()


def test_preview_accepts_timing2_30s_buy_triggers_and_ignores_dip(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
            symbol="111111",
            signal_at="2026-04-16T09:00:30+09:00",
        )
        _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T09:01:00+09:00",
        )
        _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            symbol="000660",
            signal_at="2026-04-16T10:00:30+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot("005930")
        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=False,
        )

        assert result.pending_signal_count == 2
        assert result.candidate_count == 2
        assert result.preview_ready_count == 2
        assert {
            candidate.source_strategy_name
            for candidate in result.candidates
        } == {
            STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
        }
        order_service.place_order.assert_not_called()
    finally:
        conn.close()


def test_preview_blocks_timing2_30s_second_entry_until_lot_storage_exists(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        signal_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T10:00:30+09:00",
        )
        with transaction(conn):
            position_repo.apply_execution(
                symbol="005930",
                side="buy",
                qty=3,
                price=70_000,
                executed_at="2026-04-16T09:02:00+09:00",
            )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=False,
        )

        assert result.blocked_count == 1
        assert result.candidates[0].outcome == BuySignalExecutionOutcome.BLOCKED
        assert result.candidates[0].reason_code == "LIVE_POSITION_EXISTS"
        assert signal_repo.get(signal_id).acted is False
        broker.get_current_price.assert_not_called()
        order_service.place_order.assert_not_called()
    finally:
        conn.close()


def test_execute_timing2_30s_trigger_passes_source_strategy_to_order_service(
    test_db_path,
):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        signal_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            symbol="035420",
            signal_at="2026-04-16T09:12:00+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot(
            "035420",
            price=200_000,
        )

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = _make_order_result("035420")

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=True,
        )

        assert result.submitted_count == 1
        assert result.acted_count == 1
        assert signal_repo.get(signal_id).acted is True
        order_service.place_order.assert_called_once()
        assert (
            order_service.place_order.call_args.kwargs["strategy_name"]
            == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        )
    finally:
        conn.close()


def test_execute_submitted_marks_source_signal_acted_and_records_audit(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        signal_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="035420",
            signal_at="2026-04-16T09:12:00+09:00",
        )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot("035420", price=200_000)

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = _make_order_result("035420")

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=True,
        )

        assert result.submitted_count == 1
        assert result.acted_count == 1
        assert result.audit_record_count == 1
        assert result.candidates[0].outcome == BuySignalExecutionOutcome.SUBMITTED
        assert signal_repo.get(signal_id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_BUY_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].acted is True
        assert audit_rows[0].payload["source_signal_id"] == signal_id
        assert audit_rows[0].payload["execution_outcome"] == "SUBMITTED"
        order_service.place_order.assert_called_once()
    finally:
        conn.close()


def test_execute_blocks_when_unresolved_buy_order_exists(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        signal_id = _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="000660",
            signal_at="2026-04-16T09:13:00+09:00",
        )

        with transaction(conn):
            order_repo.create(
                client_order_id="20260416090000-test-000660",
                symbol="000660",
                side="buy",
                qty=1,
                price=0,
                order_type="MARKET",
                strategy_name="seed",
                requested_at="2026-04-16T09:00:00+09:00",
            )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()

        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=True,
        )

        assert result.blocked_count == 1
        assert result.candidates[0].outcome == BuySignalExecutionOutcome.BLOCKED
        assert result.candidates[0].reason_code == "UNRESOLVED_BUY_ORDER_EXISTS"
        assert result.acted_count == 1
        assert signal_repo.get(signal_id).acted is True
        order_service.place_order.assert_not_called()
    finally:
        conn.close()


def test_preview_blocks_when_kill_switch_enabled(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        control_repo = TradingControlRepository(conn)

        _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T09:13:00+09:00",
        )
        with transaction(conn):
            control_repo.set_kill_switch(
                is_enabled=True,
                updated_at="2026-04-16T09:20:00+09:00",
                note="manual stop",
            )

        broker = MagicMock()
        broker.get_balance.return_value = _make_balance()
        broker.get_current_price.return_value = _make_price_snapshot("005930")
        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_orders=False,
        )

        assert result.blocked_count == 1
        blocked = result.candidates[0]
        assert blocked.reason_code == "KILL_SWITCH_ENABLED"
        broker.get_balance.assert_not_called()
        broker.get_current_price.assert_not_called()
    finally:
        conn.close()


def test_preview_blocks_when_max_daily_loss_reached(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)
        execution_repo = ExecutionRepository(conn)

        _record_trigger_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            symbol="005930",
            signal_at="2026-04-16T09:13:00+09:00",
        )

        with transaction(conn):
            buy_order = order_repo.create(
                client_order_id="SEED-BUY-001",
                symbol="005930",
                side="buy",
                qty=2,
                price=100_000,
                order_type="LIMIT",
                strategy_name="seed",
                requested_at="2026-04-15T15:00:00+09:00",
            )
            order_repo.mark_submitted(
                client_order_id=buy_order.client_order_id,
                kis_order_no="KIS-SEED-BUY-001",
                submitted_at="2026-04-15T15:00:01+09:00",
            )
            execution_repo.insert_if_new(
                order_id=buy_order.id,
                kis_exec_no="EXEC-SEED-BUY-001",
                symbol="005930",
                side="buy",
                qty=2,
                price=100_000,
                executed_at="2026-04-15T15:01:00+09:00",
            )
            order_repo.mark_filled(
                client_order_id=buy_order.client_order_id,
                closed_at="2026-04-15T15:01:00+09:00",
            )

            sell_order = order_repo.create(
                client_order_id="SEED-SELL-001",
                symbol="005930",
                side="sell",
                qty=2,
                price=95_000,
                order_type="LIMIT",
                strategy_name="seed",
                requested_at="2026-04-16T09:10:00+09:00",
            )
            order_repo.mark_submitted(
                client_order_id=sell_order.client_order_id,
                kis_order_no="KIS-SEED-SELL-001",
                submitted_at="2026-04-16T09:10:01+09:00",
            )
            execution_repo.insert_if_new(
                order_id=sell_order.id,
                kis_exec_no="EXEC-SEED-SELL-001",
                symbol="005930",
                side="sell",
                qty=2,
                price=95_000,
                executed_at="2026-04-16T09:11:00+09:00",
            )
            order_repo.mark_filled(
                client_order_id=sell_order.client_order_id,
                closed_at="2026-04-16T09:11:00+09:00",
            )

        broker = MagicMock()
        order_service = MagicMock(spec=OrderService)

        service = _make_service(
            conn=conn,
            signal_repo=signal_repo,
            order_repo=order_repo,
            position_repo=position_repo,
            broker=broker,
            order_service=order_service,
        )

        result = service.execute_pending_signals(
            trade_date=TRADE_DATE,
            settings=BuySignalExecutionSettings(
                per_order_budget=1_000_000,
                max_holdings=3,
                max_daily_loss=5_000,
                start_time="09:00:00",
                cutoff_time="12:00:00",
            ),
            execute_orders=False,
        )

        assert result.blocked_count == 1
        blocked = result.candidates[0]
        assert blocked.reason_code == "MAX_DAILY_LOSS_REACHED"
        broker.get_balance.assert_not_called()
        broker.get_current_price.assert_not_called()
    finally:
        conn.close()
