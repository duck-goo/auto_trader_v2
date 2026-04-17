"""Order repository."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from storage.repositories.base import (
    RepositoryInvariantError,
    RowMapper,
    normalize_optional_text,
    require_aware_iso8601,
    require_non_empty_text,
    require_non_negative_int,
    require_order_type,
    require_positive_int,
    require_side,
    require_write_transaction,
)
from storage.repositories.status_map import (
    DbOrderStatus,
    UNRESOLVED_DB_ORDER_STATUSES,
    assert_transition_allowed,
    coerce_db_order_status,
)

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_trade_date(value: str) -> datetime:
    text = require_non_empty_text("trade_date", value)
    if not _DATE_PATTERN.match(text):
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}")
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date is not a valid date: {value!r}") from exc


def _day_bounds_kst(trade_date: str) -> tuple[str, str]:
    day = _parse_trade_date(trade_date)
    next_day = day + timedelta(days=1)
    start = day.strftime("%Y-%m-%dT00:00:00+09:00")
    end = next_day.strftime("%Y-%m-%dT00:00:00+09:00")
    return start, end


@dataclass(frozen=True)
class OrderRow:
    id: int
    client_order_id: str
    kis_order_no: str | None
    symbol: str
    side: str
    qty: int
    price: int
    order_type: str
    status: DbOrderStatus
    filled_qty: int
    avg_fill_price: int
    requested_at: str
    submitted_at: str | None
    closed_at: str | None
    error_code: str | None
    error_message: str | None
    strategy_name: str | None


@dataclass(frozen=True)
class ExecutionSummary:
    filled_qty: int
    avg_fill_price: int
    execution_count: int


class OrderRepository:
    """Pure DB repository for order rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: int,
        price: int,
        order_type: str,
        strategy_name: str | None,
        requested_at: str,
    ) -> OrderRow:
        """
        Create a new PENDING order row.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        symbol = require_non_empty_text("symbol", symbol)
        side = require_side(side)
        qty = require_positive_int("qty", qty)
        price = require_non_negative_int("price", price)
        order_type = require_order_type(order_type)
        requested_at = require_aware_iso8601("requested_at", requested_at)
        strategy_name = normalize_optional_text(strategy_name)

        self._conn.execute(
            """
            INSERT INTO orders (
                client_order_id,
                symbol,
                side,
                qty,
                price,
                order_type,
                status,
                requested_at,
                strategy_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_order_id,
                symbol,
                side,
                qty,
                price,
                order_type,
                DbOrderStatus.PENDING.value,
                requested_at,
                strategy_name,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_submitted(
        self,
        *,
        client_order_id: str,
        kis_order_no: str,
        submitted_at: str,
    ) -> OrderRow:
        """
        Mark an order as SUBMITTED.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        kis_order_no = require_non_empty_text("kis_order_no", kis_order_no)
        submitted_at = require_aware_iso8601("submitted_at", submitted_at)

        current = self._get_required_by_client_order_id(client_order_id)
        if current.status == DbOrderStatus.SUBMITTED:
            if current.kis_order_no and current.kis_order_no != kis_order_no:
                raise RepositoryInvariantError(
                    "kis_order_no mismatch on repeated mark_submitted call: "
                    f"{current.kis_order_no!r} != {kis_order_no!r}"
                )
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.SUBMITTED,
            client_order_id=client_order_id,
        )
        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                kis_order_no = ?,
                submitted_at = ?,
                error_code = NULL,
                error_message = NULL
            WHERE client_order_id = ?
            """,
            (
                DbOrderStatus.SUBMITTED.value,
                kis_order_no,
                submitted_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_unknown(self, *, client_order_id: str) -> OrderRow:
        """
        Mark an order as UNKNOWN.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)

        current = self._get_required_by_client_order_id(client_order_id)
        if current.status == DbOrderStatus.UNKNOWN:
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.UNKNOWN,
            client_order_id=client_order_id,
        )
        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                closed_at = NULL
            WHERE client_order_id = ?
            """,
            (DbOrderStatus.UNKNOWN.value, client_order_id),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_rejected(
        self,
        *,
        client_order_id: str,
        error_code: str | None,
        error_message: str | None,
        closed_at: str,
    ) -> OrderRow:
        """
        Mark an order as REJECTED.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        closed_at = require_aware_iso8601("closed_at", closed_at)
        error_code = normalize_optional_text(error_code)
        error_message = normalize_optional_text(error_message)

        current = self._get_required_by_client_order_id(client_order_id)
        if current.status == DbOrderStatus.REJECTED:
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.REJECTED,
            client_order_id=client_order_id,
        )
        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                error_code = ?,
                error_message = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (
                DbOrderStatus.REJECTED.value,
                error_code,
                error_message,
                closed_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_failed(
        self,
        *,
        client_order_id: str,
        error_code: str | None,
        error_message: str | None,
        closed_at: str,
    ) -> OrderRow:
        """
        Mark an order as FAILED.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        closed_at = require_aware_iso8601("closed_at", closed_at)
        error_code = normalize_optional_text(error_code)
        error_message = normalize_optional_text(error_message)

        current = self._get_required_by_client_order_id(client_order_id)
        if current.status == DbOrderStatus.FAILED:
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.FAILED,
            client_order_id=client_order_id,
        )
        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                error_code = ?,
                error_message = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (
                DbOrderStatus.FAILED.value,
                error_code,
                error_message,
                closed_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def sync_execution_summary(
        self,
        *,
        client_order_id: str,
        closed_at: str | None = None,
    ) -> OrderRow:
        """
        Recalculate filled_qty and avg_fill_price from executions.

        If executions exist:
        - unresolved orders become PARTIAL or FILLED
        - CANCELLED keeps CANCELLED and only refreshes its fill summary

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        if closed_at is not None:
            closed_at = require_aware_iso8601("closed_at", closed_at)

        current = self._get_required_by_client_order_id(client_order_id)
        summary = self._calculate_execution_summary(current.id)
        if summary.execution_count == 0:
            return current
        if summary.filled_qty > current.qty:
            raise RepositoryInvariantError(
                "Execution quantity exceeds order quantity: "
                f"client_order_id={client_order_id}, "
                f"filled_qty={summary.filled_qty}, order_qty={current.qty}"
            )

        if current.status == DbOrderStatus.CANCELLED:
            self._update_fill_summary(
                client_order_id=client_order_id,
                filled_qty=summary.filled_qty,
                avg_fill_price=summary.avg_fill_price,
                closed_at=current.closed_at,
            )
            return self._get_required_by_client_order_id(client_order_id)

        if current.status == DbOrderStatus.REJECTED:
            raise RepositoryInvariantError(
                "Rejected order cannot have executions: "
                f"client_order_id={client_order_id}"
            )
        if current.status == DbOrderStatus.FAILED:
            raise RepositoryInvariantError(
                "Failed order cannot have executions: "
                f"client_order_id={client_order_id}"
            )

        target_status = (
            DbOrderStatus.FILLED
            if summary.filled_qty == current.qty
            else DbOrderStatus.PARTIAL
        )
        assert_transition_allowed(
            current.status,
            target_status,
            client_order_id=client_order_id,
        )

        if target_status == DbOrderStatus.FILLED:
            final_closed_at = closed_at or current.closed_at
            if final_closed_at is None:
                raise ValueError(
                    "closed_at is required when executions fill the order."
                )
        else:
            final_closed_at = None

        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                filled_qty = ?,
                avg_fill_price = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (
                target_status.value,
                summary.filled_qty,
                summary.avg_fill_price,
                final_closed_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_filled(
        self,
        *,
        client_order_id: str,
        closed_at: str,
    ) -> OrderRow:
        """
        Mark an order as FILLED using executions as the source of truth.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        closed_at = require_aware_iso8601("closed_at", closed_at)

        current = self._get_required_by_client_order_id(client_order_id)
        summary = self._calculate_execution_summary(current.id)
        if summary.execution_count == 0:
            raise RepositoryInvariantError(
                "Cannot mark FILLED without executions: "
                f"client_order_id={client_order_id}"
            )
        if summary.filled_qty != current.qty:
            raise RepositoryInvariantError(
                "Filled quantity does not match order quantity: "
                f"client_order_id={client_order_id}, "
                f"filled_qty={summary.filled_qty}, order_qty={current.qty}"
            )

        if current.status == DbOrderStatus.FILLED:
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.FILLED,
            client_order_id=client_order_id,
        )
        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                filled_qty = ?,
                avg_fill_price = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (
                DbOrderStatus.FILLED.value,
                summary.filled_qty,
                summary.avg_fill_price,
                closed_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def mark_cancelled(
        self,
        *,
        client_order_id: str,
        closed_at: str,
    ) -> OrderRow:
        """
        Mark an order as CANCELLED and refresh its fill summary from executions.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        closed_at = require_aware_iso8601("closed_at", closed_at)

        current = self._get_required_by_client_order_id(client_order_id)
        if current.status == DbOrderStatus.CANCELLED:
            return current

        assert_transition_allowed(
            current.status,
            DbOrderStatus.CANCELLED,
            client_order_id=client_order_id,
        )
        summary = self._calculate_execution_summary(current.id)
        if summary.filled_qty > current.qty:
            raise RepositoryInvariantError(
                "Execution quantity exceeds order quantity on cancellation: "
                f"client_order_id={client_order_id}, "
                f"filled_qty={summary.filled_qty}, order_qty={current.qty}"
            )

        self._conn.execute(
            """
            UPDATE orders
            SET status = ?,
                filled_qty = ?,
                avg_fill_price = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (
                DbOrderStatus.CANCELLED.value,
                summary.filled_qty,
                summary.avg_fill_price,
                closed_at,
                client_order_id,
            ),
        )
        return self._get_required_by_client_order_id(client_order_id)

    def get_by_client_order_id(self, client_order_id: str) -> OrderRow | None:
        client_order_id = require_non_empty_text("client_order_id", client_order_id)
        row = self._conn.execute(
            """
            SELECT
                id,
                client_order_id,
                kis_order_no,
                symbol,
                side,
                qty,
                price,
                order_type,
                status,
                filled_qty,
                avg_fill_price,
                requested_at,
                submitted_at,
                closed_at,
                error_code,
                error_message,
                strategy_name
            FROM orders
            WHERE client_order_id = ?
            """,
            (client_order_id,),
        ).fetchone()
        return self._map_order_row(row)

    def list_by_kis_order_no(self, kis_order_no: str) -> list[OrderRow]:
        kis_order_no = require_non_empty_text("kis_order_no", kis_order_no)
        rows = self._conn.execute(
            """
            SELECT
                id,
                client_order_id,
                kis_order_no,
                symbol,
                side,
                qty,
                price,
                order_type,
                status,
                filled_qty,
                avg_fill_price,
                requested_at,
                submitted_at,
                closed_at,
                error_code,
                error_message,
                strategy_name
            FROM orders
            WHERE kis_order_no = ?
            ORDER BY requested_at ASC, id ASC
            """,
            (kis_order_no,),
        ).fetchall()
        return RowMapper.map_many(
            rows,
            OrderRow,
            converters={"status": coerce_db_order_status},
        )

    def get_by_kis_order_no(self, kis_order_no: str) -> OrderRow | None:
        rows = self.list_by_kis_order_no(kis_order_no)
        if not rows:
            return None
        if len(rows) > 1:
            raise RepositoryInvariantError(
                "Multiple orders share the same kis_order_no. "
                f"Use list_by_kis_order_no() instead: {kis_order_no!r}"
            )
        return rows[0]

    def find_unresolved(self) -> list[OrderRow]:
        statuses = tuple(status.value for status in UNRESOLVED_DB_ORDER_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"""
            SELECT
                id,
                client_order_id,
                kis_order_no,
                symbol,
                side,
                qty,
                price,
                order_type,
                status,
                filled_qty,
                avg_fill_price,
                requested_at,
                submitted_at,
                closed_at,
                error_code,
                error_message,
                strategy_name
            FROM orders
            WHERE status IN ({placeholders})
            ORDER BY requested_at ASC, id ASC
            """,
            statuses,
        ).fetchall()
        return RowMapper.map_many(
            rows,
            OrderRow,
            converters={"status": coerce_db_order_status},
        )

    def count_requested_for_trade_date(
        self,
        *,
        trade_date: str,
        side: str | None = None,
    ) -> int:
        day_start, day_end = _day_bounds_kst(trade_date)
        if side is None:
            row = self._conn.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE requested_at >= ? AND requested_at < ?
                """,
                (day_start, day_end),
            ).fetchone()
            return int(row[0]) if row is not None else 0

        normalized_side = require_side(side)
        row = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE requested_at >= ? AND requested_at < ?
              AND side = ?
            """,
            (day_start, day_end, normalized_side),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def _get_required_by_client_order_id(self, client_order_id: str) -> OrderRow:
        row = self.get_by_client_order_id(client_order_id)
        if row is None:
            raise RepositoryInvariantError(
                f"Order not found: client_order_id={client_order_id!r}"
            )
        return row

    def _map_order_row(self, row: sqlite3.Row | None) -> OrderRow | None:
        return RowMapper.map_one(
            row,
            OrderRow,
            converters={"status": coerce_db_order_status},
        )

    def _calculate_execution_summary(self, order_id: int) -> ExecutionSummary:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS execution_count,
                COALESCE(SUM(qty), 0) AS filled_qty,
                COALESCE(SUM(qty * price), 0) AS total_notional
            FROM executions
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            return ExecutionSummary(filled_qty=0, avg_fill_price=0, execution_count=0)

        execution_count = int(row["execution_count"])
        filled_qty = int(row["filled_qty"])
        total_notional = int(row["total_notional"])
        avg_fill_price = 0
        if filled_qty > 0:
            avg_fill_price = (total_notional + (filled_qty // 2)) // filled_qty

        return ExecutionSummary(
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            execution_count=execution_count,
        )

    def _update_fill_summary(
        self,
        *,
        client_order_id: str,
        filled_qty: int,
        avg_fill_price: int,
        closed_at: str | None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE orders
            SET filled_qty = ?,
                avg_fill_price = ?,
                closed_at = ?
            WHERE client_order_id = ?
            """,
            (filled_qty, avg_fill_price, closed_at, client_order_id),
        )
