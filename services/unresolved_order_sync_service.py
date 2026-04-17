"""Safely synchronize unresolved order statuses from the broker."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from broker.kis.models import OrderInfo, OrderStatus
from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import DbOrderStatus, OrderRepository


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


def _default_now() -> datetime:
    return datetime.now(_KST)


class UnresolvedOrderSyncAction(str, enum.Enum):
    NONE = "NONE"
    MARK_SUBMITTED = "MARK_SUBMITTED"
    MARK_CANCELLED = "MARK_CANCELLED"
    EXECUTION_RECOVERY_REQUIRED = "EXECUTION_RECOVERY_REQUIRED"


class UnresolvedOrderSyncOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    SKIPPED = "SKIPPED"
    SYNCED = "SYNCED"
    EXECUTION_RECOVERY_REQUIRED = "EXECUTION_RECOVERY_REQUIRED"


@dataclass(frozen=True)
class UnresolvedOrderSyncCandidate:
    client_order_id: str
    symbol: str
    status_before: str
    status_after: str | None
    kis_order_no: str | None
    action: UnresolvedOrderSyncAction
    outcome: UnresolvedOrderSyncOutcome
    reason_code: str | None
    reason_message: str | None
    broker_status: str | None
    broker_filled_qty: int | None
    acted: bool


@dataclass(frozen=True)
class UnresolvedOrderSyncResult:
    trade_date: str
    scanned_at: str
    execute_sync: bool
    unresolved_order_count: int
    candidate_count: int
    preview_ready_count: int
    skipped_count: int
    synced_count: int
    execution_recovery_required_count: int
    acted_count: int
    candidates: tuple[UnresolvedOrderSyncCandidate, ...]


class UnresolvedOrderSyncService:
    """Sync only the broker states that are safe to persist without fill data."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._order_repo = order_repo
        self._now_fn = now_fn or _default_now

    def sync_unresolved_orders(
        self,
        *,
        trade_date: str,
        execute_sync: bool = False,
    ) -> UnresolvedOrderSyncResult:
        scanned_at = self._now_fn().astimezone(_KST).isoformat()
        unresolved_orders = self._order_repo.find_unresolved()
        candidates: list[UnresolvedOrderSyncCandidate] = []

        _log.info(
            f"[unresolved_order_sync:start] trade_date={trade_date} "
            f"unresolved_order_count={len(unresolved_orders)} execute_sync={execute_sync}"
        )

        for row in unresolved_orders:
            candidates.append(
                self._evaluate_and_maybe_sync(
                    row=row,
                    trade_date=trade_date,
                    execute_sync=execute_sync,
                    synced_at=scanned_at,
                )
            )

        preview_ready_count = sum(
            1
            for item in candidates
            if item.outcome == UnresolvedOrderSyncOutcome.PREVIEW_READY
        )
        skipped_count = sum(
            1 for item in candidates if item.outcome == UnresolvedOrderSyncOutcome.SKIPPED
        )
        synced_count = sum(
            1 for item in candidates if item.outcome == UnresolvedOrderSyncOutcome.SYNCED
        )
        execution_recovery_required_count = sum(
            1
            for item in candidates
            if item.outcome == UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED
        )
        acted_count = sum(1 for item in candidates if item.acted)

        _log.info(
            f"[unresolved_order_sync:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} synced_count={synced_count} "
            f"execution_recovery_required_count={execution_recovery_required_count}"
        )

        return UnresolvedOrderSyncResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            execute_sync=execute_sync,
            unresolved_order_count=len(unresolved_orders),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            skipped_count=skipped_count,
            synced_count=synced_count,
            execution_recovery_required_count=execution_recovery_required_count,
            acted_count=acted_count,
            candidates=tuple(candidates),
        )

    def _evaluate_and_maybe_sync(
        self,
        *,
        row,
        trade_date: str,
        execute_sync: bool,
        synced_at: str,
    ) -> UnresolvedOrderSyncCandidate:
        if row.requested_at[:10] != trade_date:
            return self._build_skipped(
                row=row,
                reason_code="TRADE_DATE_MISMATCH",
                reason_message=(
                    "Only same-day unresolved orders are handled here: "
                    f"requested_date={row.requested_at[:10]}, trade_date={trade_date}"
                ),
            )

        if row.status == DbOrderStatus.PARTIAL:
            return self._build_recovery_required(
                row=row,
                broker_info=None,
                reason_code="LOCAL_PARTIAL_REQUIRES_EXECUTION_RECOVERY",
                reason_message=(
                    "PARTIAL orders require execution recovery before status sync."
                ),
            )

        order_no = (row.kis_order_no or "").strip()
        if not order_no:
            return self._build_skipped(
                row=row,
                reason_code="MISSING_KIS_ORDER_NO",
                reason_message="Broker sync requires kis_order_no.",
            )

        broker_info = self._load_broker_order_info(order_no)
        if broker_info is None:
            return self._build_skipped(
                row=row,
                reason_code="NO_BROKER_MATCH",
                reason_message="Broker order lookup returned no rows.",
            )

        action = self._resolve_safe_action(row.status, broker_info)
        if action == UnresolvedOrderSyncAction.NONE:
            return self._build_skipped(
                row=row,
                broker_info=broker_info,
                reason_code="NO_SAFE_SYNC_ACTION",
                reason_message=(
                    "Broker status does not require a safe local state change."
                ),
            )
        if action == UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED:
            return self._build_recovery_required(
                row=row,
                broker_info=broker_info,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message=(
                    "Broker reports fills or partial state. Recover executions first."
                ),
            )

        if not execute_sync:
            return UnresolvedOrderSyncCandidate(
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                status_before=row.status.value,
                status_after=self._status_after_for_action(action),
                kis_order_no=row.kis_order_no,
                action=action,
                outcome=UnresolvedOrderSyncOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                broker_status=broker_info.status.value,
                broker_filled_qty=broker_info.filled_qty,
                acted=False,
            )

        updated_row = self._apply_action(
            row=row,
            action=action,
            broker_info=broker_info,
            synced_at=synced_at,
        )
        return UnresolvedOrderSyncCandidate(
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            status_before=row.status.value,
            status_after=updated_row.status.value,
            kis_order_no=updated_row.kis_order_no,
            action=action,
            outcome=UnresolvedOrderSyncOutcome.SYNCED,
            reason_code=None,
            reason_message=None,
            broker_status=broker_info.status.value,
            broker_filled_qty=broker_info.filled_qty,
            acted=True,
        )

    def _load_broker_order_info(self, order_no: str) -> OrderInfo | None:
        try:
            pending_rows = self._broker.get_order_status(
                order_no=order_no,
                filled_only=False,
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to load pending broker order status: "
                f"order_no={order_no}, {type(exc).__name__}: {exc}"
            ) from exc
        if len(pending_rows) > 1:
            raise ServiceError(
                f"Broker returned multiple pending rows for order_no={order_no!r}"
            )
        if len(pending_rows) == 1:
            return pending_rows[0]

        try:
            filled_rows = self._broker.get_order_status(
                order_no=order_no,
                filled_only=True,
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to load filled broker order status: "
                f"order_no={order_no}, {type(exc).__name__}: {exc}"
            ) from exc
        if len(filled_rows) > 1:
            raise ServiceError(
                f"Broker returned multiple filled rows for order_no={order_no!r}"
            )
        if len(filled_rows) == 1:
            return filled_rows[0]
        return None

    @staticmethod
    def _resolve_safe_action(
        current_status: DbOrderStatus,
        broker_info: OrderInfo,
    ) -> UnresolvedOrderSyncAction:
        if broker_info.filled_qty > 0:
            return UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED
        if broker_info.status in (OrderStatus.PARTIAL, OrderStatus.FILLED):
            return UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED
        if broker_info.status == OrderStatus.CANCELLED:
            return UnresolvedOrderSyncAction.MARK_CANCELLED
        if (
            current_status == DbOrderStatus.UNKNOWN
            and broker_info.status == OrderStatus.ACCEPTED
        ):
            return UnresolvedOrderSyncAction.MARK_SUBMITTED
        return UnresolvedOrderSyncAction.NONE

    def _apply_action(
        self,
        *,
        row,
        action: UnresolvedOrderSyncAction,
        broker_info: OrderInfo,
        synced_at: str,
    ):
        with transaction(self._conn):
            if action == UnresolvedOrderSyncAction.MARK_SUBMITTED:
                broker_order_no = (broker_info.order_no or "").strip()
                if not broker_order_no:
                    raise ServiceError(
                        "Broker confirmed ACCEPTED but order_no is missing."
                    )
                return self._order_repo.mark_submitted(
                    client_order_id=row.client_order_id,
                    kis_order_no=broker_order_no,
                    submitted_at=synced_at,
                )
            if action == UnresolvedOrderSyncAction.MARK_CANCELLED:
                return self._order_repo.mark_cancelled(
                    client_order_id=row.client_order_id,
                    closed_at=synced_at,
                )
        raise ServiceError(f"Unsupported sync action: {action.value}")

    @staticmethod
    def _status_after_for_action(action: UnresolvedOrderSyncAction) -> str | None:
        if action == UnresolvedOrderSyncAction.MARK_SUBMITTED:
            return DbOrderStatus.SUBMITTED.value
        if action == UnresolvedOrderSyncAction.MARK_CANCELLED:
            return DbOrderStatus.CANCELLED.value
        return None

    @staticmethod
    def _build_skipped(
        *,
        row,
        reason_code: str,
        reason_message: str,
        broker_info: OrderInfo | None = None,
    ) -> UnresolvedOrderSyncCandidate:
        return UnresolvedOrderSyncCandidate(
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            status_before=row.status.value,
            status_after=None,
            kis_order_no=row.kis_order_no,
            action=UnresolvedOrderSyncAction.NONE,
            outcome=UnresolvedOrderSyncOutcome.SKIPPED,
            reason_code=reason_code,
            reason_message=reason_message,
            broker_status=None if broker_info is None else broker_info.status.value,
            broker_filled_qty=None if broker_info is None else broker_info.filled_qty,
            acted=False,
        )

    @staticmethod
    def _build_recovery_required(
        *,
        row,
        broker_info: OrderInfo | None,
        reason_code: str,
        reason_message: str,
    ) -> UnresolvedOrderSyncCandidate:
        return UnresolvedOrderSyncCandidate(
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            status_before=row.status.value,
            status_after=None,
            kis_order_no=row.kis_order_no,
            action=UnresolvedOrderSyncAction.EXECUTION_RECOVERY_REQUIRED,
            outcome=UnresolvedOrderSyncOutcome.EXECUTION_RECOVERY_REQUIRED,
            reason_code=reason_code,
            reason_message=reason_message,
            broker_status=None if broker_info is None else broker_info.status.value,
            broker_filled_qty=None if broker_info is None else broker_info.filled_qty,
            acted=False,
        )
