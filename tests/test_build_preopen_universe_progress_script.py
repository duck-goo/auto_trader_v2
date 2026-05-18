"""Tests for build_preopen_universe_progress script helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from services import UniverseFilterInput


def _load_script_module():
    path = Path("scripts/build_preopen_universe_progress.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "build_preopen_universe_progress_script",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_source_item_payload_round_trip():
    module = _load_script_module()

    payload = {
        "symbol": "005930",
        "name": "Samsung Electronics",
        "market": "KOSPI",
        "close_price": 70000,
        "prev_day_trade_value": 900_000_000_000,
        "avg_trade_value_20": 800_000_000_000,
        "is_preferred_stock": False,
        "is_spac": False,
    }

    item = module._payload_to_filter_input(payload)

    assert isinstance(item, UniverseFilterInput)
    assert item.symbol == "005930"
    assert item.close_price == 70000
    assert item.avg_trade_value_20 == 800_000_000_000


def test_jsonl_load_uses_latest_row_per_symbol(tmp_path):
    module = _load_script_module()
    path = tmp_path / "source.jsonl"

    module._append_jsonl(path, {"symbol": "005930", "close_price": 70000})
    module._append_jsonl(path, {"symbol": "005930", "close_price": 71000})
    module._append_jsonl(path, {"symbol": "000660", "close_price": 120000})

    rows = module._load_jsonl_by_symbol(path)

    assert sorted(rows) == ["000660", "005930"]
    assert rows["005930"]["close_price"] == 71000


def test_select_master_items_applies_offset_and_limit():
    module = _load_script_module()
    rows = [
        SimpleNamespace(
            symbol=f"00000{index}",
            name=f"Name {index}",
            market="KOSPI",
            is_managed=False,
            is_investment_warning=False,
            is_investment_risk=False,
            is_attention_issue=False,
            is_disclosure_violation=False,
            is_liquidation_trade=False,
            is_trading_halt=False,
            is_rights_ex_date=False,
            is_preferred_stock=False,
            is_etf=False,
            is_etn=False,
            is_spac=False,
        )
        for index in range(5)
    ]

    selected = module._select_master_items(
        rows,
        start_offset=2,
        max_symbols=2,
    )

    assert [item.symbol for item in selected] == ["000002", "000003"]
