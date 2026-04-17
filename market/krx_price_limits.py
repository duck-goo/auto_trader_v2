"""KRX stock-market price limit helpers."""

from __future__ import annotations


_SUPPORTED_MARKETS = {"KOSPI", "KOSDAQ"}
_PRICE_LIMIT_NUMERATOR = 3
_PRICE_LIMIT_DENOMINATOR = 10


def _normalize_market(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"market must be a string: {value!r}")
    normalized = value.strip().upper()
    if normalized not in _SUPPORTED_MARKETS:
        raise ValueError(
            "market must be one of "
            f"{sorted(_SUPPORTED_MARKETS)}: {value!r}"
        )
    return normalized


def _require_positive_price(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def get_krx_tick_size(*, market: str, price: int) -> int:
    """
    Return the stock tick size for KOSPI/KOSDAQ common stocks.

    Source basis:
    - KRX Guide to Trading in the Korean Stock Market
    - KRX KOSDAQ market daily price limit example page
    """

    normalized_market = _normalize_market(market)
    normalized_price = _require_positive_price("price", price)

    if normalized_price < 1_000:
        return 1
    if normalized_price < 5_000:
        return 5
    if normalized_price < 10_000:
        return 10
    if normalized_price < 50_000:
        return 50
    if normalized_price < 200_000:
        return 100
    if normalized_price < 500_000:
        return 500
    return 1_000


def calculate_krx_price_limit_amount(*, market: str, base_price: int) -> int:
    """
    Calculate the daily price-limit amount from the base price.

    KRX currently uses +/-30% and drops any remainder smaller than the
    tick size that corresponds to the base price.
    """

    normalized_base_price = _require_positive_price("base_price", base_price)
    tick_size = get_krx_tick_size(
        market=market,
        price=normalized_base_price,
    )
    raw_limit = (
        normalized_base_price * _PRICE_LIMIT_NUMERATOR
    ) // _PRICE_LIMIT_DENOMINATOR
    return (raw_limit // tick_size) * tick_size


def calculate_krx_upper_price_limit(*, market: str, base_price: int) -> int:
    normalized_base_price = _require_positive_price("base_price", base_price)
    return normalized_base_price + calculate_krx_price_limit_amount(
        market=market,
        base_price=normalized_base_price,
    )
