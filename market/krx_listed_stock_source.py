"""KRX listed-stock master source."""

from __future__ import annotations

import re
from typing import Any

import requests

from market.universe_master import (
    UniverseMasterItem,
    UniverseMasterSourceInterface,
)

_KRX_FINDER_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_KRX_REFERER = (
    "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/"
    "index.cmd?menuId=MDC0201020101"
)
_MARKET_CODE_MAP = {
    "STK": "KOSPI",
    "KSQ": "KOSDAQ",
}
_DEFAULT_MARKETS = frozenset(_MARKET_CODE_MAP.values())
_PREFERRED_STOCK_PATTERN = re.compile(r"(?:\d?\uc6b0[A-Z]?$|\uc6b0$)")


def infer_krx_preferred_stock(name: str) -> bool:
    """Infer Korean preferred stocks from common KRX display-name suffixes."""
    if not isinstance(name, str):
        raise ValueError(f"name must be a string: {name!r}")
    normalized_name = name.strip().upper().replace(" ", "")
    if not normalized_name:
        return False
    return bool(_PREFERRED_STOCK_PATTERN.search(normalized_name))


def infer_krx_spac(name: str) -> bool:
    """Infer SPAC issues from common KRX display names."""
    if not isinstance(name, str):
        raise ValueError(f"name must be a string: {name!r}")
    normalized_name = name.strip().upper().replace(" ", "")
    return (
        "SPAC" in normalized_name
        or "\uc2a4\ud329" in normalized_name
        or "\uae30\uc5c5\uc778\uc218\ubaa9\uc801" in normalized_name
    )


def _normalize_markets(markets: set[str] | None) -> frozenset[str]:
    if markets is None:
        return _DEFAULT_MARKETS
    normalized: set[str] = set()
    for market in markets:
        if not isinstance(market, str):
            raise ValueError(f"markets must contain only strings: {market!r}")
        value = market.strip().upper()
        if not value:
            raise ValueError("markets cannot contain empty values.")
        normalized.add(value)
    return frozenset(normalized)


def _require_text(raw: dict[str, Any], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"KRX row {index} field {key!r} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"KRX row {index} field {key!r} cannot be empty.")
    return normalized


class KrxListedStockSource(UniverseMasterSourceInterface):
    """
    Load KOSPI/KOSDAQ listed stock names from the public KRX issue finder.

    The finder returns code, name, and market only. Status flags such as
    administrative issue or trading halt require separate data sources, so this
    source sets those flags to False and infers only preferred stocks and SPACs.
    """

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 20.0,
        markets: set[str] | None = None,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds
        self._markets = _normalize_markets(markets)

    def load(self) -> list[UniverseMasterItem]:
        response = self._session.post(
            _KRX_FINDER_URL,
            data={
                "locale": "ko_KR",
                "mktsel": "ALL",
                "searchText": "",
                "typeNo": "0",
                "bld": "dbms/comm/finder/finder_stkisu",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": _KRX_REFERER,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("KRX listed-stock response must be a JSON object.")

        rows = payload.get("block1")
        if not isinstance(rows, list):
            raise ValueError("KRX listed-stock response is missing block1 list.")

        items: list[UniverseMasterItem] = []
        seen_symbols: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"KRX row {index} must be a JSON object.")

            raw_market_code = _require_text(row, "marketCode", index).upper()
            market = _MARKET_CODE_MAP.get(raw_market_code)
            if market is None or market not in self._markets:
                continue

            symbol = _require_text(row, "short_code", index)
            name = _require_text(row, "codeName", index)
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            if symbol in seen_symbols:
                raise ValueError(f"Duplicate KRX listed symbol: {symbol!r}")
            seen_symbols.add(symbol)

            items.append(
                UniverseMasterItem(
                    symbol=symbol,
                    name=name,
                    market=market,
                    is_preferred_stock=infer_krx_preferred_stock(name),
                    is_spac=infer_krx_spac(name),
                )
            )

        if not items:
            raise ValueError("KRX listed-stock response produced no master items.")
        return items
