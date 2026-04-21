"""Repository for persisted 30-second intraday bars."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytz

from storage.repositories.base import (
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_positive_int,
    require_write_transaction,
)


_KST = pytz.timezone("Asia/Seoul")
_BAR_SECONDS = 30
_SELECT_COLUMNS = (
    "trade_date, symbol, bar_start_at, bar_end_at, "
    "open, high, low, close, volume, refreshed_at"
)


def _require_trade_date(name: str, value: str) -> str:
    text = require_non_empty_text(name, value)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD: {value!r}") from exc
    return text


def _require_time_text(name: str, value: str) -> str:
    text = require_non_empty_text(name, value)
    try:
        datetime.strptime(text, "%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM:SS: {value!r}") from exc
    return text


def _parse_kst_iso(name: str, value: str) -> datetime:
    text = require_aware_iso8601(name, value)
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(_KST)


def _kst_datetime_for_time(trade_date: str, time_text: str) -> datetime:
    return _KST.localize(
        datetime.strptime(
            f"{trade_date} {time_text}",
            "%Y-%m-%d %H:%M:%S",
        )
    )


@dataclass(frozen=True)
class IntradayBar30s:
    bar_start_at: str
    bar_end_at: str
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class IntradayBar30sRow:
    trade_date: str
    symbol: str
    bar_start_at: str
    bar_end_at: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    refreshed_at: str


class IntradayBar30sRepository:
    """Pure DB repository for completed 30-second bars."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_many_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
        bars: Sequence[IntradayBar30s],
        refreshed_at: str,
    ) -> list[IntradayBar30sRow]:
        require_write_transaction(self._conn)
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        normalized_refreshed_at = require_aware_iso8601(
            "refreshed_at",
            refreshed_at,
        )

        normalized_bars = self._validate_bars(
            trade_date=normalized_trade_date,
            bars=bars,
        )

        for bar in normalized_bars:
            self._conn.execute(
                """
                INSERT INTO intraday_bars_30s (
                    trade_date,
                    symbol,
                    bar_start_at,
                    bar_end_at,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    refreshed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, bar_start_at) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    bar_end_at = excluded.bar_end_at,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    refreshed_at = excluded.refreshed_at
                """,
                (
                    normalized_trade_date,
                    normalized_symbol,
                    bar.bar_start_at,
                    bar.bar_end_at,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    normalized_refreshed_at,
                ),
            )

        return self.list_for_symbol_and_date(
            trade_date=normalized_trade_date,
            symbol=normalized_symbol,
        )

    def list_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
    ) -> list[IntradayBar30sRow]:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM intraday_bars_30s
            WHERE trade_date = ? AND symbol = ?
            ORDER BY bar_start_at ASC
            """,
            (normalized_trade_date, normalized_symbol),
        ).fetchall()
        return RowMapper.map_many(rows, IntradayBar30sRow)

    def get_latest_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
    ) -> IntradayBar30sRow | None:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM intraday_bars_30s
            WHERE trade_date = ? AND symbol = ?
            ORDER BY bar_end_at DESC
            LIMIT 1
            """,
            (normalized_trade_date, normalized_symbol),
        ).fetchone()
        return RowMapper.map_one(row, IntradayBar30sRow)

    def get_session_open_price(
        self,
        *,
        trade_date: str,
        symbol: str,
    ) -> int | None:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        row = self._conn.execute(
            """
            SELECT open
            FROM intraday_bars_30s
            WHERE trade_date = ? AND symbol = ?
            ORDER BY bar_start_at ASC
            LIMIT 1
            """,
            (normalized_trade_date, normalized_symbol),
        ).fetchone()
        if row is None:
            return None
        return int(row["open"])

    def get_max_close_between(
        self,
        *,
        trade_date: str,
        symbol: str,
        start_time: str = "09:00:00",
        end_time: str = "10:00:00",
    ) -> int | None:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        normalized_start_time = _require_time_text("start_time", start_time)
        normalized_end_time = _require_time_text("end_time", end_time)
        start_at = _kst_datetime_for_time(
            normalized_trade_date,
            normalized_start_time,
        )
        end_at = _kst_datetime_for_time(
            normalized_trade_date,
            normalized_end_time,
        )
        if end_at <= start_at:
            raise ValueError(
                "end_time must be later than start_time: "
                f"start={normalized_start_time}, end={normalized_end_time}"
            )

        row = self._conn.execute(
            """
            SELECT MAX(close) AS max_close
            FROM intraday_bars_30s
            WHERE trade_date = ?
              AND symbol = ?
              AND bar_start_at >= ?
              AND bar_end_at <= ?
            """,
            (
                normalized_trade_date,
                normalized_symbol,
                start_at.isoformat(),
                end_at.isoformat(),
            ),
        ).fetchone()
        if row is None or row["max_close"] is None:
            return None
        return int(row["max_close"])

    def list_recent_for_symbol(
        self,
        *,
        symbol: str,
        end_at: str,
        limit: int = 120,
    ) -> list[IntradayBar30sRow]:
        normalized_symbol = require_non_empty_text("symbol", symbol)
        normalized_end_at = require_aware_iso8601("end_at", end_at)
        normalized_limit = require_positive_int("limit", limit)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM intraday_bars_30s
            WHERE symbol = ? AND bar_end_at <= ?
            ORDER BY bar_end_at DESC
            LIMIT ?
            """,
            (normalized_symbol, normalized_end_at, normalized_limit),
        ).fetchall()
        mapped = RowMapper.map_many(rows, IntradayBar30sRow)
        mapped.reverse()
        return mapped

    def _validate_bars(
        self,
        *,
        trade_date: str,
        bars: Sequence[IntradayBar30s],
    ) -> list[IntradayBar30s]:
        normalized_bars: list[IntradayBar30s] = []
        seen_bar_start_at: set[str] = set()

        for bar in bars:
            if not isinstance(bar, IntradayBar30s):
                raise ValueError(
                    "bars must contain only IntradayBar30s instances."
                )

            bar_start_at = require_aware_iso8601(
                "bar_start_at",
                bar.bar_start_at,
            )
            bar_end_at = require_aware_iso8601("bar_end_at", bar.bar_end_at)
            start_dt = _parse_kst_iso("bar_start_at", bar_start_at)
            end_dt = _parse_kst_iso("bar_end_at", bar_end_at)

            if end_dt - start_dt != timedelta(seconds=_BAR_SECONDS):
                raise ValueError(
                    "30-second bar duration mismatch: "
                    f"start={bar_start_at!r}, end={bar_end_at!r}"
                )
            if start_dt.strftime("%Y-%m-%d") != trade_date:
                raise ValueError(
                    "bar_start_at trade_date mismatch: "
                    f"expected={trade_date}, actual="
                    f"{start_dt.strftime('%Y-%m-%d')}"
                )
            if end_dt.strftime("%Y-%m-%d") != trade_date:
                raise ValueError(
                    "bar_end_at trade_date mismatch: "
                    f"expected={trade_date}, actual="
                    f"{end_dt.strftime('%Y-%m-%d')}"
                )

            if bar_start_at in seen_bar_start_at:
                raise ValueError(
                    f"Duplicate 30-second bar_start_at: {bar_start_at!r}"
                )
            seen_bar_start_at.add(bar_start_at)

            open_price = require_non_negative_int("open", bar.open)
            high_price = require_non_negative_int("high", bar.high)
            low_price = require_non_negative_int("low", bar.low)
            close_price = require_non_negative_int("close", bar.close)
            volume = require_non_negative_int("volume", bar.volume)

            if high_price < max(open_price, low_price, close_price):
                raise ValueError(
                    "high must be >= open/low/close: "
                    f"{high_price!r}"
                )
            if low_price > min(open_price, high_price, close_price):
                raise ValueError(
                    "low must be <= open/high/close: "
                    f"{low_price!r}"
                )

            normalized_bars.append(
                IntradayBar30s(
                    bar_start_at=bar_start_at,
                    bar_end_at=bar_end_at,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                )
            )

        return normalized_bars
