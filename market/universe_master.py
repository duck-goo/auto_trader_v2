"""Universe master definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseMasterItem:
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


class UniverseMasterSourceInterface(ABC):
    """Load symbol master items for universe building."""

    @abstractmethod
    def load(self) -> list[UniverseMasterItem]:
        raise NotImplementedError
