"""Tests for UnresolvedOrderSyncService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from broker.kis.models import OrderInfo, OrderSide, OrderStatus, OrderType
from services import (
    UnresolvedOrderSyncAction,
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import DbOrderStatus, OrderRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 16, 10, 5, 0))


def _seed_unknown_with_order_no(conn, order_repo: OrderRepository, *, client_order_id: str, symbol: str):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            qty=1,
            price=70_000,
            order_type="LIMIT",
            strategy_name="seed",
            requested_at="2026-04-16T09:55:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no=f"KIS-{symbol}",
            submitted_at="2026-04-16T09:55:01+09:00",
        )
        order_repo.mark_unknown(client_order_id=client_order_id)


def _seed_submitted(conn, order_repo: OrderRepository, *, client_order_id: str, symbol: str):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol=symbol,
            side="buy",
            qty=1,
            price=70_000,
            order_type="LIMIT",
            strategy_name="seed",
            requested_at="2026-04-16T09:50:00+09:00",
        )
        order_repo.mark_submitted(
            client_order_id=client_order_id,
            kis_order_no=f"KIS-{symbol}",
            submitted_at="2026-04-16T09:50:01+09:00",
        )


def _accepted_info(symbol: str) -> OrderInfo:
    return OrderInfo(
        code=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        price=70_000,
        status=OrderStatus.ACCEPTED,
        order_no=f"KIS-{symbol}",
        filled_qty=0,
        timestamp=_fixed_now(),
        raw_response={"odno": f"KIS-{symbol}"},
    )


def _cancelled_info(symbol: str) -> OrderInfo:
    return OrderInfo(
        code=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        price=70_000,
        status=OrderStatus.CANCELLED,
        order_no=f"KIS-{symbol}",
        filled_qty=0,
        timestamp=_fixed_now(),
        raw_response={"odno": f"KIS-{symbol}"},
    )


def _filled_info(symbol: str) -> OrderInfo:
    return OrderInfo(
        code=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        price=70_000,
        status=OrderStatus.FILLED,
        order_no=f"KIS-{symbol}",
        filled_qty=1,
        timestamp=_fixed_now(),
        raw_response={"odno": f"KIS-{symbol}"},
    )


def test_preview_unknown_can_be_marked_submitted(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_unknown_with_order_no(
            conn,
            order_repo,
            client_order_id="COID_UNKNOWN",
            symbol="005930",
        )

        broker = MagicMock()
        broker.get_order_status.side_effect = [
            [_accepted_info("005930")],
        ]
        service = UnresolvedOrderSyncService(
            broker=broker,
            conn=conn,
            order_repo=order_repo,
            now_fn=_fixed_now,
        )

        result = service.sync_unresolved_orders(
            trade_date=TRADE_DATE,
            execute_sync=False,
        )

        assert result.preview_ready_count == 1
        assert result.synced_count == 0
        candidate = result.candidates[0]
        assert candidate.action == UnresolvedOrderSyncAction.MARK_SUBMITTED
        assert candidate.outcome == UnresolvedOrderSyncOutcome.PREVIEW_READY
        assert order_repo.get_by_client_order_id("COID_UNKNOWN").status == DbOrderStatus.UNKNOWN
    finally:
        conn.close()


def test_execute_unknown_is_marked_submitted(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_unknown_with_order_no(
            conn,
            order_repo,
            client_order_id="COID_SYNC_SUBMITTED",
            symbol="000660",
        )

        broker = MagicMock()
        broker.get_order_status.side_effect = [
            [_accepted_info("000660")],
        ]
        service = UnresolvedOrderSyncService(
            broker=broker,
            conn=conn,
            order_repo=order_repo,
            now_fn=_fixed_now,
        )

        result = service.sync_unresolved_orders(
            trade_date=TRADE_DATE,
            execute_sync=True,
        )

        assert result.synced_count == 1
        assert result.acted_count == 1
        assert result.candidates[0].outcome == UnresolvedOrderSyncOutcome.SYNCED
        assert order_repo.get_by_client_order_id("COID_SYNC_SUBMITTED").status == DbOrderStatus.SUBMITTED
    finally:
        conn.close()


def test_execute_submitted_cancelled_no_fill_is_marked_cancelled(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_submitted(
            conn,
            order_repo,
            client_order_id="COID_SYNC_CANCELLED",
            symbol="035420",
        )

        broker = MagicMock()
        broker.get_order_status.side_effect = [
            [],
            [_cancelled_info("035420")],
        ]
        service = UnresolvedOrderSyncService(
            broker=broker,
            conn=conn,
            order_repo=order_repo,
            now_fn=_fixed_now,
        )

        result = service.sync_unresolved_orders(
            trade_date=TRADE_DATE,
            execute_sync=True,
        )

        assert result.synced_count == 1
        assert result.candidates[0].action == UnresolvedOrderSyncAction.MARK_CANCELLED
        assert order_repo.get_by_client_order_id("COID_SYNC_CANCELLED").status == DbOrderStatus.CANCELLED
    finally:
        conn.close()


def test_filled_order_is_reported_as_execution_recovery_required(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_submitted(
            conn,
            order_repo,
            client_order_id="COID_RECOVERY",
            symbol="069500",
        )

        broker = MagicMock()
        broker.get_order_status.side_effect = [
            [],
            [_filled_info("069500")],
        ]
        service = UnresolvedOrderSyncService(
            broker=broker,
            conn=conn,
            order_repo=order_repo,
            now_fn=_fixed_now,
        )

        result = service.sync_unresolved_orders(
            trade_date=TRADE_DATE,
            execute_sync=False,
        )

        assert result.execution_recovery_required_count == 1
        assert result.candidates[0].outcome == UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED
        assert order_repo.get_by_client_order_id("COID_RECOVERY").status == DbOrderStatus.SUBMITTED
    finally:
        conn.close()
