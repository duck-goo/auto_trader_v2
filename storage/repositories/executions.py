"""Execution repository."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from storage.repositories.base import (
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_positive_int,
    require_side,
    require_write_transaction,
)


@dataclass(frozen=True)
class ExecutionRow:
    id: int
    order_id: int
    kis_exec_no: str
    symbol: str
    side: str
    qty: int
    price: int
    executed_at: str


class ExecutionRepository:
    """Pure DB repository for execution rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_if_new(
        self,
        *,
        order_id: int,
        kis_exec_no: str,
        symbol: str,
        side: str,
        qty: int,
        price: int,
        executed_at: str,
    ) -> bool:
        """
        Insert a new execution row.

        This method must run inside `with transaction(conn):`.
        It returns False only for a duplicate `(order_id, kis_exec_no)` pair.
        """
        require_write_transaction(self._conn)
        order_id = require_positive_int("order_id", order_id)
        kis_exec_no = require_non_empty_text("kis_exec_no", kis_exec_no)
        symbol = require_non_empty_text("symbol", symbol)
        side = require_side(side)
        qty = require_positive_int("qty", qty)
        price = require_non_negative_int("price", price)
        executed_at = require_aware_iso8601("executed_at", executed_at)

        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO executions (
                order_id,
                kis_exec_no,
                symbol,
                side,
                qty,
                price,
                executed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                kis_exec_no,
                symbol,
                side,
                qty,
                price,
                executed_at,
            ),
        )
        return cursor.rowcount == 1

    def list_by_order(self, order_id: int) -> list[ExecutionRow]:
        order_id = require_positive_int("order_id", order_id)
        rows = self._conn.execute(
            """
            SELECT
                id,
                order_id,
                kis_exec_no,
                symbol,
                side,
                qty,
                price,
                executed_at
            FROM executions
            WHERE order_id = ?
            ORDER BY executed_at ASC, id ASC
            """,
            (order_id,),
        ).fetchall()
        return RowMapper.map_many(rows, ExecutionRow)
