"""Central order status mapping between broker and storage layers."""

from __future__ import annotations

import enum

from broker.kis.models import OrderStatus as BrokerOrderStatus

from storage.repositories.base import IllegalStateTransition


class DbOrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


BROKER_TO_DB_STATUS_MAP: dict[BrokerOrderStatus, DbOrderStatus] = {
    BrokerOrderStatus.PENDING: DbOrderStatus.PENDING,
    BrokerOrderStatus.ACCEPTED: DbOrderStatus.SUBMITTED,
    BrokerOrderStatus.UNKNOWN: DbOrderStatus.UNKNOWN,
    BrokerOrderStatus.PARTIAL: DbOrderStatus.PARTIAL,
    BrokerOrderStatus.FILLED: DbOrderStatus.FILLED,
    BrokerOrderStatus.CANCELLED: DbOrderStatus.CANCELLED,
    BrokerOrderStatus.REJECTED: DbOrderStatus.REJECTED,
}

TERMINAL_DB_ORDER_STATUSES = frozenset(
    {
        DbOrderStatus.FILLED,
        DbOrderStatus.CANCELLED,
        DbOrderStatus.REJECTED,
        DbOrderStatus.FAILED,
    }
)

UNRESOLVED_DB_ORDER_STATUSES = frozenset(
    {
        DbOrderStatus.PENDING,
        DbOrderStatus.SUBMITTED,
        DbOrderStatus.UNKNOWN,
        DbOrderStatus.PARTIAL,
    }
)

ALLOWED_TRANSITIONS: dict[DbOrderStatus, frozenset[DbOrderStatus]] = {
    DbOrderStatus.PENDING: frozenset(DbOrderStatus),
    DbOrderStatus.SUBMITTED: frozenset(
        {
            DbOrderStatus.SUBMITTED,
            DbOrderStatus.UNKNOWN,
            DbOrderStatus.PARTIAL,
            DbOrderStatus.FILLED,
            DbOrderStatus.CANCELLED,
            DbOrderStatus.REJECTED,
            DbOrderStatus.FAILED,
        }
    ),
    DbOrderStatus.UNKNOWN: frozenset(DbOrderStatus),
    DbOrderStatus.PARTIAL: frozenset(
        {
            DbOrderStatus.PARTIAL,
            DbOrderStatus.UNKNOWN,
            DbOrderStatus.FILLED,
            DbOrderStatus.CANCELLED,
            DbOrderStatus.FAILED,
        }
    ),
    DbOrderStatus.FILLED: frozenset({DbOrderStatus.FILLED}),
    DbOrderStatus.CANCELLED: frozenset({DbOrderStatus.CANCELLED}),
    DbOrderStatus.REJECTED: frozenset({DbOrderStatus.REJECTED}),
    DbOrderStatus.FAILED: frozenset({DbOrderStatus.FAILED}),
}


def coerce_db_order_status(value: DbOrderStatus | str) -> DbOrderStatus:
    if isinstance(value, DbOrderStatus):
        return value
    try:
        return DbOrderStatus(value)
    except ValueError as exc:
        raise ValueError(f"Unknown DB order status: {value!r}") from exc


def broker_status_to_db(status: BrokerOrderStatus) -> DbOrderStatus:
    try:
        return BROKER_TO_DB_STATUS_MAP[status]
    except KeyError as exc:
        raise ValueError(f"Unsupported broker order status: {status!r}") from exc


def assert_transition_allowed(
    current_status: DbOrderStatus | str,
    target_status: DbOrderStatus | str,
    *,
    client_order_id: str,
) -> None:
    current = coerce_db_order_status(current_status)
    target = coerce_db_order_status(target_status)

    allowed_targets = ALLOWED_TRANSITIONS[current]
    if target not in allowed_targets:
        raise IllegalStateTransition(
            client_order_id=client_order_id,
            current_status=current.value,
            target_status=target.value,
        )
