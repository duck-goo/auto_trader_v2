"""Daily stats repository.

Per-trade-day operational statistics, recomputed from orders/executions
on demand. This repository is intentionally a recompute-only design:
no real-time counters are incremented anywhere. Call recompute_day()
after market close, or any time the underlying rows change.

Date semantics:
    trade_date is a KST calendar date in YYYY-MM-DD form.
    The day spans [YYYY-MM-DDT00:00:00+09:00, next-dayT00:00:00+09:00).
    All stored timestamps are expected to use the +09:00 offset.

All write methods must run inside `with transaction(conn):`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from storage.repositories.base import (
    RepositoryInvariantError,
    RowMapper,
    require_non_empty_text,
    require_write_transaction,
)

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class DailyStatsRow:
    trade_date: str
    realized_pnl: int
    order_count: int
    fill_count: int
    error_count: int


@dataclass
class _OpenLot:
    qty: int
    price: int


_SELECT_COLUMNS = "trade_date, realized_pnl, order_count, fill_count, error_count"


def _parse_trade_date(value: str) -> datetime:
    text = require_non_empty_text("trade_date", value)
    if not _DATE_PATTERN.match(text):
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}")
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date is not a valid date: {value!r}") from exc


def _day_bounds_kst(trade_date: str) -> tuple[str, str]:
    """Return [start, end) as ISO8601 strings with KST +09:00 offset."""
    day = _parse_trade_date(trade_date)
    next_day = day + timedelta(days=1)
    start = day.strftime("%Y-%m-%dT00:00:00+09:00")
    end = next_day.strftime("%Y-%m-%dT00:00:00+09:00")
    return start, end


class DailyStatsRepository:
    """Pure DB repository for daily statistics rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def calculate_day(self, trade_date: str) -> DailyStatsRow:
        """
        Return a fresh read-only snapshot for one trade day.

        realized_pnl is computed from execution history using FIFO matching.
        Only sell executions that happened within the requested trade day
        contribute to that day's realized_pnl, but their cost basis may come
        from prior-day buy executions.
        """
        _parse_trade_date(trade_date)
        day_start, day_end = _day_bounds_kst(trade_date)

        order_count = self._conn.execute(
            """
            SELECT COUNT(*) FROM orders
            WHERE requested_at >= ? AND requested_at < ?
            """,
            (day_start, day_end),
        ).fetchone()[0]

        error_count = self._conn.execute(
            """
            SELECT COUNT(*) FROM orders
            WHERE requested_at >= ? AND requested_at < ?
              AND status IN ('REJECTED', 'FAILED')
            """,
            (day_start, day_end),
        ).fetchone()[0]

        fill_count = self._conn.execute(
            """
            SELECT COUNT(*) FROM executions
            WHERE executed_at >= ? AND executed_at < ?
            """,
            (day_start, day_end),
        ).fetchone()[0]

        realized_pnl = self._calculate_realized_pnl(
            day_start=day_start,
            day_end=day_end,
        )

        return DailyStatsRow(
            trade_date=trade_date,
            realized_pnl=int(realized_pnl),
            order_count=int(order_count),
            fill_count=int(fill_count),
            error_count=int(error_count),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def recompute_day(self, trade_date: str) -> DailyStatsRow:
        """
        Recompute stats for a single trade day from orders/executions and
        UPSERT into daily_stats.

        This method must run inside `with transaction(conn):`.
        """
        require_write_transaction(self._conn)
        snapshot = self.calculate_day(trade_date)

        self._conn.execute(
            """
            INSERT INTO daily_stats (
                trade_date, realized_pnl, order_count, fill_count, error_count
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                realized_pnl = excluded.realized_pnl,
                order_count = excluded.order_count,
                fill_count = excluded.fill_count,
                error_count = excluded.error_count
            """,
            (
                snapshot.trade_date,
                snapshot.realized_pnl,
                snapshot.order_count,
                snapshot.fill_count,
                snapshot.error_count,
            ),
        )
        return self._get_required(snapshot.trade_date)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get(self, trade_date: str) -> DailyStatsRow | None:
        _parse_trade_date(trade_date)
        row = self._conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM daily_stats WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        return RowMapper.map_one(row, DailyStatsRow)

    def list_between(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> list[DailyStatsRow]:
        _parse_trade_date(start_date)
        _parse_trade_date(end_date)
        if start_date > end_date:
            raise ValueError(
                f"start_date must be <= end_date: {start_date!r} > {end_date!r}"
            )
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM daily_stats
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date ASC
            """,
            (start_date, end_date),
        ).fetchall()
        return RowMapper.map_many(rows, DailyStatsRow)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get_required(self, trade_date: str) -> DailyStatsRow:
        row = self.get(trade_date)
        if row is None:
            raise RepositoryInvariantError(
                f"daily_stats row expected but not found: "
                f"trade_date={trade_date!r}"
            )
        return row

    def _calculate_realized_pnl(
        self,
        *,
        day_start: str,
        day_end: str,
    ) -> int:
        rows = self._conn.execute(
            """
            SELECT id, symbol, side, qty, price, executed_at
            FROM executions
            WHERE executed_at < ?
            ORDER BY executed_at ASC, id ASC
            """,
            (day_end,),
        ).fetchall()

        open_lots_by_symbol: dict[str, list[_OpenLot]] = {}
        realized_pnl = 0

        for row in rows:
            symbol = require_non_empty_text("symbol", row["symbol"])
            side = require_non_empty_text("side", row["side"])
            qty = int(row["qty"])
            price = int(row["price"])
            executed_at = require_non_empty_text("executed_at", row["executed_at"])

            if side == "buy":
                lots = open_lots_by_symbol.setdefault(symbol, [])
                lots.append(_OpenLot(qty=qty, price=price))
                continue

            if side != "sell":
                raise RepositoryInvariantError(
                    "Unsupported execution side while computing realized_pnl: "
                    f"symbol={symbol}, side={side!r}, executed_at={executed_at}"
                )

            realized_pnl += self._match_sell_execution_fifo(
                symbol=symbol,
                sell_qty=qty,
                sell_price=price,
                executed_at=executed_at,
                day_start=day_start,
                day_end=day_end,
                open_lots_by_symbol=open_lots_by_symbol,
            )

        return realized_pnl

    def _match_sell_execution_fifo(
        self,
        *,
        symbol: str,
        sell_qty: int,
        sell_price: int,
        executed_at: str,
        day_start: str,
        day_end: str,
        open_lots_by_symbol: dict[str, list[_OpenLot]],
    ) -> int:
        lots = open_lots_by_symbol.setdefault(symbol, [])
        remaining_qty = sell_qty
        matched_realized_pnl = 0
        is_target_day_sell = day_start <= executed_at < day_end

        while remaining_qty > 0:
            if not lots:
                raise RepositoryInvariantError(
                    "Cannot compute FIFO realized_pnl because a sell execution "
                    "has no matching open buy lot: "
                    f"symbol={symbol}, executed_at={executed_at}, "
                    f"sell_qty={sell_qty}, remaining_qty={remaining_qty}"
                )

            current_lot = lots[0]
            matched_qty = min(remaining_qty, current_lot.qty)
            if is_target_day_sell:
                matched_realized_pnl += (sell_price - current_lot.price) * matched_qty

            current_lot.qty -= matched_qty
            remaining_qty -= matched_qty
            if current_lot.qty == 0:
                lots.pop(0)

        return matched_realized_pnl
