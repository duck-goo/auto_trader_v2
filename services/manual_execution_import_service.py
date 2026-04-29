"""Safely import missing execution rows from a manual JSON source."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import pytz

from logger import get_logger
from services.errors import ServiceError
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
from storage.db import transaction
from storage.repositories import (
    DbOrderStatus,
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
    SignalRepository,
)
from strategy import DEFAULT_TIMING2_SELL_COST_RATE


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")
_SELL_EXECUTION_AUDIT_STRATEGY = "sell_execution_attempt"
_LOT_LEVEL_SELL_STRATEGIES = {
    STRATEGY_NAME_TIMING2_LOT_STOP_LOSS,
    STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK,
    STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL,
}
_TIMING2_BUY_STRATEGIES = {
    STRATEGY_NAME_TIMING2_INTRADAY_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
}


@dataclass(frozen=True)
class ManualExecutionImportItem:
    client_order_id: str
    kis_exec_no: str
    qty: int
    price: int
    executed_at: str


class ManualExecutionImportOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    IMPORTED = "IMPORTED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ManualExecutionImportCandidate:
    client_order_id: str
    kis_exec_no: str
    symbol: str | None
    side: str | None
    status_before: str | None
    status_after: str | None
    local_filled_qty_before: int
    local_filled_qty_after: int | None
    outcome: ManualExecutionImportOutcome
    reason_code: str | None
    reason_message: str | None
    acted: bool


@dataclass(frozen=True)
class ManualExecutionImportResult:
    imported_at: str
    execute_import: bool
    item_count: int
    candidate_count: int
    preview_ready_count: int
    imported_count: int
    skipped_count: int
    blocked_count: int
    acted_count: int
    candidates: tuple[ManualExecutionImportCandidate, ...]


@dataclass(frozen=True)
class _SellLotExecutionContext:
    lot_id: int
    sell_cost_rate: float


class ManualExecutionImportService:
    """Import manual execution rows in a conservative, idempotent way."""

    _IMPORTABLE_STATUSES = frozenset(
        {
            DbOrderStatus.SUBMITTED,
            DbOrderStatus.UNKNOWN,
            DbOrderStatus.PARTIAL,
            DbOrderStatus.CANCELLED,
        }
    )

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        execution_repo: ExecutionRepository,
        position_repo: PositionRepository,
        entry_lot_repo: EntryLotRepository | None = None,
        signal_repo: SignalRepository | None = None,
    ) -> None:
        self._conn = conn
        self._order_repo = order_repo
        self._execution_repo = execution_repo
        self._position_repo = position_repo
        self._entry_lot_repo = entry_lot_repo
        self._signal_repo = signal_repo

    def import_items(
        self,
        *,
        items: list[ManualExecutionImportItem],
        execute_import: bool = False,
    ) -> ManualExecutionImportResult:
        imported_at = datetime.now(_KST).isoformat()
        candidates: list[ManualExecutionImportCandidate] = []

        _log.info(
            f"[manual_execution_import:start] item_count={len(items)} "
            f"execute_import={execute_import}"
        )

        for item in items:
            if not isinstance(item, ManualExecutionImportItem):
                raise ValueError(
                    "items must contain only ManualExecutionImportItem instances."
                )
            candidates.append(
                self._evaluate_item(
                    item=item,
                    execute_import=execute_import,
                )
            )

        preview_ready_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == ManualExecutionImportOutcome.PREVIEW_READY
        )
        imported_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == ManualExecutionImportOutcome.IMPORTED
        )
        skipped_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == ManualExecutionImportOutcome.SKIPPED
        )
        blocked_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == ManualExecutionImportOutcome.BLOCKED
        )
        acted_count = sum(1 for candidate in candidates if candidate.acted)

        _log.info(
            f"[manual_execution_import:done] candidate_count={len(candidates)} "
            f"imported_count={imported_count} blocked_count={blocked_count}"
        )

        return ManualExecutionImportResult(
            imported_at=imported_at,
            execute_import=execute_import,
            item_count=len(items),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            imported_count=imported_count,
            skipped_count=skipped_count,
            blocked_count=blocked_count,
            acted_count=acted_count,
            candidates=tuple(candidates),
        )

    def _evaluate_item(
        self,
        *,
        item: ManualExecutionImportItem,
        execute_import: bool,
    ) -> ManualExecutionImportCandidate:
        order_row = self._order_repo.get_by_client_order_id(item.client_order_id)
        if order_row is None:
            return ManualExecutionImportCandidate(
                client_order_id=item.client_order_id,
                kis_exec_no=item.kis_exec_no,
                symbol=None,
                side=None,
                status_before=None,
                status_after=None,
                local_filled_qty_before=0,
                local_filled_qty_after=None,
                outcome=ManualExecutionImportOutcome.BLOCKED,
                reason_code="ORDER_NOT_FOUND",
                reason_message=(
                    "Manual execution import requires an existing order row."
                ),
                acted=False,
            )

        self._validate_item(item)
        execution_rows = self._execution_repo.list_by_order(order_row.id)
        existing_filled_qty = sum(row.qty for row in execution_rows)

        if order_row.status not in self._IMPORTABLE_STATUSES:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=existing_filled_qty,
                reason_code="STATUS_NOT_IMPORTABLE",
                reason_message=(
                    "Manual execution import is allowed only for "
                    "SUBMITTED/UNKNOWN/PARTIAL/CANCELLED orders."
                ),
            )

        if any(row.kis_exec_no == item.kis_exec_no for row in execution_rows):
            return ManualExecutionImportCandidate(
                client_order_id=item.client_order_id,
                kis_exec_no=item.kis_exec_no,
                symbol=order_row.symbol,
                side=order_row.side,
                status_before=order_row.status.value,
                status_after=order_row.status.value,
                local_filled_qty_before=existing_filled_qty,
                local_filled_qty_after=existing_filled_qty,
                outcome=ManualExecutionImportOutcome.SKIPPED,
                reason_code="EXECUTION_ALREADY_EXISTS",
                reason_message=(
                    "This kis_exec_no already exists for the target order."
                ),
                acted=False,
            )

        projected_filled_qty = existing_filled_qty + item.qty
        if projected_filled_qty > order_row.qty:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=existing_filled_qty,
                reason_code="FILLED_QTY_EXCEEDS_ORDER_QTY",
                reason_message=(
                    "Import would make local filled_qty exceed order qty: "
                    f"before={existing_filled_qty}, import_qty={item.qty}, "
                    f"order_qty={order_row.qty}"
                ),
            )

        if order_row.status == DbOrderStatus.CANCELLED and projected_filled_qty >= order_row.qty:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=existing_filled_qty,
                reason_code="CANCELLED_ORDER_CANNOT_REACH_FULL_FILL",
                reason_message=(
                    "A cancelled order may keep partial fills, but cannot be "
                    "fully reconstructed as filled by this manual import path."
                ),
            )

        buy_lot_block = self._validate_buy_lot_context(
            item=item,
            order_row=order_row,
            local_filled_qty_before=existing_filled_qty,
        )
        if buy_lot_block is not None:
            return buy_lot_block

        sell_lot_block = self._validate_sell_lot_context(
            item=item,
            order_row=order_row,
            local_filled_qty_before=existing_filled_qty,
        )
        if sell_lot_block is not None:
            return sell_lot_block

        projected_status = self._project_status(
            order_row=order_row,
            projected_filled_qty=projected_filled_qty,
        )

        if not execute_import:
            return ManualExecutionImportCandidate(
                client_order_id=item.client_order_id,
                kis_exec_no=item.kis_exec_no,
                symbol=order_row.symbol,
                side=order_row.side,
                status_before=order_row.status.value,
                status_after=projected_status,
                local_filled_qty_before=existing_filled_qty,
                local_filled_qty_after=projected_filled_qty,
                outcome=ManualExecutionImportOutcome.PREVIEW_READY,
                reason_code=None,
                reason_message=None,
                acted=False,
            )

        updated_order = self._apply_import(
            item=item,
            order_row=order_row,
        )
        return ManualExecutionImportCandidate(
            client_order_id=item.client_order_id,
            kis_exec_no=item.kis_exec_no,
            symbol=order_row.symbol,
            side=order_row.side,
            status_before=order_row.status.value,
            status_after=updated_order.status.value,
            local_filled_qty_before=existing_filled_qty,
            local_filled_qty_after=updated_order.filled_qty,
            outcome=ManualExecutionImportOutcome.IMPORTED,
            reason_code=None,
            reason_message=None,
            acted=True,
        )

    def _apply_import(self, *, item: ManualExecutionImportItem, order_row):
        sell_lot_context = self._resolve_sell_lot_execution_context(order_row)
        with transaction(self._conn):
            inserted = self._execution_repo.insert_if_new(
                order_id=order_row.id,
                kis_exec_no=item.kis_exec_no,
                symbol=order_row.symbol,
                side=order_row.side,
                qty=item.qty,
                price=item.price,
                executed_at=item.executed_at,
            )
            if not inserted:
                raise ServiceError(
                    "Execution insert unexpectedly became duplicate during import: "
                    f"client_order_id={item.client_order_id}, "
                    f"kis_exec_no={item.kis_exec_no}"
                )

            self._position_repo.apply_execution(
                symbol=order_row.symbol,
                side=order_row.side,
                qty=item.qty,
                price=item.price,
                executed_at=item.executed_at,
            )
            if order_row.side == "buy" and self._entry_lot_repo is not None:
                self._entry_lot_repo.apply_buy_execution(
                    entry_order_id=order_row.id,
                    symbol=order_row.symbol,
                    qty=item.qty,
                    price=item.price,
                    executed_at=item.executed_at,
                    entry_strategy_name=order_row.strategy_name or "unknown",
                )
            if order_row.side == "sell" and sell_lot_context is not None:
                if self._entry_lot_repo is None:
                    raise ServiceError(
                        "EntryLotRepository is required for lot-level sell import."
                    )
                self._entry_lot_repo.apply_sell_to_lot(
                    lot_id=sell_lot_context.lot_id,
                    qty=item.qty,
                    price=item.price,
                    executed_at=item.executed_at,
                    sell_cost_rate=sell_lot_context.sell_cost_rate,
                )

            if order_row.status == DbOrderStatus.CANCELLED:
                updated_order = self._order_repo.sync_execution_summary(
                    client_order_id=order_row.client_order_id,
                )
            else:
                updated_order = self._order_repo.sync_execution_summary(
                    client_order_id=order_row.client_order_id,
                    closed_at=item.executed_at,
                )

        return updated_order

    def _validate_buy_lot_context(
        self,
        *,
        item: ManualExecutionImportItem,
        order_row,
        local_filled_qty_before: int,
    ) -> ManualExecutionImportCandidate | None:
        if not self._is_timing2_buy_order(order_row):
            return None

        if self._entry_lot_repo is not None:
            return None

        return self._build_blocked(
            item=item,
            order_row=order_row,
            local_filled_qty_before=local_filled_qty_before,
            reason_code="ENTRY_LOT_REPOSITORY_MISSING",
            reason_message=(
                "Timing2 buy execution import requires EntryLotRepository so "
                "the actual filled quantity is persisted as a separate entry lot."
            ),
        )

    def _validate_sell_lot_context(
        self,
        *,
        item: ManualExecutionImportItem,
        order_row,
        local_filled_qty_before: int,
    ) -> ManualExecutionImportCandidate | None:
        if not self._is_lot_level_sell_order(order_row):
            return None

        if self._entry_lot_repo is None:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=local_filled_qty_before,
                reason_code="ENTRY_LOT_REPOSITORY_MISSING",
                reason_message=(
                    "Lot-level sell execution import requires EntryLotRepository."
                ),
            )
        if self._signal_repo is None:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=local_filled_qty_before,
                reason_code="SIGNAL_REPOSITORY_MISSING",
                reason_message=(
                    "Lot-level sell execution import requires the sell execution "
                    "audit signal to identify source_lot_id."
                ),
            )

        context = self._resolve_sell_lot_execution_context(order_row)
        if context is None:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=local_filled_qty_before,
                reason_code="LOT_SELL_AUDIT_NOT_FOUND",
                reason_message=(
                    "No sell execution audit signal with source_lot_id was found "
                    f"for client_order_id={order_row.client_order_id}."
                ),
            )

        lot = self._entry_lot_repo.get(context.lot_id)
        if lot is None or lot.status != "OPEN" or lot.remaining_qty <= 0:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=local_filled_qty_before,
                reason_code="ENTRY_LOT_NOT_OPEN",
                reason_message=(
                    "The source lot is missing or no longer open: "
                    f"lot_id={context.lot_id}"
                ),
            )
        if lot.symbol != order_row.symbol:
            raise ServiceError(
                "Lot-level sell import symbol mismatch: "
                f"order_symbol={order_row.symbol}, lot_symbol={lot.symbol}, "
                f"lot_id={context.lot_id}"
            )
        if item.qty > lot.remaining_qty:
            return self._build_blocked(
                item=item,
                order_row=order_row,
                local_filled_qty_before=local_filled_qty_before,
                reason_code="SELL_QTY_EXCEEDS_LOT_REMAINING",
                reason_message=(
                    "Imported sell execution qty exceeds current lot remaining_qty: "
                    f"import_qty={item.qty}, lot_remaining_qty={lot.remaining_qty}, "
                    f"lot_id={context.lot_id}"
                ),
            )
        return None

    def _resolve_sell_lot_execution_context(self, order_row) -> _SellLotExecutionContext | None:
        if not self._is_lot_level_sell_order(order_row):
            return None
        if self._signal_repo is None:
            return None

        rows = self._signal_repo.list_by_strategy(
            _SELL_EXECUTION_AUDIT_STRATEGY,
            limit=2000,
        )
        for row in rows:
            payload = row.payload or {}
            if payload.get("client_order_id") != order_row.client_order_id:
                continue
            lot_id = payload.get("source_lot_id")
            if isinstance(lot_id, bool) or not isinstance(lot_id, int) or lot_id <= 0:
                raise ServiceError(
                    "Sell execution audit has invalid source_lot_id: "
                    f"audit_signal_id={row.id}, value={lot_id!r}"
                )
            raw_cost_rate = payload.get(
                "sell_cost_rate",
                DEFAULT_TIMING2_SELL_COST_RATE,
            )
            sell_cost_rate = self._require_non_negative_float(
                "sell_cost_rate",
                raw_cost_rate,
            )
            return _SellLotExecutionContext(
                lot_id=lot_id,
                sell_cost_rate=sell_cost_rate,
            )
        return None

    @staticmethod
    def _is_lot_level_sell_order(order_row) -> bool:
        return (
            order_row.side == "sell"
            and order_row.strategy_name in _LOT_LEVEL_SELL_STRATEGIES
        )

    @staticmethod
    def _is_timing2_buy_order(order_row) -> bool:
        return (
            order_row.side == "buy"
            and order_row.strategy_name in _TIMING2_BUY_STRATEGIES
        )

    @staticmethod
    def _project_status(*, order_row, projected_filled_qty: int) -> str:
        if order_row.status == DbOrderStatus.CANCELLED:
            return DbOrderStatus.CANCELLED.value
        if projected_filled_qty == order_row.qty:
            return DbOrderStatus.FILLED.value
        return DbOrderStatus.PARTIAL.value

    @staticmethod
    def _validate_item(item: ManualExecutionImportItem) -> None:
        if not isinstance(item.client_order_id, str) or not item.client_order_id.strip():
            raise ValueError("client_order_id must be a non-empty string.")
        if not isinstance(item.kis_exec_no, str) or not item.kis_exec_no.strip():
            raise ValueError("kis_exec_no must be a non-empty string.")
        if isinstance(item.qty, bool) or not isinstance(item.qty, int) or item.qty <= 0:
            raise ValueError(f"qty must be a positive integer: {item.qty!r}")
        if (
            isinstance(item.price, bool)
            or not isinstance(item.price, int)
            or item.price <= 0
        ):
            raise ValueError(
                f"price must be a positive integer actual execution price: {item.price!r}"
            )
        try:
            parsed = datetime.fromisoformat(item.executed_at)
        except Exception as exc:
            raise ValueError(
                f"executed_at must be a valid ISO-8601 string: {item.executed_at!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(
                f"executed_at must be timezone-aware: {item.executed_at!r}"
            )

    @staticmethod
    def _require_non_negative_float(name: str, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a non-negative number: {value!r}")
        normalized = float(value)
        if normalized < 0.0:
            raise ValueError(f"{name} must be a non-negative number: {value!r}")
        return normalized

    @staticmethod
    def _build_blocked(
        *,
        item: ManualExecutionImportItem,
        order_row,
        local_filled_qty_before: int,
        reason_code: str,
        reason_message: str,
    ) -> ManualExecutionImportCandidate:
        return ManualExecutionImportCandidate(
            client_order_id=item.client_order_id,
            kis_exec_no=item.kis_exec_no,
            symbol=order_row.symbol,
            side=order_row.side,
            status_before=order_row.status.value,
            status_after=None,
            local_filled_qty_before=local_filled_qty_before,
            local_filled_qty_after=None,
            outcome=ManualExecutionImportOutcome.BLOCKED,
            reason_code=reason_code,
            reason_message=reason_message,
            acted=False,
        )
