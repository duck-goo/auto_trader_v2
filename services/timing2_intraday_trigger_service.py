"""Intraday trigger scan service for buy timing 2."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import ServiceError
from services.timing2_setup_scan_service import STRATEGY_NAME_TIMING2_SETUP
from storage.db import transaction
from storage.repositories import SignalRepository, SignalRow
from strategy import (
    Timing2IntradayStage,
    Timing2IntradayTransition,
    Timing2IntradayTriggerDecision,
    Timing2IntradayTriggerEvaluator,
    Timing2IntradayTriggerSettings,
)


STRATEGY_NAME_TIMING2_INTRADAY_BREAKOUT = "buy_timing2_intraday_breakout"
STRATEGY_NAME_TIMING2_INTRADAY_PULLBACK = "buy_timing2_intraday_pullback"
STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER = "buy_timing2_intraday_trigger"
STRATEGY_NAME_TIMING2_INTRADAY_EXPIRED = "buy_timing2_intraday_expired"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class Timing2IntradayTriggerCandidate:
    symbol: str
    name: str
    market: str
    setup_signal_id: int
    decision: Timing2IntradayTriggerDecision
    transition_strategy_name: str | None
    transition_recorded: bool


@dataclass(frozen=True)
class Timing2IntradayTriggerScanResult:
    trade_date: str
    scanned_at: str
    setup_signal_count: int
    candidate_count: int
    transition_count: int
    triggered_count: int
    expired_count: int
    recorded_count: int
    candidates: tuple[Timing2IntradayTriggerCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2IntradayTriggerService:
    """
    Poll current prices for timing2 setup symbols and persist stage transitions.

    Persistence approach:
    - timing2 daily setup signals identify today's candidates
    - intraday transitions are stored as append-only signal rows
    - current stage is reconstructed from previously recorded transition signals
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing2IntradayTriggerEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing2IntradayTriggerEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing2IntradayTriggerSettings,
        write_signals: bool = False,
    ) -> Timing2IntradayTriggerScanResult:
        setup_signals = self._load_setup_signal_map(trade_date=trade_date)
        if not setup_signals:
            raise ServiceError(
                f"Timing2 setup signals are missing for trade_date={trade_date!r}."
            )

        breakout_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_INTRADAY_BREAKOUT,
            trade_date=trade_date,
        )
        pullback_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_INTRADAY_PULLBACK,
            trade_date=trade_date,
        )
        trigger_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
            trade_date=trade_date,
        )
        expired_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_INTRADAY_EXPIRED,
            trade_date=trade_date,
        )

        normalized_settings = settings.validated()
        scanned_at = self._now_fn().astimezone(_KST).isoformat()
        candidates: list[Timing2IntradayTriggerCandidate] = []
        payloads_to_record: list[tuple[str, dict]] = []

        _log.info(
            f"[timing2_intraday_scan:start] trade_date={trade_date} "
            f"setup_signal_count={len(setup_signals)} write_signals={write_signals}"
        )

        for symbol, setup_signal in setup_signals.items():
            setup_payload = self._require_payload_dict(
                setup_signal,
                strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            )
            name = self._require_payload_text(setup_payload, "name", setup_signal.id)
            market = self._require_payload_text(
                setup_payload,
                "market",
                setup_signal.id,
            )
            stage_before = self._resolve_stage_before(
                symbol=symbol,
                breakout_map=breakout_map,
                pullback_map=pullback_map,
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
                    base_open_price=snapshot.open,
                    current_price=snapshot.price,
                    stage_before=stage_before,
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate timing2 intraday trigger for symbol={symbol}: "
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
                            setup_signal_id=setup_signal.id,
                            candidate_name=name,
                            candidate_market=market,
                            decision=decision,
                        ),
                    )
                )
                transition_recorded = write_signals

            candidates.append(
                Timing2IntradayTriggerCandidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    setup_signal_id=setup_signal.id,
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
            if candidate.decision.stage_after == Timing2IntradayStage.TRIGGERED
        )
        expired_count = sum(
            1
            for candidate in candidates
            if candidate.decision.stage_after == Timing2IntradayStage.EXPIRED
        )

        _log.info(
            f"[timing2_intraday_scan:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} transition_count={transition_count} "
            f"recorded_count={len(recorded_signals)} triggered_count={triggered_count} "
            f"expired_count={expired_count}"
        )
        return Timing2IntradayTriggerScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            setup_signal_count=len(setup_signals),
            candidate_count=len(candidates),
            transition_count=transition_count,
            triggered_count=triggered_count,
            expired_count=expired_count,
            recorded_count=len(recorded_signals),
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _load_setup_signal_map(self, *, trade_date: str) -> dict[str, SignalRow]:
        rows = self._signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_SETUP,
            limit=5000,
        )
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

    def _resolve_stage_before(
        self,
        *,
        symbol: str,
        breakout_map: dict[str, SignalRow],
        pullback_map: dict[str, SignalRow],
        trigger_map: dict[str, SignalRow],
        expired_map: dict[str, SignalRow],
    ) -> Timing2IntradayStage:
        if symbol in trigger_map:
            return Timing2IntradayStage.TRIGGERED
        if symbol in expired_map:
            return Timing2IntradayStage.EXPIRED
        if symbol in pullback_map:
            return Timing2IntradayStage.WAIT_REBOUND
        if symbol in breakout_map:
            return Timing2IntradayStage.WAIT_PULLBACK
        return Timing2IntradayStage.WAIT_BREAKOUT

    @staticmethod
    def _map_transition_strategy(
        transition: Timing2IntradayTransition,
    ) -> str | None:
        if transition == Timing2IntradayTransition.BREAKOUT_CONFIRMED:
            return STRATEGY_NAME_TIMING2_INTRADAY_BREAKOUT
        if transition == Timing2IntradayTransition.PULLBACK_CONFIRMED:
            return STRATEGY_NAME_TIMING2_INTRADAY_PULLBACK
        if transition == Timing2IntradayTransition.REBOUND_TRIGGERED:
            return STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER
        if transition == Timing2IntradayTransition.EXPIRED:
            return STRATEGY_NAME_TIMING2_INTRADAY_EXPIRED
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
    def _build_payload(
        *,
        trade_date: str,
        setup_signal_id: int,
        candidate_name: str,
        candidate_market: str,
        decision: Timing2IntradayTriggerDecision,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": decision.symbol,
            "name": candidate_name,
            "market": candidate_market,
            "setup_signal_id": setup_signal_id,
            "observed_at": decision.observed_at,
            "stage_before": decision.stage_before.value,
            "stage_after": decision.stage_after.value,
            "transition": decision.transition.value,
            "base_open_price": decision.base_open_price,
            "current_price": decision.current_price,
            "breakout_trigger_price": decision.breakout_trigger_price,
            "pullback_trigger_price": decision.pullback_trigger_price,
        }
