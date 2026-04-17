"""Tests for universe master file loader helpers."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from market import (
    UniverseMasterItem,
    load_universe_master_items,
    normalize_universe_master_format,
    resolve_universe_master_format,
)


def _make_path(test_db_path: Path, suffix: str) -> Path:
    return test_db_path.parent / f"{test_db_path.stem}_{uuid4().hex}{suffix}"


def test_resolve_universe_master_format_auto_json(test_db_path: Path):
    path = _make_path(test_db_path, ".json")

    assert resolve_universe_master_format(path) == "json"


def test_resolve_universe_master_format_auto_csv(test_db_path: Path):
    path = _make_path(test_db_path, ".csv")

    assert resolve_universe_master_format(path) == "csv"


def test_resolve_universe_master_format_rejects_unknown_extension(
    test_db_path: Path,
):
    path = _make_path(test_db_path, ".txt")

    with pytest.raises(ValueError, match="Could not infer"):
        resolve_universe_master_format(path)


def test_normalize_universe_master_format_rejects_bad_value():
    with pytest.raises(ValueError, match="source_format must be one of"):
        normalize_universe_master_format("xml")


def test_load_universe_master_items_loads_json(test_db_path: Path):
    path = _make_path(test_db_path, ".json")
    path.write_text(
        json.dumps(
            [
                {
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "market": "KOSPI",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    items = load_universe_master_items(path)

    assert len(items) == 1
    assert isinstance(items[0], UniverseMasterItem)
    assert items[0].symbol == "005930"


def test_load_universe_master_items_loads_csv_with_explicit_format(
    test_db_path: Path,
):
    path = _make_path(test_db_path, ".data")
    path.write_text(
        (
            "symbol,name,market,is_etf\n"
            "069500,KODEX 200,ETF,1\n"
        ),
        encoding="utf-8",
    )

    items = load_universe_master_items(path, source_format="csv")

    assert len(items) == 1
    assert items[0].symbol == "069500"
    assert items[0].is_etf is True
