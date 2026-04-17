"""Consume sell trigger signals and optionally place market sell orders."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from broker.kis.models import OrderType
from logger import get_logger
from services.errors import ServiceError
from services.order_service import OrderOutcome, OrderService
from services.trading_risk_guard_service import TradingRiskGuardService
from services.sell_macd_exit_scan_service import STRATEGY_NAME_SELL_MACD_DECREASE
from services.sell_exit_scan_service import (
    STRATEGY_NAME_SELL_STOP_LOSS,
    STRATEGY_NAME_SELL_TAKE_PROFIT,
)
from storage.db import transaction
from storage.repositories import OrderRepository, PositionRepository, SignalRepository, SignalRow


STRATEGY_NAME_SELL_EXECUTION_AUDIT = "sell_execution_attempt"

_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _require_time_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    try:
        datetime.strptime(value, "%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM:SS: {value!r}") from exc
    return value


class SellSignalExecutionOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SellSignalExecutionSettings:
    start_time: str = "09:00:00"
    cutoff_time: str = "15:20:00"

    def validated(self) -> "SellSignalExecutionSettings":
        start_time = _require_time_text("start_time", self.start_time)
        cutoff_time = _require_time_text("cutoff_time", self.cutoff_time)
        if start_time >= cutoff_time:
            raise ValueError(
                "cutoff_time must be later than start_time: "
                f"start={start_time}, cutoff={cutoff_time}"
            )
        return SellSignalExecutionSettings(
            start_time=start_time,
            cutoff_time=cutoff_time,
        )


@dataclass(frozen=True)
class SellTriggerSignalCandidate:
    signal_id: int
    signal_scanned_at: str
    symbol: str
    name: str
    trade_date: str
    source_strategy_name: str
    strategy_priority: int


@dataclass(frozen=True)
class SellSignalExecutionCandidate:
    signal_id: int
    symbol: str
    name: str
    source_strategy_name: str
    outcome: SellSignalExecutionOutcome
    reason_code: str | None
    reason_message: str | None
    current_price: int | None
    position_qty: int | None
    avg_price: int | None
    client_order_id: str | None
    order_error_code: str | None
    order_error_message: str | None
    acted: bool


@dataclass(frozen=True)
class SellSignalExecutionResult:
    trade_date: str
    executed_at: str
    execute_orders: bool
    pending_signal_count: int
    candidate_count: int
    preview_ready_count: int
    blocked_count: int
    submitted_count: int
    unknown_count: int
    rejected_count: int
    failed_count: int
    acted_count: int
    audit_record_count: int
    candidates: tuple[SellSignalExecutionCandidate, ...]
    acted_signal_ids: tuple[int, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class SellSignalExecutionService:
    """Execute sell signals with conservative duplicate and position guards."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        order_service: OrderService,
        risk_guard_service: TradingRiskGuardService,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._signal_repo = signal_repo
        self._order_repo = order_repo
        self._position_repo = position_repo
        self._order_service = order_service
        self._risk_guard_service = risk_guard_service
        self._now_fn = now_fn or _default_now

    def execute_pending_signals(
        self,
        *,
        trade_date: str,
        settings: SellSignalExecutionSettings,
        signal_limit: int = 200,
        execute_orders: bool = False,
    ) -> SellSignalExecutionResult:
        normalized_settings = settings.validated()
        normalized_limit = _require_positive_int("signal_limit", signal_limit)
        executed_at = self._now_fn().astimezone(_KST).isoformat()

        pending_candidates = self._load_pending_candidates(
            trade_date=trade_date,
            signal_limit=normalized_limit,
        )
        primary_candidates, superseded_candidates = self._split_candidates(
            pending_candidates
        )

        processed: list[SellSignalExecutionCandidate] = []
        acted_signal_ids: list[int] = []
        audit_record_count = 0

        _log.info(
            f"[sell_signal_execution:start] trade_date={trade_date} "
            f"pending_signal_count={len(pending_candidates)} "
            f"execute_orders={execute_orders}"
        )

        for candidate, winner in superseded_candidates:
            result_candidate = self._build_blocked_candidate(
                candidate=candidate,
                reason_code="SUPERSEDED_BY_HIGHER_PRIORITY",
                reason_message=(
                    "Another higher-priority sell signal for the same symbol "
                    f"was selected first: winner_strategy={winner.source_strategy_name}, "
                    f"winner_signal_id={winner.signal_id}"
                ),
            )
            processed.append(result_candidate)
            if execute_orders:
                audit_record_count += 1
                acted_signal_ids.append(candidate.signal_id)
                self._persist_consumed_signal(
                    candidate=candidate,
                    execution_candidate=result_candidate,
                    trade_date=trade_date,
                    executed_at=executed_at,
                    settings=normalized_settings,
                )

        risk_guard = self._risk_guard_service.evaluate(trade_date=trade_date)
        for candidate in primary_candidates:
            if not risk_guard.sell_allowed:
                result_candidate = self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code=(
                        risk_guard.sell_block_reason_code
                        or "SELL_BLOCKED_BY_RISK_GUARD"
                    ),
                    reason_message=(
                        risk_guard.sell_block_reason_message
                        or "Sell execution blocked by trading risk guard."
                    ),
                )
            else:
                result_candidate = self._evaluate_primary_candidate(
                    candidate=candidate,
                    trade_date=trade_date,
                    settings=normalized_settings,
                    execute_orders=execute_orders,
                )
            processed.append(result_candidate)

            if execute_orders and self._should_consume_signal(result_candidate):
                audit_record_count += 1
                acted_signal_ids.append(candidate.signal_id)
                self._persist_consumed_signal(
                    candidate=candidate,
                    execution_candidate=result_candidate,
                    trade_date=trade_date,
                    executed_at=executed_at,
                    settings=normalized_settings,
                )

        preview_ready_count = sum(
            1
            for item in processed
            if item.outcome == SellSignalExecutionOutcome.PREVIEW_READY
        )
        blocked_count = sum(
            1 for item in processed if item.outcome == SellSignalExecutionOutcome.BLOCKED
        )
        submitted_count = sum(
            1 for item in processed if item.outcome == SellSignalExecutionOutcome.SUBMITTED
        )
        unknown_count = sum(
            1 for item in processed if item.outcome == SellSignalExecutionOutcome.UNKNOWN
        )
        rejected_count = sum(
            1 for item in processed if item.outcome == SellSignalExecutionOutcome.REJECTED
        )
        failed_count = sum(
            1 for item in processed if item.outcome == SellSignalExecutionOutcome.FAILED
        )

        _log.info(
            f"[sell_signal_execution:done] trade_date={trade_date} "
            f"candidate_count={len(processed)} submitted_count={submitted_count} "
            f"unknown_count={unknown_count} blocked_count={blocked_count} "
            f"acted_count={len(acted_signal_ids)}"
        )

        return SellSignalExecutionResult(
            trade_date=trade_date,
            executed_at=executed_at,
            execute_orders=execute_orders,
            pending_signal_count=len(pending_candidates),
            candidate_count=len(processed),
            preview_ready_count=preview_ready_count,
            blocked_count=blocked_count,
            submitted_count=submitted_count,
            unknown_count=unknown_count,
            rejected_count=rejected_count,
            failed_count=failed_count,
            acted_count=len(acted_signal_ids),
            audit_record_count=audit_record_count,
            candidates=tuple(processed),
            acted_signal_ids=tuple(acted_signal_ids),
        )

    def _load_pending_candidates(
        self,
        *,
        trade_date: str,
        signal_limit: int,
    ) -> list[SellTriggerSignalCandidate]:
        rows = self._signal_repo.list_unacted(limit=signal_limit)
        candidates: list[SellTriggerSignalCandidate] = []
        for row in rows:
            if row.strategy_name not in (
                STRATEGY_NAME_SELL_STOP_LOSS,
                STRATEGY_NAME_SELL_TAKE_PROFIT,
                STRATEGY_NAME_SELL_MACD_DECREASE,
            ):
                continue
            if not row.payload or row.payload.get("trade_date") != trade_date:
                continue
            candidates.append(self._to_trigger_candidate(row))
        return candidates

    def _to_trigger_candidate(self, row: SignalRow) -> SellTriggerSignalCandidate:
        payload = row.payload or {}
        name = self._require_payload_text(payload, "name", row.id)
        trade_date = self._require_payload_text(payload, "trade_date", row.id)
        if row.strategy_name == STRATEGY_NAME_SELL_STOP_LOSS:
            priority = 0
        elif row.strategy_name == STRATEGY_NAME_SELL_TAKE_PROFIT:
            priority = 1
        else:
            priority = 2
        return SellTriggerSignalCandidate(
            signal_id=row.id,
            signal_scanned_at=row.scanned_at,
            symbol=row.symbol,
            name=name,
            trade_date=trade_date,
            source_strategy_name=row.strategy_name,
            strategy_priority=priority,
        )

    @staticmethod
    def _split_candidates(
        candidates: list[SellTriggerSignalCandidate],
    ) -> tuple[
        list[SellTriggerSignalCandidate],
        list[tuple[SellTriggerSignalCandidate, SellTriggerSignalCandidate]],
    ]:
        primary_by_symbol: dict[str, SellTriggerSignalCandidate] = {}
        superseded: list[tuple[SellTriggerSignalCandidate, SellTriggerSignalCandidate]] = []
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.symbol,
                item.strategy_priority,
                item.signal_scanned_at,
                item.signal_id,
            ),
        )
        for candidate in ordered:
            winner = primary_by_symbol.get(candidate.symbol)
            if winner is None:
                primary_by_symbol[candidate.symbol] = candidate
                continue
            superseded.append((candidate, winner))

        primary = sorted(
            primary_by_symbol.values(),
            key=lambda item: (
                item.strategy_priority,
                item.signal_scanned_at,
                item.signal_id,
            ),
        )
        return primary, superseded

    def _evaluate_primary_candidate(
        self,
        *,
        candidate: SellTriggerSignalCandidate,
        trade_date: str,
        settings: SellSignalExecutionSettings,
        execute_orders: bool,
    ) -> SellSignalExecutionCandidate:
        now = self._now_fn().astimezone(_KST)
        current_time = now.strftime("%H:%M:%S")

        if now.strftime("%Y-%m-%d") != trade_date:
            return self._build_blocked_candidate(
                candidate=candidate,
                reason_code="TRADE_DATE_MISMATCH",
                reason_message=(
                    "Current KST date does not match trade_date: "
                    f"now={now.strftime('%Y-%m-%d')}, trade_date={trade_date}"
                ),
            )

        if current_time < settings.start_time:
            return self._build_blocked_candidate(
                candidate=candidate,
                reason_code="BEFORE_START_TIME",
                reason_message=(
                    "Sell execution window has not started yet: "
                    f"current_time={current_time}, start_time={settings.start_time}"
                ),
            )

        if current_time >= settings.cutoff_time:
            return self._build_blocked_candidate(
                candidate=candidate,
                reason_code="AFTER_CUTOFF_TIME",
                reason_message=(
                    "Sell execution is blocked after cutoff time: "
                    f"current_time={current_time}, cutoff_time={settings.cutoff_time}"
                ),
            )

        live_position = self._position_repo.get(candidate.symbol)
        if live_position is None or live_position.qty <= 0:
            return self._build_blocked_candidate(
                candidate=candidate,
                reason_code="LIVE_POSITION_MISSING",
                reason_message="No live position exists for this symbol.",
            )

        if live_position.avg_price <= 0:
            raise ServiceError(
                "Live position has invalid avg_price before sell execution: "
                f"symbol={candidate.symbol}, qty={live_position.qty}, "
                f"avg_price={live_position.avg_price}"
            )

        unresolved_sell_exists = any(
            row.symbol == candidate.symbol and row.side == "sell"
            for row in self._order_repo.find_unresolved()
        )
        if unresolved_sell_exists:
            return SellSignalExecutionCandidate(
                signal_id=candidate.signal_id,
                symbol=candidate.symbol,
                name=candidate.name,
                source_strategy_name=candidate.source_strategy_name,
                outcome=SellSignalExecutionOutcome.BLOCKED,
                reason_code="UNRESOLVED_SELL_ORDER_EXISTS",
                reason_message="An unresolved sell order already exists for this symbol.",
                current_price=None,
                position_qty=live_position.qty,
                avg_price=live_position.avg_price,
                client_order_id=None,
                order_error_code=None,
                order_error_message=None,
                acted=False,
            )

        current_price = self._load_current_price(candidate.symbol)

        if not execute_orders:
            return SellSignalExecutionCandidate(
                signal_id=candidate.signal_id,
                symbol=candidate.symbol,
                name=candidate.name,
                source_strategy_name=candidate.source_strategy_name,
                outcome=SellSignalExecutionOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                current_price=current_price,
                position_qty=live_position.qty,
                avg_price=live_position.avg_price,
                client_order_id=None,
                order_error_code=None,
                order_error_message=None,
                acted=False,
            )

        order_result = self._place_order(
            symbol=candidate.symbol,
            qty=live_position.qty,
            strategy_name=candidate.source_strategy_name,
        )
        outcome = SellSignalExecutionOutcome(order_result.outcome.value)
        return SellSignalExecutionCandidate(
            signal_id=candidate.signal_id,
            symbol=candidate.symbol,
            name=candidate.name,
            source_strategy_name=candidate.source_strategy_name,
            outcome=outcome,
            reason_code=order_result.error_code,
            reason_message=order_result.error_message,
            current_price=current_price,
            position_qty=live_position.qty,
            avg_price=live_position.avg_price,
            client_order_id=order_result.client_order_id,
            order_error_code=order_result.error_code,
            order_error_message=order_result.error_message,
            acted=self._is_terminal_outcome(outcome),
        )

    def _persist_consumed_signal(
        self,
        *,
        candidate: SellTriggerSignalCandidate,
        execution_candidate: SellSignalExecutionCandidate,
        trade_date: str,
        executed_at: str,
        settings: SellSignalExecutionSettings,
    ) -> None:
        with transaction(self._conn):
            audit_row = self._signal_repo.record(
                symbol=candidate.symbol,
                strategy_name=STRATEGY_NAME_SELL_EXECUTION_AUDIT,
                scanned_at=executed_at,
                payload={
                    "trade_date": trade_date,
                    "source_signal_id": candidate.signal_id,
                    "source_strategy_name": candidate.source_strategy_name,
                    "symbol": candidate.symbol,
                    "name": candidate.name,
                    "execution_outcome": execution_candidate.outcome.value,
                    "reason_code": execution_candidate.reason_code,
                    "reason_message": execution_candidate.reason_message,
                    "current_price": execution_candidate.current_price,
                    "position_qty": execution_candidate.position_qty,
                    "avg_price": execution_candidate.avg_price,
                    "client_order_id": execution_candidate.client_order_id,
                    "order_error_code": execution_candidate.order_error_code,
                    "order_error_message": execution_candidate.order_error_message,
                    "start_time": settings.start_time,
                    "cutoff_time": settings.cutoff_time,
                },
            )
            self._signal_repo.mark_acted(audit_row.id)
            self._signal_repo.mark_acted(candidate.signal_id)

    def _load_current_price(self, symbol: str) -> int:
        try:
            snapshot = self._broker.get_current_price(symbol)
        except Exception as exc:
            raise ServiceError(
                f"Failed to load current price for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return _require_positive_int("current_price", snapshot.price)

    def _place_order(
        self,
        *,
        symbol: str,
        qty: int,
        strategy_name: str,
    ):
        try:
            return self._order_service.place_order(
                symbol=symbol,
                side="sell",
                qty=qty,
                price=0,
                order_type=OrderType.MARKET,
                strategy_name=strategy_name,
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to place sell order for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _build_blocked_candidate(
        *,
        candidate: SellTriggerSignalCandidate,
        reason_code: str,
        reason_message: str,
    ) -> SellSignalExecutionCandidate:
        return SellSignalExecutionCandidate(
            signal_id=candidate.signal_id,
            symbol=candidate.symbol,
            name=candidate.name,
            source_strategy_name=candidate.source_strategy_name,
            outcome=SellSignalExecutionOutcome.BLOCKED,
            reason_code=reason_code,
            reason_message=reason_message,
            current_price=None,
            position_qty=None,
            avg_price=None,
            client_order_id=None,
            order_error_code=None,
            order_error_message=None,
            acted=False,
        )

    @staticmethod
    def _require_payload_text(payload: dict, field_name: str, signal_id: int) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "Signal payload field is missing or invalid: "
                f"id={signal_id}, field={field_name!r}"
            )
        return value.strip()

    @staticmethod
    def _is_terminal_outcome(outcome: SellSignalExecutionOutcome) -> bool:
        return outcome in (
            SellSignalExecutionOutcome.SUBMITTED,
            SellSignalExecutionOutcome.UNKNOWN,
            SellSignalExecutionOutcome.REJECTED,
            SellSignalExecutionOutcome.FAILED,
        )

    def _should_consume_signal(
        self,
        candidate: SellSignalExecutionCandidate,
    ) -> bool:
        if self._is_terminal_outcome(candidate.outcome):
            return True
        if candidate.outcome != SellSignalExecutionOutcome.BLOCKED:
            return False
        return candidate.reason_code in {
            "SUPERSEDED_BY_HIGHER_PRIORITY",
            "LIVE_POSITION_MISSING",
            "UNRESOLVED_SELL_ORDER_EXISTS",
        }
