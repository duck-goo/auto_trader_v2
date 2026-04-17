"""Refresh persisted same-day 15-minute bars for live positions."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd
import pytz

from broker.base import BrokerInterface
from logger import get_logger
from market import resample_minute_candles_to_fixed_bars
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import (
    IntradayBar15m,
    IntradayBar15mRepository,
    PositionRepository,
)


_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


class IntradayBar15mRefreshOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    REFRESHED = "REFRESHED"
    SKIPPED_NO_COMPLETED_BAR = "SKIPPED_NO_COMPLETED_BAR"
    SKIPPED_REGRESSION = "SKIPPED_REGRESSION"
    FAILED = "FAILED"


@dataclass(frozen=True)
class IntradayBar15mRefreshCandidate:
    symbol: str
    qty: int
    avg_price: int
    minute_candle_count: int
    existing_bar_count: int
    completed_bar_count: int
    stored_bar_count: int
    outcome: IntradayBar15mRefreshOutcome
    reason: str | None


@dataclass(frozen=True)
class IntradayBar15mRefreshResult:
    trade_date: str
    refreshed_at: str
    position_count: int
    candidate_count: int
    preview_ready_count: int
    refreshed_symbol_count: int
    skipped_count: int
    failed_count: int
    candidates: tuple[IntradayBar15mRefreshCandidate, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class IntradayBar15mRefreshService:
    """
    Refresh persisted same-day 15-minute bars for live positions.

    Safety rules:
    - KIS stock minute API is same-day only, so this service supports only the
      current KST trade_date.
    - If newly built completed bars are fewer than already stored same-day bars,
      the service skips that symbol instead of deleting valid history.
    - Per-symbol failures are isolated so one bad symbol does not wipe or block
      every other symbol.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        position_repo: PositionRepository,
        intraday_bar_repo: IntradayBar15mRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._position_repo = position_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._now_fn = now_fn or _default_now

    def refresh_live_positions(
        self,
        *,
        trade_date: str,
        end_time: str | None = None,
        bar_minutes: int = 15,
        write: bool = False,
    ) -> IntradayBar15mRefreshResult:
        normalized_trade_date = self._require_trade_date(trade_date)
        normalized_bar_minutes = self._require_positive_int(
            "bar_minutes",
            bar_minutes,
        )
        observed_at = self._now_fn().astimezone(_KST)
        self._validate_runtime_trade_date(
            trade_date=normalized_trade_date,
            observed_at=observed_at,
        )
        refreshed_at = observed_at.isoformat()
        query_end_time = (
            observed_at.strftime("%H%M%S")
            if end_time is None
            else self._require_hms("end_time", end_time)
        )
        positions = tuple(self._position_repo.list_all())

        _log.info(
            f"[intraday_bar_refresh:start] trade_date={normalized_trade_date} "
            f"position_count={len(positions)} write={write} "
            f"end_time={query_end_time} bar_minutes={normalized_bar_minutes}"
        )

        candidates: list[IntradayBar15mRefreshCandidate] = []

        for row in positions:
            existing_rows = self._intraday_bar_repo.list_for_symbol_and_date(
                trade_date=normalized_trade_date,
                symbol=row.symbol,
            )
            existing_count = len(existing_rows)

            try:
                minute_candles = self._broker.get_same_day_minute_candles(
                    row.symbol,
                    end_time=query_end_time,
                )
                completed_df = resample_minute_candles_to_fixed_bars(
                    minute_candles=minute_candles,
                    trade_date=normalized_trade_date,
                    observed_at=observed_at,
                    bar_minutes=normalized_bar_minutes,
                )
            except Exception as exc:
                candidates.append(
                    IntradayBar15mRefreshCandidate(
                        symbol=row.symbol,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        minute_candle_count=0,
                        existing_bar_count=existing_count,
                        completed_bar_count=0,
                        stored_bar_count=existing_count,
                        outcome=IntradayBar15mRefreshOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            minute_candle_count = len(minute_candles)
            completed_bar_count = len(completed_df)
            if completed_bar_count == 0 and existing_count == 0:
                candidates.append(
                    IntradayBar15mRefreshCandidate(
                        symbol=row.symbol,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        minute_candle_count=minute_candle_count,
                        existing_bar_count=existing_count,
                        completed_bar_count=completed_bar_count,
                        stored_bar_count=existing_count,
                        outcome=IntradayBar15mRefreshOutcome.SKIPPED_NO_COMPLETED_BAR,
                        reason="No completed same-day 15-minute bar is available yet.",
                    )
                )
                continue

            if completed_bar_count < existing_count:
                candidates.append(
                    IntradayBar15mRefreshCandidate(
                        symbol=row.symbol,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        minute_candle_count=minute_candle_count,
                        existing_bar_count=existing_count,
                        completed_bar_count=completed_bar_count,
                        stored_bar_count=existing_count,
                        outcome=IntradayBar15mRefreshOutcome.SKIPPED_REGRESSION,
                        reason=(
                            "New completed 15-minute bar count is smaller than "
                            "already stored same-day bars."
                        ),
                    )
                )
                continue

            if not write:
                candidates.append(
                    IntradayBar15mRefreshCandidate(
                        symbol=row.symbol,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        minute_candle_count=minute_candle_count,
                        existing_bar_count=existing_count,
                        completed_bar_count=completed_bar_count,
                        stored_bar_count=completed_bar_count,
                        outcome=IntradayBar15mRefreshOutcome.PREVIEW_READY,
                        reason=None,
                    )
                )
                continue

            try:
                bars = self._bars_from_df(completed_df)
                with transaction(self._conn):
                    stored_rows = self._intraday_bar_repo.replace_for_symbol_and_date(
                        trade_date=normalized_trade_date,
                        symbol=row.symbol,
                        bars=bars,
                        refreshed_at=refreshed_at,
                    )
            except Exception as exc:
                candidates.append(
                    IntradayBar15mRefreshCandidate(
                        symbol=row.symbol,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        minute_candle_count=minute_candle_count,
                        existing_bar_count=existing_count,
                        completed_bar_count=completed_bar_count,
                        stored_bar_count=existing_count,
                        outcome=IntradayBar15mRefreshOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            candidates.append(
                IntradayBar15mRefreshCandidate(
                    symbol=row.symbol,
                    qty=row.qty,
                    avg_price=row.avg_price,
                    minute_candle_count=minute_candle_count,
                    existing_bar_count=existing_count,
                    completed_bar_count=completed_bar_count,
                    stored_bar_count=len(stored_rows),
                    outcome=IntradayBar15mRefreshOutcome.REFRESHED,
                    reason=None,
                )
            )

        preview_ready_count = sum(
            1
            for row in candidates
            if row.outcome == IntradayBar15mRefreshOutcome.PREVIEW_READY
        )
        refreshed_symbol_count = sum(
            1
            for row in candidates
            if row.outcome == IntradayBar15mRefreshOutcome.REFRESHED
        )
        skipped_count = sum(
            1
            for row in candidates
            if row.outcome
            in (
                IntradayBar15mRefreshOutcome.SKIPPED_NO_COMPLETED_BAR,
                IntradayBar15mRefreshOutcome.SKIPPED_REGRESSION,
            )
        )
        failed_count = sum(
            1
            for row in candidates
            if row.outcome == IntradayBar15mRefreshOutcome.FAILED
        )

        _log.info(
            f"[intraday_bar_refresh:done] trade_date={normalized_trade_date} "
            f"candidate_count={len(candidates)} "
            f"preview_ready_count={preview_ready_count} "
            f"refreshed_symbol_count={refreshed_symbol_count} "
            f"skipped_count={skipped_count} failed_count={failed_count}"
        )

        return IntradayBar15mRefreshResult(
            trade_date=normalized_trade_date,
            refreshed_at=refreshed_at,
            position_count=len(positions),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            refreshed_symbol_count=refreshed_symbol_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            candidates=tuple(candidates),
        )

    @staticmethod
    def _require_trade_date(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"trade_date must be a string: {value!r}")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
        return value

    @staticmethod
    def _require_hms(name: str, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string: {value!r}")
        try:
            datetime.strptime(value, "%H%M%S")
        except ValueError as exc:
            raise ValueError(f"{name} must be HHMMSS digits: {value!r}") from exc
        return value

    @staticmethod
    def _require_positive_int(name: str, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer: {value!r}")
        return value

    @staticmethod
    def _validate_runtime_trade_date(
        *,
        trade_date: str,
        observed_at: datetime,
    ) -> None:
        runtime_trade_date = observed_at.astimezone(_KST).strftime("%Y-%m-%d")
        if runtime_trade_date != trade_date:
            raise ServiceError(
                "15-minute refresh supports only the current KST trade_date: "
                f"trade_date={trade_date}, runtime_trade_date={runtime_trade_date}"
            )

    @staticmethod
    def _bars_from_df(df: pd.DataFrame) -> list[IntradayBar15m]:
        result: list[IntradayBar15m] = []
        for _, row in df.iterrows():
            result.append(
                IntradayBar15m(
                    bar_start_at=str(row["bar_start_at"]),
                    bar_end_at=str(row["bar_end_at"]),
                    open=int(row["open"]),
                    high=int(row["high"]),
                    low=int(row["low"]),
                    close=int(row["close"]),
                    volume=int(row["volume"]),
                )
            )
        return result
