"""Signal repository.

Append-only audit log of scan/strategy signals. Each row records:
    - when a symbol was flagged (scanned_at)
    - which strategy flagged it (strategy_name)
    - an optional numeric score
    - an optional structured payload (serialized as JSON in the DB,
      returned as a dict to callers)
    - whether this signal led to an action (acted)

All write methods must run inside `with transaction(conn):`.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from storage.repositories.base import (
    RepositoryInvariantError,
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_positive_int,
    require_write_transaction,
)


@dataclass(frozen=True)
class SignalRow:
    id: int
    scanned_at: str
    symbol: str
    strategy_name: str
    score: float | None
    payload: dict | None
    acted: bool


_SELECT_COLUMNS = (
    "id, scanned_at, symbol, strategy_name, score, payload_json AS payload, acted"
)


def _serialize_payload(payload: dict | None) -> str | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"payload must be a dict or None: {type(payload).__name__}")
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not JSON-serializable: {exc}") from exc


def _deserialize_payload(raw: Any) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RepositoryInvariantError(
            f"payload_json column must be TEXT, got {type(raw).__name__}"
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RepositoryInvariantError(
            f"Stored payload_json is not valid JSON: {raw!r}"
        ) from exc
    if not isinstance(value, dict):
        raise RepositoryInvariantError(
            f"Stored payload_json must deserialize to a dict, got "
            f"{type(value).__name__}"
        )
    return value


def _coerce_acted(value: Any) -> bool:
    # SQLite stores booleans as INTEGER (0/1).
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    raise RepositoryInvariantError(
        f"acted column must be INTEGER, got {type(value).__name__}"
    )


_ROW_CONVERTERS = {
    "payload": _deserialize_payload,
    "acted": _coerce_acted,
}


class SignalRepository:
    """Pure DB repository for signal rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def record(
        self,
        *,
        symbol: str,
        strategy_name: str,
        scanned_at: str,
        score: float | None = None,
        payload: dict | None = None,
    ) -> SignalRow:
        """
        Append a new signal row.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        symbol = require_non_empty_text("symbol", symbol)
        strategy_name = require_non_empty_text("strategy_name", strategy_name)
        scanned_at = require_aware_iso8601("scanned_at", scanned_at)

        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError(f"score must be float or None: {score!r}")
            if not math.isfinite(float(score)):
                raise ValueError(f"score must be finite: {score!r}")
            score = float(score)

        payload_json = _serialize_payload(payload)

        cursor = self._conn.execute(
            """
            INSERT INTO signals (
                scanned_at, symbol, strategy_name, score, payload_json, acted
            )
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (scanned_at, symbol, strategy_name, score, payload_json),
        )
        signal_id = cursor.lastrowid
        if signal_id is None:
            raise RepositoryInvariantError(
                "INSERT into signals returned no lastrowid."
            )
        return self._get_required(signal_id)

    def mark_acted(self, signal_id: int) -> SignalRow:
        """
        Mark a signal as acted (idempotent).

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        signal_id = require_positive_int("signal_id", signal_id)

        current = self._get_required(signal_id)
        if current.acted:
            return current

        self._conn.execute(
            "UPDATE signals SET acted = 1 WHERE id = ?",
            (signal_id,),
        )
        return self._get_required(signal_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, signal_id: int) -> SignalRow | None:
        signal_id = require_positive_int("signal_id", signal_id)
        row = self._conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        return RowMapper.map_one(row, SignalRow, converters=_ROW_CONVERTERS)

    def list_by_symbol(self, symbol: str, *, limit: int = 100) -> list[SignalRow]:
        symbol = require_non_empty_text("symbol", symbol)
        limit = require_positive_int("limit", limit)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM signals
            WHERE symbol = ?
            ORDER BY scanned_at DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        return RowMapper.map_many(rows, SignalRow, converters=_ROW_CONVERTERS)

    def list_by_strategy(
        self,
        strategy_name: str,
        *,
        limit: int = 100,
    ) -> list[SignalRow]:
        strategy_name = require_non_empty_text("strategy_name", strategy_name)
        limit = require_positive_int("limit", limit)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM signals
            WHERE strategy_name = ?
            ORDER BY scanned_at DESC, id DESC
            LIMIT ?
            """,
            (strategy_name, limit),
        ).fetchall()
        return RowMapper.map_many(rows, SignalRow, converters=_ROW_CONVERTERS)

    def list_between(
        self,
        *,
        start_at: str,
        end_at: str,
        limit: int = 1000,
    ) -> list[SignalRow]:
        """
        Return signals whose scanned_at falls in [start_at, end_at] inclusive.
        """
        start_at = require_aware_iso8601("start_at", start_at)
        end_at = require_aware_iso8601("end_at", end_at)
        limit = require_positive_int("limit", limit)
        if start_at > end_at:
            raise ValueError(
                f"start_at must be <= end_at: {start_at!r} > {end_at!r}"
            )
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM signals
            WHERE scanned_at BETWEEN ? AND ?
            ORDER BY scanned_at ASC, id ASC
            LIMIT ?
            """,
            (start_at, end_at, limit),
        ).fetchall()
        return RowMapper.map_many(rows, SignalRow, converters=_ROW_CONVERTERS)

    def list_unacted(self, *, limit: int = 100) -> list[SignalRow]:
        limit = require_positive_int("limit", limit)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM signals
            WHERE acted = 0
            ORDER BY scanned_at ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return RowMapper.map_many(rows, SignalRow, converters=_ROW_CONVERTERS)

    def list_unacted_by_strategies(
        self,
        strategy_names: list[str] | tuple[str, ...] | frozenset[str],
        *,
        limit: int = 100,
    ) -> list[SignalRow]:
        limit = require_positive_int("limit", limit)
        normalized_strategy_names = self._normalize_strategy_names(strategy_names)
        if not normalized_strategy_names:
            return []

        placeholders = ", ".join("?" for _ in normalized_strategy_names)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM signals
            WHERE acted = 0
              AND strategy_name IN ({placeholders})
            ORDER BY scanned_at ASC, id ASC
            LIMIT ?
            """,
            (*normalized_strategy_names, limit),
        ).fetchall()
        return RowMapper.map_many(rows, SignalRow, converters=_ROW_CONVERTERS)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get_required(self, signal_id: int) -> SignalRow:
        row = self.get(signal_id)
        if row is None:
            raise RepositoryInvariantError(
                f"Signal row expected but not found: id={signal_id!r}"
            )
        return row

    def _normalize_strategy_names(
        self,
        strategy_names: list[str] | tuple[str, ...] | frozenset[str],
    ) -> tuple[str, ...]:
        if not isinstance(strategy_names, (list, tuple, frozenset)):
            raise ValueError(
                "strategy_names must be a list, tuple, or frozenset of strings."
            )

        normalized_names: list[str] = []
        seen_names: set[str] = set()
        for raw_name in strategy_names:
            strategy_name = require_non_empty_text("strategy_name", raw_name)
            if strategy_name in seen_names:
                continue
            normalized_names.append(strategy_name)
            seen_names.add(strategy_name)
        return tuple(normalized_names)
