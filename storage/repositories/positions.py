"""Position repository.

Positions table holds one row per symbol. This repository supports two
independent write paths:

    * apply_execution()     - internal ledger update driven by executions
    * upsert_from_broker()  - snapshot overwrite from KIS balance API

Both paths are necessary. Use apply_execution() inside the same transaction
that inserts the execution row. Use upsert_from_broker() only during an
explicit reconciliation step; it overwrites the internal ledger without
regard to prior state.

All write methods must run inside `with transaction(conn):`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from storage.repositories.base import (
    NegativePositionError,
    RepositoryInvariantError,
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_positive_int,
    require_side,
    require_write_transaction,
)


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    qty: int
    avg_price: int
    updated_at: str


_POSITION_COLUMNS = "symbol, qty, avg_price, updated_at"


class PositionRepository:
    """Pure DB repository for position rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, symbol: str) -> PositionRow | None:
        symbol = require_non_empty_text("symbol", symbol)
        row = self._conn.execute(
            f"SELECT {_POSITION_COLUMNS} FROM positions WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        return RowMapper.map_one(row, PositionRow)

    def list_all(self) -> list[PositionRow]:
        """Return positions with qty > 0 (live holdings)."""
        rows = self._conn.execute(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM positions
            WHERE qty > 0
            ORDER BY symbol ASC
            """
        ).fetchall()
        return RowMapper.map_many(rows, PositionRow)

    def list_all_including_zero(self) -> list[PositionRow]:
        """Return all rows including qty = 0 (audit trail of closed positions)."""
        rows = self._conn.execute(
            f"SELECT {_POSITION_COLUMNS} FROM positions ORDER BY symbol ASC"
        ).fetchall()
        return RowMapper.map_many(rows, PositionRow)

    # ------------------------------------------------------------------
    # Write: internal ledger
    # ------------------------------------------------------------------
    def apply_execution(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        price: int,
        executed_at: str,
    ) -> PositionRow:
        """
        Update position based on a single execution event.

        BUY:
            new_qty   = old_qty + exec_qty
            new_avg   = round((old_qty * old_avg + exec_qty * exec_price) / new_qty)
            (banker's rounding via Python round(); long-run bias ~ 0)

        SELL:
            new_qty   = old_qty - exec_qty
            new_avg   = old_avg (unchanged)
            if new_qty == 0: new_avg = 0
            if new_qty < 0: raise NegativePositionError

        Caller must ensure this runs inside `with transaction(conn):` alongside
        the matching ExecutionRepository.insert_if_new() call.
        """
        require_write_transaction(self._conn)
        symbol = require_non_empty_text("symbol", symbol)
        side = require_side(side)
        qty = require_positive_int("qty", qty)
        price = require_non_negative_int("price", price)
        executed_at = require_aware_iso8601("executed_at", executed_at)

        current = self.get(symbol)
        old_qty = current.qty if current else 0
        old_avg = current.avg_price if current else 0

        if side == "buy":
            new_qty = old_qty + qty
            if new_qty <= 0:
                # Defensive: cannot happen with current validation, but catch
                # any future bug before it corrupts the ledger.
                raise RepositoryInvariantError(
                    f"Non-positive qty after BUY: symbol={symbol}, "
                    f"old_qty={old_qty}, exec_qty={qty}"
                )
            if old_qty == 0:
                new_avg = price
            else:
                total_notional = old_qty * old_avg + qty * price
                new_avg = int(round(total_notional / new_qty))
        else:  # sell
            if qty > old_qty:
                raise NegativePositionError(
                    symbol=symbol,
                    current_qty=old_qty,
                    sell_qty=qty,
                )
            new_qty = old_qty - qty
            new_avg = 0 if new_qty == 0 else old_avg

        self._upsert(symbol, new_qty, new_avg, executed_at)
        return self._get_required(symbol)

    # ------------------------------------------------------------------
    # Write: broker snapshot reconciliation
    # ------------------------------------------------------------------
    def upsert_from_broker(
        self,
        *,
        symbol: str,
        qty: int,
        avg_price: int,
        updated_at: str,
    ) -> PositionRow:
        """
        Overwrite a position with a broker balance snapshot.

        Warning: this bypasses the internal ledger. Call only during an
        explicit reconciliation pass, never in the normal execution flow.
        The caller is responsible for logging any diff between pre- and
        post-reconciliation state.
        """
        require_write_transaction(self._conn)
        symbol = require_non_empty_text("symbol", symbol)
        qty = require_non_negative_int("qty", qty)
        avg_price = require_non_negative_int("avg_price", avg_price)
        updated_at = require_aware_iso8601("updated_at", updated_at)

        # Enforce the CHECK-style invariant explicitly: qty=0 must pair with
        # avg_price=0 (no orphan average for a closed position).
        if qty == 0 and avg_price != 0:
            raise RepositoryInvariantError(
                f"avg_price must be 0 when qty == 0: symbol={symbol}, "
                f"avg_price={avg_price}"
            )

        self._upsert(symbol, qty, avg_price, updated_at)
        return self._get_required(symbol)

    # ------------------------------------------------------------------
    # Write: force close
    # ------------------------------------------------------------------
    def clear(self, *, symbol: str, updated_at: str) -> PositionRow:
        """
        Force a position to qty=0, avg_price=0 (audit row retained).

        Intended for manual reconciliation or recovery, not normal flow.
        """
        require_write_transaction(self._conn)
        symbol = require_non_empty_text("symbol", symbol)
        updated_at = require_aware_iso8601("updated_at", updated_at)

        self._upsert(symbol, 0, 0, updated_at)
        return self._get_required(symbol)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _upsert(
        self,
        symbol: str,
        qty: int,
        avg_price: int,
        updated_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO positions (symbol, qty, avg_price, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                qty = excluded.qty,
                avg_price = excluded.avg_price,
                updated_at = excluded.updated_at
            """,
            (symbol, qty, avg_price, updated_at),
        )

    def _get_required(self, symbol: str) -> PositionRow:
        row = self.get(symbol)
        if row is None:
            raise RepositoryInvariantError(
                f"Position row expected but not found: symbol={symbol!r}"
            )
        return row