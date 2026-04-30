"""30-second-bar intraday trigger scan service for buy timing 2."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from logger import get_logger
from services.errors import MissingTiming2SetupSignalsError, ServiceError
from services.timing2_setup_scan_service import STRATEGY_NAME_TIMING2_SETUP
from storage.db import transaction
from storage.repositories import (
    IntradayBar30sRepository,
    IntradayBar30sRow,
    SignalRepository,
    SignalRow,
)
from strategy import (
    Timing2ThirtySecondTransition,
    Timing2ThirtySecondTriggerDecision,
    Timing2ThirtySecondTriggerEvaluator,
    Timing2ThirtySecondTriggerSettings,
    Timing2ThirtySecondTriggerState,
)


STRATEGY_NAME_TIMING2_30S_MORNING_DIP = "buy_timing2_30s_morning_dip"
STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER = (
    "buy_timing2_30s_morning_open_reclaim"
)
STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER = "buy_timing2_30s_range_high_breakout"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


class Timing2ThirtySecondTriggerOutcome(str, enum.Enum):
    EVALUATED = "EVALUATED"
    SKIPPED_NO_30S_BAR = "SKIPPED_NO_30S_BAR"
    SKIPPED_NO_SESSION_OPEN = "SKIPPED_NO_SESSION_OPEN"
    SKIPPED_NO_MORNING_HIGH = "SKIPPED_NO_MORNING_HIGH"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Timing2ThirtySecondTriggerCandidate:
    symbol: str
    name: str
    market: str
    setup_signal_id: int
    outcome: Timing2ThirtySecondTriggerOutcome
    reason: str | None
    latest_bar: IntradayBar30sRow | None
    decision: Timing2ThirtySecondTriggerDecision | None
    transition_strategy_name: str | None
    transition_recorded: bool


@dataclass(frozen=True)
class Timing2ThirtySecondTriggerScanResult:
    trade_date: str
    scanned_at: str
    setup_signal_count: int
    candidate_count: int
    evaluated_count: int
    skipped_count: int
    failed_count: int
    transition_count: int
    buy_triggered_count: int
    recorded_count: int
    candidates: tuple[Timing2ThirtySecondTriggerCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2ThirtySecondTriggerService:
    """
    Evaluate timing2 intraday triggers from stored completed 30-second bars.

    This service records state transitions as append-only signal rows so a
    restart can reconstruct whether the morning dip, morning entry, and
    post-10:00 entry already happened.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        intraday_bar_repo: IntradayBar30sRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing2ThirtySecondTriggerEvaluator | None = None,
    ) -> None:
        self._conn = conn
        self._signal_repo = signal_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing2ThirtySecondTriggerEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing2ThirtySecondTriggerSettings,
        write_signals: bool = False,
    ) -> Timing2ThirtySecondTriggerScanResult:
        setup_signals = self._load_setup_signal_map(trade_date=trade_date)
        if not setup_signals:
            raise MissingTiming2SetupSignalsError(trade_date=trade_date)

        dip_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
            trade_date=trade_date,
        )
        morning_trigger_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
            trade_date=trade_date,
        )
        range_trigger_map = self._load_signal_map(
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            trade_date=trade_date,
        )

        normalized_settings = settings.validated()
        scanned_at = self._now_fn().astimezone(_KST).isoformat()
        candidates: list[Timing2ThirtySecondTriggerCandidate] = []
        payloads_to_record: list[tuple[str, dict]] = []

        _log.info(
            f"[timing2_30s_trigger_scan:start] trade_date={trade_date} "
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

            try:
                latest_bar = self._intraday_bar_repo.get_latest_for_symbol_and_date(
                    trade_date=trade_date,
                    symbol=symbol,
                )
                if latest_bar is None:
                    candidates.append(
                        self._candidate(
                            symbol=symbol,
                            name=name,
                            market=market,
                            setup_signal_id=setup_signal.id,
                            outcome=Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_30S_BAR,
                            reason="No completed 30-second bar is stored.",
                            latest_bar=None,
                            decision=None,
                            transition_strategy_name=None,
                            transition_recorded=False,
                        )
                    )
                    continue

                session_open_price = self._intraday_bar_repo.get_session_open_price(
                    trade_date=trade_date,
                    symbol=symbol,
                )
                if session_open_price is None:
                    candidates.append(
                        self._candidate(
                            symbol=symbol,
                            name=name,
                            market=market,
                            setup_signal_id=setup_signal.id,
                            outcome=(
                                Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_SESSION_OPEN
                            ),
                            reason="Session open price is unavailable.",
                            latest_bar=latest_bar,
                            decision=None,
                            transition_strategy_name=None,
                            transition_recorded=False,
                        )
                    )
                    continue

                state_before = self._resolve_state_before(
                    symbol=symbol,
                    dip_map=dip_map,
                    morning_trigger_map=morning_trigger_map,
                    range_trigger_map=range_trigger_map,
                )
                morning_high_close = self._resolve_morning_high_close(
                    trade_date=trade_date,
                    symbol=symbol,
                    latest_bar=latest_bar,
                    settings=normalized_settings,
                )
                if (
                    morning_high_close is None
                    and self._is_range_phase(
                        latest_bar=latest_bar,
                        settings=normalized_settings,
                    )
                    and not state_before.range_triggered
                ):
                    candidates.append(
                        self._candidate(
                            symbol=symbol,
                            name=name,
                            market=market,
                            setup_signal_id=setup_signal.id,
                            outcome=(
                                Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_MORNING_HIGH
                            ),
                            reason="Morning 09:00-10:00 close high is unavailable.",
                            latest_bar=latest_bar,
                            decision=None,
                            transition_strategy_name=None,
                            transition_recorded=False,
                        )
                    )
                    continue

                decision = self._evaluator.evaluate(
                    symbol=symbol,
                    trade_date=trade_date,
                    bar_end_at=self._parse_kst_iso("bar_end_at", latest_bar.bar_end_at),
                    session_open_price=session_open_price,
                    bar_close_price=latest_bar.close,
                    morning_high_close=morning_high_close,
                    state_before=state_before,
                    settings=normalized_settings,
                )
            except Exception as exc:
                candidates.append(
                    self._candidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        outcome=Timing2ThirtySecondTriggerOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                        latest_bar=None,
                        decision=None,
                        transition_strategy_name=None,
                        transition_recorded=False,
                    )
                )
                continue

            transition_strategy_name = self._map_transition_strategy(
                decision.transition
            )
            transition_recorded = False
            if transition_strategy_name is not None:
                payload = self._build_payload(
                    trade_date=trade_date,
                    setup_signal_id=setup_signal.id,
                    candidate_name=name,
                    candidate_market=market,
                    latest_bar=latest_bar,
                    decision=decision,
                )
                payloads_to_record.append((transition_strategy_name, payload))
                transition_recorded = write_signals

            candidates.append(
                self._candidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    setup_signal_id=setup_signal.id,
                    outcome=Timing2ThirtySecondTriggerOutcome.EVALUATED,
                    reason=None,
                    latest_bar=latest_bar,
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

        evaluated_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2ThirtySecondTriggerOutcome.EVALUATED
        )
        skipped_count = sum(
            1
            for candidate in candidates
            if candidate.outcome
            in (
                Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_30S_BAR,
                Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_SESSION_OPEN,
                Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_MORNING_HIGH,
            )
        )
        failed_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2ThirtySecondTriggerOutcome.FAILED
        )
        transition_count = sum(
            1
            for candidate in candidates
            if candidate.transition_strategy_name is not None
        )
        buy_triggered_count = sum(
            1
            for candidate in candidates
            if candidate.decision is not None and candidate.decision.buy_triggered
        )

        _log.info(
            f"[timing2_30s_trigger_scan:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} evaluated_count={evaluated_count} "
            f"transition_count={transition_count} "
            f"buy_triggered_count={buy_triggered_count} "
            f"recorded_count={len(recorded_signals)} skipped_count={skipped_count} "
            f"failed_count={failed_count}"
        )
        return Timing2ThirtySecondTriggerScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            setup_signal_count=len(setup_signals),
            candidate_count=len(candidates),
            evaluated_count=evaluated_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            transition_count=transition_count,
            buy_triggered_count=buy_triggered_count,
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

    @staticmethod
    def _resolve_state_before(
        *,
        symbol: str,
        dip_map: dict[str, SignalRow],
        morning_trigger_map: dict[str, SignalRow],
        range_trigger_map: dict[str, SignalRow],
    ) -> Timing2ThirtySecondTriggerState:
        morning_triggered = symbol in morning_trigger_map
        return Timing2ThirtySecondTriggerState(
            morning_dipped_below_open=(symbol in dip_map or morning_triggered),
            morning_triggered=morning_triggered,
            range_triggered=symbol in range_trigger_map,
        )

    def _resolve_morning_high_close(
        self,
        *,
        trade_date: str,
        symbol: str,
        latest_bar: IntradayBar30sRow,
        settings: Timing2ThirtySecondTriggerSettings,
    ) -> int | None:
        if not self._is_range_phase(latest_bar=latest_bar, settings=settings):
            return None
        return self._intraday_bar_repo.get_max_close_between(
            trade_date=trade_date,
            symbol=symbol,
            start_time=settings.morning_start_time,
            end_time=settings.morning_end_time,
        )

    @staticmethod
    def _is_range_phase(
        *,
        latest_bar: IntradayBar30sRow,
        settings: Timing2ThirtySecondTriggerSettings,
    ) -> bool:
        bar_end_at = Timing2ThirtySecondTriggerService._parse_kst_iso(
            "bar_end_at",
            latest_bar.bar_end_at,
        )
        range_start = datetime.strptime(
            settings.range_breakout_start_time,
            "%H:%M:%S",
        ).time()
        return bar_end_at.time() >= range_start

    @staticmethod
    def _map_transition_strategy(
        transition: Timing2ThirtySecondTransition,
    ) -> str | None:
        if transition == Timing2ThirtySecondTransition.MORNING_DIP_CONFIRMED:
            return STRATEGY_NAME_TIMING2_30S_MORNING_DIP
        if (
            transition
            == Timing2ThirtySecondTransition.MORNING_OPEN_RECLAIM_TRIGGERED
        ):
            return STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        if transition == Timing2ThirtySecondTransition.RANGE_HIGH_BREAKOUT_TRIGGERED:
            return STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
        return None

    @staticmethod
    def _candidate(
        *,
        symbol: str,
        name: str,
        market: str,
        setup_signal_id: int,
        outcome: Timing2ThirtySecondTriggerOutcome,
        reason: str | None,
        latest_bar: IntradayBar30sRow | None,
        decision: Timing2ThirtySecondTriggerDecision | None,
        transition_strategy_name: str | None,
        transition_recorded: bool,
    ) -> Timing2ThirtySecondTriggerCandidate:
        return Timing2ThirtySecondTriggerCandidate(
            symbol=symbol,
            name=name,
            market=market,
            setup_signal_id=setup_signal_id,
            outcome=outcome,
            reason=reason,
            latest_bar=latest_bar,
            decision=decision,
            transition_strategy_name=transition_strategy_name,
            transition_recorded=transition_recorded,
        )

    @staticmethod
    def _parse_kst_iso(name: str, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be ISO8601: {value!r}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone offset: {value!r}")
        return parsed.astimezone(_KST)

    @staticmethod
    def _serialize_state(state: Timing2ThirtySecondTriggerState) -> dict:
        return {
            "morning_dipped_below_open": state.morning_dipped_below_open,
            "morning_triggered": state.morning_triggered,
            "range_triggered": state.range_triggered,
        }

    @staticmethod
    def _build_payload(
        *,
        trade_date: str,
        setup_signal_id: int,
        candidate_name: str,
        candidate_market: str,
        latest_bar: IntradayBar30sRow,
        decision: Timing2ThirtySecondTriggerDecision,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": decision.symbol,
            "name": candidate_name,
            "market": candidate_market,
            "setup_signal_id": setup_signal_id,
            "bar_start_at": latest_bar.bar_start_at,
            "bar_end_at": decision.bar_end_at,
            "state_before": Timing2ThirtySecondTriggerService._serialize_state(
                decision.state_before,
            ),
            "state_after": Timing2ThirtySecondTriggerService._serialize_state(
                decision.state_after,
            ),
            "transition": decision.transition.value,
            "trigger_type": decision.trigger_type.value,
            "buy_triggered": decision.buy_triggered,
            "session_open_price": decision.session_open_price,
            "bar_close_price": decision.bar_close_price,
            "morning_high_close": decision.morning_high_close,
        }

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
