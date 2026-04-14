"""First-stage universe filter service."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from services.universe_refresh_service import UniverseRefreshItem


def _require_non_empty_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} cannot be empty.")
    return stripped


def _require_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer: {value!r}")
    return value


def _require_bool(name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool: {value!r}")
    return value


class UniverseRejectReason(str, enum.Enum):
    MANAGED = "MANAGED"
    INVESTMENT_WARNING = "INVESTMENT_WARNING"
    INVESTMENT_RISK = "INVESTMENT_RISK"
    ATTENTION_ISSUE = "ATTENTION_ISSUE"
    DISCLOSURE_VIOLATION = "DISCLOSURE_VIOLATION"
    LIQUIDATION_TRADE = "LIQUIDATION_TRADE"
    TRADING_HALT = "TRADING_HALT"
    RIGHTS_EX_DATE = "RIGHTS_EX_DATE"
    PREFERRED_STOCK = "PREFERRED_STOCK"
    ETF = "ETF"
    ETN = "ETN"
    SPAC = "SPAC"
    PRICE_BELOW_MIN = "PRICE_BELOW_MIN"
    PRICE_ABOVE_MAX = "PRICE_ABOVE_MAX"
    AVG_TRADE_VALUE_20_BELOW_MIN = "AVG_TRADE_VALUE_20_BELOW_MIN"


@dataclass(frozen=True)
class UniverseFilterSettings:
    min_price: int = 5_000
    max_price: int = 200_000
    min_avg_trade_value_20: int = 100_000_000


@dataclass(frozen=True)
class UniverseFilterInput:
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


@dataclass(frozen=True)
class UniverseRejectedItem:
    item: UniverseFilterInput
    reasons: tuple[UniverseRejectReason, ...]


@dataclass(frozen=True)
class UniverseFilterResult:
    settings: UniverseFilterSettings
    total_count: int
    accepted_count: int
    rejected_count: int
    accepted_items: tuple[UniverseFilterInput, ...]
    refresh_items: tuple[UniverseRefreshItem, ...]
    rejected_items: tuple[UniverseRejectedItem, ...]


class UniverseFilterService:
    """Apply first-stage filter rules to raw universe inputs."""

    def filter_candidates(
        self,
        *,
        items: Sequence[UniverseFilterInput],
        settings: UniverseFilterSettings,
    ) -> UniverseFilterResult:
        if not isinstance(settings, UniverseFilterSettings):
            raise ValueError(
                "settings must be a UniverseFilterSettings instance."
            )

        normalized_settings = self._normalize_settings(settings)
        accepted_items: list[UniverseFilterInput] = []
        refresh_items: list[UniverseRefreshItem] = []
        rejected_items: list[UniverseRejectedItem] = []

        seen_symbols: set[str] = set()

        for item in items:
            if not isinstance(item, UniverseFilterInput):
                raise ValueError(
                    "items must contain only UniverseFilterInput instances."
                )

            normalized_item = self._normalize_item(item)

            if normalized_item.symbol in seen_symbols:
                raise ValueError(
                    f"Duplicate symbol in filter input: {normalized_item.symbol!r}"
                )
            seen_symbols.add(normalized_item.symbol)

            reasons = self._collect_reasons(
                item=normalized_item,
                settings=normalized_settings,
            )

            if reasons:
                rejected_items.append(
                    UniverseRejectedItem(
                        item=normalized_item,
                        reasons=tuple(reasons),
                    )
                )
                continue

            accepted_items.append(normalized_item)
            refresh_items.append(
                UniverseRefreshItem(
                    symbol=normalized_item.symbol,
                    name=normalized_item.name,
                    market=normalized_item.market,
                    close_price=normalized_item.close_price,
                    prev_day_trade_value=normalized_item.prev_day_trade_value,
                )
            )

        return UniverseFilterResult(
            settings=normalized_settings,
            total_count=len(items),
            accepted_count=len(accepted_items),
            rejected_count=len(rejected_items),
            accepted_items=tuple(accepted_items),
            refresh_items=tuple(refresh_items),
            rejected_items=tuple(rejected_items),
        )

    def _normalize_settings(
        self,
        settings: UniverseFilterSettings,
    ) -> UniverseFilterSettings:
        min_price = _require_non_negative_int("min_price", settings.min_price)
        max_price = _require_non_negative_int("max_price", settings.max_price)
        min_avg_trade_value_20 = _require_non_negative_int(
            "min_avg_trade_value_20",
            settings.min_avg_trade_value_20,
        )

        if min_price > max_price:
            raise ValueError(
                f"max_price must be >= min_price: "
                f"min={min_price}, max={max_price}"
            )

        return UniverseFilterSettings(
            min_price=min_price,
            max_price=max_price,
            min_avg_trade_value_20=min_avg_trade_value_20,
        )

    def _normalize_item(
        self,
        item: UniverseFilterInput,
    ) -> UniverseFilterInput:
        return UniverseFilterInput(
            symbol=_require_non_empty_text("symbol", item.symbol),
            name=_require_non_empty_text("name", item.name),
            market=_require_non_empty_text("market", item.market),
            close_price=_require_non_negative_int(
                "close_price",
                item.close_price,
            ),
            prev_day_trade_value=_require_non_negative_int(
                "prev_day_trade_value",
                item.prev_day_trade_value,
            ),
            avg_trade_value_20=_require_non_negative_int(
                "avg_trade_value_20",
                item.avg_trade_value_20,
            ),
            is_managed=_require_bool("is_managed", item.is_managed),
            is_investment_warning=_require_bool(
                "is_investment_warning",
                item.is_investment_warning,
            ),
            is_investment_risk=_require_bool(
                "is_investment_risk",
                item.is_investment_risk,
            ),
            is_attention_issue=_require_bool(
                "is_attention_issue",
                item.is_attention_issue,
            ),
            is_disclosure_violation=_require_bool(
                "is_disclosure_violation",
                item.is_disclosure_violation,
            ),
            is_liquidation_trade=_require_bool(
                "is_liquidation_trade",
                item.is_liquidation_trade,
            ),
            is_trading_halt=_require_bool(
                "is_trading_halt",
                item.is_trading_halt,
            ),
            is_rights_ex_date=_require_bool(
                "is_rights_ex_date",
                item.is_rights_ex_date,
            ),
            is_preferred_stock=_require_bool(
                "is_preferred_stock",
                item.is_preferred_stock,
            ),
            is_etf=_require_bool("is_etf", item.is_etf),
            is_etn=_require_bool("is_etn", item.is_etn),
            is_spac=_require_bool("is_spac", item.is_spac),
        )

    def _collect_reasons(
        self,
        *,
        item: UniverseFilterInput,
        settings: UniverseFilterSettings,
    ) -> list[UniverseRejectReason]:
        reasons: list[UniverseRejectReason] = []

        if item.is_managed:
            reasons.append(UniverseRejectReason.MANAGED)
        if item.is_investment_warning:
            reasons.append(UniverseRejectReason.INVESTMENT_WARNING)
        if item.is_investment_risk:
            reasons.append(UniverseRejectReason.INVESTMENT_RISK)
        if item.is_attention_issue:
            reasons.append(UniverseRejectReason.ATTENTION_ISSUE)
        if item.is_disclosure_violation:
            reasons.append(UniverseRejectReason.DISCLOSURE_VIOLATION)
        if item.is_liquidation_trade:
            reasons.append(UniverseRejectReason.LIQUIDATION_TRADE)
        if item.is_trading_halt:
            reasons.append(UniverseRejectReason.TRADING_HALT)
        if item.is_rights_ex_date:
            reasons.append(UniverseRejectReason.RIGHTS_EX_DATE)
        if item.is_preferred_stock:
            reasons.append(UniverseRejectReason.PREFERRED_STOCK)
        if item.is_etf:
            reasons.append(UniverseRejectReason.ETF)
        if item.is_etn:
            reasons.append(UniverseRejectReason.ETN)
        if item.is_spac:
            reasons.append(UniverseRejectReason.SPAC)

        if item.close_price < settings.min_price:
            reasons.append(UniverseRejectReason.PRICE_BELOW_MIN)
        if item.close_price > settings.max_price:
            reasons.append(UniverseRejectReason.PRICE_ABOVE_MAX)
        if item.avg_trade_value_20 < settings.min_avg_trade_value_20:
            reasons.append(UniverseRejectReason.AVG_TRADE_VALUE_20_BELOW_MIN)

        return reasons
