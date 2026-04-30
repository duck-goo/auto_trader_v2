"""Intraday trigger scan service for buy timing 1."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd
import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import MissingTiming1ConvergenceSignalsError, ServiceError
from services.timing1_convergence_scan_service import (
    STRATEGY_NAME_TIMING1_CONVERGENCE,
)
from storage.db import transaction
from storage.repositories import SignalRepository, SignalRow
from strategy import (
    Timing1IntradayStage,
    Timing1IntradayTransition,
    Timing1IntradayTriggerDecision,
    Timing1IntradayTriggerEvaluator,
    Timing1IntradayTriggerSettings,
)


STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER = "buy_timing1_intraday_trigger"
STRATEGY_NAME_TIMING1_INTRADAY_EXPIRED = "buy_timing1_intraday_expired"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class Timing1IntradayTriggerCandidate:
    symbol: str
    name: str
    market: str
    convergence_signal_id: int
    convergence_trade_date: str
    decision: Timing1IntradayTriggerDecision
    transition_strategy_name: str | None
    transition_recorded: bool


@dataclass(frozen=True)
class Timing1IntradayTriggerScanResult:
    trade_date: str
    scanned_at: str
    convergence_signal_count: int
    skipped_not_next_trading_day_count: int
    candidate_count: int
    transition_count: int
    triggered_count: int
    expired_count: int
    recorded_count: int
    candidates: tuple[Timing1IntradayTriggerCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing1IntradayTriggerService:
    """
    Poll current prices for timing1 convergence symbols and record intraday
    trigger or expiry transitions.

    Safety rules:
    - A symbol is monitored only when `trade_date` is the immediate next
      trading day after its convergence day.
    - Next-trading-day validation uses daily candles instead of a guessed
      calendar, so weekend and holiday gaps do not need hardcoded rules.
    - The service never places orders.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing1IntradayTriggerEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing1IntradayTriggerEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing1IntradayTriggerSettings,
        daily_count: int = 5,
        write_signals: bool = False,
    ) -> Timing1IntradayTriggerScanResult:
        normalized_daily_count = self._validate_daily_count(daily_count)
        convergence_signals = self._load_convergence_signal_map(trade_date=trade_date)
        if not convergence_signals:
            raise MissingTiming1ConvergenceSignalsError(trade_date=trade_date)

        trigger_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
            trade_date=trade_date,
        )
        expired_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING1_INTRADAY_EXPIRED,
            trade_date=trade_date,
        )

        normalized_settings = settings.validated()
        observed_now = self._now_fn().astimezone(_KST)
        self._validate_runtime_trade_date(
            trade_date=trade_date,
            observed_at=observed_now,
        )
        scanned_at = observed_now.isoformat()
        candidates: list[Timing1IntradayTriggerCandidate] = []
        payloads_to_record: list[tuple[str, dict]] = []
        skipped_not_next_trading_day_count = 0

        _log.info(
            f"[timing1_intraday_scan:start] trade_date={trade_date} "
            f"convergence_signal_count={len(convergence_signals)} "
            f"write_signals={write_signals}"
        )

        for symbol, convergence_signal in convergence_signals.items():
            payload = self._require_payload_dict(
                convergence_signal,
                strategy_name=STRATEGY_NAME_TIMING1_CONVERGENCE,
            )
            name = self._require_payload_text(payload, "name", convergence_signal.id)
            market = self._require_payload_text(
                payload,
                "market",
                convergence_signal.id,
            )
            convergence_trade_date = self._require_payload_text(
                payload,
                "convergence_trade_date",
                convergence_signal.id,
            )
            target_price = self._require_payload_int(
                payload,
                "convergence_day_high",
                convergence_signal.id,
            )

            is_next_trading_day = self._is_next_trading_day(
                symbol=symbol,
                trade_date=trade_date,
                convergence_trade_date=convergence_trade_date,
                daily_count=normalized_daily_count,
            )
            if not is_next_trading_day:
                skipped_not_next_trading_day_count += 1
                continue

            stage_before = self._resolve_stage_before(
                symbol=symbol,
                trigger_map=trigger_map,
                expired_map=expired_map,
            )

            try:
                snapshot = self._broker.get_current_price(symbol)
            except Exception as exc:
                raise ServiceError(
                    f"Failed to load current price for symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            try:
                decision = self._evaluator.evaluate(
                    symbol=symbol,
                    trade_date=trade_date,
                    observed_at=snapshot.timestamp,
                    target_price=target_price,
                    current_price=snapshot.price,
                    stage_before=stage_before,
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate timing1 intraday trigger for symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            transition_strategy_name = self._map_transition_strategy(
                decision.transition
            )
            transition_recorded = False
            if transition_strategy_name is not None:
                payloads_to_record.append(
                    (
                        transition_strategy_name,
                        self._build_payload(
                            trade_date=trade_date,
                            convergence_signal_id=convergence_signal.id,
                            convergence_trade_date=convergence_trade_date,
                            candidate_name=name,
                            candidate_market=market,
                            decision=decision,
                        ),
                    )
                )
                transition_recorded = write_signals

            candidates.append(
                Timing1IntradayTriggerCandidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    convergence_signal_id=convergence_signal.id,
                    convergence_trade_date=convergence_trade_date,
                    decision=decision,
                    transition_strategy_name=transition_strategy_name,
                    transition_recorded=transition_recorded,
                )
            )

        recorded_signals: list[SignalRow] = []
        if write_signals and payloads_to_record:
            with transaction(self._conn):
                for strategy_name, payload in payloads_to_record:
                    recorded_signals.append(
                        self._signal_repo.record(
                            symbol=payload["symbol"],
                            strategy_name=strategy_name,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        transition_count = sum(
            1
            for candidate in candidates
            if candidate.transition_strategy_name is not None
        )
        triggered_count = sum(
            1
            for candidate in candidates
            if candidate.decision.stage_after == Timing1IntradayStage.TRIGGERED
        )
        expired_count = sum(
            1
            for candidate in candidates
            if candidate.decision.stage_after == Timing1IntradayStage.EXPIRED
        )

        _log.info(
            f"[timing1_intraday_scan:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} transition_count={transition_count} "
            f"recorded_count={len(recorded_signals)} triggered_count={triggered_count} "
            f"expired_count={expired_count} "
            f"skipped_not_next_trading_day_count={skipped_not_next_trading_day_count}"
        )
        return Timing1IntradayTriggerScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            convergence_signal_count=len(convergence_signals),
            skipped_not_next_trading_day_count=(
                skipped_not_next_trading_day_count
            ),
            candidate_count=len(candidates),
            transition_count=transition_count,
            triggered_count=triggered_count,
            expired_count=expired_count,
            recorded_count=len(recorded_signals),
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    @staticmethod
    def _validate_daily_count(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"daily_count must be an integer: {value!r}")
        if value < 2:
            raise ValueError(f"daily_count must be >= 2: {value!r}")
        return value

    def _load_convergence_signal_map(self, *, trade_date: str) -> dict[str, SignalRow]:
        rows = self._signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING1_CONVERGENCE,
            limit=5000,
        )
        result: dict[str, SignalRow] = {}
        for row in rows:
            if row.symbol in result:
                continue
            if not row.payload:
                continue
            convergence_trade_date = row.payload.get("convergence_trade_date")
            if not isinstance(convergence_trade_date, str):
                continue
            if convergence_trade_date >= trade_date:
                continue
            result[row.symbol] = row
        return result

    def _load_signal_map(
        self,
        *,
        strategy_name: str,
        trade_date: str,
    ) -> dict[str, SignalRow]:
        rows = self._signal_repo.list_by_strategy(strategy_name, limit=5000)
        result: dict[str, SignalRow] = {}
        for row in rows:
            if row.symbol in result:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") != trade_date:
                continue
            result[row.symbol] = row
        return result

    @staticmethod
    def _resolve_stage_before(
        *,
        symbol: str,
        trigger_map: dict[str, SignalRow],
        expired_map: dict[str, SignalRow],
    ) -> Timing1IntradayStage:
        if symbol in trigger_map:
            return Timing1IntradayStage.TRIGGERED
        if symbol in expired_map:
            return Timing1IntradayStage.EXPIRED
        return Timing1IntradayStage.WAIT_BREAKOUT

    def _is_next_trading_day(
        self,
        *,
        symbol: str,
        trade_date: str,
        convergence_trade_date: str,
        daily_count: int,
    ) -> bool:
        try:
            daily_candles = self._broker.get_daily_candles(
                symbol,
                count=daily_count,
                end_date=trade_date.replace("-", ""),
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to load daily candles for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if not isinstance(daily_candles, pd.DataFrame):
            raise ServiceError(
                f"Daily candles must be a DataFrame for symbol={symbol}."
            )
        if "datetime" not in daily_candles.columns:
            raise ServiceError(
                f"Daily candles are missing datetime for symbol={symbol}."
            )

        normalized = daily_candles.copy(deep=True)
        try:
            normalized = normalized.assign(
                datetime=pd.to_datetime(
                    normalized["datetime"],
                    errors="raise",
                )
            )
        except Exception as exc:
            raise ServiceError(
                f"Daily candle datetime parse failed for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        normalized = normalized.assign(
            date_text=normalized["datetime"].dt.strftime("%Y-%m-%d")
        )
        completed = normalized[normalized["date_text"] < trade_date].copy()
        if completed.empty:
            return False

        latest_completed_date = str(
            completed.sort_values("datetime").iloc[-1]["date_text"]
        )
        return latest_completed_date == convergence_trade_date

    @staticmethod
    def _map_transition_strategy(
        transition: Timing1IntradayTransition,
    ) -> str | None:
        if transition == Timing1IntradayTransition.BREAKOUT_TRIGGERED:
            return STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER
        if transition == Timing1IntradayTransition.EXPIRED:
            return STRATEGY_NAME_TIMING1_INTRADAY_EXPIRED
        return None

    @staticmethod
    def _require_payload_dict(
        signal_row: SignalRow,
        *,
        strategy_name: str,
    ) -> dict:
        if not signal_row.payload:
            raise ServiceError(
                f"Signal payload is missing for strategy={strategy_name} "
                f"id={signal_row.id}."
            )
        return signal_row.payload

    @staticmethod
    def _require_payload_text(
        payload: dict,
        field_name: str,
        signal_id: int,
    ) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(
                f"Signal payload field is missing or invalid: "
                f"id={signal_id}, field={field_name!r}"
            )
        return value.strip()

    @staticmethod
    def _require_payload_int(
        payload: dict,
        field_name: str,
        signal_id: int,
    ) -> int:
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ServiceError(
                f"Signal payload field is missing or invalid: "
                f"id={signal_id}, field={field_name!r}"
            )
        return value

    @staticmethod
    def _build_payload(
        *,
        trade_date: str,
        convergence_signal_id: int,
        convergence_trade_date: str,
        candidate_name: str,
        candidate_market: str,
        decision: Timing1IntradayTriggerDecision,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": decision.symbol,
            "name": candidate_name,
            "market": candidate_market,
            "convergence_signal_id": convergence_signal_id,
            "convergence_trade_date": convergence_trade_date,
            "observed_at": decision.observed_at,
            "stage_before": decision.stage_before.value,
            "stage_after": decision.stage_after.value,
            "transition": decision.transition.value,
            "target_price": decision.target_price,
            "current_price": decision.current_price,
        }

    @staticmethod
    def _validate_runtime_trade_date(
        *,
        trade_date: str,
        observed_at: datetime,
    ) -> None:
        runtime_trade_date = observed_at.astimezone(_KST).strftime("%Y-%m-%d")
        if runtime_trade_date != trade_date:
            raise ServiceError(
                "Timing1 intraday trigger scan supports only the current KST "
                f"trade_date: trade_date={trade_date}, "
                f"runtime_trade_date={runtime_trade_date}"
            )
