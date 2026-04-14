"""Tests for JsonUniverseSource."""

from __future__ import annotations

import json

import pytest

from market import JsonUniverseSource, UniverseSourceItem


def _write_json(tmp_path, name: str, payload) -> str:
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def test_json_universe_source_loads_valid_items(tmp_path):
    path = _write_json(
        tmp_path,
        "universe.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "close_price": 70500,
                "prev_day_trade_value": 950000000000,
                "avg_trade_value_20": 880000000000,
            }
        ],
    )

    source = JsonUniverseSource(path)
    items = source.load()

    assert len(items) == 1
    assert isinstance(items[0], UniverseSourceItem)
    assert items[0].symbol == "005930"
    assert items[0].is_etf is False


def test_json_universe_source_rejects_non_list_payload(tmp_path):
    path = _write_json(
        tmp_path,
        "bad.json",
        {"symbol": "005930"},
    )

    source = JsonUniverseSource(path)

    with pytest.raises(ValueError, match="Input JSON must be a list"):
        source.load()


def test_json_universe_source_rejects_missing_key(tmp_path):
    path = _write_json(
        tmp_path,
        "missing.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "close_price": 70500,
                "prev_day_trade_value": 950000000000,
            }
        ],
    )

    source = JsonUniverseSource(path)

    with pytest.raises(ValueError, match="missing key"):
        source.load()


def test_json_universe_source_rejects_bad_bool_type(tmp_path):
    path = _write_json(
        tmp_path,
        "bad_bool.json",
        [
            {
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
                "close_price": 70500,
                "prev_day_trade_value": 950000000000,
                "avg_trade_value_20": 880000000000,
                "is_etf": "false",
            }
        ],
    )

    source = JsonUniverseSource(path)

    with pytest.raises(ValueError, match="must be a bool"):
        source.load()
