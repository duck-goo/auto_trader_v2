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
from services.unresolved_order_sync_service import (
    UnresolvedOrderSyncOutcome,
    UnresolvedOrderSyncResult,
    UnresolvedOrderSyncService,
)
from storage.db import transaction
from storage.repositories import DbOrderStatus, ExecutionRepository, OrderRepository


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


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
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._order_repo = order_repo
        self._execution_repo = execution_repo
        self._sync_service = sync_service
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
