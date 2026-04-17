"""CSV-based universe master source."""

from __future__ import annotations

import csv
from pathlib import Path

from market.universe_master import (
    UniverseMasterItem,
    UniverseMasterSourceInterface,
)

_ENCODINGS = ("utf-8-sig", "cp949", "utf-8")

_REQUIRED_HEADER_ALIASES = {
    "symbol": (
        "symbol",
        "code",
        "stock_code",
        "\uc885\ubaa9\ucf54\ub4dc",
        "\ub2e8\ucd95\ucf54\ub4dc",
    ),
    "name": (
        "name",
        "stock_name",
        "\uc885\ubaa9\uba85",
        "\ud55c\uae00\uba85",
    ),
    "market": (
        "market",
        "market_type",
        "\uc2dc\uc7a5",
        "\uc2dc\uc7a5\uad6c\ubd84",
        "\uc2dc\uc7a5\uad6c\ubd84\uba85",
    ),
}

_OPTIONAL_BOOL_HEADER_ALIASES = {
    "is_managed": ("is_managed", "\uad00\ub9ac\uc885\ubaa9"),
    "is_investment_warning": (
        "is_investment_warning",
        "\ud22c\uc790\uacbd\uace0",
    ),
    "is_investment_risk": (
        "is_investment_risk",
        "\ud22c\uc790\uc704\ud5d8",
    ),
    "is_attention_issue": (
        "is_attention_issue",
        "\ud658\uae30\uc885\ubaa9",
    ),
    "is_disclosure_violation": (
        "is_disclosure_violation",
        "\ubd88\uc131\uc2e4\uacf5\uc2dc",
    ),
    "is_liquidation_trade": (
        "is_liquidation_trade",
        "\uc815\ub9ac\ub9e4\ub9e4",
    ),
    "is_trading_halt": ("is_trading_halt", "\uac70\ub798\uc815\uc9c0"),
    "is_rights_ex_date": ("is_rights_ex_date", "\uad8c\ub9ac\ub77d"),
    "is_preferred_stock": (
        "is_preferred_stock",
        "\uc6b0\uc120\uc8fc",
    ),
    "is_etf": ("is_etf", "ETF"),
    "is_etn": ("is_etn", "ETN"),
    "is_spac": ("is_spac", "SPAC", "\uc2a4\ud329"),
}

_TRUE_VALUES = {"1", "true", "t", "y", "yes", "\uc608", "\ub124"}
_FALSE_VALUES = {
    "",
    "0",
    "false",
    "f",
    "n",
    "no",
    "\uc544\ub2c8\uc624",
    "\uc544\ub2c8\uc694",
}


def _normalize_header(value: str) -> str:
    return value.strip()


def _read_text_with_fallback(path: Path) -> str:
    last_error: UnicodeDecodeError | None = None
    for encoding in _ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc

    raise ValueError(
        f"Could not decode CSV with supported encodings {_ENCODINGS!r}: {path}"
    ) from last_error


def _detect_dialect(text: str) -> csv.Dialect:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return csv.get_dialect("excel")


def _parse_bool(
    *,
    raw_value: str | None,
    canonical_name: str,
    row_number: int,
) -> bool:
    if raw_value is None:
        return False

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    raise ValueError(
        f"Row {row_number} field {canonical_name!r} must be a boolean-like "
        f"value: {raw_value!r}"
    )


def _resolve_header(
    fieldnames: list[str],
    *,
    aliases: tuple[str, ...],
) -> str | None:
    normalized_map = {
        _normalize_header(fieldname): fieldname
        for fieldname in fieldnames
    }
    for alias in aliases:
        normalized_alias = _normalize_header(alias)
        if normalized_alias in normalized_map:
            return normalized_map[normalized_alias]
    return None


def _is_empty_row(raw_row: dict[str, str | None]) -> bool:
    return all(
        value is None or not str(value).strip()
        for value in raw_row.values()
    )


def _require_text_cell(
    *,
    raw_row: dict[str, str | None],
    header_name: str,
    canonical_name: str,
    row_number: int,
) -> str:
    raw_value = raw_row.get(header_name)
    value = "" if raw_value is None else str(raw_value).strip()
    if not value:
        raise ValueError(
            f"Row {row_number} field {canonical_name!r} cannot be empty."
        )
    return value


class CsvUniverseMasterSource(UniverseMasterSourceInterface):
    """Load universe master items from a local CSV file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> list[UniverseMasterItem]:
        text = _read_text_with_fallback(self._path)
        dialect = _detect_dialect(text)
        reader = csv.DictReader(text.splitlines(), dialect=dialect)

        if reader.fieldnames is None:
            raise ValueError("CSV header row is missing.")

        required_headers = {
            canonical_name: _resolve_header(
                reader.fieldnames,
                aliases=aliases,
            )
            for canonical_name, aliases in _REQUIRED_HEADER_ALIASES.items()
        }
        missing_required_headers = [
            canonical_name
            for canonical_name, header in required_headers.items()
            if header is None
        ]
        if missing_required_headers:
            missing_text = ", ".join(sorted(missing_required_headers))
            raise ValueError(f"CSV is missing required columns: {missing_text}")

        optional_headers = {
            canonical_name: _resolve_header(
                reader.fieldnames,
                aliases=aliases,
            )
            for canonical_name, aliases in _OPTIONAL_BOOL_HEADER_ALIASES.items()
        }

        items: list[UniverseMasterItem] = []
        seen_symbols: set[str] = set()
        for row_number, raw_row in enumerate(reader, start=2):
            if raw_row is None:
                raise ValueError(f"Row {row_number} must be an object.")
            if _is_empty_row(raw_row):
                continue

            symbol = _require_text_cell(
                raw_row=raw_row,
                header_name=required_headers["symbol"],
                canonical_name="symbol",
                row_number=row_number,
            )
            name = _require_text_cell(
                raw_row=raw_row,
                header_name=required_headers["name"],
                canonical_name="name",
                row_number=row_number,
            )
            market = _require_text_cell(
                raw_row=raw_row,
                header_name=required_headers["market"],
                canonical_name="market",
                row_number=row_number,
            )

            if symbol in seen_symbols:
                raise ValueError(f"Duplicate symbol in master input: {symbol!r}")
            seen_symbols.add(symbol)

            optional_values = {
                canonical_name: _parse_bool(
                    raw_value=None if header is None else raw_row.get(header),
                    canonical_name=canonical_name,
                    row_number=row_number,
                )
                for canonical_name, header in optional_headers.items()
            }

            items.append(
                UniverseMasterItem(
                    symbol=symbol,
                    name=name,
                    market=market,
                    **optional_values,
                )
            )

        return items
