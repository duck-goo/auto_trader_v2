"""Market master refresh service."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from storage.db import transaction
from storage.repositories import (
    MarketMasterEntry,
    MarketMasterRepository,
    MarketMasterRow,
)

_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class MarketMasterRefreshItem:
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
class MarketMasterRefreshResult:
    refreshed_at: str
    symbol_count: int
    rows: tuple[MarketMasterRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class MarketMasterRefreshService:
    """Validate and persist the current market master snapshot."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        market_master_repo: MarketMasterRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._market_master_repo = market_master_repo
        self._now_fn = now_fn or _default_now

    def refresh_snapshot(
        self,
        *,
        items: Sequence[MarketMasterRefreshItem],
        refreshed_at: str | None = None,
    ) -> MarketMasterRefreshResult:
        if refreshed_at is None:
            refreshed_at = self._now_fn().isoformat()

        repo_entries: list[MarketMasterEntry] = []
        for item in items:
            if not isinstance(item, MarketMasterRefreshItem):
                raise ValueError(
                    "items must contain only MarketMasterRefreshItem instances."
                )
            repo_entries.append(
                MarketMasterEntry(
                    symbol=item.symbol,
                    name=item.name,
                    market=item.market,
                    is_managed=item.is_managed,
                    is_investment_warning=item.is_investment_warning,
                    is_investment_risk=item.is_investment_risk,
                    is_attention_issue=item.is_attention_issue,
                    is_disclosure_violation=item.is_disclosure_violation,
                    is_liquidation_trade=item.is_liquidation_trade,
                    is_trading_halt=item.is_trading_halt,
                    is_rights_ex_date=item.is_rights_ex_date,
                    is_preferred_stock=item.is_preferred_stock,
                    is_etf=item.is_etf,
                    is_etn=item.is_etn,
                    is_spac=item.is_spac,
                )
            )

        with transaction(self._conn):
            rows = self._market_master_repo.replace_all(
                entries=repo_entries,
                refreshed_at=refreshed_at,
            )

        return MarketMasterRefreshResult(
            refreshed_at=refreshed_at,
            symbol_count=len(rows),
            rows=tuple(rows),
        )
