"""Universe candidate repository.

Stores the daily snapshot produced by the first-stage filter.
One row = one symbol selected for one trade_date.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from storage.repositories.base import (
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_write_transaction,
)


def _require_trade_date(name: str, value: str) -> str:
    text = require_non_empty_text(name, value)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD: {value!r}") from exc
    return text


@dataclass(frozen=True)
class UniverseCandidate:
    symbol: str
    name: str
    market: str
    close_price: int
    prev_day_trade_value: int


@dataclass(frozen=True)
class UniverseCandidateRow:
    trade_date: str
    symbol: str
    name: str
    market: str
    close_price: int
    prev_day_trade_value: int
    refreshed_at: str


_SELECT_COLUMNS = (
    "trade_date, symbol, name, market, close_price, "
    "prev_day_trade_value, refreshed_at"
)


class UniverseCandidateRepository:
    """Pure DB repository for daily universe candidate snapshots."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_for_date(
        self,
        *,
        trade_date: str,
        candidates: Sequence[UniverseCandidate],
        refreshed_at: str,
    ) -> list[UniverseCandidateRow]:
        """
        Replace the full snapshot for one trade_date.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        trade_date = _require_trade_date("trade_date", trade_date)
        refreshed_at = require_aware_iso8601("refreshed_at", refreshed_at)

        normalized: list[UniverseCandidate] = []
        seen_symbols: set[str] = set()

        for candidate in candidates:
            if not isinstance(candidate, UniverseCandidate):
                raise ValueError(
                    "candidates must contain only UniverseCandidate instances."
                )

            symbol = require_non_empty_text("symbol", candidate.symbol)
            name = require_non_empty_text("name", candidate.name)
            market = require_non_empty_text("market", candidate.market)
            close_price = require_non_negative_int(
                "close_price", candidate.close_price
            )
            prev_day_trade_value = require_non_negative_int(
                "prev_day_trade_value",
                candidate.prev_day_trade_value,
            )

            if symbol in seen_symbols:
                raise ValueError(
                    f"Duplicate symbol in candidate snapshot: {symbol!r}"
                )
            seen_symbols.add(symbol)

            normalized.append(
                UniverseCandidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    close_price=close_price,
                    prev_day_trade_value=prev_day_trade_value,
                )
            )

        self._conn.execute(
            "DELETE FROM universe_candidates WHERE trade_date = ?",
            (trade_date,),
        )

        for candidate in normalized:
            self._conn.execute(
                """
                INSERT INTO universe_candidates (
                    trade_date,
                    symbol,
                    name,
                    market,
                    close_price,
                    prev_day_trade_value,
                    refreshed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    candidate.symbol,
                    candidate.name,
                    candidate.market,
                    candidate.close_price,
                    candidate.prev_day_trade_value,
                    refreshed_at,
                ),
            )

        return self.list_for_date(trade_date)

    def get(self, *, trade_date: str, symbol: str) -> UniverseCandidateRow | None:
        trade_date = _require_trade_date("trade_date", trade_date)
        symbol = require_non_empty_text("symbol", symbol)

        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM universe_candidates
            WHERE trade_date = ? AND symbol = ?
            """,
            (trade_date, symbol),
        ).fetchone()
        return RowMapper.map_one(row, UniverseCandidateRow)

    def list_for_date(self, trade_date: str) -> list[UniverseCandidateRow]:
        trade_date = _require_trade_date("trade_date", trade_date)

        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM universe_candidates
            WHERE trade_date = ?
            ORDER BY symbol ASC
            """,
            (trade_date,),
        ).fetchall()
        return RowMapper.map_many(rows, UniverseCandidateRow)
