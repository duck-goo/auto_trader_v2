"""Tests for StartupService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from broker.kis.models import Balance, Holding
from services import StartupOutcome, StartupService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
    UniverseCandidate,
    UniverseCandidateRepository,
)


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-14"
REFRESHED_AT = "2026-04-14T08:30:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def repos(conn):
    return (
        OrderRepository(conn),
        PositionRepository(conn),
        UniverseCandidateRepository(conn),
    )


def _fixed_now(year=2026, month=4, day=14, hour=10, minute=30, second=0):
    fixed = KST.localize(datetime(year, month, day, hour, minute, second))
    return lambda: fixed


def _holding(*, code: str, qty: int, avg_price: float) -> Holding:
    return Holding(
        code=code,
        name=code,
        quantity=qty,
        available=qty,
        avg_price=avg_price,
        current_price=0,
        eval_amount=0,
        profit=0,
        profit_rate=0.0,
    )


def _balance(*holdings: Holding) -> Balance:
    return Balance(
        cash=1_000_000,
        available_cash=1_000_000,
        total_eval=0,
        total_profit=0,
        holdings=tuple(holdings),
        has_more_pages=False,
        timestamp=KST.localize(datetime(2026, 4, 14, 10, 30, 0)),
    )


def _make_service(conn, repos, broker, *, now_fn=None):
    order_repo, position_repo, universe_repo = repos
    return StartupService(
        broker=broker,
        conn=conn,
        order_repo=order_repo,
        position_repo=position_repo,
        universe_repo=universe_repo,
        now_fn=now_fn or _fixed_now(),
    )


def _seed_unresolved_order(conn, order_repo):
    with transaction(conn):
        order_repo.create(
            client_order_id="COID_STARTUP_BLOCK",
            symbol="005930",
            side="buy",
            qty=1,
            price=70000,
            order_type="LIMIT",
            strategy_name="startup",
            requested_at="2026-04-14T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id="COID_STARTUP_BLOCK",
            kis_order_no="KIS_STARTUP_001",
            submitted_at="2026-04-14T09:00:01+09:00",
        )


def _seed_open_entry_lot(conn, order_repo, *, symbol="005930"):
    entry_lot_repo = EntryLotRepository(conn)
    execution_repo = ExecutionRepository(conn)
    with transaction(conn):
        order = order_repo.create(
            client_order_id=f"COID_STARTUP_LOT_{symbol}",
            symbol=symbol,
            side="buy",
            qty=1,
            price=0,
            order_type="MARKET",
            strategy_name="buy_timing2_30s_morning_open_reclaim",
            requested_at="2026-04-14T09:10:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no=f"KIS_STARTUP_LOT_{symbol}",
            submitted_at="2026-04-14T09:10:01+09:00",
        )
        assert execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no=f"EXEC_STARTUP_LOT_{symbol}",
            symbol=symbol,
            side="buy",
            qty=1,
            price=70_000,
            executed_at="2026-04-14T09:11:00+09:00",
        ) is True
        order_repo.sync_execution_summary(
            client_order_id=order.client_order_id,
            closed_at="2026-04-14T09:11:00+09:00",
        )
        entry_lot_repo.apply_buy_execution(
            entry_order_id=order.id,
            symbol=symbol,
            qty=1,
            price=70_000,
            executed_at="2026-04-14T09:11:00+09:00",
            entry_strategy_name="buy_timing2_30s_morning_open_reclaim",
        )


def _seed_universe_snapshot(conn, universe_repo):
    with transaction(conn):
        universe_repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="000660",
                    name="SK hynix",
                    market="KOSPI",
                    close_price=206000,
                    prev_day_trade_value=1_200_000,
                ),
                UniverseCandidate(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=15_000_000,
                ),
            ],
            refreshed_at=REFRESHED_AT,
        )


def test_startup_check_blocks_when_universe_snapshot_missing(conn, repos):
    broker = MagicMock()
    broker.get_balance.return_value = _balance()

    service = _make_service(conn, repos, broker)
    result = service.run_startup_check()

    assert result.outcome == StartupOutcome.BLOCKED
    assert result.trade_date == TRADE_DATE
    assert result.universe_snapshot.exists is False
    assert result.universe_snapshot.candidate_count == 0
    assert result.reconcile_result is None
    assert result.live_positions == ()
    assert result.reason == (
        "Universe snapshot is missing for trade_date=2026-04-14. "
        "Startup is blocked."
    )
    broker.get_balance.assert_not_called()


def test_startup_check_blocks_when_unresolved_orders_exist(conn, repos):
    order_repo, _position_repo, universe_repo = repos
    _seed_universe_snapshot(conn, universe_repo)
    _seed_unresolved_order(conn, order_repo)

    broker = MagicMock()
    broker.get_balance.return_value = _balance()

    service = _make_service(conn, repos, broker)
    result = service.run_startup_check()

    assert result.outcome == StartupOutcome.BLOCKED
    assert result.universe_snapshot.exists is True
    assert result.universe_snapshot.candidate_count == 2
    assert result.reconcile_result is not None
    assert len(result.reconcile_result.unresolved_orders) == 1
    assert result.reason == "Unresolved orders exist. Startup is blocked."
    broker.get_balance.assert_not_called()


def test_startup_check_ready_when_reconcile_succeeds(conn, repos):
    _order_repo, _position_repo, universe_repo = repos
    _seed_universe_snapshot(conn, universe_repo)

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=1, avg_price=206500.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.run_startup_check()

    assert result.outcome == StartupOutcome.READY
    assert result.reason is None
    assert result.universe_snapshot.exists is True
    assert result.universe_snapshot.candidate_count == 2
    assert result.universe_snapshot.refreshed_at == REFRESHED_AT
    assert result.reconcile_result is not None
    assert result.reconcile_result.changed_rows == 1
    assert len(result.live_positions) == 1
    assert result.live_positions[0].symbol == "005930"
    assert result.live_positions[0].qty == 1


def test_startup_check_allows_override(conn, repos):
    order_repo, _position_repo, universe_repo = repos
    _seed_universe_snapshot(conn, universe_repo)
    _seed_unresolved_order(conn, order_repo)

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=205000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.run_startup_check(allow_unresolved_orders=True)

    assert result.outcome == StartupOutcome.READY
    assert result.universe_snapshot.exists is True
    assert len(result.reconcile_result.unresolved_orders) == 1
    assert result.reconcile_result.changed_rows == 1
    broker.get_balance.assert_called_once()


def test_startup_check_blocks_when_open_entry_lot_position_would_change(conn, repos):
    order_repo, position_repo, universe_repo = repos
    _seed_universe_snapshot(conn, universe_repo)
    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=1,
            avg_price=70_000,
            updated_at="2026-04-14T09:20:00+09:00",
        )
    _seed_open_entry_lot(conn, order_repo, symbol="005930")

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=70500.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.run_startup_check()

    assert result.outcome == StartupOutcome.BLOCKED
    assert result.reconcile_result is not None
    assert (
        result.reconcile_result.reason_code
        == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    )
    assert "005930" in (result.reason or "")
