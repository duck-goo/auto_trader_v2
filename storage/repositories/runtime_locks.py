"""Repository for persisted runtime lock leases."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from storage.repositories.base import (
    RepositoryInvariantError,
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_write_transaction,
)


@dataclass(frozen=True)
class RuntimeLockRow:
    lock_name: str
    owner_id: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str


_SELECT_COLUMNS = (
    "lock_name, owner_id, acquired_at, heartbeat_at, expires_at"
)


class RuntimeLockRepository:
    """Pure DB repository for runtime lock leases."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, lock_name: str) -> RuntimeLockRow | None:
        lock_name = require_non_empty_text("lock_name", lock_name)
        row = self._conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM runtime_locks WHERE lock_name = ?",
            (lock_name,),
        ).fetchone()
        return RowMapper.map_one(row, RuntimeLockRow)

    def acquire_or_renew(
        self,
        *,
        lock_name: str,
        owner_id: str,
        now_at: str,
        expires_at: str,
    ) -> RuntimeLockRow | None:
        require_write_transaction(self._conn)
        lock_name = require_non_empty_text("lock_name", lock_name)
        owner_id = require_non_empty_text("owner_id", owner_id)
        now_at = require_aware_iso8601("now_at", now_at)
        expires_at = require_aware_iso8601("expires_at", expires_at)

        now_dt = datetime.fromisoformat(now_at)
        expires_dt = datetime.fromisoformat(expires_at)
        if expires_dt <= now_dt:
            raise ValueError(
                "expires_at must be later than now_at: "
                f"now_at={now_at}, expires_at={expires_at}"
            )

        current = self.get(lock_name)
        if current is None:
            self._conn.execute(
                """
                INSERT INTO runtime_locks (
                    lock_name, owner_id, acquired_at, heartbeat_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (lock_name, owner_id, now_at, now_at, expires_at),
            )
            return self._get_required(lock_name)

        current_expires_dt = datetime.fromisoformat(current.expires_at)
        if current.owner_id == owner_id:
            self._conn.execute(
                """
                UPDATE runtime_locks
                SET heartbeat_at = ?, expires_at = ?
                WHERE lock_name = ? AND owner_id = ?
                """,
                (now_at, expires_at, lock_name, owner_id),
            )
            return self._get_required(lock_name)

        if current_expires_dt <= now_dt:
            self._conn.execute(
                """
                UPDATE runtime_locks
                SET owner_id = ?,
                    acquired_at = ?,
                    heartbeat_at = ?,
                    expires_at = ?
                WHERE lock_name = ?
                """,
                (owner_id, now_at, now_at, expires_at, lock_name),
            )
            return self._get_required(lock_name)

        return None

    def heartbeat(
        self,
        *,
        lock_name: str,
        owner_id: str,
        heartbeat_at: str,
        expires_at: str,
    ) -> RuntimeLockRow | None:
        require_write_transaction(self._conn)
        lock_name = require_non_empty_text("lock_name", lock_name)
        owner_id = require_non_empty_text("owner_id", owner_id)
        heartbeat_at = require_aware_iso8601("heartbeat_at", heartbeat_at)
        expires_at = require_aware_iso8601("expires_at", expires_at)

        heartbeat_dt = datetime.fromisoformat(heartbeat_at)
        expires_dt = datetime.fromisoformat(expires_at)
        if expires_dt <= heartbeat_dt:
            raise ValueError(
                "expires_at must be later than heartbeat_at: "
                f"heartbeat_at={heartbeat_at}, expires_at={expires_at}"
            )

        current = self.get(lock_name)
        if current is None or current.owner_id != owner_id:
            return None

        self._conn.execute(
            """
            UPDATE runtime_locks
            SET heartbeat_at = ?, expires_at = ?
            WHERE lock_name = ? AND owner_id = ?
            """,
            (heartbeat_at, expires_at, lock_name, owner_id),
        )
        return self._get_required(lock_name)

    def release(
        self,
        *,
        lock_name: str,
        owner_id: str,
    ) -> bool:
        require_write_transaction(self._conn)
        lock_name = require_non_empty_text("lock_name", lock_name)
        owner_id = require_non_empty_text("owner_id", owner_id)
        cursor = self._conn.execute(
            """
            DELETE FROM runtime_locks
            WHERE lock_name = ? AND owner_id = ?
            """,
            (lock_name, owner_id),
        )
        return (cursor.rowcount or 0) > 0

    def _get_required(self, lock_name: str) -> RuntimeLockRow:
        row = self.get(lock_name)
        if row is None:
            raise RepositoryInvariantError(
                f"Runtime lock row expected but not found: lock_name={lock_name!r}"
            )
        return row
