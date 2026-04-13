"""OrderService.cancel_order() - Phase 3-A-3."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from broker.kis.errors import KisApiError, KisError, KisOrderError
from broker.kis.models import OrderInfo, OrderSide, OrderStatus, OrderType
from services import CancelOutcome, CancelResult, OrderService
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
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
    return (
        OrderRepository(conn),
        PositionRepository(conn),
        ExecutionRepository(conn),
    )


def _fixed_now(year=2026, month=4, day=13, hour=9, minute=5, second=0):
    fixed = KST.localize(datetime(year, month, day, hour, minute, second))
    return lambda: fixed


def _make_service(conn, repos, broker, *, now_fn=None):
    order_repo, position_repo, _ = repos
    return OrderService(
        broker=broker,
        conn=conn,
        order_repo=order_repo,
        position_repo=position_repo,
        now_fn=now_fn or _fixed_now(),
    )


def _seed_submitted_order(
    conn,
    order_repo,
    *,
    client_order_id="COID_CANCEL",
    symbol="005930",
    side="buy",
    qty=10,
    price=70000,
):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type="LIMIT",
            strategy_name="cancel",
            requested_at="2026-04-13T09:00:00+09:00",
        )
        row = order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no=f"KIS_{client_order_id}",
            submitted_at="2026-04-13T09:00:01+09:00",
        )
    return row


def _seed_partial_order(
    conn,
    order_repo,
    execution_repo,
    *,
    client_order_id="COID_PARTIAL_CANCEL",
):
    row = _seed_submitted_order(conn, order_repo, client_order_id=client_order_id)
    with transaction(conn):
        execution_repo.insert_if_new(
            order_id=row.id,
            kis_exec_no=f"EXEC_{client_order_id}",
            symbol=row.symbol,
            side=row.side,
            qty=4,
            price=70100,
            executed_at="2026-04-13T09:01:00+09:00",
        )
        row = order_repo.sync_execution_summary(
            client_order_id=client_order_id,
        )
    return row


def _seed_filled_order(
    conn,
    order_repo,
    execution_repo,
    *,
    client_order_id="COID_FILLED_CANCEL",
):
    row = _seed_submitted_order(conn, order_repo, client_order_id=client_order_id)
    with transaction(conn):
        execution_repo.insert_if_new(
            order_id=row.id,
            kis_exec_no=f"EXEC_{client_order_id}",
            symbol=row.symbol,
            side=row.side,
            qty=row.qty,
            price=row.price,
            executed_at="2026-04-13T09:01:00+09:00",
        )
        row = order_repo.mark_filled(
            client_order_id=client_order_id,
            closed_at="2026-04-13T09:02:00+09:00",
        )
    return row


def _cancel_broker_response(*, code="005930", quantity=10, order_no="KIS_CANCEL_DONE"):
    return OrderInfo(
        code=code,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=0,
        status=OrderStatus.CANCELLED,
        order_no=order_no,
        filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 5, 1)),
        raw_response={"odno": order_no},
    )


def test_cancel_order_success_marks_submitted_order_cancelled(conn, repos):
    order_repo, _, _ = repos
    seeded = _seed_submitted_order(conn, order_repo)

    broker = MagicMock()
    broker.cancel_order.return_value = _cancel_broker_response(
        code=seeded.symbol,
        quantity=seeded.qty,
    )
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=seeded.client_order_id)

    assert isinstance(result, CancelResult)
    assert result.outcome == CancelOutcome.CANCELLED
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.CANCELLED
    assert result.order_row.filled_qty == 0
    assert result.order_row.avg_fill_price == 0
    assert result.order_row.closed_at == "2026-04-13T09:05:00+09:00"

    broker.cancel_order.assert_called_once_with(
        order_no=seeded.kis_order_no,
        code=seeded.symbol,
        quantity=seeded.qty,
    )


def test_cancel_order_success_keeps_partial_fill_summary(conn, repos):
    order_repo, _, execution_repo = repos
    seeded = _seed_partial_order(conn, order_repo, execution_repo)

    broker = MagicMock()
    broker.cancel_order.return_value = _cancel_broker_response(
        code=seeded.symbol,
        quantity=seeded.qty,
        order_no="KIS_CANCEL_PARTIAL",
    )
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=seeded.client_order_id)

    assert result.outcome == CancelOutcome.CANCELLED
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.CANCELLED
    assert result.order_row.filled_qty == 4
    assert result.order_row.avg_fill_price == 70100
    assert result.order_row.closed_at == "2026-04-13T09:05:00+09:00"

    broker.cancel_order.assert_called_once_with(
        order_no=seeded.kis_order_no,
        code=seeded.symbol,
        quantity=seeded.qty,
    )


def test_cancel_order_blocks_pending_without_broker_call(conn, repos):
    order_repo, _, _ = repos
    with transaction(conn):
        pending = order_repo.create(
            client_order_id="COID_PENDING_CANCEL",
            symbol="005930",
            side="buy",
            qty=10,
            price=70000,
            order_type="LIMIT",
            strategy_name="cancel",
            requested_at="2026-04-13T09:00:00+09:00",
        )

    broker = MagicMock()
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=pending.client_order_id)

    assert result.outcome == CancelOutcome.BLOCKED
    assert result.error_code == "CANCEL_NOT_ALLOWED_STATUS"
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.PENDING
    broker.cancel_order.assert_not_called()


def test_cancel_order_blocks_terminal_status_without_broker_call(conn, repos):
    order_repo, _, execution_repo = repos
    filled = _seed_filled_order(conn, order_repo, execution_repo)

    broker = MagicMock()
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=filled.client_order_id)

    assert result.outcome == CancelOutcome.BLOCKED
    assert result.error_code == "CANCEL_NOT_ALLOWED_STATUS"
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.FILLED
    broker.cancel_order.assert_not_called()


def test_cancel_order_broker_rejection_keeps_original_status(conn, repos):
    order_repo, _, _ = repos
    seeded = _seed_submitted_order(conn, order_repo)

    broker = MagicMock()
    broker.cancel_order.side_effect = KisApiError(
        "cancel rejected",
        msg_cd="APBK0001",
        msg="already filled",
    )
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=seeded.client_order_id)

    assert result.outcome == CancelOutcome.REJECTED
    assert result.error_code == "APBK0001"
    assert result.error_message == "already filled"
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.SUBMITTED


def test_cancel_order_network_unknown_keeps_original_status(conn, repos):
    order_repo, _, _ = repos
    seeded = _seed_submitted_order(conn, order_repo)

    unknown_info = OrderInfo(
        code=seeded.symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=seeded.qty,
        price=0,
        status=OrderStatus.UNKNOWN,
        order_no=seeded.kis_order_no,
        filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 5, 1)),
        raw_response={},
    )
    broker = MagicMock()
    broker.cancel_order.side_effect = KisOrderError(
        "timeout",
        order_info=unknown_info,
    )
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=seeded.client_order_id)

    assert result.outcome == CancelOutcome.UNKNOWN
    assert result.error_code == "CANCEL_BROKER_CALL_NETWORK_OR_ORDER_ERROR"
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.SUBMITTED
    assert result.broker_info == unknown_info


def test_cancel_order_generic_kis_error_keeps_original_status(conn, repos):
    order_repo, _, _ = repos
    seeded = _seed_submitted_order(conn, order_repo)

    broker = MagicMock()
    broker.cancel_order.side_effect = KisError("parse failed")
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id=seeded.client_order_id)

    assert result.outcome == CancelOutcome.UNKNOWN
    assert result.error_code == "CANCEL_BROKER_CALL_UNEXPECTED_KIS_ERROR"
    assert result.order_row is not None
    assert result.order_row.status == DbOrderStatus.SUBMITTED
    assert result.broker_info is None


def test_cancel_order_missing_client_order_id_returns_blocked(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)

    result = service.cancel_order(client_order_id="COID_DOES_NOT_EXIST")

    assert result.outcome == CancelOutcome.BLOCKED
    assert result.error_code == "CANCEL_ORDER_NOT_FOUND"
    assert result.order_row is None
    broker.cancel_order.assert_not_called()
