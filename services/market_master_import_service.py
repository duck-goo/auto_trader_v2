"""Market master import service."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from market import UniverseMasterItem, load_universe_master_items
from services.market_master_refresh_service import (
    MarketMasterRefreshItem,
    MarketMasterRefreshResult,
    MarketMasterRefreshService,
)
from storage.repositories import MarketMasterRepository


def _to_refresh_items(
    items: Sequence[UniverseMasterItem],
) -> list[MarketMasterRefreshItem]:
    return [
        MarketMasterRefreshItem(
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
        for item in items
    ]


@dataclass(frozen=True)
class MarketMasterImportRequest:
    path: Path
    source_format: str = "auto"


class MarketMasterImportService:
    """Load a market master file and persist the current snapshot."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        market_master_repo: MarketMasterRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._market_master_repo = market_master_repo
        self._now_fn = now_fn

    def import_from_file(
        self,
        *,
        path: str | Path,
        source_format: str = "auto",
    ) -> MarketMasterRefreshResult:
        request = self._normalize_request(
            path=path,
            source_format=source_format,
        )
        items = load_universe_master_items(
            request.path,
            source_format=request.source_format,
        )
        return self.import_items(items=items)

    def import_items(
        self,
        *,
        items: Sequence[UniverseMasterItem],
    ) -> MarketMasterRefreshResult:
        return MarketMasterRefreshService(
            conn=self._conn,
            market_master_repo=self._market_master_repo,
            now_fn=self._now_fn,
        ).refresh_snapshot(items=_to_refresh_items(items))

    @staticmethod
    def _normalize_request(
        *,
        path: str | Path,
        source_format: str,
    ) -> MarketMasterImportRequest:
        resolved_path = Path(path)
        if not resolved_path.exists():
            raise ValueError(f"Market master input file not found: {resolved_path}")
        return MarketMasterImportRequest(
            path=resolved_path,
            source_format=source_format,
        )
