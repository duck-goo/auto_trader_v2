"""Universe source abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseSourceItem:
    symbol: str
    name: str
    market: str
    close_price: int
    prev_day_trade_value: int
    avg_trade_value_20: int
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


class UniverseSourceInterface(ABC):
    """Load raw universe items from one source."""

    @abstractmethod
    def load(self) -> list[UniverseSourceItem]:
        raise NotImplementedError
