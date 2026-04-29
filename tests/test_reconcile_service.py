"""Tests for ReconcileService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from broker.kis.models import Balance, Holding
from services import (
    ReconcileAction,
    ReconcileOutcome,
    ReconcileResult,
    ReconcileService,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
)


KST = pytz.timezone("Asia/Seoul")


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
    return OrderRepository(conn), PositionRepository(conn), EntryLotRepository(conn)


def _fixed_now(year=2026, month=4, day=14, hour=10, minute=0, second=0):
    fixed = KST.localize(datetime(year, month, day, hour, minute, second))
    return lambda: fixed


def _holding(
    *,
    code: str,
    qty: int,
    avg_price: float,
    current_price: int = 0,
) -> Holding:
    return Holding(
        code=code,
        name=code,
        quantity=qty,
        available=qty,
        avg_price=avg_price,
        current_price=current_price,
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
        timestamp=KST.localize(datetime(2026, 4, 14, 10, 0, 0)),
    )


def _make_service(conn, repos, broker, *, now_fn=None):
    order_repo, position_repo, entry_lot_repo = repos
    return ReconcileService(
        broker=broker,
        conn=conn,
        order_repo=order_repo,
        position_repo=position_repo,
        entry_lot_repo=entry_lot_repo,
        now_fn=now_fn or _fixed_now(),
    )


def _seed_submitted_order(conn, order_repo, *, client_order_id="COID_UNRESOLVED"):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol="005930",
            side="buy",
            qty=1,
            price=70000,
            order_type="LIMIT",
            strategy_name="reconcile",
            requested_at="2026-04-14T09:00:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no="KIS_001",
            submitted_at="2026-04-14T09:00:01+09:00",
        )


def _seed_open_entry_lot(conn, order_repo, entry_lot_repo, *, symbol="005930"):
    execution_repo = ExecutionRepository(conn)
    with transaction(conn):
        order = order_repo.create(
            client_order_id=f"COID_LOT_{symbol}",
            symbol=symbol,
            side="buy",
            qty=1,
            price=0,
            order_type="MARKET",
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            requested_at="2026-04-14T09:10:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=order.client_order_id,
            kis_order_no=f"KIS_LOT_{symbol}",
            submitted_at="2026-04-14T09:10:01+09:00",
        )
        assert execution_repo.insert_if_new(
            order_id=order.id,
            kis_exec_no=f"EXEC_LOT_{symbol}",
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
            entry_strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        )


def test_reconcile_blocks_when_unresolved_orders_exist(conn, repos):
    order_repo, position_repo, _entry_lot_repo = repos
    _seed_submitted_order(conn, order_repo)

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=3, avg_price=70000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert isinstance(result, ReconcileResult)
    assert result.outcome == ReconcileOutcome.BLOCKED
    assert result.changed_rows == 0
    assert len(result.unresolved_orders) == 1
    assert result.diffs == ()

    assert position_repo.get("005930") is None
    broker.get_balance.assert_not_called()


def test_reconcile_allows_override_with_unresolved_orders(conn, repos):
    order_repo, position_repo, _entry_lot_repo = repos
    _seed_submitted_order(conn, order_repo)

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=3, avg_price=70100.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions(allow_unresolved_orders=True)

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 1
    assert len(result.unresolved_orders) == 1
    assert result.diffs[0].action == ReconcileAction.UPSERT

    row = position_repo.get("005930")
    assert row is not None
    assert row.qty == 3
    assert row.avg_price == 70100


def test_reconcile_inserts_missing_broker_holding(conn, repos):
    _order_repo, position_repo, _entry_lot_repo = repos

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=70000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 1
    diff = result.diffs[0]
    assert diff.symbol == "005930"
    assert diff.action == ReconcileAction.UPSERT
    assert diff.local_qty == 0
    assert diff.broker_qty == 2

    row = position_repo.get("005930")
    assert row is not None
    assert row.qty == 2
    assert row.avg_price == 70000


def test_reconcile_updates_mismatched_position(conn, repos):
    _order_repo, position_repo, _entry_lot_repo = repos

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=1,
            avg_price=65000,
            updated_at="2026-04-14T09:30:00+09:00",
        )

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=69000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 1

    row = position_repo.get("005930")
    assert row is not None
    assert row.qty == 2
    assert row.avg_price == 69000


def test_reconcile_clears_stale_local_position_absent_from_broker(conn, repos):
    _order_repo, position_repo, _entry_lot_repo = repos

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="000660",
            qty=5,
            avg_price=120000,
            updated_at="2026-04-14T09:30:00+09:00",
        )

    broker = MagicMock()
    broker.get_balance.return_value = _balance()

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 1
    diff = result.diffs[0]
    assert diff.symbol == "000660"
    assert diff.action == ReconcileAction.CLEAR

    row = position_repo.get("000660")
    assert row is not None
    assert row.qty == 0
    assert row.avg_price == 0


def test_reconcile_is_noop_when_already_synced(conn, repos):
    _order_repo, position_repo, _entry_lot_repo = repos

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=2,
            avg_price=70000,
            updated_at="2026-04-14T09:30:00+09:00",
        )

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=70000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 0
    assert result.diffs == ()
    assert result.reason_code is None
    assert result.reason_message is None


def test_reconcile_blocks_when_open_entry_lot_symbol_would_change(conn, repos):
    order_repo, position_repo, entry_lot_repo = repos

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=1,
            avg_price=70_000,
            updated_at="2026-04-14T09:30:00+09:00",
        )
    _seed_open_entry_lot(conn, order_repo, entry_lot_repo, symbol="005930")

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=2, avg_price=71000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.BLOCKED
    assert result.changed_rows == 0
    assert result.diffs == ()
    assert result.reason_code == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
    assert "005930" in (result.reason_message or "")

    row = position_repo.get("005930")
    assert row is not None
    assert row.qty == 1
    assert row.avg_price == 70_000


def test_reconcile_allows_open_entry_lot_when_position_already_matches(conn, repos):
    order_repo, position_repo, entry_lot_repo = repos

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=1,
            avg_price=70_000,
            updated_at="2026-04-14T09:30:00+09:00",
        )
    _seed_open_entry_lot(conn, order_repo, entry_lot_repo, symbol="005930")

    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=1, avg_price=70000.0)
    )

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    assert result.changed_rows == 0
    assert result.diffs == ()
    assert result.reason_code is None
    assert result.reason_message is None


def test_reconcile_calls_broker_outside_transaction(conn, repos):
    broker = MagicMock()

    def _get_balance():
        assert conn.in_transaction is False
        return _balance()

    broker.get_balance.side_effect = _get_balance

    service = _make_service(conn, repos, broker)
    result = service.reconcile_positions()

    assert result.outcome == ReconcileOutcome.RECONCILED
    broker.get_balance.assert_called_once()


def test_reconcile_raises_on_duplicate_symbols_in_broker_snapshot(conn, repos):
    broker = MagicMock()
    broker.get_balance.return_value = _balance(
        _holding(code="005930", qty=1, avg_price=70000.0),
        _holding(code="005930", qty=2, avg_price=71000.0),
    )

    service = _make_service(conn, repos, broker)

    with pytest.raises(Exception, match="Duplicate symbol"):
        service.reconcile_positions()
