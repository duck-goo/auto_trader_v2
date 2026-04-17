"""Market master validation service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from market import UniverseMasterItem

_FLAG_FIELDS = (
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
)


@dataclass(frozen=True)
class MarketMasterValidationCount:
    name: str
    count: int


@dataclass(frozen=True)
class MarketMasterValidationResult:
    total_count: int
    is_valid: bool
    market_counts: tuple[MarketMasterValidationCount, ...]
    flag_counts: tuple[MarketMasterValidationCount, ...]
    warnings: tuple[str, ...]


def _normalize_optional_positive_int(
    name: str,
    value: int | None,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer or None: {value!r}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1 when provided: {value!r}")
    return value


def _normalize_required_markets(
    values: Sequence[str] | None,
) -> tuple[str, ...]:
    if values is None:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(
                f"required_markets must contain only strings: {value!r}"
            )
        market = value.strip().upper()
        if not market:
            raise ValueError("required_markets cannot contain empty strings.")
        if market in seen:
            continue
        seen.add(market)
        normalized.append(market)
    return tuple(normalized)


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be empty.")
    return normalized


class MarketMasterValidationService:
    """Validate loaded market master items before persistence."""

    def validate_items(
        self,
        *,
        items: Sequence[UniverseMasterItem],
        min_symbol_count: int | None = None,
        required_markets: Sequence[str] | None = None,
    ) -> MarketMasterValidationResult:
        min_symbol_count = _normalize_optional_positive_int(
            "min_symbol_count",
            min_symbol_count,
        )
        normalized_required_markets = _normalize_required_markets(
            required_markets
        )

        market_counter: dict[str, int] = {}
        flag_counter = {field: 0 for field in _FLAG_FIELDS}
        seen_symbols: set[str] = set()

        for item in items:
            if not isinstance(item, UniverseMasterItem):
                raise ValueError(
                    "items must contain only UniverseMasterItem instances."
                )

            symbol = _require_text("symbol", item.symbol)
            _require_text("name", item.name)
            market = _require_text("market", item.market).upper()

            if symbol in seen_symbols:
                raise ValueError(
                    f"Duplicate symbol in market master validation: {symbol!r}"
                )
            seen_symbols.add(symbol)

            market_counter[market] = market_counter.get(market, 0) + 1
            for field in _FLAG_FIELDS:
                if getattr(item, field):
                    flag_counter[field] += 1

        warnings: list[str] = []
        total_count = len(items)

        if min_symbol_count is not None and total_count < min_symbol_count:
            warnings.append(
                "market master item count is below minimum: "
                f"actual={total_count}, minimum={min_symbol_count}"
            )

        missing_markets = [
            market
            for market in normalized_required_markets
            if market not in market_counter
        ]
        if missing_markets:
            warnings.append(
                "required markets are missing: "
                + ", ".join(missing_markets)
            )

        market_counts = tuple(
            MarketMasterValidationCount(name=name, count=count)
            for name, count in sorted(market_counter.items())
        )
        flag_counts = tuple(
            MarketMasterValidationCount(name=name, count=count)
            for name, count in flag_counter.items()
        )

        return MarketMasterValidationResult(
            total_count=total_count,
            is_valid=not warnings,
            market_counts=market_counts,
            flag_counts=flag_counts,
            warnings=tuple(warnings),
        )
