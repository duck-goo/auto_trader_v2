"""Market master repository."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from storage.repositories.base import (
    RepositoryInvariantError,
    RowMapper,
    require_aware_iso8601,
    require_non_empty_text,
    require_write_transaction,
)


def _require_bool(name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool: {value!r}")
    return value


def _int_to_bool(value: int) -> bool:
    if value not in (0, 1):
        raise RepositoryInvariantError(
            f"Boolean column must be 0 or 1, got {value!r}."
        )
    return bool(value)


@dataclass(frozen=True)
class MarketMasterEntry:
    symbol: str
    name: str
    market: str
    is_managed: bool = False
    is_investment_warning: bool = False
    is_investment_risk: bool = False
    is_attention_issue: bool = False
    is_disclosure_violation: bool = False
    is_liquidation_trade: bool = False
    is_trading_halt: bool = False
    is_rights_ex_date: bool = False
    is_preferred_stock: bool = False
    is_etf: bool = False
    is_etn: bool = False
    is_spac: bool = False


@dataclass(frozen=True)
class MarketMasterRow:
    symbol: str
    name: str
    market: str
    is_managed: bool
    is_investment_warning: bool
    is_investment_risk: bool
    is_attention_issue: bool
    is_disclosure_violation: bool
    is_liquidation_trade: bool
    is_trading_halt: bool
    is_rights_ex_date: bool
    is_preferred_stock: bool
    is_etf: bool
    is_etn: bool
    is_spac: bool
    refreshed_at: str


_SELECT_COLUMNS = (
    "symbol, name, market, "
    "is_managed, is_investment_warning, is_investment_risk, "
    "is_attention_issue, is_disclosure_violation, is_liquidation_trade, "
    "is_trading_halt, is_rights_ex_date, is_preferred_stock, "
    "is_etf, is_etn, is_spac, refreshed_at"
)

_BOOL_COLUMNS = {
    "is_managed",
    "is_investment_warning",
    "is_investment_risk",
    "is_attention_issue",
    "is_disclosure_violation",
    "is_liquidation_trade",
    "is_trading_halt",
    "is_rights_ex_date",
    "is_preferred_stock",
    "is_etf",
    "is_etn",
    "is_spac",
}


class MarketMasterRepository:
    """Pure DB repository for the current market master snapshot."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_all(
        self,
        *,
        entries: Sequence[MarketMasterEntry],
        refreshed_at: str,
    ) -> list[MarketMasterRow]:
        require_write_transaction(self._conn)
        refreshed_at = require_aware_iso8601("refreshed_at", refreshed_at)

        normalized: list[MarketMasterEntry] = []
        seen_symbols: set[str] = set()

        for entry in entries:
            if not isinstance(entry, MarketMasterEntry):
                raise ValueError(
                    "entries must contain only MarketMasterEntry instances."
                )

            symbol = require_non_empty_text("symbol", entry.symbol)
            name = require_non_empty_text("name", entry.name)
            market = require_non_empty_text("market", entry.market)

            if symbol in seen_symbols:
                raise ValueError(f"Duplicate symbol in market master: {symbol!r}")
            seen_symbols.add(symbol)

            normalized.append(
                MarketMasterEntry(
                    symbol=symbol,
                    name=name,
                    market=market,
                    is_managed=_require_bool("is_managed", entry.is_managed),
                    is_investment_warning=_require_bool(
                        "is_investment_warning",
                        entry.is_investment_warning,
                    ),
                    is_investment_risk=_require_bool(
                        "is_investment_risk",
                        entry.is_investment_risk,
                    ),
                    is_attention_issue=_require_bool(
                        "is_attention_issue",
                        entry.is_attention_issue,
                    ),
                    is_disclosure_violation=_require_bool(
                        "is_disclosure_violation",
                        entry.is_disclosure_violation,
                    ),
                    is_liquidation_trade=_require_bool(
                        "is_liquidation_trade",
                        entry.is_liquidation_trade,
                    ),
                    is_trading_halt=_require_bool(
                        "is_trading_halt",
                        entry.is_trading_halt,
                    ),
                    is_rights_ex_date=_require_bool(
                        "is_rights_ex_date",
                        entry.is_rights_ex_date,
                    ),
                    is_preferred_stock=_require_bool(
                        "is_preferred_stock",
                        entry.is_preferred_stock,
                    ),
                    is_etf=_require_bool("is_etf", entry.is_etf),
                    is_etn=_require_bool("is_etn", entry.is_etn),
                    is_spac=_require_bool("is_spac", entry.is_spac),
                )
            )

        self._conn.execute("DELETE FROM market_master")

        for entry in normalized:
            self._conn.execute(
                """
                INSERT INTO market_master (
                    symbol,
                    name,
                    market,
                    is_managed,
                    is_investment_warning,
                    is_investment_risk,
                    is_attention_issue,
                    is_disclosure_violation,
                    is_liquidation_trade,
                    is_trading_halt,
                    is_rights_ex_date,
                    is_preferred_stock,
                    is_etf,
                    is_etn,
                    is_spac,
                    refreshed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.symbol,
                    entry.name,
                    entry.market,
                    int(entry.is_managed),
                    int(entry.is_investment_warning),
                    int(entry.is_investment_risk),
                    int(entry.is_attention_issue),
                    int(entry.is_disclosure_violation),
                    int(entry.is_liquidation_trade),
                    int(entry.is_trading_halt),
                    int(entry.is_rights_ex_date),
                    int(entry.is_preferred_stock),
                    int(entry.is_etf),
                    int(entry.is_etn),
                    int(entry.is_spac),
                    refreshed_at,
                ),
            )

        return self.list_all()

    def get(self, *, symbol: str) -> MarketMasterRow | None:
        symbol = require_non_empty_text("symbol", symbol)
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM market_master
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()
        return RowMapper.map_one(
            row,
            MarketMasterRow,
            converters={column: _int_to_bool for column in _BOOL_COLUMNS},
        )

    def list_all(self) -> list[MarketMasterRow]:
        rows = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM market_master
            ORDER BY symbol ASC
            """
        ).fetchall()
        return RowMapper.map_many(
            rows,
            MarketMasterRow,
            converters={column: _int_to_bool for column in _BOOL_COLUMNS},
        )
