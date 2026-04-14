"""Tests for JsonUniverseMasterSource."""

from __future__ import annotations

import json

import pytest

from market import JsonUniverseMasterSource, UniverseMasterItem


def _write_json(tmp_path, name: str, payload) -> str:
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def test_json_universe_master_source_loads_valid_items(tmp_path):
    path = _write_json(
        tmp_path,
        "master.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            }
        ],
    )

    source = JsonUniverseMasterSource(path)
    items = source.load()

    assert len(items) == 1
    assert isinstance(items[0], UniverseMasterItem)
    assert items[0].symbol == "005930"
    assert items[0].is_etf is False


def test_json_universe_master_source_rejects_non_list_payload(tmp_path):
    path = _write_json(
        tmp_path,
        "bad.json",
        {"symbol": "005930"},
    )

    source = JsonUniverseMasterSource(path)

    with pytest.raises(ValueError, match="Input JSON must be a list"):
        source.load()


def test_json_universe_master_source_rejects_missing_key(tmp_path):
    path = _write_json(
        tmp_path,
        "missing.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
            }
        ],
    )

    source = JsonUniverseMasterSource(path)

    with pytest.raises(ValueError, match="missing key"):
        source.load()


def test_json_universe_master_source_rejects_bad_bool_type(tmp_path):
    path = _write_json(
        tmp_path,
        "bad_bool.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "is_etf": "false",
            }
        ],
    )

    source = JsonUniverseMasterSource(path)

    with pytest.raises(ValueError, match="must be a bool"):
        source.load()


def test_json_universe_master_source_rejects_duplicate_symbols(tmp_path):
    path = _write_json(
        tmp_path,
        "duplicate.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
            {
                "symbol": "005930",
                "name": "Samsung Electronics Duplicate",
                "market": "KOSPI",
            },
        ],
    )

    source = JsonUniverseMasterSource(path)

    with pytest.raises(ValueError, match="Duplicate symbol"):
        source.load()
