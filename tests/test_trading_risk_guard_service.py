"""Tests for TradingRiskGuardService."""

from __future__ import annotations

from datetime import datetime

import pytz

from services import TradingRiskGuardService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import OrderRepository, TradingControlRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 10, 0, 0))


def test_evaluate_blocks_buy_when_max_daily_order_count_reached(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        control_repo = TradingControlRepository(conn)
        with transaction(conn):
            order_repo.create(
                client_order_id="BUY-001",
                symbol="005930",
                side="buy",
                qty=1,
                price=0,
                order_type="MARKET",
                strategy_name="test",
                requested_at="2026-04-17T09:10:00+09:00",
            )
            order_repo.create(
                client_order_id="SELL-001",
                symbol="005930",
                side="sell",
                qty=1,
                price=0,
                order_type="MARKET",
                strategy_name="test",
                requested_at="2026-04-17T09:20:00+09:00",
            )

        service = TradingRiskGuardService(
            order_repo=order_repo,
            trading_control_repo=control_repo,
            now_fn=_fixed_now,
        )
        result = service.evaluate(
            trade_date=TRADE_DATE,
            max_daily_order_count=2,
        )

        assert result.today_order_count == 2
        assert result.buy_allowed is False
        assert result.buy_block_reason_code == "MAX_DAILY_ORDER_COUNT_REACHED"
        assert result.sell_allowed is True
    finally:
        conn.close()


def test_evaluate_blocks_buy_and_sell_when_kill_switch_enabled(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        control_repo = TradingControlRepository(conn)
        with transaction(conn):
            control_repo.set_kill_switch(
                is_enabled=True,
                updated_at="2026-04-17T09:55:00+09:00",
                note="emergency stop",
            )

        service = TradingRiskGuardService(
            order_repo=order_repo,
            trading_control_repo=control_repo,
            now_fn=_fixed_now,
        )
        result = service.evaluate(trade_date=TRADE_DATE)

        assert result.kill_switch_enabled is True
        assert result.buy_allowed is False
        assert result.sell_allowed is False
        assert result.buy_block_reason_code == "KILL_SWITCH_ENABLED"
        assert result.sell_block_reason_code == "KILL_SWITCH_ENABLED"
    finally:
        conn.close()
