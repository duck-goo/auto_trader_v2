"""Consume buy trigger signals and optionally place market buy orders."""

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
from services.timing1_intraday_trigger_service import (
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER,
)
from services.timing2_intraday_trigger_service import (
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
)
from services.timing2_30s_trigger_service import (
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
)
from storage.db import transaction
from storage.repositories import (
    OrderRepository,
    PositionRepository,
    SignalRepository,
    SignalRow,
)


STRATEGY_NAME_BUY_EXECUTION_AUDIT = "buy_execution_attempt"
BUY_TRIGGER_STRATEGY_PRIORITIES = {
    STRATEGY_NAME_TIMING1_INTRADAY_TRIGGER: 0,
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER: 1,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER: 1,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER: 1,
}

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


class BuySignalExecutionOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BuySignalExecutionSettings:
    per_order_budget: int
    max_holdings: int
    max_daily_order_count: int | None = None
    max_daily_loss: int | None = None
    start_time: str = "09:00:00"
    cutoff_time: str = "12:00:00"

    def validated(self) -> "BuySignalExecutionSettings":
        per_order_budget = _require_positive_int(
            "per_order_budget",
            self.per_order_budget,
        )
        max_holdings = _require_positive_int("max_holdings", self.max_holdings)
        max_daily_order_count = self.max_daily_order_count
        if max_daily_order_count is not None:
            max_daily_order_count = _require_positive_int(
                "max_daily_order_count",
                max_daily_order_count,
            )
        max_daily_loss = self.max_daily_loss
        if max_daily_loss is not None:
            max_daily_loss = _require_positive_int(
                "max_daily_loss",
                max_daily_loss,
            )
        start_time = _require_time_text("start_time", self.start_time)
        cutoff_time = _require_time_text("cutoff_time", self.cutoff_time)
        if start_time >= cutoff_time:
            raise ValueError(
                "cutoff_time must be later than start_time: "
                f"start={start_time}, cutoff={cutoff_time}"
            )
        return BuySignalExecutionSettings(
            per_order_budget=per_order_budget,
            max_holdings=max_holdings,
            max_daily_order_count=max_daily_order_count,
            max_daily_loss=max_daily_loss,
            start_time=start_time,
            cutoff_time=cutoff_time,
        )


@dataclass(frozen=True)
class BuyTriggerSignalCandidate:
    signal_id: int
    signal_scanned_at: str
    symbol: str
    name: str
    market: str
    trade_date: str
    source_strategy_name: str
    strategy_priority: int


@dataclass(frozen=True)
class BuySignalExecutionCandidate:
    signal_id: int
    symbol: str
    name: str
    market: str
    source_strategy_name: str
    outcome: BuySignalExecutionOutcome
    reason_code: str | None
    reason_message: str | None
    current_price: int | None
    planned_qty: int | None
    remaining_cash_before: int | None
    remaining_cash_after: int | None
    client_order_id: str | None
    order_error_code: str | None
    order_error_message: str | None
    acted: bool


@dataclass(frozen=True)
class BuySignalExecutionResult:
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
    candidates: tuple[BuySignalExecutionCandidate, ...]
    acted_signal_ids: tuple[int, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class BuySignalExecutionService:
    """Execute buy trigger signals with conservative duplicate/risk guards."""

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
        settings: BuySignalExecutionSettings,
        signal_limit: int = 200,
        execute_orders: bool = False,
    ) -> BuySignalExecutionResult:
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

        processed: list[BuySignalExecutionCandidate] = []
        acted_signal_ids: list[int] = []
        audit_record_count = 0

        _log.info(
            f"[buy_signal_execution:start] trade_date={trade_date} "
            f"pending_signal_count={len(pending_candidates)} "
            f"execute_orders={execute_orders}"
        )

        for candidate, winner in superseded_candidates:
            result_candidate = self._build_blocked_candidate(
                candidate=candidate,
                reason_code="SUPERSEDED_BY_HIGHER_PRIORITY",
                reason_message=(
                    "Another higher-priority signal for the same symbol "
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

        if primary_candidates:
            risk_guard = self._risk_guard_service.evaluate(
                trade_date=trade_date,
                max_daily_order_count=normalized_settings.max_daily_order_count,
                max_daily_loss=normalized_settings.max_daily_loss,
            )
            if not risk_guard.buy_allowed:
                for candidate in primary_candidates:
                    result_candidate = self._build_blocked_candidate(
                        candidate=candidate,
                        reason_code=(
                            risk_guard.buy_block_reason_code
                            or "BUY_BLOCKED_BY_RISK_GUARD"
                        ),
                        reason_message=(
                            risk_guard.buy_block_reason_message
                            or "Buy execution blocked by trading risk guard."
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
            else:
                balance = self._load_balance()
                live_symbols = {
                    row.symbol
                    for row in self._position_repo.list_all()
                    if row.qty > 0
                }
                unresolved_buy_symbols = {
                    row.symbol
                    for row in self._order_repo.find_unresolved()
                    if row.side == "buy"
                }
                active_symbols = set(live_symbols) | set(unresolved_buy_symbols)
                remaining_cash = balance.available_cash
                simulated_day_order_count = risk_guard.today_order_count

                for candidate in primary_candidates:
                    result_candidate, reserve_cash, reserve_order_slot = (
                        self._evaluate_primary_candidate(
                            candidate=candidate,
                            trade_date=trade_date,
                            settings=normalized_settings,
                            execute_orders=execute_orders,
                            live_symbols=live_symbols,
                            unresolved_buy_symbols=unresolved_buy_symbols,
                            active_symbols=active_symbols,
                            remaining_cash=remaining_cash,
                            current_day_order_count=simulated_day_order_count,
                        )
                    )
                    processed.append(result_candidate)

                    if reserve_cash > 0:
                        remaining_cash = max(0, remaining_cash - reserve_cash)
                        active_symbols.add(candidate.symbol)
                        unresolved_buy_symbols.add(candidate.symbol)
                    if reserve_order_slot:
                        simulated_day_order_count += 1

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

        preview_ready_count = sum(
            1
            for item in processed
            if item.outcome == BuySignalExecutionOutcome.PREVIEW_READY
        )
        blocked_count = sum(
            1 for item in processed if item.outcome == BuySignalExecutionOutcome.BLOCKED
        )
        submitted_count = sum(
            1
            for item in processed
            if item.outcome == BuySignalExecutionOutcome.SUBMITTED
        )
        unknown_count = sum(
            1 for item in processed if item.outcome == BuySignalExecutionOutcome.UNKNOWN
        )
        rejected_count = sum(
            1
            for item in processed
            if item.outcome == BuySignalExecutionOutcome.REJECTED
        )
        failed_count = sum(
            1 for item in processed if item.outcome == BuySignalExecutionOutcome.FAILED
        )

        _log.info(
            f"[buy_signal_execution:done] trade_date={trade_date} "
            f"candidate_count={len(processed)} submitted_count={submitted_count} "
            f"unknown_count={unknown_count} blocked_count={blocked_count} "
            f"acted_count={len(acted_signal_ids)}"
        )

        return BuySignalExecutionResult(
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
    ) -> list[BuyTriggerSignalCandidate]:
        rows = self._signal_repo.list_unacted(limit=signal_limit)
        candidates: list[BuyTriggerSignalCandidate] = []
        for row in rows:
            if row.strategy_name not in BUY_TRIGGER_STRATEGY_PRIORITIES:
                continue
            if not row.payload or row.payload.get("trade_date") != trade_date:
                continue
            candidates.append(self._to_trigger_candidate(row))
        return candidates

    def _to_trigger_candidate(self, row: SignalRow) -> BuyTriggerSignalCandidate:
        payload = row.payload or {}
        name = self._require_payload_text(payload, "name", row.id)
        market = self._require_payload_text(payload, "market", row.id)
        trade_date = self._require_payload_text(payload, "trade_date", row.id)
        priority = BUY_TRIGGER_STRATEGY_PRIORITIES[row.strategy_name]
        return BuyTriggerSignalCandidate(
            signal_id=row.id,
            signal_scanned_at=row.scanned_at,
            symbol=row.symbol,
            name=name,
            market=market,
            trade_date=trade_date,
            source_strategy_name=row.strategy_name,
            strategy_priority=priority,
        )

    @staticmethod
    def _split_candidates(
        candidates: list[BuyTriggerSignalCandidate],
    ) -> tuple[
        list[BuyTriggerSignalCandidate],
        list[tuple[BuyTriggerSignalCandidate, BuyTriggerSignalCandidate]],
    ]:
        primary_by_symbol: dict[str, BuyTriggerSignalCandidate] = {}
        superseded: list[tuple[BuyTriggerSignalCandidate, BuyTriggerSignalCandidate]] = []
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
        candidate: BuyTriggerSignalCandidate,
        trade_date: str,
        settings: BuySignalExecutionSettings,
        execute_orders: bool,
        live_symbols: set[str],
        unresolved_buy_symbols: set[str],
        active_symbols: set[str],
        remaining_cash: int,
        current_day_order_count: int,
    ) -> tuple[BuySignalExecutionCandidate, int, bool]:
        now = self._now_fn().astimezone(_KST)
        current_time = now.strftime("%H:%M:%S")

        if now.strftime("%Y-%m-%d") != trade_date:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="TRADE_DATE_MISMATCH",
                    reason_message=(
                        "Current KST date does not match trade_date: "
                        f"now={now.strftime('%Y-%m-%d')}, trade_date={trade_date}"
                    ),
                ),
                0,
                False,
            )

        if current_time < settings.start_time:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="BEFORE_START_TIME",
                    reason_message=(
                        "Order execution window has not started yet: "
                        f"current_time={current_time}, start_time={settings.start_time}"
                    ),
                ),
                0,
                False,
            )

        if current_time >= settings.cutoff_time:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="AFTER_CUTOFF_TIME",
                    reason_message=(
                        "New buy orders are blocked after cutoff time: "
                        f"current_time={current_time}, cutoff_time={settings.cutoff_time}"
                    ),
                ),
                0,
                False,
            )

        if candidate.symbol in live_symbols:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="LIVE_POSITION_EXISTS",
                    reason_message="A live position already exists for this symbol.",
                ),
                0,
                False,
            )

        if candidate.symbol in unresolved_buy_symbols:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="UNRESOLVED_BUY_ORDER_EXISTS",
                    reason_message="An unresolved buy order already exists for this symbol.",
                ),
                0,
                False,
            )

        if len(active_symbols) >= settings.max_holdings:
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="MAX_HOLDINGS_REACHED",
                    reason_message=(
                        "Max holdings limit reached before placing this order: "
                        f"max_holdings={settings.max_holdings}"
                    ),
                ),
                0,
                False,
            )

        if (
            settings.max_daily_order_count is not None
            and current_day_order_count >= settings.max_daily_order_count
        ):
            return (
                self._build_blocked_candidate(
                    candidate=candidate,
                    reason_code="MAX_DAILY_ORDER_COUNT_REACHED",
                    reason_message=(
                        "Daily order count limit reached for new buy orders: "
                        f"today_order_count={current_day_order_count}, "
                        f"max_daily_order_count={settings.max_daily_order_count}"
                    ),
                ),
                0,
                False,
            )

        current_price = self._load_current_price(candidate.symbol)
        budget_to_use = min(settings.per_order_budget, remaining_cash)
        planned_qty = budget_to_use // current_price
        if planned_qty <= 0:
            return (
                BuySignalExecutionCandidate(
                    signal_id=candidate.signal_id,
                    symbol=candidate.symbol,
                    name=candidate.name,
                    market=candidate.market,
                    source_strategy_name=candidate.source_strategy_name,
                    outcome=BuySignalExecutionOutcome.BLOCKED,
                    reason_code="INSUFFICIENT_AVAILABLE_CASH",
                    reason_message=(
                        "Available cash is lower than one share price: "
                        f"available_cash={remaining_cash}, current_price={current_price}"
                    ),
                    current_price=current_price,
                    planned_qty=0,
                    remaining_cash_before=remaining_cash,
                    remaining_cash_after=remaining_cash,
                    client_order_id=None,
                    order_error_code=None,
                    order_error_message=None,
                    acted=execute_orders,
                ),
                0,
                False,
            )

        reserved_cash = planned_qty * current_price
        remaining_cash_after = max(0, remaining_cash - reserved_cash)

        if not execute_orders:
            return (
                BuySignalExecutionCandidate(
                    signal_id=candidate.signal_id,
                    symbol=candidate.symbol,
                    name=candidate.name,
                    market=candidate.market,
                    source_strategy_name=candidate.source_strategy_name,
                    outcome=BuySignalExecutionOutcome.PREVIEW_READY,
                    reason_code=None,
                    reason_message=None,
                    current_price=current_price,
                    planned_qty=planned_qty,
                    remaining_cash_before=remaining_cash,
                    remaining_cash_after=remaining_cash_after,
                    client_order_id=None,
                    order_error_code=None,
                    order_error_message=None,
                    acted=False,
                ),
                0,
                True,
            )

        order_result = self._place_order(
            symbol=candidate.symbol,
            qty=planned_qty,
            strategy_name=candidate.source_strategy_name,
        )
        outcome = BuySignalExecutionOutcome(order_result.outcome.value)
        reserve_after_order = 0
        if outcome in (
            BuySignalExecutionOutcome.SUBMITTED,
            BuySignalExecutionOutcome.UNKNOWN,
        ):
            reserve_after_order = reserved_cash

        candidate_result = BuySignalExecutionCandidate(
            signal_id=candidate.signal_id,
            symbol=candidate.symbol,
            name=candidate.name,
            market=candidate.market,
            source_strategy_name=candidate.source_strategy_name,
            outcome=outcome,
            reason_code=order_result.error_code,
            reason_message=order_result.error_message,
            current_price=current_price,
            planned_qty=planned_qty,
            remaining_cash_before=remaining_cash,
            remaining_cash_after=remaining_cash_after,
            client_order_id=order_result.client_order_id,
            order_error_code=order_result.error_code,
            order_error_message=order_result.error_message,
            acted=True,
        )
        reserve_order_slot = candidate_result.outcome in {
            BuySignalExecutionOutcome.SUBMITTED,
            BuySignalExecutionOutcome.UNKNOWN,
        }
        return (
            candidate_result,
            reserve_after_order,
            reserve_order_slot,
        )

    def _persist_consumed_signal(
        self,
        *,
        candidate: BuyTriggerSignalCandidate,
        execution_candidate: BuySignalExecutionCandidate,
        trade_date: str,
        executed_at: str,
        settings: BuySignalExecutionSettings,
    ) -> None:
        with transaction(self._conn):
            audit_row = self._signal_repo.record(
                symbol=candidate.symbol,
                strategy_name=STRATEGY_NAME_BUY_EXECUTION_AUDIT,
                scanned_at=executed_at,
                payload={
                    "trade_date": trade_date,
                    "source_signal_id": candidate.signal_id,
                    "source_strategy_name": candidate.source_strategy_name,
                    "symbol": candidate.symbol,
                    "name": candidate.name,
                    "market": candidate.market,
                    "execution_outcome": execution_candidate.outcome.value,
                    "reason_code": execution_candidate.reason_code,
                    "reason_message": execution_candidate.reason_message,
                    "current_price": execution_candidate.current_price,
                    "planned_qty": execution_candidate.planned_qty,
                    "remaining_cash_before": execution_candidate.remaining_cash_before,
                    "remaining_cash_after": execution_candidate.remaining_cash_after,
                    "client_order_id": execution_candidate.client_order_id,
                    "order_error_code": execution_candidate.order_error_code,
                    "order_error_message": execution_candidate.order_error_message,
                    "per_order_budget": settings.per_order_budget,
                    "max_holdings": settings.max_holdings,
                    "max_daily_order_count": settings.max_daily_order_count,
                    "max_daily_loss": settings.max_daily_loss,
                    "start_time": settings.start_time,
                    "cutoff_time": settings.cutoff_time,
                },
            )
            self._signal_repo.mark_acted(audit_row.id)
            self._signal_repo.mark_acted(candidate.signal_id)

    def _load_balance(self):
        try:
            return self._broker.get_balance()
        except Exception as exc:
            raise ServiceError(
                "Failed to load broker balance before buy execution: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

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
                side="buy",
                qty=qty,
                price=0,
                order_type=OrderType.MARKET,
                strategy_name=strategy_name,
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to place buy order for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _build_blocked_candidate(
        *,
        candidate: BuyTriggerSignalCandidate,
        reason_code: str,
        reason_message: str,
    ) -> BuySignalExecutionCandidate:
        return BuySignalExecutionCandidate(
            signal_id=candidate.signal_id,
            symbol=candidate.symbol,
            name=candidate.name,
            market=candidate.market,
            source_strategy_name=candidate.source_strategy_name,
            outcome=BuySignalExecutionOutcome.BLOCKED,
            reason_code=reason_code,
            reason_message=reason_message,
            current_price=None,
            planned_qty=None,
            remaining_cash_before=None,
            remaining_cash_after=None,
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
