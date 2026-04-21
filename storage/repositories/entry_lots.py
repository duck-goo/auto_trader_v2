"""Repository for actual filled entry lots."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pytz

from storage.repositories.base import (
    NegativePositionError,
    RepositoryError,
    RepositoryInvariantError,
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_positive_int,
    require_write_transaction,
)


_KST = pytz.timezone("Asia/Seoul")
ENTRY_SLOT_TIMING1 = "timing1"
ENTRY_SLOT_TIMING2_LEGACY = "timing2_legacy"
ENTRY_SLOT_TIMING2_MORNING = "timing2_morning"
ENTRY_SLOT_TIMING2_RANGE = "timing2_range"
ENTRY_SLOT_MANUAL = "manual"
ENTRY_SLOT_UNKNOWN = "unknown"
_ENTRY_SLOTS = {
    ENTRY_SLOT_TIMING1,
    ENTRY_SLOT_TIMING2_LEGACY,
    ENTRY_SLOT_TIMING2_MORNING,
    ENTRY_SLOT_TIMING2_RANGE,
    ENTRY_SLOT_MANUAL,
    ENTRY_SLOT_UNKNOWN,
}
_BUY_TIMING1_TRIGGER = "buy_timing1_intraday_trigger"
_BUY_TIMING2_LEGACY_TRIGGER = "buy_timing2_intraday_trigger"
_BUY_TIMING2_30S_MORNING_TRIGGER = "buy_timing2_30s_morning_open_reclaim"
_BUY_TIMING2_30S_RANGE_TRIGGER = "buy_timing2_30s_range_high_breakout"
_SELECT_COLUMNS = (
    "id, symbol, entry_order_id, entry_signal_id, entry_strategy_name, "
    "entry_slot, opened_at, closed_at, total_buy_qty, remaining_qty, "
    "avg_buy_price, realized_sell_qty, realized_pnl, status, updated_at"
)


@dataclass(frozen=True)
class EntryLotRow:
    id: int
    symbol: str
    entry_order_id: int
    entry_signal_id: int | None
    entry_strategy_name: str
    entry_slot: str
    opened_at: str
    closed_at: str | None
    total_buy_qty: int
    remaining_qty: int
    avg_buy_price: int
    realized_sell_qty: int
    realized_pnl: int
    status: str
    updated_at: str


class EntryLotRepository:
    """Pure DB repository for entry lots built from actual executions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def apply_buy_execution(
        self,
        *,
        entry_order_id: int,
        symbol: str,
        qty: int,
        price: int,
        executed_at: str,
        entry_strategy_name: str,
        entry_signal_id: int | None = None,
        entry_slot: str | None = None,
    ) -> EntryLotRow:
        """
        Create or expand an entry lot from an actual BUY execution.

        Split fills for the same buy order are aggregated into one lot. This
        method intentionally uses `qty`, not original order quantity.
        """
        require_write_transaction(self._conn)
        normalized_order_id = require_positive_int("entry_order_id", entry_order_id)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        normalized_qty = require_positive_int("qty", qty)
        normalized_price = require_non_negative_int("price", price)
        normalized_executed_at = require_aware_iso8601(
            "executed_at",
            executed_at,
        )
        normalized_strategy_name = require_non_empty_text(
            "entry_strategy_name",
            entry_strategy_name,
        )
        normalized_signal_id = self._normalize_optional_positive_int(
            "entry_signal_id",
            entry_signal_id,
        )
        normalized_slot = self._require_entry_slot(
            entry_slot or self.infer_entry_slot(normalized_strategy_name)
        )

        current = self.get_by_entry_order_id(normalized_order_id)
        if current is None:
            self._conn.execute(
                """
                INSERT INTO entry_lots (
                    symbol,
                    entry_order_id,
                    entry_signal_id,
                    entry_strategy_name,
                    entry_slot,
                    opened_at,
                    closed_at,
                    total_buy_qty,
                    remaining_qty,
                    avg_buy_price,
                    realized_sell_qty,
                    realized_pnl,
                    status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, 0, 'OPEN', ?)
                """,
                (
                    normalized_symbol,
                    normalized_order_id,
                    normalized_signal_id,
                    normalized_strategy_name,
                    normalized_slot,
                    normalized_executed_at,
                    normalized_qty,
                    normalized_qty,
                    normalized_price,
                    normalized_executed_at,
                ),
            )
            return self._get_required_by_entry_order_id(normalized_order_id)

        self._assert_same_lot_identity(
            current=current,
            symbol=normalized_symbol,
            entry_strategy_name=normalized_strategy_name,
            entry_signal_id=normalized_signal_id,
            entry_slot=normalized_slot,
        )
        if current.status != "OPEN":
            raise RepositoryError(
                "Cannot add a buy execution to a closed entry lot: "
                f"entry_order_id={normalized_order_id}, lot_id={current.id}"
            )

        new_total_buy_qty = current.total_buy_qty + normalized_qty
        new_remaining_qty = current.remaining_qty + normalized_qty
        total_notional = (
            current.total_buy_qty * current.avg_buy_price
            + normalized_qty * normalized_price
        )
        new_avg_buy_price = self._rounded_average(
            total_notional=total_notional,
            total_qty=new_total_buy_qty,
        )
        opened_at = self._min_iso(current.opened_at, normalized_executed_at)
        updated_at = self._max_iso(current.updated_at, normalized_executed_at)

        self._conn.execute(
            """
            UPDATE entry_lots
            SET opened_at = ?,
                total_buy_qty = ?,
                remaining_qty = ?,
                avg_buy_price = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                opened_at,
                new_total_buy_qty,
                new_remaining_qty,
                new_avg_buy_price,
                updated_at,
                current.id,
            ),
        )
        return self._get_required(current.id)

    def apply_sell_to_lot(
        self,
        *,
        lot_id: int,
        qty: int,
        price: int,
        executed_at: str,
        sell_cost_rate: float = 0.0,
    ) -> EntryLotRow:
        """
        Reduce one lot from an actual SELL execution.

        `sell_cost_rate` is the combined fee/tax ratio. It defaults to zero so
        the repository can be used before broker-specific rates are configured.
        """
        require_write_transaction(self._conn)
        normalized_lot_id = require_positive_int("lot_id", lot_id)
        normalized_qty = require_positive_int("qty", qty)
        normalized_price = require_non_negative_int("price", price)
        normalized_executed_at = require_aware_iso8601(
            "executed_at",
            executed_at,
        )
        normalized_cost_rate = self._require_non_negative_float(
            "sell_cost_rate",
            sell_cost_rate,
        )
        current = self._get_required(normalized_lot_id)
        if current.status != "OPEN":
            raise RepositoryError(f"Entry lot is already closed: lot_id={lot_id}")
        if normalized_qty > current.remaining_qty:
            raise NegativePositionError(
                symbol=current.symbol,
                current_qty=current.remaining_qty,
                sell_qty=normalized_qty,
            )

        gross_sell_amount = normalized_qty * normalized_price
        sell_cost = int(round(gross_sell_amount * normalized_cost_rate))
        realized_pnl_delta = (
            gross_sell_amount - sell_cost - normalized_qty * current.avg_buy_price
        )
        remaining_qty = current.remaining_qty - normalized_qty
        status = "CLOSED" if remaining_qty == 0 else "OPEN"
        closed_at = normalized_executed_at if status == "CLOSED" else None
        updated_at = self._max_iso(current.updated_at, normalized_executed_at)

        self._conn.execute(
            """
            UPDATE entry_lots
            SET remaining_qty = ?,
                realized_sell_qty = ?,
                realized_pnl = ?,
                status = ?,
                closed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                remaining_qty,
                current.realized_sell_qty + normalized_qty,
                current.realized_pnl + realized_pnl_delta,
                status,
                closed_at,
                updated_at,
                current.id,
            ),
        )
        return self._get_required(current.id)

    def get(self, lot_id: int) -> EntryLotRow | None:
        normalized_lot_id = require_positive_int("lot_id", lot_id)
        row = self._conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM entry_lots WHERE id = ?",
            (normalized_lot_id,),
        ).fetchone()
        return RowMapper.map_one(row, EntryLotRow)

    def get_by_entry_order_id(self, entry_order_id: int) -> EntryLotRow | None:
        normalized_order_id = require_positive_int("entry_order_id", entry_order_id)
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM entry_lots
            WHERE entry_order_id = ?
            """,
            (normalized_order_id,),
        ).fetchone()
        return RowMapper.map_one(row, EntryLotRow)

    def list_open_by_symbol(self, *, symbol: str) -> list[EntryLotRow]:
        normalized_symbol = require_non_empty_text("symbol", symbol)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM entry_lots
            WHERE symbol = ? AND status = 'OPEN'
            ORDER BY opened_at ASC, id ASC
            """,
            (normalized_symbol,),
        ).fetchall()
        return RowMapper.map_many(rows, EntryLotRow)

    def list_open_by_entry_slots(
        self,
        *,
        entry_slots: Sequence[str],
    ) -> list[EntryLotRow]:
        normalized_slots = self._normalize_entry_slots(entry_slots)
        placeholders = ", ".join("?" for _ in normalized_slots)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM entry_lots
            WHERE status = 'OPEN'
              AND entry_slot IN ({placeholders})
            ORDER BY opened_at ASC, id ASC
            """,
            tuple(normalized_slots),
        ).fetchall()
        return RowMapper.map_many(rows, EntryLotRow)

    def list_by_symbol(self, *, symbol: str) -> list[EntryLotRow]:
        normalized_symbol = require_non_empty_text("symbol", symbol)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM entry_lots
            WHERE symbol = ?
            ORDER BY opened_at ASC, id ASC
            """,
            (normalized_symbol,),
        ).fetchall()
        return RowMapper.map_many(rows, EntryLotRow)

    def _get_required(self, lot_id: int) -> EntryLotRow:
        row = self.get(lot_id)
        if row is None:
            raise RepositoryInvariantError(f"Entry lot not found: id={lot_id}")
        return row

    def _get_required_by_entry_order_id(self, entry_order_id: int) -> EntryLotRow:
        row = self.get_by_entry_order_id(entry_order_id)
        if row is None:
            raise RepositoryInvariantError(
                f"Entry lot not found: entry_order_id={entry_order_id}"
            )
        return row

    @staticmethod
    def infer_entry_slot(entry_strategy_name: str) -> str:
        strategy_name = require_non_empty_text(
            "entry_strategy_name",
            entry_strategy_name,
        )
        if strategy_name == _BUY_TIMING1_TRIGGER:
            return ENTRY_SLOT_TIMING1
        if strategy_name == _BUY_TIMING2_LEGACY_TRIGGER:
            return ENTRY_SLOT_TIMING2_LEGACY
        if strategy_name == _BUY_TIMING2_30S_MORNING_TRIGGER:
            return ENTRY_SLOT_TIMING2_MORNING
        if strategy_name == _BUY_TIMING2_30S_RANGE_TRIGGER:
            return ENTRY_SLOT_TIMING2_RANGE
        return ENTRY_SLOT_UNKNOWN

    @staticmethod
    def _require_entry_slot(value: str) -> str:
        entry_slot = require_non_empty_text("entry_slot", value)
        if entry_slot not in _ENTRY_SLOTS:
            raise ValueError(
                f"entry_slot must be one of {sorted(_ENTRY_SLOTS)}: {value!r}"
            )
        return entry_slot

    @classmethod
    def _normalize_entry_slots(cls, values: Sequence[str]) -> tuple[str, ...]:
        if isinstance(values, str) or not isinstance(values, Sequence):
            raise ValueError("entry_slots must be a non-empty sequence of strings.")
        normalized = tuple(cls._require_entry_slot(value) for value in values)
        if not normalized:
            raise ValueError("entry_slots cannot be empty.")
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"entry_slots cannot contain duplicates: {values!r}")
        return normalized

    @staticmethod
    def _normalize_optional_positive_int(
        name: str,
        value: int | None,
    ) -> int | None:
        if value is None:
            return None
        return require_positive_int(name, value)

    @staticmethod
    def _require_non_negative_float(name: str, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{name} must be a non-negative number: {value!r}")
        return float(value)

    @staticmethod
    def _parse_kst_iso(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(_KST)

    @classmethod
    def _min_iso(cls, left: str, right: str) -> str:
        left_dt = cls._parse_kst_iso(left)
        right_dt = cls._parse_kst_iso(right)
        return left if left_dt <= right_dt else right

    @classmethod
    def _max_iso(cls, left: str, right: str) -> str:
        left_dt = cls._parse_kst_iso(left)
        right_dt = cls._parse_kst_iso(right)
        return left if left_dt >= right_dt else right

    @staticmethod
    def _rounded_average(*, total_notional: int, total_qty: int) -> int:
        return (total_notional + (total_qty // 2)) // total_qty

    @staticmethod
    def _assert_same_lot_identity(
        *,
        current: EntryLotRow,
        symbol: str,
        entry_strategy_name: str,
        entry_signal_id: int | None,
        entry_slot: str,
    ) -> None:
        if current.symbol != symbol:
            raise RepositoryInvariantError(
                "Entry lot symbol mismatch: "
                f"stored={current.symbol}, incoming={symbol}"
            )
        if current.entry_strategy_name != entry_strategy_name:
            raise RepositoryInvariantError(
                "Entry lot strategy mismatch: "
                f"stored={current.entry_strategy_name}, incoming={entry_strategy_name}"
            )
        if current.entry_signal_id != entry_signal_id:
            raise RepositoryInvariantError(
                "Entry lot signal mismatch: "
                f"stored={current.entry_signal_id}, incoming={entry_signal_id}"
            )
        if current.entry_slot != entry_slot:
            raise RepositoryInvariantError(
                "Entry lot slot mismatch: "
                f"stored={current.entry_slot}, incoming={entry_slot}"
            )
