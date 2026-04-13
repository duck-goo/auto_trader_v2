"""Shared helpers for storage repositories."""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import datetime
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class RepositoryError(RuntimeError):
    """Base repository error."""


class RepositoryInvariantError(RepositoryError):
    """Raised when persisted data violates a repository invariant."""


class IllegalStateTransition(RepositoryError):
    """Raised when an order status transition is not allowed."""

    def __init__(
        self,
        *,
        client_order_id: str,
        current_status: str,
        target_status: str,
    ) -> None:
        super().__init__(
            "Illegal order status transition: "
            f"client_order_id={client_order_id}, "
            f"current={current_status}, target={target_status}"
        )
        self.client_order_id = client_order_id
        self.current_status = current_status
        self.target_status = target_status


def require_write_transaction(conn: sqlite3.Connection) -> None:
    """Fail fast when a write method runs outside an explicit transaction."""
    if not conn.in_transaction:
        raise RepositoryError(
            "Write methods must run inside 'with transaction(conn):'."
        )


def require_non_empty_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} cannot be empty.")
    return stripped


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def require_positive_int(name: str, value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def require_non_negative_int(name: str, value: int) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer: {value!r}")
    return value


def require_side(value: str) -> str:
    side = require_non_empty_text("side", value).lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell': {value!r}")
    return side


def require_order_type(value: str) -> str:
    order_type = require_non_empty_text("order_type", value).upper()
    if order_type not in ("LIMIT", "MARKET"):
        raise ValueError(
            f"order_type must be 'LIMIT' or 'MARKET': {value!r}"
        )
    return order_type


def require_aware_iso8601(name: str, value: str) -> str:
    text = require_non_empty_text(name, value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO8601: {value!r}") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset: {value!r}")

    return text


class RowMapper:
    """Convert sqlite rows into dataclasses."""

    @staticmethod
    def map_one(
        row: sqlite3.Row | None,
        cls: type[T],
        *,
        converters: dict[str, Callable[[Any], Any]] | None = None,
    ) -> T | None:
        if row is None:
            return None

        mapped: dict[str, Any] = {}
        available_keys = set(row.keys())
        for field in fields(cls):
            if field.name not in available_keys:
                raise RepositoryInvariantError(
                    f"Missing column '{field.name}' for {cls.__name__}."
                )
            value = row[field.name]
            if converters and field.name in converters:
                value = converters[field.name](value)
            mapped[field.name] = value
        return cls(**mapped)

    @staticmethod
    def map_many(
        rows: list[sqlite3.Row],
        cls: type[T],
        *,
        converters: dict[str, Callable[[Any], Any]] | None = None,
    ) -> list[T]:
        result: list[T] = []
        for row in rows:
            item = RowMapper.map_one(row, cls, converters=converters)
            if item is not None:
                result.append(item)
        return result

class NegativePositionError(RepositoryError):
    """Raised when an execution would drive a position negative (short sell)."""

    def __init__(
        self,
        *,
        symbol: str,
        current_qty: int,
        sell_qty: int,
    ) -> None:
        super().__init__(
            "Execution would cause negative position: "
            f"symbol={symbol}, current_qty={current_qty}, sell_qty={sell_qty}"
        )
        self.symbol = symbol
        self.current_qty = current_qty
        self.sell_qty = sell_qty