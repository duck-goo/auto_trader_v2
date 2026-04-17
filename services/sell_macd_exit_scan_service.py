"""Read-only scan service for sell MACD decrease signals."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd
import pytz

from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import (
    IntradayBar15mRepository,
    PositionRepository,
    SignalRepository,
    SignalRow,
)
from strategy import SellMacdExitEvaluator, SellMacdExitMatch, SellMacdExitSettings


STRATEGY_NAME_SELL_MACD_DECREASE = "sell_macd_decrease"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class SellMacdExitScanCandidate:
    symbol: str
    name: str
    qty: int
    avg_price: int
    history_bar_count: int
    already_recorded: bool
    match: SellMacdExitMatch


@dataclass(frozen=True)
class SellMacdExitScanResult:
    trade_date: str
    scanned_at: str
    position_count: int
    matched_count: int
    recorded_count: int
    skipped_existing_count: int
    candidates: tuple[SellMacdExitScanCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class SellMacdExitScanService:
    """
    Scan persisted completed 15-minute bars for MACD decrease sell signals.

    Safety rules:
    - reads only persisted completed bars from SQLite
    - does not use in-progress bars
    - records append-only signals only when write_signals=True
    - never places orders
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        position_repo: PositionRepository,
        intraday_bar_repo: IntradayBar15mRepository,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: SellMacdExitEvaluator | None = None,
    ) -> None:
        self._conn = conn
        self._position_repo = position_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or SellMacdExitEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: SellMacdExitSettings,
        history_limit: int = 300,
        write_signals: bool = False,
    ) -> SellMacdExitScanResult:
        normalized_settings = settings.validated()
        normalized_history_limit = self._validate_history_limit(
            history_limit,
            settings=normalized_settings,
        )
        observed_at = self._now_fn().astimezone(_KST)
        scanned_at = observed_at.isoformat()
        positions = tuple(self._position_repo.list_all())

        candidates: list[SellMacdExitScanCandidate] = []
        payloads_to_record: list[dict] = []

        _log.info(
            f"[sell_macd_exit_scan:start] trade_date={trade_date} "
            f"position_count={len(positions)} write_signals={write_signals}"
        )

        for row in positions:
            history_rows = self._intraday_bar_repo.list_recent_for_symbol(
                symbol=row.symbol,
                end_at=scanned_at,
                limit=normalized_history_limit,
            )
            history_df = self._history_df(history_rows)

            try:
                match = self._evaluator.evaluate(
                    symbol=row.symbol,
                    intraday_bars=history_df,
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate sell MACD exit for symbol={row.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if match is None:
                continue

            already_recorded = self._has_existing_signal(
                symbol=row.symbol,
                trade_date=trade_date,
            )
            candidate = SellMacdExitScanCandidate(
                symbol=row.symbol,
                name=row.symbol,
                qty=row.qty,
                avg_price=row.avg_price,
                history_bar_count=len(history_df),
                already_recorded=already_recorded,
                match=match,
            )
            candidates.append(candidate)

            if not already_recorded:
                payloads_to_record.append(
                    self._build_payload(
                        trade_date=trade_date,
                        qty=row.qty,
                        avg_price=row.avg_price,
                        match=match,
                    )
                )

        recorded_signals: list[SignalRow] = []
        if write_signals and payloads_to_record:
            with transaction(self._conn):
                for payload in payloads_to_record:
                    recorded_signals.append(
                        self._signal_repo.record(
                            symbol=payload["symbol"],
                            strategy_name=STRATEGY_NAME_SELL_MACD_DECREASE,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        skipped_existing_count = sum(
            1 for candidate in candidates if candidate.already_recorded
        )
        _log.info(
            f"[sell_macd_exit_scan:done] trade_date={trade_date} "
            f"matched_count={len(candidates)} recorded_count={len(recorded_signals)} "
            f"skipped_existing_count={skipped_existing_count}"
        )

        return SellMacdExitScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            position_count=len(positions),
            matched_count=len(candidates),
            recorded_count=len(recorded_signals),
            skipped_existing_count=skipped_existing_count,
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _validate_history_limit(
        self,
        value: int,
        *,
        settings: SellMacdExitSettings,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"history_limit must be an integer: {value!r}")
        minimum = settings.min_required_bars()
        if value < minimum:
            raise ValueError(f"history_limit must be >= {minimum}: {value!r}")
        return value

    def _has_existing_signal(self, *, symbol: str, trade_date: str) -> bool:
        existing = self._signal_repo.list_by_symbol(symbol, limit=100)
        for row in existing:
            if row.strategy_name != STRATEGY_NAME_SELL_MACD_DECREASE:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") == trade_date:
                return True
        return False

    @staticmethod
    def _history_df(rows) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["bar_start_at", "bar_end_at", "close"])
        return pd.DataFrame(
            [
                {
                    "bar_start_at": row.bar_start_at,
                    "bar_end_at": row.bar_end_at,
                    "close": row.close,
                }
                for row in rows
            ]
        )

    def _build_payload(
        self,
        *,
        trade_date: str,
        qty: int,
        avg_price: int,
        match: SellMacdExitMatch,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": match.symbol,
            "name": match.symbol,
            "position_qty": qty,
            "avg_price": avg_price,
            "bar_start_at": match.bar_start_at,
            "bar_end_at": match.bar_end_at,
            "close_price": match.close_price,
            "macd_value": round(match.macd_value, 6),
            "signal_value": round(match.signal_value, 6),
            "hist_t_minus_2": round(match.hist_t_minus_2, 6),
            "hist_t_minus_1": round(match.hist_t_minus_1, 6),
            "hist_t": round(match.hist_t, 6),
            "consecutive_decline_bars": match.consecutive_decline_bars,
        }
