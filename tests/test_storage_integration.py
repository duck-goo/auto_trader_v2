"""End-to-end integration test spanning all repositories.

Simulates a realistic trading day and verifies that all repositories
agree on the final state.
"""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    DbOrderStatus,
    ExecutionRepository,
    NegativePositionError,
    OrderRepository,
    PositionRepository,
    SignalRepository,
)


DAY = "2026-04-13"

T_SCAN      = "2026-04-13T08:55:00+09:00"
T_REQ_A     = "2026-04-13T09:00:00+09:00"
T_SUB_A     = "2026-04-13T09:00:01+09:00"
T_FILL_A1   = "2026-04-13T09:01:00+09:00"
T_FILL_A2   = "2026-04-13T09:02:00+09:00"
T_CLOSE_A   = "2026-04-13T09:02:01+09:00"
T_REQ_B     = "2026-04-13T09:10:00+09:00"
T_REJECT_B  = "2026-04-13T09:10:05+09:00"
T_REQ_C     = "2026-04-13T14:00:00+09:00"
T_SUB_C     = "2026-04-13T14:00:01+09:00"
T_FILL_C    = "2026-04-13T14:05:00+09:00"
T_CLOSE_C   = "2026-04-13T14:05:01+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_trading_day_scenario(conn):
    """
    Scenario:
      - Signal A flagged 005930 -> 10 qty BUY order split-filled (5+5) -> FILLED
      - Signal B flagged 000660 -> BUY order REJECTED by broker
      - Signal C flagged 005930 -> 4 qty SELL order -> fully filled
    Final state:
      - 005930 position: qty=6, avg_price=70500 (unchanged after sell)
      - daily_stats: 3 orders, 3 fills, 1 error
      - 3 signals, all acted
    """
    order_repo = OrderRepository(conn)
    exec_repo = ExecutionRepository(conn)
    pos_repo = PositionRepository(conn)
    signal_repo = SignalRepository(conn)
    stats_repo = DailyStatsRepository(conn)

    # ---- Signal A + order A (BUY 005930 x 10, split fill 5+5 @70000/71000) ----
    with transaction(conn):
        sig_a = signal_repo.record(
            symbol="005930", strategy_name="momo", scanned_at=T_SCAN,
            score=0.9, payload={"reason": "breakout"},
        )
        order_a = order_repo.create(
            client_order_id="COID_A", symbol="005930", side="buy",
            qty=10, price=70500, order_type="LIMIT",
            strategy_name="momo", requested_at=T_REQ_A,
        )
        order_repo.mark_submitted(
            client_order_id="COID_A", kis_order_no="KIS_A",
            submitted_at=T_SUB_A,
        )
        signal_repo.mark_acted(sig_a.id)

    # Fills arrive (separate transactions, as in real life).
    with transaction(conn):
        exec_repo.insert_if_new(
            order_id=order_a.id, kis_exec_no="EA1",
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at=T_FILL_A1,
        )
        pos_repo.apply_execution(
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at=T_FILL_A1,
        )

    with transaction(conn):
        exec_repo.insert_if_new(
            order_id=order_a.id, kis_exec_no="EA2",
            symbol="005930", side="buy", qty=5, price=71000,
            executed_at=T_FILL_A2,
        )
        pos_repo.apply_execution(
            symbol="005930", side="buy", qty=5, price=71000,
            executed_at=T_FILL_A2,
        )
        order_repo.mark_filled(
            client_order_id="COID_A", closed_at=T_CLOSE_A,
        )

    # ---- Signal B + order B (BUY 000660 x 3, REJECTED) ----
    with transaction(conn):
        sig_b = signal_repo.record(
            symbol="000660", strategy_name="rsi", scanned_at=T_SCAN,
            score=0.75,
        )
        order_repo.create(
            client_order_id="COID_B", symbol="000660", side="buy",
            qty=3, price=120000, order_type="LIMIT",
            strategy_name="rsi", requested_at=T_REQ_B,
        )
        order_repo.mark_rejected(
            client_order_id="COID_B",
            error_code="E40001",
            error_message="예수금 부족",
            closed_at=T_REJECT_B,
        )
        signal_repo.mark_acted(sig_b.id)

    # ---- Signal C + order C (SELL 005930 x 4, full fill) ----
    with transaction(conn):
        sig_c = signal_repo.record(
            symbol="005930", strategy_name="momo", scanned_at=T_SCAN,
            score=0.6, payload={"reason": "take_profit"},
        )
        order_c = order_repo.create(
            client_order_id="COID_C", symbol="005930", side="sell",
            qty=4, price=72000, order_type="LIMIT",
            strategy_name="momo", requested_at=T_REQ_C,
        )
        order_repo.mark_submitted(
            client_order_id="COID_C", kis_order_no="KIS_C",
            submitted_at=T_SUB_C,
        )
        signal_repo.mark_acted(sig_c.id)

    with transaction(conn):
        exec_repo.insert_if_new(
            order_id=order_c.id, kis_exec_no="EC1",
            symbol="005930", side="sell", qty=4, price=72000,
            executed_at=T_FILL_C,
        )
        pos_repo.apply_execution(
            symbol="005930", side="sell", qty=4, price=72000,
            executed_at=T_FILL_C,
        )
        order_repo.mark_filled(
            client_order_id="COID_C", closed_at=T_CLOSE_C,
        )

    # ---- Recompute daily stats ----
    with transaction(conn):
        stats = stats_repo.recompute_day(DAY)

    # ======================================================
    # Cross-repository assertions
    # ======================================================

    # Orders
    a_final = order_repo.get_by_client_order_id("COID_A")
    b_final = order_repo.get_by_client_order_id("COID_B")
    c_final = order_repo.get_by_client_order_id("COID_C")
    assert a_final.status == DbOrderStatus.FILLED
    assert a_final.filled_qty == 10
    # Weighted avg: (5*70000 + 5*71000) / 10 = 70500
    assert a_final.avg_fill_price == 70500
    assert b_final.status == DbOrderStatus.REJECTED
    assert c_final.status == DbOrderStatus.FILLED
    assert c_final.filled_qty == 4
    assert c_final.avg_fill_price == 72000

    # Unresolved should be empty.
    assert order_repo.find_unresolved() == []

    # Executions per order
    assert len(exec_repo.list_by_order(a_final.id)) == 2
    assert len(exec_repo.list_by_order(c_final.id)) == 1

    # Position: 10 bought (avg 70500) - 4 sold = 6 qty, avg unchanged.
    pos = pos_repo.get("005930")
    assert pos is not None
    assert pos.qty == 6
    assert pos.avg_price == 70500
    assert pos.updated_at == T_FILL_C
    # 000660 never traded.
    assert pos_repo.get("000660") is None

    # Signals all acted.
    assert signal_repo.list_unacted() == []
    by_symbol_005930 = signal_repo.list_by_symbol("005930")
    assert len(by_symbol_005930) == 2

    # Daily stats
    assert stats.trade_date == DAY
    assert stats.order_count == 3
    assert stats.fill_count == 3     # EA1, EA2, EC1
    assert stats.error_count == 1    # COID_B rejected
    assert stats.realized_pnl == 0   # FIFO not yet implemented


def test_sell_more_than_held_aborts_transaction(conn):
    """
    Critical safety check: an oversell must raise NegativePositionError
    and roll back the enclosing transaction, so neither the execution
    row nor the position change survives.
    """
    order_repo = OrderRepository(conn)
    exec_repo = ExecutionRepository(conn)
    pos_repo = PositionRepository(conn)

    # Seed a 5-qty BUY position.
    with transaction(conn):
        buy_order = order_repo.create(
            client_order_id="COID_SEED", symbol="005930", side="buy",
            qty=5, price=70000, order_type="LIMIT",
            strategy_name="seed", requested_at=T_REQ_A,
        )
        order_repo.mark_submitted(
            client_order_id="COID_SEED", kis_order_no="KIS_SEED",
            submitted_at=T_SUB_A,
        )
        exec_repo.insert_if_new(
            order_id=buy_order.id, kis_exec_no="ES1",
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at=T_FILL_A1,
        )
        pos_repo.apply_execution(
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at=T_FILL_A1,
        )
        order_repo.mark_filled(
            client_order_id="COID_SEED", closed_at=T_CLOSE_A,
        )

    # Try to sell 6 -> must fail atomically.
    with pytest.raises(NegativePositionError):
        with transaction(conn):
            sell_order = order_repo.create(
                client_order_id="COID_OVERSELL", symbol="005930", side="sell",
                qty=6, price=72000, order_type="LIMIT",
                strategy_name="bad", requested_at=T_REQ_C,
            )
            order_repo.mark_submitted(
                client_order_id="COID_OVERSELL", kis_order_no="KIS_OS",
                submitted_at=T_SUB_C,
            )
            exec_repo.insert_if_new(
                order_id=sell_order.id, kis_exec_no="EOS1",
                symbol="005930", side="sell", qty=6, price=72000,
                executed_at=T_FILL_C,
            )
            pos_repo.apply_execution(
                symbol="005930", side="sell", qty=6, price=72000,
                executed_at=T_FILL_C,
            )

    # Verify rollback: no oversell order, no oversell execution,
    # position unchanged at 5.
    assert order_repo.get_by_client_order_id("COID_OVERSELL") is None
    pos = pos_repo.get("005930")
    assert pos.qty == 5
    assert pos.avg_price == 70000