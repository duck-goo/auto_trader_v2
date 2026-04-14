"""Universe refresh service."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from storage.db import transaction
from storage.repositories import (
    UniverseCandidate,
    UniverseCandidateRepository,
    UniverseCandidateRow,
)

_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class UniverseRefreshItem:
    symbol: str
    name: str
    market: str
    close_price: int
    prev_day_trade_value: int


@dataclass(frozen=True)
class UniverseRefreshResult:
    trade_date: str
    refreshed_at: str
    candidate_count: int
    rows: tuple[UniverseCandidateRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class UniverseRefreshService:
    """Validate and persist one daily universe snapshot."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        universe_repo: UniverseCandidateRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._universe_repo = universe_repo
        self._now_fn = now_fn or _default_now

    def refresh_snapshot(
        self,
        *,
        trade_date: str,
        candidates: Sequence[UniverseRefreshItem],
        refreshed_at: str | None = None,
    ) -> UniverseRefreshResult:
        if refreshed_at is None:
            refreshed_at = self._now_fn().isoformat()

        repo_candidates: list[UniverseCandidate] = []
        for item in candidates:
            if not isinstance(item, UniverseRefreshItem):
                raise ValueError(
                    "candidates must contain only UniverseRefreshItem instances."
                )
            repo_candidates.append(
                UniverseCandidate(
                    symbol=item.symbol,
                    name=item.name,
                    market=item.market,
                    close_price=item.close_price,
                    prev_day_trade_value=item.prev_day_trade_value,
                )
            )

        with transaction(self._conn):
            rows = self._universe_repo.replace_for_date(
                trade_date=trade_date,
                candidates=repo_candidates,
                refreshed_at=refreshed_at,
            )

        return UniverseRefreshResult(
            trade_date=trade_date,
            refreshed_at=refreshed_at,
            candidate_count=len(rows),
            rows=tuple(rows),
        )
