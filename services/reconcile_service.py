"""Position reconciliation service."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import OrderRepository, OrderRow, PositionRepository


_log = get_logger("system")
_KST = pytz.timezone("Asia/Seoul")


class ReconcileOutcome(str, enum.Enum):
    RECONCILED = "RECONCILED"
    BLOCKED = "BLOCKED"


class ReconcileAction(str, enum.Enum):
    UPSERT = "UPSERT"
    CLEAR = "CLEAR"


@dataclass(frozen=True)
class PositionDiff:
    symbol: str
    action: ReconcileAction
    local_qty: int
    local_avg_price: int
    broker_qty: int
    broker_avg_price: int


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    reconciled_at: str
    changed_rows: int
    diffs: tuple[PositionDiff, ...]
    unresolved_orders: tuple[OrderRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class ReconcileService:
    """Reconcile local positions against broker balance snapshot."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._order_repo = order_repo
        self._position_repo = position_repo
        self._now_fn = now_fn or _default_now

    def reconcile_positions(
        self,
        *,
        allow_unresolved_orders: bool = False,
    ) -> ReconcileResult:
        """
        Reconcile local positions to the broker balance snapshot.

        Safety policy:
            - By default, block reconciliation if unresolved orders exist.
            - Broker API is called outside any DB transaction.
            - Only positions are changed here; orders/executions are untouched.
        """
        reconciled_at = self._now_fn().isoformat()
        unresolved_orders = tuple(self._order_repo.find_unresolved())

        if unresolved_orders and not allow_unresolved_orders:
            _log.warning(
                f"[reconcile_positions:blocked] unresolved_orders="
                f"{len(unresolved_orders)}"
            )
            return ReconcileResult(
                outcome=ReconcileOutcome.BLOCKED,
                reconciled_at=reconciled_at,
                changed_rows=0,
                diffs=(),
                unresolved_orders=unresolved_orders,
            )

        # Must stay outside a DB transaction.
        balance = self._broker.get_balance()

        broker_map = self._build_broker_position_map(balance.holdings)
        local_rows = {
            row.symbol: row for row in self._position_repo.list_all_including_zero()
        }
        diffs = self._compute_diffs(local_rows, broker_map)

        if diffs:
            with transaction(self._conn):
                for diff in diffs:
                    if diff.action == ReconcileAction.UPSERT:
                        self._position_repo.upsert_from_broker(
                            symbol=diff.symbol,
                            qty=diff.broker_qty,
                            avg_price=diff.broker_avg_price,
                            updated_at=reconciled_at,
                        )
                    else:
                        self._position_repo.clear(
                            symbol=diff.symbol,
                            updated_at=reconciled_at,
                        )

        _log.info(
            f"[reconcile_positions:done] changed_rows={len(diffs)} "
            f"unresolved_orders={len(unresolved_orders)} "
            f"allow_unresolved_orders={allow_unresolved_orders}"
        )
        return ReconcileResult(
            outcome=ReconcileOutcome.RECONCILED,
            reconciled_at=reconciled_at,
            changed_rows=len(diffs),
            diffs=diffs,
            unresolved_orders=unresolved_orders,
        )

    def _build_broker_position_map(
        self,
        holdings,
    ) -> dict[str, tuple[int, int]]:
        broker_map: dict[str, tuple[int, int]] = {}

        for holding in holdings:
            symbol = str(holding.code).strip()
            if not symbol:
                raise ServiceError("Broker balance contains a holding with empty code.")

            qty = int(holding.quantity)
            if qty < 0:
                raise ServiceError(
                    f"Broker balance contains negative quantity: "
                    f"symbol={symbol}, qty={qty}"
                )

            avg_price = 0 if qty == 0 else int(round(float(holding.avg_price)))
            if avg_price < 0:
                raise ServiceError(
                    f"Broker balance contains negative avg_price: "
                    f"symbol={symbol}, avg_price={avg_price}"
                )

            if symbol in broker_map:
                raise ServiceError(
                    f"Duplicate symbol in broker balance snapshot: {symbol!r}"
                )

            broker_map[symbol] = (qty, avg_price)

        return broker_map

    def _compute_diffs(
        self,
        local_rows: dict[str, object],
        broker_map: dict[str, tuple[int, int]],
    ) -> tuple[PositionDiff, ...]:
        symbols = sorted(set(local_rows.keys()) | set(broker_map.keys()))
        diffs: list[PositionDiff] = []

        for symbol in symbols:
            local = local_rows.get(symbol)
            local_qty = local.qty if local is not None else 0
            local_avg_price = local.avg_price if local is not None else 0

            broker_tuple = broker_map.get(symbol)
            if broker_tuple is None:
                if local is not None and (local_qty != 0 or local_avg_price != 0):
                    diffs.append(
                        PositionDiff(
                            symbol=symbol,
                            action=ReconcileAction.CLEAR,
                            local_qty=local_qty,
                            local_avg_price=local_avg_price,
                            broker_qty=0,
                            broker_avg_price=0,
                        )
                    )
                continue

            broker_qty, broker_avg_price = broker_tuple

            if broker_qty == 0 and broker_avg_price == 0:
                if local_qty != 0 or local_avg_price != 0:
                    diffs.append(
                        PositionDiff(
                            symbol=symbol,
                            action=ReconcileAction.CLEAR,
                            local_qty=local_qty,
                            local_avg_price=local_avg_price,
                            broker_qty=0,
                            broker_avg_price=0,
                        )
                    )
                continue

            if local_qty != broker_qty or local_avg_price != broker_avg_price:
                diffs.append(
                    PositionDiff(
                        symbol=symbol,
                        action=ReconcileAction.UPSERT,
                        local_qty=local_qty,
                        local_avg_price=local_avg_price,
                        broker_qty=broker_qty,
                        broker_avg_price=broker_avg_price,
                    )
                )

        return tuple(diffs)
