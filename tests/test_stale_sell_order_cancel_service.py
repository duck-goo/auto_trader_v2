"""Tests for StaleSellOrderCancelService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz

from services import (
    CancelOutcome,
    CancelResult,
    OrderService,
    StaleSellOrderCancelOutcome,
    StaleSellOrderCancelService,
    StaleSellOrderCancelSettings,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import DbOrderStatus, OrderRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


def _fixed_now() -> datetime:
    return KST.localize(datetime(2026, 4, 17, 10, 0, 0))


def _settings() -> StaleSellOrderCancelSettings:
    return StaleSellOrderCancelSettings(timeout_seconds=300)


def _seed_order(
    conn,
    order_repo: OrderRepository,
    *,
    client_order_id: str,
    symbol: str,
    side: str,
    requested_at: str,
    status: DbOrderStatus,
    kis_order_no: str | None = None,
):
    with transaction(conn):
        order_repo.create(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            qty=1,
            price=70_000,
            order_type="LIMIT",
            strategy_name="seed",
            requested_at=requested_at,
        )
        if status == DbOrderStatus.SUBMITTED:
            order_repo.mark_submitted(
                client_order_id=client_order_id,
                kis_order_no=kis_order_no or f"KIS-{symbol}",
                submitted_at=requested_at,
            )


def test_preview_marks_only_stale_submitted_sell_as_ready(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_order(
            conn,
            order_repo,
            client_order_id="SELL_STALE",
            symbol="005930",
            side="sell",
            requested_at="2026-04-17T09:50:00+09:00",
            status=DbOrderStatus.SUBMITTED,
        )
        _seed_order(
            conn,
            order_repo,
            client_order_id="BUY_STALE",
            symbol="000660",
            side="buy",
            requested_at="2026-04-17T09:50:00+09:00",
            status=DbOrderStatus.SUBMITTED,
        )

        order_service = MagicMock(spec=OrderService)
        service = StaleSellOrderCancelService(
            order_repo=order_repo,
            order_service=order_service,
            now_fn=_fixed_now,
        )

        result = service.cancel_stale_orders(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_cancels=False,
        )

        assert result.unresolved_order_count == 2
        assert result.preview_ready_count == 1
        assert result.skipped_count == 1
        ready = next(
            item
            for item in result.candidates
            if item.outcome == StaleSellOrderCancelOutcome.PREVIEW_READY
        )
        assert ready.client_order_id == "SELL_STALE"
        order_service.cancel_order.assert_not_called()
    finally:
        conn.close()


def test_execute_cancels_stale_submitted_sell_order(test_db_path):
    run_migrations(test_db_path)
    conn = get_connection(test_db_path)
    try:
        order_repo = OrderRepository(conn)
        _seed_order(
            conn,
            order_repo,
            client_order_id="SELL_CANCEL",
            symbol="005930",
            side="sell",
            requested_at="2026-04-17T09:40:00+09:00",
            status=DbOrderStatus.SUBMITTED,
        )

        order_service = MagicMock(spec=OrderService)
        order_service.cancel_order.return_value = CancelResult(
            outcome=CancelOutcome.CANCELLED,
            client_order_id="SELL_CANCEL",
            order_row=order_repo.get_by_client_order_id("SELL_CANCEL"),
            broker_info=None,
            error_code=None,
            error_message=None,
        )
        service = StaleSellOrderCancelService(
            order_repo=order_repo,
            order_service=order_service,
            now_fn=_fixed_now,
        )

        result = service.cancel_stale_orders(
            trade_date=TRADE_DATE,
            settings=_settings(),
            execute_cancels=True,
        )

        assert result.cancelled_count == 1
        assert result.acted_count == 1
        assert result.candidates[0].outcome == StaleSellOrderCancelOutcome.CANCELLED
        order_service.cancel_order.assert_called_once_with(
            client_order_id="SELL_CANCEL"
        )
    finally:
        conn.close()
