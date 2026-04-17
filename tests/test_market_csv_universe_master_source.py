"""Tests for CsvUniverseMasterSource."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from market import CsvUniverseMasterSource


def _write_text(
    test_db_path: Path,
    name: str,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    path = test_db_path.parent / f"{test_db_path.stem}_{uuid4().hex}_{name}"
    path.write_text(text, encoding=encoding)
    return path


def test_load_accepts_english_headers(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master.csv",
        (
            "symbol,name,market,is_etf,is_attention_issue\n"
            "005930,Samsung Electronics,KOSPI,0,0\n"
            "069500,KODEX 200,ETF,1,0\n"
        ),
    )

    items = CsvUniverseMasterSource(path).load()

    assert [item.symbol for item in items] == ["005930", "069500"]
    assert items[0].is_etf is False
    assert items[1].is_etf is True


def test_load_accepts_korean_headers_with_cp949(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master_kr.csv",
        (
            "\uc885\ubaa9\ucf54\ub4dc,\uc885\ubaa9\uba85,"
            "\uc2dc\uc7a5,ETF,\ud658\uae30\uc885\ubaa9\n"
            "005930,\uc0bc\uc131\uc804\uc790,KOSPI,\uc544\ub2c8\uc694,"
            "\uc544\ub2c8\uc624\n"
            "069500,KODEX 200,ETF,\uc608,\uc544\ub2c8\uc694\n"
        ),
        encoding="cp949",
    )

    items = CsvUniverseMasterSource(path).load()

    assert [item.symbol for item in items] == ["005930", "069500"]
    assert items[0].name == "\uc0bc\uc131\uc804\uc790"
    assert items[0].is_etf is False
    assert items[1].is_etf is True


def test_load_skips_blank_rows(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master_blank.csv",
        (
            "symbol,name,market,is_etf\n"
            "\n"
            "005930,Samsung Electronics,KOSPI,0\n"
            "\n"
            "069500,KODEX 200,ETF,1\n"
            "\n"
        ),
    )

    items = CsvUniverseMasterSource(path).load()

    assert [item.symbol for item in items] == ["005930", "069500"]


def test_load_rejects_missing_required_columns(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master_missing.csv",
        "symbol,name\n005930,Samsung Electronics\n",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        CsvUniverseMasterSource(path).load()


def test_load_rejects_duplicate_symbols(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master_dup.csv",
        (
            "symbol,name,market\n"
            "005930,Samsung Electronics,KOSPI\n"
            "005930,Samsung Electronics Duplicate,KOSPI\n"
        ),
    )

    with pytest.raises(ValueError, match="Duplicate symbol"):
        CsvUniverseMasterSource(path).load()


def test_load_rejects_invalid_boolean_value(test_db_path: Path):
    path = _write_text(
        test_db_path,
        "master_bool.csv",
        (
            "symbol,name,market,is_etf\n"
            "069500,KODEX 200,ETF,maybe\n"
        ),
    )

    with pytest.raises(ValueError, match="must be a boolean-like value"):
        CsvUniverseMasterSource(path).load()
