"""Repository for raw intraday current-price samples."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pytz

from storage.repositories.base import (
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_write_transaction,
)


_KST = pytz.timezone("Asia/Seoul")
_SELECT_COLUMNS = (
    "trade_date, symbol, observed_at, price, open, high, low, "
    "prev_close, change, change_rate, volume, source, captured_at"
)


def _require_trade_date(name: str, value: str) -> str:
    text = require_non_empty_text(name, value)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD: {value!r}") from exc
    return text


def _parse_kst_iso(name: str, value: str) -> datetime:
    text = require_aware_iso8601(name, value)
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(_KST)


def _require_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number: {value!r}")
    return float(value)


@dataclass(frozen=True)
class CurrentPriceSample:
    trade_date: str
    symbol: str
    observed_at: str
    price: int
    open: int
    high: int
    low: int
    prev_close: int
    change: int
    change_rate: float
    volume: int
    source: str = "broker_current_price"


@dataclass(frozen=True)
class CurrentPriceSampleRow:
    trade_date: str
    symbol: str
    observed_at: str
    price: int
    open: int
    high: int
    low: int
    prev_close: int
    change: int
    change_rate: float
    volume: int
    source: str
    captured_at: str


class CurrentPriceSampleRepository:
    """Pure DB repository for raw current-price samples."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_many(
        self,
        *,
        samples: Sequence[CurrentPriceSample],
        captured_at: str,
    ) -> list[CurrentPriceSampleRow]:
        require_write_transaction(self._conn)
        normalized_captured_at = require_aware_iso8601(
            "captured_at",
            captured_at,
        )
        normalized_samples = self._validate_samples(samples)

        for sample in normalized_samples:
            self._conn.execute(
                """
                INSERT INTO current_price_samples (
                    trade_date,
                    symbol,
                    observed_at,
                    price,
                    open,
                    high,
                    low,
                    prev_close,
                    change,
                    change_rate,
                    volume,
                    source,
                    captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, observed_at) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    price = excluded.price,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    prev_close = excluded.prev_close,
                    change = excluded.change,
                    change_rate = excluded.change_rate,
                    volume = excluded.volume,
                    source = excluded.source,
                    captured_at = excluded.captured_at
                """,
                (
                    sample.trade_date,
                    sample.symbol,
                    sample.observed_at,
                    sample.price,
                    sample.open,
                    sample.high,
                    sample.low,
                    sample.prev_close,
                    sample.change,
                    sample.change_rate,
                    sample.volume,
                    sample.source,
                    normalized_captured_at,
                ),
            )

        return self._list_by_sample_keys(normalized_samples)

    def list_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
    ) -> list[CurrentPriceSampleRow]:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM current_price_samples
            WHERE trade_date = ? AND symbol = ?
            ORDER BY observed_at ASC
            """,
            (normalized_trade_date, normalized_symbol),
        ).fetchall()
        return RowMapper.map_many(rows, CurrentPriceSampleRow)

    def list_for_symbol_between(
        self,
        *,
        symbol: str,
        start_at: str,
        end_at: str,
    ) -> list[CurrentPriceSampleRow]:
        normalized_symbol = require_non_empty_text("symbol", symbol)
        normalized_start_at = require_aware_iso8601("start_at", start_at)
        normalized_end_at = require_aware_iso8601("end_at", end_at)
        if _parse_kst_iso("end_at", normalized_end_at) <= _parse_kst_iso(
            "start_at",
            normalized_start_at,
        ):
            raise ValueError(
                "end_at must be later than start_at: "
                f"start_at={normalized_start_at}, end_at={normalized_end_at}"
            )

        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM current_price_samples
            WHERE symbol = ?
              AND observed_at >= ?
              AND observed_at < ?
            ORDER BY observed_at ASC
            """,
            (normalized_symbol, normalized_start_at, normalized_end_at),
        ).fetchall()
        return RowMapper.map_many(rows, CurrentPriceSampleRow)

    def get_latest_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
    ) -> CurrentPriceSampleRow | None:
        normalized_trade_date = _require_trade_date("trade_date", trade_date)
        normalized_symbol = require_non_empty_text("symbol", symbol)
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM current_price_samples
            WHERE trade_date = ? AND symbol = ?
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (normalized_trade_date, normalized_symbol),
        ).fetchone()
        return RowMapper.map_one(row, CurrentPriceSampleRow)

    def _list_by_sample_keys(
        self,
        samples: Sequence[CurrentPriceSample],
    ) -> list[CurrentPriceSampleRow]:
        if not samples:
            return []

        result: list[CurrentPriceSampleRow] = []
        for sample in samples:
            row = self._conn.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM current_price_samples
                WHERE symbol = ? AND observed_at = ?
                """,
                (sample.symbol, sample.observed_at),
            ).fetchone()
            mapped = RowMapper.map_one(row, CurrentPriceSampleRow)
            if mapped is not None:
                result.append(mapped)
        return result

    def _validate_samples(
        self,
        samples: Sequence[CurrentPriceSample],
    ) -> list[CurrentPriceSample]:
        normalized_samples: list[CurrentPriceSample] = []
        seen_keys: set[tuple[str, str]] = set()

        for sample in samples:
            if not isinstance(sample, CurrentPriceSample):
                raise ValueError(
                    "samples must contain only CurrentPriceSample instances."
                )

            trade_date = _require_trade_date("trade_date", sample.trade_date)
            symbol = require_non_empty_text("symbol", sample.symbol)
            observed_at = require_aware_iso8601("observed_at", sample.observed_at)
            observed_dt = _parse_kst_iso("observed_at", observed_at)
            if observed_dt.strftime("%Y-%m-%d") != trade_date:
                raise ValueError(
                    "observed_at trade_date mismatch: "
                    f"expected={trade_date}, actual="
                    f"{observed_dt.strftime('%Y-%m-%d')}"
                )

            key = (symbol, observed_at)
            if key in seen_keys:
                raise ValueError(
                    "Duplicate current price sample in input: "
                    f"symbol={symbol}, observed_at={observed_at}"
                )
            seen_keys.add(key)

            price = require_non_negative_int("price", sample.price)
            open_price = require_non_negative_int("open", sample.open)
            high_price = require_non_negative_int("high", sample.high)
            low_price = require_non_negative_int("low", sample.low)
            prev_close = require_non_negative_int(
                "prev_close",
                sample.prev_close,
            )
            volume = require_non_negative_int("volume", sample.volume)
            if not isinstance(sample.change, int) or isinstance(sample.change, bool):
                raise ValueError(f"change must be an integer: {sample.change!r}")
            change_rate = _require_number("change_rate", sample.change_rate)
            source = require_non_empty_text("source", sample.source)

            if high_price < max(open_price, low_price, price):
                raise ValueError(
                    "high must be >= open/low/price: "
                    f"{high_price!r}"
                )
            if low_price > min(open_price, high_price, price):
                raise ValueError(
                    "low must be <= open/high/price: "
                    f"{low_price!r}"
                )

            normalized_samples.append(
                CurrentPriceSample(
                    trade_date=trade_date,
                    symbol=symbol,
                    observed_at=observed_at,
                    price=price,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    prev_close=prev_close,
                    change=sample.change,
                    change_rate=change_rate,
                    volume=volume,
                    source=source,
                )
            )

        return normalized_samples
