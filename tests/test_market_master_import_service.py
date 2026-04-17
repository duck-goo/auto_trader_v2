"""Tests for MarketMasterImportService."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytz
import pytest

from services import (
    MarketMasterImportService,
    MarketMasterRefreshResult,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository


KST = pytz.timezone("Asia/Seoul")


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 15, 8, 30, 0))
    return lambda: fixed


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _make_path(test_db_path: Path, suffix: str) -> Path:
    return test_db_path.parent / f"{test_db_path.stem}_{uuid4().hex}{suffix}"


def test_import_from_json_file_persists_snapshot(conn, test_db_path: Path):
    path = _make_path(test_db_path, ".json")
    path.write_text(
        json.dumps(
            [
                {
                    "symbol": "005930",
                    "name": "Samsung Electronics",
                    "market": "KOSPI",
                },
                {
                    "symbol": "069500",
                    "name": "KODEX 200",
                    "market": "ETF",
                    "is_etf": True,
                },
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    service = MarketMasterImportService(
        conn=conn,
        market_master_repo=MarketMasterRepository(conn),
        now_fn=_fixed_now(),
    )
    result = service.import_from_file(path=path, source_format="json")

    assert isinstance(result, MarketMasterRefreshResult)
    assert result.symbol_count == 2
    assert result.refreshed_at == "2026-04-15T08:30:00+09:00"
    assert [row.symbol for row in result.rows] == ["005930", "069500"]


def test_import_from_csv_file_persists_snapshot(conn, test_db_path: Path):
    path = _make_path(test_db_path, ".csv")
    path.write_text(
        (
            "symbol,name,market,is_attention_issue,is_etf\n"
            "005930,Samsung Electronics,KOSPI,0,0\n"
            "069500,KODEX 200,ETF,0,1\n"
        ),
        encoding="utf-8",
    )

    service = MarketMasterImportService(
        conn=conn,
        market_master_repo=MarketMasterRepository(conn),
        now_fn=_fixed_now(),
    )
    result = service.import_from_file(path=path, source_format="csv")

    assert result.symbol_count == 2
    assert result.rows[0].symbol == "005930"
    assert result.rows[1].is_etf is True


def test_import_from_file_supports_auto_format(conn, test_db_path: Path):
    path = _make_path(test_db_path, ".csv")
    path.write_text(
        (
            "symbol,name,market\n"
            "035420,NAVER,KOSPI\n"
        ),
        encoding="utf-8",
    )

    service = MarketMasterImportService(
        conn=conn,
        market_master_repo=MarketMasterRepository(conn),
        now_fn=_fixed_now(),
    )
    result = service.import_from_file(path=path)

    assert result.symbol_count == 1
    assert result.rows[0].symbol == "035420"


def test_import_from_file_rejects_missing_path(conn, test_db_path: Path):
    path = _make_path(test_db_path, ".json")

    service = MarketMasterImportService(
        conn=conn,
        market_master_repo=MarketMasterRepository(conn),
        now_fn=_fixed_now(),
    )

    with pytest.raises(ValueError, match="file not found"):
        service.import_from_file(path=path, source_format="json")
