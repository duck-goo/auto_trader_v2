"""OrderService.place_order() - success path (Phase 3-A-1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from broker.kis.models import OrderInfo, OrderSide, OrderStatus, OrderType
from services import (
    DuplicateClientOrderIdError,
    OrderOutcome,
    OrderResult,
    OrderService,
)
from services.order_service import _normalize_strategy_name
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
    """Deterministic id_fn that yields a fixed sequence of hex strings."""
    values: list[str]
    index: int = 0

    def __call__(self) -> str:
        if self.index >= len(self.values):
            raise RuntimeError("id_fn sequence exhausted")
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


def _fixed_now(year=2026, month=4, day=13, hour=9, minute=0, second=12):
    fixed = KST.localize(datetime(year, month, day, hour, minute, second))
    return lambda: fixed


def _make_broker_returning(order_no: str) -> MagicMock:
    broker = MagicMock()
    broker.place_order.return_value = OrderInfo(
        code="005930",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        price=70000,
        status=OrderStatus.ACCEPTED,
        order_no=order_no,
        filled_qty=0,
        timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
        raw_response={"odno": order_no},
    )
    return broker


def _make_service(conn, repos, broker, *, now_fn=None, id_fn=None):
    order_repo, position_repo = repos
    return OrderService(
        broker=broker,
        conn=conn,
        order_repo=order_repo,
        position_repo=position_repo,
        now_fn=now_fn or _fixed_now(),
        id_fn=id_fn or _IdSeq(["aaaaaaaa", "bbbbbbbb", "cccccccc"]),
    )


# ---------------------------------------------------------------------
# _normalize_strategy_name
# ---------------------------------------------------------------------
def test_normalize_strategy_name_handles_none_and_empty():
    assert _normalize_strategy_name(None) == "nostrategy"
    assert _normalize_strategy_name("") == "nostrategy"
    assert _normalize_strategy_name("   ") == "nostrategy"


def test_normalize_strategy_name_sanitizes_special_chars():
    assert _normalize_strategy_name("rsi reversal") == "rsi_reversal"
    assert _normalize_strategy_name("모멘텀-v2") == "____v2"
    assert _normalize_strategy_name("ok_strategy_123") == "ok_strategy_123"


def test_normalize_strategy_name_truncates_to_20():
    long_name = "a" * 50
    assert _normalize_strategy_name(long_name) == "a" * 20


# ---------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------
def test_place_order_success_creates_pending_then_submitted(conn, repos):
    order_repo, _ = repos
    broker = _make_broker_returning("KIS_ORDER_001")
    service = _make_service(
        conn, repos, broker,
        now_fn=_fixed_now(),
        id_fn=_IdSeq(["aaaaaaaa"]),
    )

    result = service.place_order(
        symbol="005930",
        side=OrderSide.BUY,
        qty=10,
        price=70000,
        order_type=OrderType.LIMIT,
        strategy_name="momo",
    )

    # OrderResult structure
    assert isinstance(result, OrderResult)
    assert result.outcome == OrderOutcome.SUBMITTED
    assert result.error_code is None
    assert result.error_message is None
    assert result.broker_info is not None
    assert result.broker_info.order_no == "KIS_ORDER_001"

    # client_order_id format: YYYYMMDDHHMMSS-strategy-xxxxxxxx
    assert result.client_order_id == "20260413090012-momo-aaaaaaaa"

    # DB row is SUBMITTED with kis_order_no set
    row = order_repo.get_by_client_order_id(result.client_order_id)
    assert row is not None
    assert row.status == DbOrderStatus.SUBMITTED
    assert row.kis_order_no == "KIS_ORDER_001"
    assert row.symbol == "005930"
    assert row.qty == 10
    assert row.price == 70000
    assert row.submitted_at is not None

    # Broker called exactly once with correct args
    broker.place_order.assert_called_once_with(
        code="005930",
        side="buy",
        quantity=10,
        price=70000,
    )


def test_place_order_accepts_string_side_and_type(conn, repos):
    """Lowercase 'sell' + 'limit' strings should be accepted and normalized.
    Requires seeding a position since Phase 3-A-2 added pre-sell validation.
    """
    _order_repo, position_repo = repos

    # Seed enough position to pass the pre-sell check.
    with transaction(conn):
        position_repo.apply_execution(
            symbol="000660", side="buy", qty=3, price=120000,
            executed_at="2026-04-12T09:00:00+09:00",
        )

    broker = _make_broker_returning("KIS_002")
    service = _make_service(conn, repos, broker)

    result = service.place_order(
        symbol="000660",
        side="sell",
        qty=3,
        price=120000,
        order_type="limit",
        strategy_name="rsi",
    )
    assert result.outcome == OrderOutcome.SUBMITTED
    assert result.order_row.side == "sell"
    assert result.order_row.order_type == "LIMIT"


def test_place_order_handles_none_strategy_name(conn, repos):
    broker = _make_broker_returning("KIS_003")
    service = _make_service(
        conn, repos, broker,
        id_fn=_IdSeq(["deadbeef"]),
    )

    result = service.place_order(
        symbol="005930", side="buy", qty=1, price=70000,
        order_type="LIMIT", strategy_name=None,
    )
    assert "nostrategy" in result.client_order_id
    assert result.client_order_id.endswith("-deadbeef")


# ---------------------------------------------------------------------
# client_order_id UNIQUE collision retry
# ---------------------------------------------------------------------
def test_place_order_retries_once_on_unique_collision(conn, repos):
    order_repo, _ = repos
    # Seed a row with the colliding client_order_id up front.
    from storage.db import transaction

    pre_existing_coid = "20260413090012-momo-aaaaaaaa"
    with transaction(conn):
        order_repo.create(
            client_order_id=pre_existing_coid,
            symbol="000660", side="buy", qty=1, price=1,
            order_type="LIMIT", strategy_name="prev",
            requested_at="2026-04-12T09:00:00+09:00",
        )

    broker = _make_broker_returning("KIS_RETRY")
    service = _make_service(
        conn, repos, broker,
        now_fn=_fixed_now(),
        id_fn=_IdSeq(["aaaaaaaa", "bbbbbbbb"]),  # 1st collides, 2nd succeeds
    )

    result = service.place_order(
        symbol="005930", side="buy", qty=10, price=70000,
        order_type="LIMIT", strategy_name="momo",
    )
    assert result.outcome == OrderOutcome.SUBMITTED
    assert result.client_order_id == "20260413090012-momo-bbbbbbbb"


def test_place_order_raises_after_two_collisions(conn, repos):
    order_repo, _ = repos
    from storage.db import transaction

    # Seed BOTH values the id_fn will produce.
    for hex_val in ("aaaaaaaa", "bbbbbbbb"):
        coid = f"20260413090012-momo-{hex_val}"
        with transaction(conn):
            order_repo.create(
                client_order_id=coid, symbol="000660", side="buy",
                qty=1, price=1, order_type="LIMIT",
                strategy_name="prev",
                requested_at="2026-04-12T09:00:00+09:00",
            )

    broker = _make_broker_returning("KIS_X")
    service = _make_service(
        conn, repos, broker,
        now_fn=_fixed_now(),
        id_fn=_IdSeq(["aaaaaaaa", "bbbbbbbb"]),
    )

    with pytest.raises(DuplicateClientOrderIdError) as exc_info:
        service.place_order(
            symbol="005930", side="buy", qty=10, price=70000,
            order_type="LIMIT", strategy_name="momo",
        )
    assert exc_info.value.attempts == 2
    # Broker must NOT have been called.
    broker.place_order.assert_not_called()


# ---------------------------------------------------------------------
# Atomicity: broker is called AFTER PENDING row exists
# ---------------------------------------------------------------------
def test_broker_call_happens_after_pending_row_exists(conn, repos):
    """
    Invariant: by the time the broker is called, the DB already has a
    PENDING row. We verify this by having the broker mock inspect the DB
    at call time.
    """
    order_repo, _ = repos
    captured = {}

    def _inspect_db(**kwargs):
        rows = conn.execute(
            "SELECT client_order_id, status FROM orders"
        ).fetchall()
        captured["rows_at_broker_call"] = [
            (r["client_order_id"], r["status"]) for r in rows
        ]
        return OrderInfo(
            code=kwargs["code"],
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=kwargs["quantity"],
            price=kwargs["price"],
            status=OrderStatus.ACCEPTED,
            order_no="KIS_INSPECT",
            filled_qty=0,
            timestamp=KST.localize(datetime(2026, 4, 13, 9, 0, 13)),
            raw_response={},
        )

    broker = MagicMock()
    broker.place_order.side_effect = _inspect_db
    service = _make_service(conn, repos, broker, id_fn=_IdSeq(["aaaaaaaa"]))

    service.place_order(
        symbol="005930", side="buy", qty=10, price=70000,
        order_type="LIMIT", strategy_name="momo",
    )

    assert captured["rows_at_broker_call"] == [
        ("20260413090012-momo-aaaaaaaa", "PENDING")
    ]


# ---------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------
def test_place_order_rejects_bad_side(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)
    with pytest.raises(ValueError):
        service.place_order(
            symbol="005930", side="long", qty=1, price=1,
            order_type="LIMIT", strategy_name="x",
        )
    broker.place_order.assert_not_called()


def test_place_order_rejects_bad_order_type(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)
    with pytest.raises(ValueError):
        service.place_order(
            symbol="005930", side="buy", qty=1, price=1,
            order_type="stop", strategy_name="x",
        )
    broker.place_order.assert_not_called()


def test_place_order_rejects_non_positive_qty(conn, repos):
    broker = MagicMock()
    service = _make_service(conn, repos, broker)
    with pytest.raises(ValueError):
        service.place_order(
            symbol="005930", side="buy", qty=0, price=70000,
            order_type="LIMIT", strategy_name="x",
        )
    broker.place_order.assert_not_called()