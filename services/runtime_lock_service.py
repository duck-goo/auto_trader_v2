"""Service for acquiring and maintaining persisted runtime lock leases."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pytz

from services.errors import RuntimeLockBusyError, ServiceError
from storage.db import transaction
from storage.repositories import RuntimeLockRepository, RuntimeLockRow


_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class RuntimeLockLease:
    lock_name: str
    owner_id: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str


def _default_now() -> datetime:
    return datetime.now(_KST)


def _default_owner_id() -> str:
    return f"pid{os.getpid()}-{uuid.uuid4().hex[:12]}"


class RuntimeLockService:
    """Acquire, heartbeat, and release a persisted runtime lock."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        lock_repo: RuntimeLockRepository,
        now_fn: Callable[[], datetime] | None = None,
        owner_id: str | None = None,
        owner_id_fn: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._lock_repo = lock_repo
        self._now_fn = now_fn or _default_now
        if owner_id is not None:
            self._owner_id = owner_id.strip()
        else:
            generator = owner_id_fn or _default_owner_id
            self._owner_id = str(generator()).strip()
        if not self._owner_id:
            raise ValueError("owner_id must not be empty.")

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def acquire(
        self,
        *,
        lock_name: str,
        lease_seconds: int,
    ) -> RuntimeLockLease:
        normalized_lease = self._validate_lease_seconds(lease_seconds)
        now_at, expires_at = self._build_window(normalized_lease)
        with transaction(self._conn):
            row = self._lock_repo.acquire_or_renew(
                lock_name=lock_name,
                owner_id=self._owner_id,
                now_at=now_at,
                expires_at=expires_at,
            )
            if row is None:
                current = self._lock_repo.get(lock_name)
                if current is None:
                    raise ServiceError(
                        f"Runtime lock acquisition failed unexpectedly: {lock_name!r}"
                    )
                raise RuntimeLockBusyError(
                    lock_name=current.lock_name,
                    owner_id=current.owner_id,
                    expires_at=current.expires_at,
                )
        return self._to_lease(row)

    def heartbeat(
        self,
        *,
        lock_name: str,
        lease_seconds: int,
    ) -> RuntimeLockLease:
        normalized_lease = self._validate_lease_seconds(lease_seconds)
        heartbeat_at, expires_at = self._build_window(normalized_lease)
        with transaction(self._conn):
            row = self._lock_repo.heartbeat(
                lock_name=lock_name,
                owner_id=self._owner_id,
                heartbeat_at=heartbeat_at,
                expires_at=expires_at,
            )
            if row is None:
                raise ServiceError(
                    "Runtime lock heartbeat failed because ownership was lost: "
                    f"lock_name={lock_name!r}, owner_id={self._owner_id!r}"
                )
        return self._to_lease(row)

    def release(self, *, lock_name: str) -> bool:
        with transaction(self._conn):
            return self._lock_repo.release(
                lock_name=lock_name,
                owner_id=self._owner_id,
            )

    def _build_window(self, lease_seconds: int) -> tuple[str, str]:
        now = self._now_fn().astimezone(_KST)
        expires_at = now + timedelta(seconds=lease_seconds)
        return now.isoformat(), expires_at.isoformat()

    @staticmethod
    def _validate_lease_seconds(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"lease_seconds must be a positive integer: {value!r}")
        return value

    @staticmethod
    def _to_lease(row: RuntimeLockRow) -> RuntimeLockLease:
        return RuntimeLockLease(
            lock_name=row.lock_name,
            owner_id=row.owner_id,
            acquired_at=row.acquired_at,
            heartbeat_at=row.heartbeat_at,
            expires_at=row.expires_at,
        )
