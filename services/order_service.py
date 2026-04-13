"""OrderService: single entry point for placing and cancelling orders.

Responsibilities:
    - Generate client_order_id (our idempotency key).
    - Pre-trade validation (e.g. enough position for sell).
    - Create a PENDING order row *before* the broker call.
    - Call the broker OUTSIDE of any DB transaction.
    - Classify the broker outcome into one of:
        SUBMITTED : broker accepted, got order_no
        REJECTED  : broker explicitly refused (rt_cd != 0)
        UNKNOWN   : acceptance unclear (network/parse failure, missing order_no)
        FAILED    : our pre-trade check blocked it before broker was called

Design invariants (MUST hold):
    - One order request == one client_order_id == one DB row.
    - Broker call never happens inside a DB transaction.
    - POST is never retried (safety).
    - UNKNOWN recovery is NOT attempted here; Phase 3-B owns it.
"""

from __future__ import annotations

import enum
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from broker.kis.errors import KisApiError, KisError, KisOrderError
from broker.kis.models import OrderInfo, OrderSide, OrderType
from logger import get_logger
from services.errors import (
    DuplicateClientOrderIdError,
    InsufficientPositionError,
    ServiceError,
)
from storage.db import transaction
from storage.repositories import (
    DbOrderStatus,
    OrderRepository,
    OrderRow,
    PositionRepository,
)


_log = get_logger("order")
_KST = pytz.timezone("Asia/Seoul")
_STRATEGY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")
_STRATEGY_MAX_LEN = 20
_MAX_CLIENT_ORDER_ID_ATTEMPTS = 2

# Pre-trade failure codes.
ERR_INSUFFICIENT_POSITION = "PRE_TRADE_INSUFFICIENT_POSITION"

# Unknown-outcome reason codes (recorded as error_code for later diagnosis).
ERR_UNKNOWN_NO_ORDER_NO = "BROKER_ACCEPTED_WITHOUT_ORDER_NO"
ERR_UNKNOWN_NETWORK = "BROKER_CALL_NETWORK_OR_ORDER_ERROR"
ERR_UNKNOWN_GENERIC = "BROKER_CALL_UNEXPECTED_KIS_ERROR"
ERR_CANCEL_ORDER_NOT_FOUND = "CANCEL_ORDER_NOT_FOUND"
ERR_CANCEL_NOT_ALLOWED_STATUS = "CANCEL_NOT_ALLOWED_STATUS"
ERR_CANCEL_MISSING_ORDER_NO = "CANCEL_MISSING_ORDER_NO"
ERR_CANCEL_BROKER_REJECTED = "CANCEL_BROKER_REJECTED"
ERR_CANCEL_UNKNOWN_NETWORK = "CANCEL_BROKER_CALL_NETWORK_OR_ORDER_ERROR"
ERR_CANCEL_UNKNOWN_GENERIC = "CANCEL_BROKER_CALL_UNEXPECTED_KIS_ERROR"



class OrderOutcome(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OrderResult:
    outcome: OrderOutcome
    client_order_id: str
    order_row: OrderRow
    broker_info: OrderInfo | None
    error_code: str | None
    error_message: str | None

class CancelOutcome(str, enum.Enum):
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    client_order_id: str
    order_row: OrderRow | None
    broker_info: OrderInfo | None
    error_code: str | None
    error_message: str | None



def _default_now() -> datetime:
    return datetime.now(_KST)


def _default_id_fn() -> str:
    return uuid.uuid4().hex[:8]


def _normalize_strategy_name(strategy_name: str | None) -> str:
    if strategy_name is None:
        return "nostrategy"
    stripped = strategy_name.strip()
    if not stripped:
        return "nostrategy"
    sanitized = _STRATEGY_SANITIZE_RE.sub("_", stripped)
    return sanitized[:_STRATEGY_MAX_LEN] or "nostrategy"


class OrderService:
    """High-level order orchestration over broker + storage."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._order_repo = order_repo
        self._position_repo = position_repo
        self._now_fn = now_fn or _default_now
        self._id_fn = id_fn or _default_id_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def place_order(
        self,
        *,
        symbol: str,
        side: OrderSide | str,
        qty: int,
        price: int,
        order_type: OrderType | str,
        strategy_name: str | None,
    ) -> OrderResult:
        """
        Place an order through the broker and persist the result.
        Returns OrderResult with one of four outcomes (SUBMITTED/REJECTED/
        UNKNOWN/FAILED). Only programming errors and DB invariant violations
        are raised; normal business failures are returned as OrderResult.
        """
        side_value = self._coerce_side(side)
        order_type_value = self._coerce_order_type(order_type)

        # Broker distinguishes MARKET vs LIMIT purely by price==0 vs price>0.
        if order_type_value == "MARKET" and price != 0:
            raise ValueError(f"MARKET order requires price=0, got {price!r}")
        if order_type_value == "LIMIT" and price <= 0:
            raise ValueError(f"LIMIT order requires price > 0, got {price!r}")

        requested_at = self._now_fn()
        requested_at_iso = requested_at.isoformat()

        # ---- Create PENDING row first ----
        client_order_id = self._create_pending_order(
            symbol=symbol,
            side=side_value,
            qty=qty,
            price=price,
            order_type=order_type_value,
            strategy_name=strategy_name,
            requested_at_iso=requested_at_iso,
        )
        _log.info(
            f"[place_order:pending_created] client_order_id={client_order_id} "
            f"symbol={symbol} side={side_value} qty={qty} price={price} "
            f"type={order_type_value}"
        )

        # ---- Pre-trade checks (convert to FAILED if violated) ----
        pre_check_err = self._check_pre_trade(symbol, side_value, qty)
        if pre_check_err is not None:
            error_code, error_message = pre_check_err
            return self._finalize_failed(
                client_order_id=client_order_id,
                error_code=error_code,
                error_message=error_message,
            )

        # ---- Broker call (OUTSIDE any DB transaction) ----
        broker_info: OrderInfo | None = None
        try:
            broker_info = self._broker.place_order(
                code=symbol,
                side=side_value,
                quantity=qty,
                price=price,
            )
        except KisApiError as exc:
            # Explicit broker refusal.
            error_code, error_message = self._extract_api_error_info(exc)
            _log.warning(
                f"[place_order:rejected] client_order_id={client_order_id} "
                f"error_code={error_code} error_message={error_message}"
            )
            return self._finalize_rejected(
                client_order_id=client_order_id,
                error_code=error_code,
                error_message=error_message,
            )
        except KisOrderError as exc:
            # Network failure / duplicate / acceptance unclear.
            _log.warning(
                f"[place_order:unknown_order_error] "
                f"client_order_id={client_order_id} error={exc}"
            )
            return self._finalize_unknown(
                client_order_id=client_order_id,
                error_code=ERR_UNKNOWN_NETWORK,
                error_message=str(exc),
            )
        except KisError as exc:
            # Any other KIS-layer error: treat as UNKNOWN, never retry.
            _log.warning(
                f"[place_order:unknown_kis_error] "
                f"client_order_id={client_order_id} error={exc}"
            )
            return self._finalize_unknown(
                client_order_id=client_order_id,
                error_code=ERR_UNKNOWN_GENERIC,
                error_message=str(exc),
            )

        # ---- Broker returned without raising: check order_no ----
        order_no = (broker_info.order_no or "").strip() if broker_info else ""
        if not order_no:
            _log.warning(
                f"[place_order:unknown_no_order_no] "
                f"client_order_id={client_order_id} "
                f"broker_status={getattr(broker_info, 'status', None)}"
            )
            return self._finalize_unknown(
                client_order_id=client_order_id,
                error_code=ERR_UNKNOWN_NO_ORDER_NO,
                error_message="Broker returned success without order_no.",
                broker_info=broker_info,
            )

        # ---- Success: SUBMITTED ----
        submitted_at_iso = self._now_fn().isoformat()
        with transaction(self._conn):
            order_row = self._order_repo.mark_submitted(
                client_order_id=client_order_id,
                kis_order_no=order_no,
                submitted_at=submitted_at_iso,
            )
        _log.info(
            f"[place_order:submitted] client_order_id={client_order_id} "
            f"kis_order_no={order_no}"
        )
        return OrderResult(
            outcome=OrderOutcome.SUBMITTED,
            client_order_id=client_order_id,
            order_row=order_row,
            broker_info=broker_info,
            error_code=None,
            error_message=None,
        )
    
    def cancel_order(self, *, client_order_id: str) -> CancelResult:
        """
        Cancel a previously submitted order by our client_order_id.

        Normal business outcomes are returned as CancelResult.
        Unexpected non-KIS exceptions propagate unchanged.
        """
        if not isinstance(client_order_id, str) or not client_order_id.strip():
            raise ValueError(
                f"client_order_id must be a non-empty string: {client_order_id!r}"
            )
        client_order_id = client_order_id.strip()

        current = self._order_repo.get_by_client_order_id(client_order_id)
        if current is None:
            error_code = ERR_CANCEL_ORDER_NOT_FOUND
            error_message = (
                "Order not found for cancellation: "
                f"client_order_id={client_order_id}"
            )
            _log.warning(
                f"[cancel_order:blocked_not_found] client_order_id={client_order_id}"
            )
            return CancelResult(
                outcome=CancelOutcome.BLOCKED,
                client_order_id=client_order_id,
                order_row=None,
                broker_info=None,
                error_code=error_code,
                error_message=error_message,
            )

        if current.status not in (DbOrderStatus.SUBMITTED, DbOrderStatus.PARTIAL):
            error_code = ERR_CANCEL_NOT_ALLOWED_STATUS
            error_message = (
                "Order is not cancellable in current status: "
                f"client_order_id={client_order_id} "
                f"status={current.status.value}"
            )
            _log.warning(
                f"[cancel_order:blocked_status] client_order_id={client_order_id} "
                f"status={current.status.value}"
            )
            return CancelResult(
                outcome=CancelOutcome.BLOCKED,
                client_order_id=client_order_id,
                order_row=current,
                broker_info=None,
                error_code=error_code,
                error_message=error_message,
            )

        order_no = (current.kis_order_no or "").strip()
        if not order_no:
            error_code = ERR_CANCEL_MISSING_ORDER_NO
            error_message = (
                "Order has no kis_order_no, so broker cancellation is impossible: "
                f"client_order_id={client_order_id}"
            )
            _log.warning(
                f"[cancel_order:blocked_missing_order_no] "
                f"client_order_id={client_order_id}"
            )
            return CancelResult(
                outcome=CancelOutcome.BLOCKED,
                client_order_id=client_order_id,
                order_row=current,
                broker_info=None,
                error_code=error_code,
                error_message=error_message,
            )

        broker_info: OrderInfo | None = None
        try:
            broker_info = self._broker.cancel_order(
                order_no=order_no,
                code=current.symbol,
                quantity=current.qty,
            )
        except KisApiError as exc:
            error_code, error_message = self._extract_api_error_info(exc)
            error_code = error_code or ERR_CANCEL_BROKER_REJECTED
            _log.warning(
                f"[cancel_order:rejected] client_order_id={client_order_id} "
                f"kis_order_no={order_no} error_code={error_code} "
                f"error_message={error_message}"
            )
            return CancelResult(
                outcome=CancelOutcome.REJECTED,
                client_order_id=client_order_id,
                order_row=self._get_order_row_or_raise(client_order_id),
                broker_info=None,
                error_code=error_code,
                error_message=error_message,
            )
        except KisOrderError as exc:
            _log.warning(
                f"[cancel_order:unknown_order_error] "
                f"client_order_id={client_order_id} kis_order_no={order_no} "
                f"error={exc}"
            )
            return CancelResult(
                outcome=CancelOutcome.UNKNOWN,
                client_order_id=client_order_id,
                order_row=self._get_order_row_or_raise(client_order_id),
                broker_info=exc.order_info,
                error_code=ERR_CANCEL_UNKNOWN_NETWORK,
                error_message=str(exc),
            )
        except KisError as exc:
            _log.warning(
                f"[cancel_order:unknown_kis_error] "
                f"client_order_id={client_order_id} kis_order_no={order_no} "
                f"error={exc}"
            )
            return CancelResult(
                outcome=CancelOutcome.UNKNOWN,
                client_order_id=client_order_id,
                order_row=self._get_order_row_or_raise(client_order_id),
                broker_info=None,
                error_code=ERR_CANCEL_UNKNOWN_GENERIC,
                error_message=str(exc),
            )

        closed_at_iso = self._now_fn().isoformat()
        with transaction(self._conn):
            row = self._order_repo.mark_cancelled(
                client_order_id=client_order_id,
                closed_at=closed_at_iso,
            )

        _log.info(
            f"[cancel_order:cancelled] client_order_id={client_order_id} "
            f"kis_order_no={order_no} cancel_order_no="
            f"{getattr(broker_info, 'order_no', None)}"
        )
        return CancelResult(
            outcome=CancelOutcome.CANCELLED,
            client_order_id=client_order_id,
            order_row=row,
            broker_info=broker_info,
            error_code=None,
            error_message=None,
        )
    

    # ------------------------------------------------------------------
    # Pre-trade validation
    # ------------------------------------------------------------------
    def _check_pre_trade(
        self, symbol: str, side: str, qty: int,
    ) -> tuple[str, str] | None:
        """
        Returns (error_code, error_message) if blocked, None if OK.
        Pre-trade checks are best-effort safeguards; the broker is still
        the final authority.
        """
        if side != "sell":
            return None

        position = self._position_repo.get(symbol)
        available = position.qty if position else 0
        if available < qty:
            return (
                ERR_INSUFFICIENT_POSITION,
                f"Insufficient position: available={available}, requested={qty}",
            )
        return None

    # ------------------------------------------------------------------
    # Terminal state finalizers
    # ------------------------------------------------------------------
    def _finalize_failed(
        self,
        *,
        client_order_id: str,
        error_code: str,
        error_message: str,
    ) -> OrderResult:
        closed_at_iso = self._now_fn().isoformat()
        with transaction(self._conn):
            row = self._order_repo.mark_failed(
                client_order_id=client_order_id,
                error_code=error_code,
                error_message=error_message,
                closed_at=closed_at_iso,
            )
        _log.warning(
            f"[place_order:failed] client_order_id={client_order_id} "
            f"error_code={error_code}"
        )
        return OrderResult(
            outcome=OrderOutcome.FAILED,
            client_order_id=client_order_id,
            order_row=row,
            broker_info=None,
            error_code=error_code,
            error_message=error_message,
        )

    def _finalize_rejected(
        self,
        *,
        client_order_id: str,
        error_code: str | None,
        error_message: str | None,
    ) -> OrderResult:
        closed_at_iso = self._now_fn().isoformat()
        with transaction(self._conn):
            row = self._order_repo.mark_rejected(
                client_order_id=client_order_id,
                error_code=error_code,
                error_message=error_message,
                closed_at=closed_at_iso,
            )
        return OrderResult(
            outcome=OrderOutcome.REJECTED,
            client_order_id=client_order_id,
            order_row=row,
            broker_info=None,
            error_code=error_code,
            error_message=error_message,
        )

    def _finalize_unknown(
        self,
        *,
        client_order_id: str,
        error_code: str,
        error_message: str,
        broker_info: OrderInfo | None = None,
    ) -> OrderResult:
        with transaction(self._conn):
            row = self._order_repo.mark_unknown(
                client_order_id=client_order_id,
            )
        # Note: mark_unknown doesn't record error_code/message in DB (current
        # Phase 2 API). We still surface them in OrderResult for the caller/log.
        return OrderResult(
            outcome=OrderOutcome.UNKNOWN,
            client_order_id=client_order_id,
            order_row=row,
            broker_info=broker_info,
            error_code=error_code,
            error_message=error_message,
        )

    # ------------------------------------------------------------------
    # Error extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_api_error_info(exc: KisApiError) -> tuple[str | None, str]:
        """Best-effort extraction of (msg_cd, msg) from KisApiError."""
        error_code = getattr(exc, "msg_cd", None)
        error_message = getattr(exc, "msg", None) or str(exc)
        if isinstance(error_code, str):
            error_code = error_code.strip() or None
        else:
            error_code = None
        return error_code, error_message
    
    def _get_order_row_or_raise(self, client_order_id: str) -> OrderRow:
        row = self._order_repo.get_by_client_order_id(client_order_id)
        if row is None:
            raise ServiceError(
                f"Order row disappeared unexpectedly: "
                f"client_order_id={client_order_id!r}"
            )
        return row
    

    # ------------------------------------------------------------------
    # Internal: pending-order creation with UNIQUE retry
    # ------------------------------------------------------------------
    def _create_pending_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        price: int,
        order_type: str,
        strategy_name: str | None,
        requested_at_iso: str,
    ) -> str:
        last_client_order_id = ""
        for attempt in range(1, _MAX_CLIENT_ORDER_ID_ATTEMPTS + 1):
            client_order_id = self._generate_client_order_id(
                strategy_name=strategy_name,
                now=self._parse_iso(requested_at_iso),
            )
            last_client_order_id = client_order_id
            try:
                with transaction(self._conn):
                    self._order_repo.create(
                        client_order_id=client_order_id,
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        price=price,
                        order_type=order_type,
                        strategy_name=strategy_name,
                        requested_at=requested_at_iso,
                    )
                return client_order_id
            except sqlite3.IntegrityError as exc:
                _log.warning(
                    f"[place_order:client_order_id_collision] "
                    f"attempt={attempt} client_order_id={client_order_id} "
                    f"error={exc}"
                )
                continue

        raise DuplicateClientOrderIdError(
            client_order_id=last_client_order_id,
            attempts=_MAX_CLIENT_ORDER_ID_ATTEMPTS,
        )

    def _generate_client_order_id(
        self,
        *,
        strategy_name: str | None,
        now: datetime,
    ) -> str:
        strategy_part = _normalize_strategy_name(strategy_name)
        time_part = now.strftime("%Y%m%d%H%M%S")
        random_part = self._id_fn()
        if not isinstance(random_part, str) or not random_part:
            raise ServiceError(
                f"id_fn() must return a non-empty string, got {random_part!r}"
            )
        return f"{time_part}-{strategy_part}-{random_part}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_side(side: OrderSide | str) -> str:
        if isinstance(side, OrderSide):
            return side.value
        if isinstance(side, str):
            lowered = side.strip().lower()
            if lowered in ("buy", "sell"):
                return lowered
        raise ValueError(f"Invalid side: {side!r}")

    @staticmethod
    def _coerce_order_type(order_type: OrderType | str) -> str:
        if isinstance(order_type, OrderType):
            return order_type.value.upper()
        if isinstance(order_type, str):
            upper = order_type.strip().upper()
            if upper in ("LIMIT", "MARKET"):
                return upper
        raise ValueError(f"Invalid order_type: {order_type!r}")

    @staticmethod
    def _parse_iso(iso_str: str) -> datetime:
        return datetime.fromisoformat(iso_str)