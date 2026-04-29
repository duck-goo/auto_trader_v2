"""Finalize recovery-required orders only when local execution rows are sufficient."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from logger import get_logger
from services.errors import ServiceError
from services.sell_signal_execution_service import STRATEGY_NAME_SELL_EXECUTION_AUDIT
from services.timing2_30s_trigger_service import (
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
)
from services.timing2_intraday_trigger_service import (
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
)
from services.timing2_lot_exit_scan_service import (
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
)
from services.unresolved_order_sync_service import (
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
    UnresolvedOrderSyncService,
)
from storage.db import transaction
from storage.repositories import (
    DbOrderStatus,
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    SignalRepository,
)


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")
_TIMING2_BUY_STRATEGIES = {
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
}
_LOT_LEVEL_SELL_STRATEGIES = {
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
}
_SELL_AUDIT_SEARCH_LIMIT = 5000


def _default_now() -> datetime:
    return datetime.now(_KST)


def _normalize_status_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServiceError(f"status text must be a string or None: {value!r}")
    normalized = value.strip().upper()
    return normalized or None


@dataclass(frozen=True)
class LocalExecutionSummary:
    execution_count: int
    filled_qty: int
    avg_fill_price: int


@dataclass(frozen=True)
class _LotSellAuditContext:
    lot_id: int
    remaining_qty_before: int
    realized_sell_qty_before: int
    order_qty: int


class ExecutionRecoveryFinalizeAction(str, enum.Enum):
    NONE = "NONE"
    FINALIZE_FROM_LOCAL_EXECUTIONS = "FINALIZE_FROM_LOCAL_EXECUTIONS"


class ExecutionRecoveryFinalizeOutcome(str, enum.Enum):
    SKIPPED = "SKIPPED"
    PREVIEW_READY = "PREVIEW_READY"
    RECOVERED = "RECOVERED"
    MANUAL_RECOVERY_REQUIRED = "MANUAL_RECOVERY_REQUIRED"


@dataclass(frozen=True)
class ExecutionRecoveryFinalizeCandidate:
    client_order_id: str
    symbol: str
    status_before: str
    status_after: str | None
    broker_status: str | None
    broker_filled_qty: int | None
    local_execution_count: int
    local_filled_qty: int
    local_avg_fill_price: int
    action: ExecutionRecoveryFinalizeAction
    outcome: ExecutionRecoveryFinalizeOutcome
    reason_code: str | None
    reason_message: str | None
    acted: bool


@dataclass(frozen=True)
class ExecutionRecoveryFinalizeResult:
    trade_date: str
    scanned_at: str
    execute_recovery: bool
    sync_result: UnresolvedOrderSyncResult
    candidate_count: int
    preview_ready_count: int
    recovered_count: int
    manual_recovery_required_count: int
    skipped_count: int
    acted_count: int
    candidates: tuple[ExecutionRecoveryFinalizeCandidate, ...]


class ExecutionRecoveryFinalizeService:
    """
    Finalize order states from already-recorded execution rows only.

    Safety assumption:
        Local execution rows are expected to come from the normal ledger flow,
        which inserts executions and applies positions in the same transaction.
        If a future manual recovery tool inserts execution rows separately,
        this service must be revisited before execute_recovery=True is used.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        execution_repo: ExecutionRepository,
        sync_service: UnresolvedOrderSyncService,
        entry_lot_repo: EntryLotRepository | None = None,
        signal_repo: SignalRepository | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._order_repo = order_repo
        self._execution_repo = execution_repo
        self._sync_service = sync_service
        self._entry_lot_repo = entry_lot_repo
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now

    def finalize_recovery(
        self,
        *,
        trade_date: str,
        execute_recovery: bool = False,
        sync_result: UnresolvedOrderSyncResult | None = None,
    ) -> ExecutionRecoveryFinalizeResult:
        actual_sync_result = sync_result or self._sync_service.sync_unresolved_orders(
            trade_date=trade_date,
            execute_sync=False,
        )
        scanned_at = self._now_fn().astimezone(_KST).isoformat()
        candidates: list[ExecutionRecoveryFinalizeCandidate] = []

        _log.info(
            f"[execution_recovery_finalize:start] trade_date={trade_date} "
            f"execute_recovery={execute_recovery} "
            f"recovery_required_count="
            f"{actual_sync_result.execution_recovery_required_count}"
        )

        for item in actual_sync_result.candidates:
            if item.outcome != UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED:
                continue
            candidates.append(
                self._evaluate_candidate(
                    sync_candidate=item,
                    scanned_at=scanned_at,
                    execute_recovery=execute_recovery,
                )
            )

        preview_ready_count = sum(
            1
            for item in candidates
            if item.outcome == ExecutionRecoveryFinalizeOutcome.PREVIEW_READY
        )
        recovered_count = sum(
            1
            for item in candidates
            if item.outcome == ExecutionRecoveryFinalizeOutcome.RECOVERED
        )
        manual_recovery_required_count = sum(
            1
            for item in candidates
            if item.outcome
            == ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED
        )
        skipped_count = sum(
            1
            for item in candidates
            if item.outcome == ExecutionRecoveryFinalizeOutcome.SKIPPED
        )
        acted_count = sum(1 for item in candidates if item.acted)

        _log.info(
            f"[execution_recovery_finalize:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} recovered_count={recovered_count} "
            f"manual_recovery_required_count={manual_recovery_required_count}"
        )

        return ExecutionRecoveryFinalizeResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            execute_recovery=execute_recovery,
            sync_result=actual_sync_result,
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            recovered_count=recovered_count,
            manual_recovery_required_count=manual_recovery_required_count,
            skipped_count=skipped_count,
            acted_count=acted_count,
            candidates=tuple(candidates),
        )

    def _evaluate_candidate(
        self,
        *,
        sync_candidate,
        scanned_at: str,
        execute_recovery: bool,
    ) -> ExecutionRecoveryFinalizeCandidate:
        order_row = self._order_repo.get_by_client_order_id(sync_candidate.client_order_id)
        if order_row is None:
            raise ServiceError(
                "Recovery candidate order row not found: "
                f"client_order_id={sync_candidate.client_order_id}"
            )

        local_summary = self._summarize_local_executions(order_row.id)
        target_status = self._resolve_target_status(
            order_row=order_row,
            sync_candidate=sync_candidate,
            local_summary=local_summary,
        )
        if target_status is None:
            return self._build_manual_required(
                order_row=order_row,
                sync_candidate=sync_candidate,
                local_summary=local_summary,
                reason_code=self._resolve_manual_reason_code(
                    order_row=order_row,
                    sync_candidate=sync_candidate,
                    local_summary=local_summary,
                ),
                reason_message=self._resolve_manual_reason_message(
                    order_row=order_row,
                    sync_candidate=sync_candidate,
                    local_summary=local_summary,
                ),
            )

        timing2_lot_block = self._resolve_timing2_buy_lot_block(
            order_row=order_row,
            local_summary=local_summary,
        )
        if timing2_lot_block is not None:
            reason_code, reason_message = timing2_lot_block
            return self._build_manual_required(
                order_row=order_row,
                sync_candidate=sync_candidate,
                local_summary=local_summary,
                reason_code=reason_code,
                reason_message=reason_message,
            )

        timing2_sell_lot_block = self._resolve_timing2_lot_sell_block(
            order_row=order_row,
            local_summary=local_summary,
        )
        if timing2_sell_lot_block is not None:
            reason_code, reason_message = timing2_sell_lot_block
            return self._build_manual_required(
                order_row=order_row,
                sync_candidate=sync_candidate,
                local_summary=local_summary,
                reason_code=reason_code,
                reason_message=reason_message,
            )

        if not execute_recovery:
            return ExecutionRecoveryFinalizeCandidate(
                client_order_id=order_row.client_order_id,
                symbol=order_row.symbol,
                status_before=order_row.status.value,
                status_after=target_status.value,
                broker_status=sync_candidate.broker_status,
                broker_filled_qty=sync_candidate.broker_filled_qty,
                local_execution_count=local_summary.execution_count,
                local_filled_qty=local_summary.filled_qty,
                local_avg_fill_price=local_summary.avg_fill_price,
                action=ExecutionRecoveryFinalizeAction.FINALIZE_FROM_LOCAL_EXECUTIONS,
                outcome=ExecutionRecoveryFinalizeOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                acted=False,
            )

        updated_row = self._apply_recovery(
            order_row=order_row,
            target_status=target_status,
            scanned_at=scanned_at,
        )
        return ExecutionRecoveryFinalizeCandidate(
            client_order_id=updated_row.client_order_id,
            symbol=updated_row.symbol,
            status_before=order_row.status.value,
            status_after=updated_row.status.value,
            broker_status=sync_candidate.broker_status,
            broker_filled_qty=sync_candidate.broker_filled_qty,
            local_execution_count=local_summary.execution_count,
            local_filled_qty=local_summary.filled_qty,
            local_avg_fill_price=local_summary.avg_fill_price,
            action=ExecutionRecoveryFinalizeAction.FINALIZE_FROM_LOCAL_EXECUTIONS,
            outcome=ExecutionRecoveryFinalizeOutcome.RECOVERED,
            reason_code=None,
            reason_message=None,
            acted=True,
        )

    def _apply_recovery(
        self,
        *,
        order_row,
        target_status: DbOrderStatus,
        scanned_at: str,
    ):
        with transaction(self._conn):
            if target_status == DbOrderStatus.CANCELLED:
                updated_row = self._order_repo.mark_cancelled(
                    client_order_id=order_row.client_order_id,
                    closed_at=scanned_at,
                )
            elif target_status in (DbOrderStatus.PARTIAL, DbOrderStatus.FILLED):
                updated_row = self._order_repo.sync_execution_summary(
                    client_order_id=order_row.client_order_id,
                    closed_at=scanned_at
                    if target_status == DbOrderStatus.FILLED
                    else None,
                )
            else:
                raise ServiceError(
                    f"Unsupported recovery target_status: {target_status.value}"
                )

        if updated_row.status != target_status:
            raise ServiceError(
                "Recovered order status mismatch: "
                f"client_order_id={order_row.client_order_id}, "
                f"expected={target_status.value}, actual={updated_row.status.value}"
            )
        return updated_row

    def _resolve_target_status(self, *, order_row, sync_candidate, local_summary) -> DbOrderStatus | None:
        broker_status = _normalize_status_text(sync_candidate.broker_status)
        broker_filled_qty = sync_candidate.broker_filled_qty

        if broker_status is None:
            return None
        if broker_filled_qty is None:
            return None
        if local_summary.execution_count <= 0:
            return None
        if local_summary.filled_qty != broker_filled_qty:
            return None

        if broker_status == DbOrderStatus.FILLED.value:
            if local_summary.filled_qty != order_row.qty:
                return None
            return DbOrderStatus.FILLED

        if broker_status == DbOrderStatus.PARTIAL.value:
            if local_summary.filled_qty <= 0 or local_summary.filled_qty >= order_row.qty:
                return None
            return DbOrderStatus.PARTIAL

        if broker_status == DbOrderStatus.CANCELLED.value:
            if local_summary.filled_qty >= order_row.qty:
                return None
            return DbOrderStatus.CANCELLED

        return None

    def _resolve_timing2_buy_lot_block(
        self,
        *,
        order_row,
        local_summary: LocalExecutionSummary,
    ) -> tuple[str, str] | None:
        if not self._is_timing2_buy_order(order_row):
            return None

        if self._entry_lot_repo is None:
            return (
                "ENTRY_LOT_REPOSITORY_MISSING",
                (
                    "Timing2 buy recovery requires EntryLotRepository so the "
                    "matching entry lot can be verified before finalizing the order."
                ),
            )

        lot = self._entry_lot_repo.get_by_entry_order_id(order_row.id)
        if lot is None:
            return (
                "TIMING2_ENTRY_LOT_MISSING",
                (
                    "Timing2 buy recovery found local executions but no matching "
                    f"entry lot: entry_order_id={order_row.id}"
                ),
            )
        if lot.symbol != order_row.symbol:
            return (
                "TIMING2_ENTRY_LOT_SYMBOL_MISMATCH",
                (
                    "Timing2 entry lot symbol does not match the order: "
                    f"order_symbol={order_row.symbol}, lot_symbol={lot.symbol}, "
                    f"lot_id={lot.id}"
                ),
            )
        if lot.total_buy_qty != local_summary.filled_qty:
            return (
                "TIMING2_ENTRY_LOT_QTY_MISMATCH",
                (
                    "Timing2 entry lot quantity does not match local executions: "
                    f"lot_total_buy_qty={lot.total_buy_qty}, "
                    f"local_filled_qty={local_summary.filled_qty}, lot_id={lot.id}"
                ),
            )
        if lot.avg_buy_price != local_summary.avg_fill_price:
            return (
                "TIMING2_ENTRY_LOT_AVG_PRICE_MISMATCH",
                (
                    "Timing2 entry lot average price does not match local executions: "
                    f"lot_avg_buy_price={lot.avg_buy_price}, "
                    f"local_avg_fill_price={local_summary.avg_fill_price}, "
                    f"lot_id={lot.id}"
                ),
            )
        return None

    def _resolve_timing2_lot_sell_block(
        self,
        *,
        order_row,
        local_summary: LocalExecutionSummary,
    ) -> tuple[str, str] | None:
        if not self._is_timing2_lot_sell_order(order_row):
            return None

        if self._entry_lot_repo is None:
            return (
                "ENTRY_LOT_REPOSITORY_MISSING",
                (
                    "Timing2 lot sell recovery requires EntryLotRepository so "
                    "the source lot can be verified before finalizing the order."
                ),
            )
        if self._signal_repo is None:
            return (
                "SIGNAL_REPOSITORY_MISSING",
                (
                    "Timing2 lot sell recovery requires SignalRepository so the "
                    "sell execution audit can be verified before finalizing the order."
                ),
            )

        audit_result = self._resolve_lot_sell_audit_context(order_row)
        if isinstance(audit_result, tuple):
            return audit_result
        audit = audit_result

        if local_summary.filled_qty > audit.order_qty:
            return (
                "TIMING2_SELL_FILLED_QTY_EXCEEDS_ORDER_QTY",
                (
                    "Local sell execution quantity exceeds the audited sell order "
                    f"quantity: local_filled_qty={local_summary.filled_qty}, "
                    f"audited_order_qty={audit.order_qty}, "
                    f"client_order_id={order_row.client_order_id}"
                ),
            )
        if local_summary.filled_qty > audit.remaining_qty_before:
            return (
                "TIMING2_SELL_FILLED_QTY_EXCEEDS_LOT_BEFORE",
                (
                    "Local sell execution quantity exceeds the lot quantity before "
                    f"the sell order: local_filled_qty={local_summary.filled_qty}, "
                    f"lot_remaining_before={audit.remaining_qty_before}, "
                    f"lot_id={audit.lot_id}"
                ),
            )

        lot = self._entry_lot_repo.get(audit.lot_id)
        if lot is None:
            return (
                "TIMING2_SELL_ENTRY_LOT_MISSING",
                f"Timing2 sell recovery found no source entry lot: lot_id={audit.lot_id}",
            )
        if lot.symbol != order_row.symbol:
            return (
                "TIMING2_SELL_ENTRY_LOT_SYMBOL_MISMATCH",
                (
                    "Timing2 sell entry lot symbol does not match the order: "
                    f"order_symbol={order_row.symbol}, lot_symbol={lot.symbol}, "
                    f"lot_id={lot.id}"
                ),
            )

        expected_remaining_qty = audit.remaining_qty_before - local_summary.filled_qty
        if lot.remaining_qty != expected_remaining_qty:
            return (
                "TIMING2_SELL_ENTRY_LOT_REMAINING_MISMATCH",
                (
                    "Timing2 sell entry lot remaining quantity does not match "
                    "local executions: "
                    f"expected_remaining_qty={expected_remaining_qty}, "
                    f"actual_remaining_qty={lot.remaining_qty}, lot_id={lot.id}"
                ),
            )

        expected_realized_sell_qty = (
            audit.realized_sell_qty_before + local_summary.filled_qty
        )
        if lot.realized_sell_qty != expected_realized_sell_qty:
            return (
                "TIMING2_SELL_ENTRY_LOT_REALIZED_QTY_MISMATCH",
                (
                    "Timing2 sell entry lot realized sell quantity does not match "
                    "local executions: "
                    f"expected_realized_sell_qty={expected_realized_sell_qty}, "
                    f"actual_realized_sell_qty={lot.realized_sell_qty}, lot_id={lot.id}"
                ),
            )

        return None

    def _resolve_lot_sell_audit_context(
        self,
        order_row,
    ) -> _LotSellAuditContext | tuple[str, str]:
        if self._signal_repo is None:
            return (
                "SIGNAL_REPOSITORY_MISSING",
                "SignalRepository is required to resolve Timing2 sell audit rows.",
            )

        matches = []
        rows = self._signal_repo.list_by_strategy(
            STRATEGY_NAME_SELL_EXECUTION_AUDIT,
            limit=_SELL_AUDIT_SEARCH_LIMIT,
        )
        for row in rows:
            payload = row.payload or {}
            if payload.get("client_order_id") == order_row.client_order_id:
                matches.append(row)

        if not matches:
            return (
                "LOT_SELL_AUDIT_NOT_FOUND",
                (
                    "Timing2 lot sell recovery found local executions but no "
                    "matching sell execution audit row: "
                    f"client_order_id={order_row.client_order_id}"
                ),
            )
        if len(matches) > 1:
            return (
                "LOT_SELL_AUDIT_AMBIGUOUS",
                (
                    "Timing2 lot sell recovery found multiple sell execution "
                    "audit rows for the same order: "
                    f"client_order_id={order_row.client_order_id}, "
                    f"count={len(matches)}"
                ),
            )

        payload = matches[0].payload or {}
        try:
            lot_id = self._read_positive_int_payload(
                payload,
                "source_lot_id",
                order_row.client_order_id,
            )
            remaining_qty_before = self._read_non_negative_int_payload(
                payload,
                "source_lot_remaining_qty_before",
                order_row.client_order_id,
            )
            realized_sell_qty_before = self._read_non_negative_int_payload(
                payload,
                "source_lot_realized_sell_qty_before",
                order_row.client_order_id,
            )
            order_qty = self._read_positive_int_payload(
                payload,
                "order_qty",
                order_row.client_order_id,
            )
        except ServiceError as exc:
            return ("LOT_SELL_AUDIT_INVALID", str(exc))

        return _LotSellAuditContext(
            lot_id=lot_id,
            remaining_qty_before=remaining_qty_before,
            realized_sell_qty_before=realized_sell_qty_before,
            order_qty=order_qty,
        )

    @staticmethod
    def _is_timing2_buy_order(order_row) -> bool:
        return (
            order_row.side == "buy"
            and order_row.strategy_name in _TIMING2_BUY_STRATEGIES
        )

    @staticmethod
    def _is_timing2_lot_sell_order(order_row) -> bool:
        return (
            order_row.side == "sell"
            and order_row.strategy_name in _LOT_LEVEL_SELL_STRATEGIES
        )

    @staticmethod
    def _read_positive_int_payload(
        payload: dict,
        field_name: str,
        client_order_id: str,
    ) -> int:
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ServiceError(
                "Timing2 sell audit payload field is missing or invalid: "
                f"client_order_id={client_order_id}, field={field_name!r}, "
                f"value={value!r}"
            )
        return value

    @staticmethod
    def _read_non_negative_int_payload(
        payload: dict,
        field_name: str,
        client_order_id: str,
    ) -> int:
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ServiceError(
                "Timing2 sell audit payload field is missing or invalid: "
                f"client_order_id={client_order_id}, field={field_name!r}, "
                f"value={value!r}"
            )
        return value

    @staticmethod
    def _resolve_manual_reason_code(*, order_row, sync_candidate, local_summary) -> str:
        broker_status = _normalize_status_text(sync_candidate.broker_status)
        if broker_status is None:
            return "BROKER_STATUS_MISSING"
        if sync_candidate.broker_filled_qty is None:
            return "BROKER_FILLED_QTY_MISSING"
        if local_summary.execution_count <= 0:
            return "LOCAL_EXECUTIONS_MISSING"
        if local_summary.filled_qty != sync_candidate.broker_filled_qty:
            return "LOCAL_BROKER_FILLED_QTY_MISMATCH"
        if broker_status == DbOrderStatus.FILLED.value:
            return "FILLED_QTY_DOES_NOT_MATCH_ORDER_QTY"
        if broker_status == DbOrderStatus.PARTIAL.value:
            return "PARTIAL_QTY_IS_NOT_STRICTLY_BETWEEN_0_AND_ORDER_QTY"
        if broker_status == DbOrderStatus.CANCELLED.value:
            return "CANCELLED_QTY_MUST_BE_BELOW_ORDER_QTY"
        return "UNSUPPORTED_BROKER_STATUS"

    @staticmethod
    def _resolve_manual_reason_message(*, order_row, sync_candidate, local_summary) -> str:
        return (
            "Automatic recovery requires broker/local execution quantities to match "
            f"and a safe terminal mapping to exist: "
            f"broker_status={sync_candidate.broker_status}, "
            f"broker_filled_qty={sync_candidate.broker_filled_qty}, "
            f"local_execution_count={local_summary.execution_count}, "
            f"local_filled_qty={local_summary.filled_qty}, "
            f"order_qty={order_row.qty}"
        )

    @staticmethod
    def _build_manual_required(*, order_row, sync_candidate, local_summary, reason_code: str, reason_message: str) -> ExecutionRecoveryFinalizeCandidate:
        return ExecutionRecoveryFinalizeCandidate(
            client_order_id=order_row.client_order_id,
            symbol=order_row.symbol,
            status_before=order_row.status.value,
            status_after=None,
            broker_status=sync_candidate.broker_status,
            broker_filled_qty=sync_candidate.broker_filled_qty,
            local_execution_count=local_summary.execution_count,
            local_filled_qty=local_summary.filled_qty,
            local_avg_fill_price=local_summary.avg_fill_price,
            action=ExecutionRecoveryFinalizeAction.NONE,
            outcome=ExecutionRecoveryFinalizeOutcome.MANUAL_RECOVERY_REQUIRED,
            reason_code=reason_code,
            reason_message=reason_message,
            acted=False,
        )

    def _summarize_local_executions(self, order_id: int) -> LocalExecutionSummary:
        rows = self._execution_repo.list_by_order(order_id)
        execution_count = len(rows)
        filled_qty = sum(row.qty for row in rows)
        total_notional = sum(row.qty * row.price for row in rows)
        avg_fill_price = 0
        if filled_qty > 0:
            avg_fill_price = (total_notional + (filled_qty // 2)) // filled_qty
        return LocalExecutionSummary(
            execution_count=execution_count,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
        )
