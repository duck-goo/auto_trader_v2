"""OrderService.place_order() - failure paths (Phase 3-A-2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from broker.kis.errors import KisApiError, KisError, KisOrderError
from broker.kis.models import OrderInfo, OrderSide, OrderStatus, OrderType
from services import (
    OrderOutcome,
    OrderResult,
    OrderService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DbOrderStatus,
    OrderRepository,
    PositionRepository,
)


KST = pytz.timezone("Asia/Seoul")


@dataclass
class _IdSeq:
    values: list[str]
    index: int = 0

    def __call__(self) -> str:
        value = self.values[self.index]
        self.index += 1
        return value


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
    return OrderRepository(conn), PositionRepository(conn)


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 13, 9, 0, 12))
    return lambda: fixed


def _make_service(conn, repos, broker, *, id_seq=None):
    order_repo, position_repo = repos
    return OrderService(
        broker=broker,
        conn=conn,
        order_repo=order_repo,
        position_repo=position_repo,
        now_fn=_fixed_now(),
        id_fn=_IdSeq(id_seq or ["aaaaaaaa"]),
    )


def _place_buy(service, qty=10, price=70000):
    return service.place_order(
        symbol="005930", side="buy", qty=qty, price=price,
        order_type="LIMIT", strategy_name="t",
    )


def _place_sell(service, qty=5, price=72000):
    return service.place_order(
        symbol="005930", side="sell", qty=qty, price=price,
        order_type="LIMIT", strategy_name="t",
    )


# =====================================================================
# REJECTED path
# =====================================================================
def test_broker_raises_kis_api_error_produces_rejected(conn, repos):
    order_repo, _ = repos
    broker = MagicMock()
    broker.place_order.side_effect = KisApiError("잔고 부족")
    # Try to give it msg_cd/msg attributes that OrderService tries to read.
    err = KisApiError("잔고 부족")
    try:
        err.msg_cd = "APBK0918"  # type: ignore[attr-defined]
        err.msg = "주문가능금액 부족"  # type: ignore[attr-defined]
    except Exception:
        pass
    broker.place_order.side_effect = err

    service = _make_service(conn, repos, broker)
    result = _place_buy(service)

    assert isinstance(result, OrderResult)
    assert result.outcome == OrderOutcome.REJECTED
    assert result.broker_info is None
    # error_code/error_message should be populated from exception if available.
    assert result.error_message  # non-empty

    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row.status == DbOrderStatus.REJECTED
    assert row.closed_at is not None
    # error_message stored.
    assert row.error_message is not None


def test_rejected_result_is_returned_not_raised(conn, repos):
    """REJECTED must be a return value, never an exception."""
    broker = MagicMock()
    broker.place_order.side_effect = KisApiError("boom")
    service = _make_service(conn, repos, broker)

    # Should not raise.
    result = _place_buy(service)
    assert result.outcome == OrderOutcome.REJECTED


# =====================================================================
# UNKNOWN path: KisOrderError (network / duplicate detection)
# =====================================================================
def test_broker_raises_kis_order_error_produces_unknown(conn, repos):
    order_repo, _ = repos
    broker = MagicMock()
    broker.place_order.side_effect = KisOrderError("timeout")
    service = _make_service(conn, repos, broker)

    result = _place_buy(service)
    assert result.outcome == OrderOutcome.UNKNOWN
    assert result.error_code == "BROKER_CALL_NETWORK_OR_ORDER_ERROR"
    assert "timeout" in (result.error_message or "")

    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row.status == DbOrderStatus.UNKNOWN


# =====================================================================
# UNKNOWN path: generic KisError
# =====================================================================
def test_broker_raises_generic_kis_error_produces_unknown(conn, repos):
    order_repo, _ = repos
    broker = MagicMock()
    broker.place_order.side_effect = KisError("parse failed")
    service = _make_service(conn, repos, broker)

    result = _place_buy(service)
    assert result.outcome == OrderOutcome.UNKNOWN
    assert result.error_code == "BROKER_CALL_UNEXPECTED_KIS_ERROR"

    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row.status == DbOrderStatus.UNKNOWN


# =====================================================================
# UNKNOWN path: broker returns success but order_no is empty/missing
# =====================================================================
def test_broker_returns_empty_order_no_produces_unknown(conn, repos):
    order_repo, _ = repos
    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=70000,
        status=OrderStatus.ACCEPTED,
        order_no="",   # <-- empty!
        filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={},
    )
    service = _make_service(conn, repos, broker)

    result = _place_buy(service)
    assert result.outcome == OrderOutcome.UNKNOWN
    assert result.error_code == "BROKER_ACCEPTED_WITHOUT_ORDER_NO"
    # We still surface broker_info so caller can inspect raw_response.
    assert result.broker_info is not None

    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row.status == DbOrderStatus.UNKNOWN


def test_broker_returns_none_order_no_produces_unknown(conn, repos):
    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        quantity=10, price=70000, status=OrderStatus.PENDING,
        order_no=None, filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={},
    )
    service = _make_service(conn, repos, broker)
    result = _place_buy(service)
    assert result.outcome == OrderOutcome.UNKNOWN


# =====================================================================
# FAILED path: pre-trade sell without position
# =====================================================================
def test_sell_without_any_position_produces_failed(conn, repos):
    order_repo, _ = repos
    broker = MagicMock()
    service = _make_service(conn, repos, broker)

    result = _place_sell(service, qty=5)
    assert result.outcome == OrderOutcome.FAILED
    assert result.error_code == "PRE_TRADE_INSUFFICIENT_POSITION"
    assert result.broker_info is None

    # Broker must NOT have been called.
    broker.place_order.assert_not_called()

    # Row exists and is FAILED (audit trail preserved).
    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row.status == DbOrderStatus.FAILED
    assert row.error_code == "PRE_TRADE_INSUFFICIENT_POSITION"
    assert "available=0" in row.error_message


def test_sell_with_insufficient_position_produces_failed(conn, repos):
    order_repo, position_repo = repos
    broker = MagicMock()

    # Seed a position of 3.
    with transaction(conn):
        position_repo.apply_execution(
            symbol="005930", side="buy", qty=3, price=70000,
            executed_at="2026-04-12T09:00:00+09:00",
        )

    service = _make_service(conn, repos, broker)
    result = _place_sell(service, qty=5)  # want to sell 5, only have 3

    assert result.outcome == OrderOutcome.FAILED
    assert result.error_code == "PRE_TRADE_INSUFFICIENT_POSITION"
    broker.place_order.assert_not_called()


def test_sell_with_exact_position_succeeds(conn, repos):
    """Exact holding should pass pre-check and reach broker."""
    _order_repo, position_repo = repos

    with transaction(conn):
        position_repo.apply_execution(
            symbol="005930", side="buy", qty=5, price=70000,
            executed_at="2026-04-12T09:00:00+09:00",
        )

    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930", side=OrderSide.SELL, order_type=OrderType.LIMIT,
        quantity=5, price=72000, status=OrderStatus.ACCEPTED,
        order_no="KIS_SELL", filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={},
    )
    service = _make_service(conn, repos, broker)

    result = _place_sell(service, qty=5)
    assert result.outcome == OrderOutcome.SUBMITTED
    broker.place_order.assert_called_once()


# =====================================================================
# Buy side: pre-trade check is a no-op
# =====================================================================
def test_buy_skips_position_check(conn, repos):
    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        quantity=10, price=70000, status=OrderStatus.ACCEPTED,
        order_no="KIS_BUY", filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={},
    )
    service = _make_service(conn, repos, broker)
    result = _place_buy(service)
    assert result.outcome == OrderOutcome.SUBMITTED


# =====================================================================
# Input validation: price/order_type consistency
# =====================================================================
def test_market_order_with_nonzero_price_raises(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)
    with pytest.raises(ValueError):
        service.place_order(
            symbol="005930", side="buy", qty=1, price=70000,
            order_type="MARKET", strategy_name="t",
        )
    broker.place_order.assert_not_called()


def test_limit_order_with_zero_price_raises(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)
    with pytest.raises(ValueError):
        service.place_order(
            symbol="005930", side="buy", qty=1, price=0,
            order_type="LIMIT", strategy_name="t",
        )
    broker.place_order.assert_not_called()


def test_market_order_with_zero_price_passes_to_broker(conn, repos):
    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930", side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=10, price=0, status=OrderStatus.ACCEPTED,
        order_no="KIS_MKT", filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={},
    )
    service = _make_service(conn, repos, broker)
    result = service.place_order(
        symbol="005930", side="buy", qty=10, price=0,
        order_type="MARKET", strategy_name="t",
    )
    assert result.outcome == OrderOutcome.SUBMITTED
    broker.place_order.assert_called_once_with(
        code="005930", side="buy", quantity=10, price=0,
    )


# =====================================================================
# Unexpected (non-KIS) exceptions: propagate unchanged
# =====================================================================
def test_unexpected_exception_propagates(conn, repos):
    """
    A RuntimeError not in the KIS hierarchy must propagate so that
    operators see the real bug instead of it being silently buried
    as UNKNOWN.
    """
    order_repo, _ = repos
    broker = MagicMock()
    broker.place_order.side_effect = RuntimeError("code bug")
    service = _make_service(conn, repos, broker)

    with pytest.raises(RuntimeError):
        _place_buy(service)

    # PENDING row remains (Phase 3-B will recover).
    # The client_order_id is not accessible from the caller, but we can
    # verify at least one PENDING row exists.
    rows = conn.execute(
        "SELECT status FROM orders WHERE status = 'PENDING'"
    ).fetchall()
    assert len(rows) == 1