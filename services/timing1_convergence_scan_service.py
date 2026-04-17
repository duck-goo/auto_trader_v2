"""After-close scan service for buy timing 1 convergence."""

from __future__ import annotations

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
from services.timing1_setup_scan_service import STRATEGY_NAME_TIMING1_SETUP
from storage.db import transaction
from storage.repositories import (
    IntradayBar15m,
    IntradayBar15mRepository,
    SignalRepository,
    SignalRow,
)
from strategy import (
    Timing1ConvergenceEvaluator,
    Timing1ConvergenceMatch,
    Timing1ConvergenceSettings,
)


STRATEGY_NAME_TIMING1_CONVERGENCE = "buy_timing1_convergence"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class Timing1ConvergenceCandidate:
    symbol: str
    name: str
    market: str
    strong_day_date: str
    minute_candle_count: int
    intraday_bar_count: int
    history_bar_count: int
    already_recorded: bool
    match: Timing1ConvergenceMatch | None


@dataclass(frozen=True)
class Timing1ConvergenceScanResult:
    trade_date: str
    scanned_at: str
    setup_signal_count: int
    processed_count: int
    stored_symbol_count: int
    matched_count: int
    recorded_count: int
    skipped_existing_count: int
    candidates: tuple[Timing1ConvergenceCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing1ConvergenceScanService:
    """
    After-close convergence scan for timing1 setup signals.

    Safety rules:
    - KIS stock minute API exposes same-day minute data only.
    - Therefore this service only supports the current KST trade_date.
    - 15-minute 60MA is computed from persisted historical bars plus today's
      captured bars. If history is insufficient, the symbol is skipped.
    - The service never places orders.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        intraday_bar_repo: IntradayBar15mRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing1ConvergenceEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._signal_repo = signal_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing1ConvergenceEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing1ConvergenceSettings,
        history_limit: int = 300,
        write: bool = False,
    ) -> Timing1ConvergenceScanResult:
        normalized_trade_date = self._require_trade_date(trade_date)
        normalized_settings = settings.validated()
        normalized_history_limit = self._validate_history_limit(
            history_limit,
            settings=normalized_settings,
        )
        observed_at = self._now_fn().astimezone(_KST)
        self._validate_runtime_window(
            trade_date=normalized_trade_date,
            observed_at=observed_at,
        )

        scanned_at = observed_at.isoformat()
        setup_signals = self._load_setup_signals(normalized_trade_date)
        candidates: list[Timing1ConvergenceCandidate] = []
        recorded_payloads: list[dict] = []
        bars_to_store: dict[str, list[IntradayBar15m]] = {}

        _log.info(
            f"[timing1_convergence_scan:start] trade_date={normalized_trade_date} "
            f"setup_signal_count={len(setup_signals)} write={write}"
        )

        for setup_signal in setup_signals:
            payload = setup_signal.payload or {}
            symbol = str(payload.get("symbol", "")).strip()
            name = str(payload.get("name", "")).strip()
            market = str(payload.get("market", "")).strip()
            strong_day = payload.get("strong_day")
            if not symbol or not isinstance(strong_day, dict):
                raise ServiceError(
                    "Timing1 setup signal payload is missing required fields: "
                    f"signal_id={setup_signal.id}"
                )
            strong_day_date = str(strong_day.get("date", "")).strip()
            if not strong_day_date:
                raise ServiceError(
                    "Timing1 setup signal payload strong_day.date is missing: "
                    f"signal_id={setup_signal.id}"
                )

            try:
                minute_candles = self._broker.get_same_day_minute_candles(
                    symbol,
                    end_time="153000",
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to load same-day minute candles for symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            try:
                today_bars_df = resample_minute_candles_to_fixed_bars(
                    minute_candles=minute_candles,
                    trade_date=normalized_trade_date,
                    observed_at=observed_at,
                    bar_minutes=normalized_settings.bar_minutes,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to resample minute candles for symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if write:
                bars_to_store[symbol] = self._bars_from_df(today_bars_df)

            history_df = self._build_history_df(
                symbol=symbol,
                trade_date=normalized_trade_date,
                today_bars_df=today_bars_df,
                history_limit=normalized_history_limit,
            )

            try:
                match = self._evaluator.evaluate(
                    symbol=symbol,
                    trade_date=normalized_trade_date,
                    strong_day_date=strong_day_date,
                    intraday_bars=history_df,
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate timing1 convergence for symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            already_recorded = self._has_existing_signal(
                symbol=symbol,
                trade_date=normalized_trade_date,
            )
            candidate = Timing1ConvergenceCandidate(
                symbol=symbol,
                name=name,
                market=market,
                strong_day_date=strong_day_date,
                minute_candle_count=len(minute_candles),
                intraday_bar_count=len(today_bars_df),
                history_bar_count=len(history_df),
                already_recorded=already_recorded,
                match=match,
            )
            candidates.append(candidate)

            if match is not None and not already_recorded:
                recorded_payloads.append(
                    self._build_payload(
                        trade_date=normalized_trade_date,
                        name=name,
                        market=market,
                        match=match,
                    )
                )

        recorded_signals: list[SignalRow] = []
        if write and (bars_to_store or recorded_payloads):
            with transaction(self._conn):
                for symbol, bars in bars_to_store.items():
                    self._intraday_bar_repo.replace_for_symbol_and_date(
                        trade_date=normalized_trade_date,
                        symbol=symbol,
                        bars=bars,
                        refreshed_at=scanned_at,
                    )
                for payload in recorded_payloads:
                    recorded_signals.append(
                        self._signal_repo.record(
                            symbol=payload["symbol"],
                            strategy_name=STRATEGY_NAME_TIMING1_CONVERGENCE,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        matched_count = sum(
            1 for candidate in candidates if candidate.match is not None
        )
        skipped_existing_count = sum(
            1
            for candidate in candidates
            if candidate.match is not None and candidate.already_recorded
        )
        _log.info(
            f"[timing1_convergence_scan:done] trade_date={normalized_trade_date} "
            f"processed_count={len(candidates)} matched_count={matched_count} "
            f"stored_symbol_count={len(bars_to_store) if write else 0} "
            f"recorded_count={len(recorded_signals)} "
            f"skipped_existing_count={skipped_existing_count}"
        )
        return Timing1ConvergenceScanResult(
            trade_date=normalized_trade_date,
            scanned_at=scanned_at,
            setup_signal_count=len(setup_signals),
            processed_count=len(candidates),
            stored_symbol_count=len(bars_to_store) if write else 0,
            matched_count=matched_count,
            recorded_count=len(recorded_signals),
            skipped_existing_count=skipped_existing_count,
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _require_trade_date(self, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"trade_date must be a string: {value!r}")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
        return value

    def _validate_history_limit(
        self,
        value: int,
        *,
        settings: Timing1ConvergenceSettings,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"history_limit must be an integer: {value!r}")
        minimum = settings.min_required_bars()
        if value < minimum:
            raise ValueError(f"history_limit must be >= {minimum}: {value!r}")
        return value

    def _validate_runtime_window(
        self,
        *,
        trade_date: str,
        observed_at: datetime,
    ) -> None:
        runtime_trade_date = observed_at.strftime("%Y-%m-%d")
        if runtime_trade_date != trade_date:
            raise ServiceError(
                "Timing1 convergence scan only supports the current KST trade_date "
                "because KIS stock minute data is same-day only: "
                f"trade_date={trade_date}, runtime_trade_date={runtime_trade_date}"
            )
        market_close = observed_at.replace(
            hour=15,
            minute=30,
            second=0,
            microsecond=0,
        )
        if observed_at < market_close:
            raise ServiceError(
                "Timing1 convergence scan requires completed same-day bars after "
                f"15:30 KST: observed_at={observed_at.isoformat()}"
            )

    def _load_setup_signals(self, trade_date: str) -> list[SignalRow]:
        rows = self._signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING1_SETUP,
            limit=2000,
        )
        by_symbol: dict[str, SignalRow] = {}
        for row in rows:
            if not row.payload:
                continue
            if row.payload.get("trade_date") != trade_date:
                continue
            symbol = str(row.payload.get("symbol", "")).strip()
            if not symbol or symbol in by_symbol:
                continue
            by_symbol[symbol] = row
        return list(by_symbol.values())

    def _has_existing_signal(self, *, symbol: str, trade_date: str) -> bool:
        existing = self._signal_repo.list_by_symbol(symbol, limit=200)
        for row in existing:
            if row.strategy_name != STRATEGY_NAME_TIMING1_CONVERGENCE:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") == trade_date:
                return True
        return False

    def _bars_from_df(self, df: pd.DataFrame) -> list[IntradayBar15m]:
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

    def _build_history_df(
        self,
        *,
        symbol: str,
        trade_date: str,
        today_bars_df: pd.DataFrame,
        history_limit: int,
    ) -> pd.DataFrame:
        end_at = f"{trade_date}T23:59:59+09:00"
        stored_rows = self._intraday_bar_repo.list_recent_for_symbol(
            symbol=symbol,
            end_at=end_at,
            limit=history_limit,
        )
        rows = [
            {
                "bar_start_at": row.bar_start_at,
                "bar_end_at": row.bar_end_at,
                "high": row.high,
                "close": row.close,
            }
            for row in stored_rows
            if row.trade_date != trade_date
        ]
        if rows:
            history_df = pd.DataFrame(rows)
        else:
            history_df = pd.DataFrame(
                columns=["bar_start_at", "bar_end_at", "high", "close"]
            )

        today_subset = today_bars_df[
            ["bar_start_at", "bar_end_at", "high", "close"]
        ].copy()
        merged = pd.concat([history_df, today_subset], ignore_index=True)
        if merged.empty:
            return merged
        merged = merged.drop_duplicates(
            subset=["bar_start_at"],
            keep="last",
        )
        return merged.sort_values("bar_end_at").reset_index(drop=True)

    def _build_payload(
        self,
        *,
        trade_date: str,
        name: str,
        market: str,
        match: Timing1ConvergenceMatch,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": match.symbol,
            "name": name,
            "market": market,
            "strong_day_date": match.strong_day_date,
            "convergence_trade_date": match.convergence_trade_date,
            "convergence_bar_start_at": match.bar_start_at,
            "convergence_bar_end_at": match.bar_end_at,
            "convergence_close_price": match.close_price,
            "ma20": round(match.ma_short, 6),
            "ma60": round(match.ma_long, 6),
            "convergence_threshold_rate": round(
                match.convergence_threshold_rate,
                6,
            ),
            "convergence_spread": round(match.convergence_spread, 6),
            "convergence_day_high": match.day_high,
        }
