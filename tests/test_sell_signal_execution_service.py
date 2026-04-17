"""Tests for SellSignalExecutionService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from broker.kis.models import OrderInfo, OrderSide, OrderStatus, OrderType, PriceSnapshot
from services import (
    OrderOutcome,
    OrderResult,
    OrderService,
    SellSignalExecutionOutcome,
    SellSignalExecutionService,
    SellSignalExecutionSettings,
    STRATEGY_NAME_SELL_STOP_LOSS,
    STRATEGY_NAME_SELL_TAKE_PROFIT,
    TradingRiskGuardService,
)
from services.sell_signal_execution_service import STRATEGY_NAME_SELL_EXECUTION_AUDIT
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    OrderRepository,
    OrderRow,
    PositionRepository,
    SignalRepository,
    TradingControlRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 10, 10, 0))


def _make_price_snapshot(symbol: str, price: int = 97_000) -> PriceSnapshot:
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


def _make_order_result(symbol: str, qty: int = 7) -> OrderResult:
    return OrderResult(
        outcome=OrderOutcome.SUBMITTED,
        client_order_id=f"20260417101000-sell-{symbol}",
        order_row=OrderRow(
            id=1,
            client_order_id=f"20260417101000-sell-{symbol}",
            kis_order_no=f"KIS-{symbol}",
            symbol=symbol,
            side="sell",
            qty=qty,
            price=0,
            order_type="MARKET",
            status=DbOrderStatus.SUBMITTED,
            filled_qty=0,
            avg_fill_price=0,
            requested_at="2026-04-17T10:10:00+09:00",
            submitted_at="2026-04-17T10:10:01+09:00",
            closed_at=None,
            error_code=None,
            error_message=None,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
        ),
        broker_info=OrderInfo(
            code=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
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


def _record_sell_signal(
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
) -> SellSignalExecutionService:
    return SellSignalExecutionService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        order_repo=order_repo,
        position_repo=position_repo,
        order_service=order_service,
        risk_guard_service=TradingRiskGuardService(
            order_repo=order_repo,
            trading_control_repo=TradingControlRepository(conn),
            now_fn=_fixed_now,
        ),
        now_fn=_fixed_now,
    )


def _settings() -> SellSignalExecutionSettings:
    return SellSignalExecutionSettings(
        start_time="09:00:00",
        cutoff_time="15:20:00",
    )


def test_preview_prefers_stop_loss_for_same_symbol(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="005930",
                qty=5,
                avg_price=100_000,
                updated_at="2026-04-17T09:00:00+09:00",
            )

        take_profit_id = _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_TAKE_PROFIT,
            symbol="005930",
            signal_at="2026-04-17T10:05:00+09:00",
        )
        stop_loss_id = _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
            symbol="005930",
            signal_at="2026-04-17T10:06:00+09:00",
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("005930", 97_000)
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
        winner = next(
            item
            for item in result.candidates
            if item.outcome == SellSignalExecutionOutcome.PREVIEW_READY
        )
        superseded = next(
            item
            for item in result.candidates
            if item.outcome == SellSignalExecutionOutcome.BLOCKED
        )
        assert winner.source_strategy_name == STRATEGY_NAME_SELL_STOP_LOSS
        assert superseded.source_strategy_name == STRATEGY_NAME_SELL_TAKE_PROFIT
        assert superseded.reason_code == "SUPERSEDED_BY_HIGHER_PRIORITY"
        assert signal_repo.get(stop_loss_id).acted is False
        assert signal_repo.get(take_profit_id).acted is False
        order_service.place_order.assert_not_called()
    finally:
        conn.close()


def test_execute_submitted_marks_source_signal_acted_and_records_audit(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="035420",
                qty=7,
                avg_price=100_000,
                updated_at="2026-04-17T09:00:00+09:00",
            )

        signal_id = _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
            symbol="035420",
            signal_at="2026-04-17T10:07:00+09:00",
        )

        broker = MagicMock()
        broker.get_current_price.return_value = _make_price_snapshot("035420", 96_000)

        order_service = MagicMock(spec=OrderService)
        order_service.place_order.return_value = _make_order_result("035420", qty=7)

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
        assert result.candidates[0].outcome == SellSignalExecutionOutcome.SUBMITTED
        assert result.candidates[0].position_qty == 7
        assert signal_repo.get(signal_id).acted is True

        audit_rows = signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=10,
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].acted is True
        assert audit_rows[0].payload["source_signal_id"] == signal_id
        assert audit_rows[0].payload["execution_outcome"] == "SUBMITTED"
        order_service.place_order.assert_called_once()
        _, kwargs = order_service.place_order.call_args
        assert kwargs["side"] == "sell"
        assert kwargs["qty"] == 7
    finally:
        conn.close()


def test_execute_blocks_when_unresolved_sell_order_exists_and_consumes_signal(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        signal_repo = SignalRepository(conn)
        order_repo = OrderRepository(conn)
        position_repo = PositionRepository(conn)

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="000660",
                qty=3,
                avg_price=100_000,
                updated_at="2026-04-17T09:00:00+09:00",
            )
            order_repo.create(
                client_order_id="20260417095900-sell-000660",
                symbol="000660",
                side="sell",
                qty=3,
                price=0,
                order_type="MARKET",
                strategy_name="seed_sell",
                requested_at="2026-04-17T09:59:00+09:00",
            )

        signal_id = _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
            symbol="000660",
            signal_at="2026-04-17T10:08:00+09:00",
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
            settings=_settings(),
            execute_orders=True,
        )

        assert result.blocked_count == 1
        assert result.acted_count == 1
        assert result.candidates[0].reason_code == "UNRESOLVED_SELL_ORDER_EXISTS"
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

        with transaction(conn):
            position_repo.upsert_from_broker(
                symbol="005930",
                qty=5,
                avg_price=100_000,
                updated_at="2026-04-17T09:00:00+09:00",
            )
            control_repo.set_kill_switch(
                is_enabled=True,
                updated_at="2026-04-17T10:00:00+09:00",
                note="manual stop",
            )

        _record_sell_signal(
            conn,
            signal_repo,
            strategy_name=STRATEGY_NAME_SELL_STOP_LOSS,
            symbol="005930",
            signal_at="2026-04-17T10:08:00+09:00",
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
            settings=_settings(),
            execute_orders=False,
        )

        assert result.blocked_count == 1
        blocked = result.candidates[0]
        assert blocked.reason_code == "KILL_SWITCH_ENABLED"
        broker.get_current_price.assert_not_called()
    finally:
        conn.close()
