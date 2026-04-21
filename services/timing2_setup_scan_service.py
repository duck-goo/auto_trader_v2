"""Read-only scan service for buy timing 2 daily setup."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import (
    SignalRepository,
    SignalRow,
    UniverseCandidateRepository,
)
from strategy import Timing2SetupEvaluator, Timing2SetupMatch, Timing2SetupSettings


STRATEGY_NAME_TIMING2_SETUP = "buy_timing2_setup"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class Timing2SetupScanCandidate:
    symbol: str
    name: str
    market: str
    match: Timing2SetupMatch
    already_recorded: bool


@dataclass(frozen=True)
class Timing2SetupScanResult:
    trade_date: str
    scanned_at: str
    universe_count: int
    matched_count: int
    recorded_count: int
    skipped_existing_count: int
    candidates: tuple[Timing2SetupScanCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2SetupScanService:
    """
    Scan the stored universe and record read-only timing2 setup signals.

    Safety rules:
    - Universe snapshot must already exist for trade_date.
    - Broker API is called outside any DB transaction.
    - Same symbol/strategy/trade_date is recorded only once.
    - This service never places orders.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        universe_repo: UniverseCandidateRepository,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing2SetupEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._universe_repo = universe_repo
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing2SetupEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing2SetupSettings,
        daily_count: int = 90,
        write_signals: bool = False,
    ) -> Timing2SetupScanResult:
        normalized_daily_count = self._validate_daily_count(
            daily_count,
            settings=settings,
        )
        rows = tuple(self._universe_repo.list_for_date(trade_date))
        if not rows:
            raise ServiceError(
                f"Universe snapshot is missing for trade_date={trade_date!r}."
            )

        scanned_at = self._now_fn().isoformat()
        candidates: list[Timing2SetupScanCandidate] = []
        payloads_to_record: list[dict] = []

        _log.info(
            f"[timing2_setup_scan:start] trade_date={trade_date} "
            f"universe_count={len(rows)} write_signals={write_signals}"
        )

        for row in rows:
            try:
                daily_candles = self._broker.get_daily_candles(
                    row.symbol,
                    count=normalized_daily_count,
                    end_date=trade_date.replace("-", ""),
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to load daily candles for symbol={row.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            try:
                match = self._evaluator.evaluate(
                    symbol=row.symbol,
                    market=row.market,
                    trade_date=trade_date,
                    daily_candles=daily_candles,
                    settings=settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate timing2 setup for symbol={row.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if match is None:
                continue

            already_recorded = self._has_existing_signal(
                symbol=row.symbol,
                trade_date=trade_date,
            )
            candidates.append(
                Timing2SetupScanCandidate(
                    symbol=row.symbol,
                    name=row.name,
                    market=row.market,
                    match=match,
                    already_recorded=already_recorded,
                )
            )
            if not already_recorded:
                payloads_to_record.append(
                    self._build_payload(
                        trade_date=trade_date,
                        candidate_name=row.name,
                        candidate_market=row.market,
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
                            strategy_name=STRATEGY_NAME_TIMING2_SETUP,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        skipped_existing_count = sum(
            1 for candidate in candidates if candidate.already_recorded
        )
        _log.info(
            f"[timing2_setup_scan:done] trade_date={trade_date} "
            f"matched_count={len(candidates)} recorded_count={len(recorded_signals)} "
            f"skipped_existing_count={skipped_existing_count}"
        )
        return Timing2SetupScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            universe_count=len(rows),
            matched_count=len(candidates),
            recorded_count=len(recorded_signals),
            skipped_existing_count=skipped_existing_count,
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _validate_daily_count(
        self,
        value: int,
        *,
        settings: Timing2SetupSettings,
    ) -> int:
        minimum = settings.validated().min_required_completed_candles()
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"daily_count must be an integer: {value!r}")
        if value < minimum:
            raise ValueError(
                f"daily_count must be >= {minimum} for timing2 setup: {value!r}"
            )
        return value

    def _has_existing_signal(self, *, symbol: str, trade_date: str) -> bool:
        existing = self._signal_repo.list_by_symbol(symbol, limit=100)
        for row in existing:
            if row.strategy_name != STRATEGY_NAME_TIMING2_SETUP:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") == trade_date:
                return True
        return False

    def _build_payload(
        self,
        *,
        trade_date: str,
        candidate_name: str,
        candidate_market: str,
        match: Timing2SetupMatch,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": match.symbol,
            "name": candidate_name,
            "market": candidate_market,
            "latest_daily_date": match.latest_daily_date,
            "latest_close": match.latest_close,
            "previous_close": match.previous_close,
            "latest_volume": match.latest_volume,
            "previous_volume": match.previous_volume,
            "close_gain_rate": match.close_gain_rate,
            "volume_ratio": match.volume_ratio,
            "lookback_highest_close": match.lookback_highest_close,
            "lookback_start_date": match.lookback_start_date,
            "lookback_end_date": match.lookback_end_date,
        }
