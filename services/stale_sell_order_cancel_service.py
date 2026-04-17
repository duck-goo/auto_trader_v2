"""Cancel stale unresolved sell orders after a timeout."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from logger import get_logger
from services.errors import ServiceError
from services.order_service import OrderService
from storage.repositories import DbOrderStatus, OrderRepository


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _default_now() -> datetime:
    return datetime.now(_KST)


class StaleSellOrderCancelOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StaleSellOrderCancelSettings:
    timeout_seconds: int

    def validated(self) -> "StaleSellOrderCancelSettings":
        return StaleSellOrderCancelSettings(
            timeout_seconds=_require_positive_int(
                "timeout_seconds",
                self.timeout_seconds,
            )
        )


@dataclass(frozen=True)
class StaleSellOrderCancelCandidate:
    client_order_id: str
    symbol: str
    status: str
    requested_at: str
    age_seconds: int
    outcome: StaleSellOrderCancelOutcome
    reason_code: str | None
    reason_message: str | None
    acted: bool


@dataclass(frozen=True)
class StaleSellOrderCancelResult:
    trade_date: str
    scanned_at: str
    execute_cancels: bool
    unresolved_order_count: int
    candidate_count: int
    preview_ready_count: int
    skipped_count: int
    cancelled_count: int
    rejected_count: int
    unknown_count: int
    blocked_count: int
    acted_count: int
    candidates: tuple[StaleSellOrderCancelCandidate, ...]


class StaleSellOrderCancelService:
    """Find stale unresolved sell orders and optionally cancel them."""

    def __init__(
        self,
        *,
        order_repo: OrderRepository,
        order_service: OrderService,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._order_repo = order_repo
        self._order_service = order_service
        self._now_fn = now_fn or _default_now

    def cancel_stale_orders(
        self,
        *,
        trade_date: str,
        settings: StaleSellOrderCancelSettings,
        execute_cancels: bool = False,
        skip_client_order_ids: set[str] | frozenset[str] | None = None,
    ) -> StaleSellOrderCancelResult:
        normalized_settings = settings.validated()
        now = self._now_fn().astimezone(_KST)
        scanned_at = now.isoformat()
        unresolved_orders = self._order_repo.find_unresolved()
        candidates: list[StaleSellOrderCancelCandidate] = []
        skip_ids = (
            frozenset()
            if skip_client_order_ids is None
            else frozenset(skip_client_order_ids)
        )

        _log.info(
            f"[stale_sell_cancel:start] trade_date={trade_date} "
            f"unresolved_order_count={len(unresolved_orders)} "
            f"execute_cancels={execute_cancels} skip_count={len(skip_ids)}"
        )

        for row in unresolved_orders:
            candidate = self._evaluate_order(
                row=row,
                trade_date=trade_date,
                timeout_seconds=normalized_settings.timeout_seconds,
                now=now,
                execute_cancels=execute_cancels,
                skip_client_order_ids=skip_ids,
            )
            candidates.append(candidate)

        preview_ready_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.PREVIEW_READY
        )
        skipped_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.SKIPPED
        )
        cancelled_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.CANCELLED
        )
        rejected_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.REJECTED
        )
        unknown_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.UNKNOWN
        )
        blocked_count = sum(
            1
            for item in candidates
            if item.outcome == StaleSellOrderCancelOutcome.BLOCKED
        )
        acted_count = sum(1 for item in candidates if item.acted)

        _log.info(
            f"[stale_sell_cancel:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} cancelled_count={cancelled_count} "
            f"unknown_count={unknown_count} blocked_count={blocked_count}"
        )

        return StaleSellOrderCancelResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            execute_cancels=execute_cancels,
            unresolved_order_count=len(unresolved_orders),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            skipped_count=skipped_count,
            cancelled_count=cancelled_count,
            rejected_count=rejected_count,
            unknown_count=unknown_count,
            blocked_count=blocked_count,
            acted_count=acted_count,
            candidates=tuple(candidates),
        )

    def _evaluate_order(
        self,
        *,
        row,
        trade_date: str,
        timeout_seconds: int,
        now: datetime,
        execute_cancels: bool,
        skip_client_order_ids: frozenset[str],
    ) -> StaleSellOrderCancelCandidate:
        requested_at = self._parse_requested_at(row.requested_at)
        age_seconds = max(0, int((now - requested_at).total_seconds()))

        if row.client_order_id in skip_client_order_ids:
            return self._build_skipped(
                row=row,
                age_seconds=age_seconds,
                reason_code="EXECUTION_RECOVERY_REQUIRED",
                reason_message=(
                    "This order was excluded because execution recovery is required first."
                ),
            )

        if row.side != "sell":
            return self._build_skipped(
                row=row,
                age_seconds=age_seconds,
                reason_code="NON_SELL_ORDER",
                reason_message="Only sell orders are handled by this cancel service.",
            )

        if requested_at.strftime("%Y-%m-%d") != trade_date:
            return self._build_skipped(
                row=row,
                age_seconds=age_seconds,
                reason_code="TRADE_DATE_MISMATCH",
                reason_message=(
                    "Order requested_at date does not match target trade_date: "
                    f"requested_date={requested_at.strftime('%Y-%m-%d')}, trade_date={trade_date}"
                ),
            )

        if row.status not in (DbOrderStatus.SUBMITTED, DbOrderStatus.PARTIAL):
            return self._build_skipped(
                row=row,
                age_seconds=age_seconds,
                reason_code="STATUS_NOT_CANCELLABLE",
                reason_message=(
                    "Only SUBMITTED/PARTIAL orders are cancellable here: "
                    f"status={row.status.value}"
                ),
            )

        if age_seconds < timeout_seconds:
            return self._build_skipped(
                row=row,
                age_seconds=age_seconds,
                reason_code="NOT_STALE_YET",
                reason_message=(
                    "Order age is still below timeout threshold: "
                    f"age_seconds={age_seconds}, timeout_seconds={timeout_seconds}"
                ),
            )

        if not execute_cancels:
            return StaleSellOrderCancelCandidate(
                client_order_id=row.client_order_id,
                symbol=row.symbol,
                status=row.status.value,
                requested_at=row.requested_at,
                age_seconds=age_seconds,
                outcome=StaleSellOrderCancelOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                acted=False,
            )

        try:
            cancel_result = self._order_service.cancel_order(
                client_order_id=row.client_order_id
            )
        except Exception as exc:
            raise ServiceError(
                f"Failed to cancel stale sell order: "
                f"client_order_id={row.client_order_id}, "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        return StaleSellOrderCancelCandidate(
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            status=row.status.value,
            requested_at=row.requested_at,
            age_seconds=age_seconds,
            outcome=StaleSellOrderCancelOutcome(cancel_result.outcome.value),
            reason_code=cancel_result.error_code,
            reason_message=cancel_result.error_message,
            acted=True,
        )

    @staticmethod
    def _parse_requested_at(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except Exception as exc:
            raise ServiceError(
                f"Invalid order requested_at timestamp: {value!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise ServiceError(
                f"Order requested_at must be timezone-aware: {value!r}"
            )
        return parsed.astimezone(_KST)

    @staticmethod
    def _build_skipped(
        *,
        row,
        age_seconds: int,
        reason_code: str,
        reason_message: str,
    ) -> StaleSellOrderCancelCandidate:
        return StaleSellOrderCancelCandidate(
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            status=row.status.value,
            requested_at=row.requested_at,
            age_seconds=age_seconds,
            outcome=StaleSellOrderCancelOutcome.SKIPPED,
            reason_code=reason_code,
            reason_message=reason_message,
            acted=False,
        )
