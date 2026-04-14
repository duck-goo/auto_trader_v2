"""JSON-based universe master source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market.universe_master import (
    UniverseMasterItem,
    UniverseMasterSourceInterface,
)


def _require_key(raw: dict[str, Any], key: str, index: int) -> Any:
    if key not in raw:
        raise ValueError(f"Item {index} is missing key: {key!r}")
    return raw[key]


def _parse_optional_bool(raw: dict[str, Any], key: str, index: int) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(
            f"Item {index} field {key!r} must be a bool: {value!r}"
        )
    return value


class JsonUniverseMasterSource(UniverseMasterSourceInterface):
    """Load universe master items from a local JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> list[UniverseMasterItem]:
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, list):
            raise ValueError("Input JSON must be a list.")

        items: list[UniverseMasterItem] = []
        seen_symbols: set[str] = set()
        for index, raw in enumerate(payload):
            if not isinstance(raw, dict):
                raise ValueError(f"Item {index} must be an object.")

            symbol = str(_require_key(raw, "symbol", index))
            if symbol in seen_symbols:
                raise ValueError(f"Duplicate symbol in master input: {symbol!r}")
            seen_symbols.add(symbol)

            items.append(
                UniverseMasterItem(
                    symbol=symbol,
                    name=str(_require_key(raw, "name", index)),
                    market=str(_require_key(raw, "market", index)),
                    is_managed=_parse_optional_bool(raw, "is_managed", index),
                    is_investment_warning=_parse_optional_bool(
                        raw,
                        "is_investment_warning",
                        index,
                    ),
                    is_investment_risk=_parse_optional_bool(
                        raw,
                        "is_investment_risk",
                        index,
                    ),
                    is_attention_issue=_parse_optional_bool(
                        raw,
                        "is_attention_issue",
                        index,
                    ),
                    is_disclosure_violation=_parse_optional_bool(
                        raw,
                        "is_disclosure_violation",
                        index,
                    ),
                    is_liquidation_trade=_parse_optional_bool(
                        raw,
                        "is_liquidation_trade",
                        index,
                    ),
                    is_trading_halt=_parse_optional_bool(
                        raw,
                        "is_trading_halt",
                        index,
                    ),
                    is_rights_ex_date=_parse_optional_bool(
                        raw,
                        "is_rights_ex_date",
                        index,
                    ),
                    is_preferred_stock=_parse_optional_bool(
                        raw,
                        "is_preferred_stock",
                        index,
                    ),
                    is_etf=_parse_optional_bool(raw, "is_etf", index),
                    is_etn=_parse_optional_bool(raw, "is_etn", index),
                    is_spac=_parse_optional_bool(raw, "is_spac", index),
                )
            )

        return items
