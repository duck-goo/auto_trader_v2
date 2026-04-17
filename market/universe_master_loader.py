"""Helpers for loading universe master files from supported formats."""

from __future__ import annotations

from pathlib import Path

from market.csv_universe_master_source import CsvUniverseMasterSource
from market.json_universe_master_source import JsonUniverseMasterSource
from market.universe_master import UniverseMasterItem

SUPPORTED_UNIVERSE_MASTER_FORMATS = ("auto", "json", "csv")


def normalize_universe_master_format(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"source_format must be a string: {value!r}"
        )

    normalized = value.strip().lower()
    if normalized not in SUPPORTED_UNIVERSE_MASTER_FORMATS:
        raise ValueError(
            "source_format must be one of "
            f"{SUPPORTED_UNIVERSE_MASTER_FORMATS!r}: {value!r}"
        )
    return normalized


def resolve_universe_master_format(
    path: str | Path,
    *,
    source_format: str = "auto",
) -> str:
    normalized_format = normalize_universe_master_format(source_format)
    if normalized_format != "auto":
        return normalized_format

    suffix = Path(path).suffix.strip().lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"

    raise ValueError(
        "Could not infer market master file format from extension: "
        f"{Path(path)}. Use source_format='json' or source_format='csv'."
    )


def load_universe_master_items(
    path: str | Path,
    *,
    source_format: str = "auto",
) -> list[UniverseMasterItem]:
    resolved_format = resolve_universe_master_format(
        path,
        source_format=source_format,
    )
    resolved_path = Path(path)

    if resolved_format == "json":
        return JsonUniverseMasterSource(resolved_path).load()
    if resolved_format == "csv":
        return CsvUniverseMasterSource(resolved_path).load()

    raise AssertionError(
        f"Unsupported resolved market master format: {resolved_format!r}"
    )
