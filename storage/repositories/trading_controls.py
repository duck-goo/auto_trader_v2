"""Trading control repository."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from storage.repositories.base import (
    RowMapper,
    normalize_optional_text,
    require_aware_iso8601,
    require_non_empty_text,
    require_write_transaction,
)


CONTROL_NAME_KILL_SWITCH = "KILL_SWITCH"


@dataclass(frozen=True)
class TradingControlRow:
    control_name: str
    is_enabled: bool
    note: str | None
    updated_at: str


class TradingControlRepository:
    """Pure DB repository for persisted trading control flags."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, control_name: str) -> TradingControlRow | None:
        control_name = require_non_empty_text("control_name", control_name)
        row = self._conn.execute(
            """
            SELECT control_name, is_enabled, note, updated_at
            FROM trading_controls
            WHERE control_name = ?
            """,
            (control_name,),
        ).fetchone()
        return RowMapper.map_one(
            row,
            TradingControlRow,
            converters={"is_enabled": lambda value: bool(int(value))},
        )

    def list_all(self) -> list[TradingControlRow]:
        rows = self._conn.execute(
            """
            SELECT control_name, is_enabled, note, updated_at
            FROM trading_controls
            ORDER BY control_name ASC
            """
        ).fetchall()
        return RowMapper.map_many(
            rows,
            TradingControlRow,
            converters={"is_enabled": lambda value: bool(int(value))},
        )

    def upsert_bool(
        self,
        *,
        control_name: str,
        is_enabled: bool,
        updated_at: str,
        note: str | None = None,
    ) -> TradingControlRow:
        require_write_transaction(self._conn)
        control_name = require_non_empty_text("control_name", control_name)
        if not isinstance(is_enabled, bool):
            raise ValueError(f"is_enabled must be a bool: {is_enabled!r}")
        updated_at = require_aware_iso8601("updated_at", updated_at)
        note = normalize_optional_text(note)

        self._conn.execute(
            """
            INSERT INTO trading_controls (
                control_name,
                is_enabled,
                note,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(control_name) DO UPDATE SET
                is_enabled = excluded.is_enabled,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                control_name,
                1 if is_enabled else 0,
                note,
                updated_at,
            ),
        )
        row = self.get(control_name)
        if row is None:
            raise RuntimeError(
                "trading control row expected after upsert: "
                f"control_name={control_name!r}"
            )
        return row

    def get_kill_switch(self) -> TradingControlRow | None:
        return self.get(CONTROL_NAME_KILL_SWITCH)

    def set_kill_switch(
        self,
        *,
        is_enabled: bool,
        updated_at: str,
        note: str | None = None,
    ) -> TradingControlRow:
        return self.upsert_bool(
            control_name=CONTROL_NAME_KILL_SWITCH,
            is_enabled=is_enabled,
            updated_at=updated_at,
            note=note,
        )
