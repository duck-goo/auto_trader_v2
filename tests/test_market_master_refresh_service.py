"""Tests for MarketMasterRefreshService."""

from __future__ import annotations

from datetime import datetime

import pytz
import pytest

from services import (
    MarketMasterRefreshItem,
    MarketMasterRefreshResult,
    MarketMasterRefreshService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository


KST = pytz.timezone("Asia/Seoul")


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 14, 8, 0, 0))
    return lambda: fixed


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _items() -> list[MarketMasterRefreshItem]:
    return [
        MarketMasterRefreshItem(
            symbol="005930",
            name="Samsung Electronics",
            market="KOSPI",
        ),
        MarketMasterRefreshItem(
            symbol="069500",
            name="KODEX 200",
            market="ETF",
            is_etf=True,
        ),
    ]


def test_refresh_snapshot_persists_items(conn):
    repo = MarketMasterRepository(conn)
    service = MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
        now_fn=_fixed_now(),
    )

    result = service.refresh_snapshot(items=_items())

    assert isinstance(result, MarketMasterRefreshResult)
    assert result.symbol_count == 2
    assert result.refreshed_at == "2026-04-14T08:00:00+09:00"
    assert [row.symbol for row in result.rows] == ["005930", "069500"]


def test_refresh_snapshot_replaces_existing_snapshot(conn):
    repo = MarketMasterRepository(conn)
    service = MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
        now_fn=_fixed_now(),
    )
    service.refresh_snapshot(items=_items())

    result = service.refresh_snapshot(
        items=[
            MarketMasterRefreshItem(
                symbol="035420",
                name="NAVER",
                market="KOSPI",
            )
        ],
        refreshed_at="2026-04-14T08:05:00+09:00",
    )

    assert result.symbol_count == 1
    assert [row.symbol for row in repo.list_all()] == ["035420"]


def test_refresh_snapshot_allows_empty_snapshot(conn):
    repo = MarketMasterRepository(conn)
    service = MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
        now_fn=_fixed_now(),
    )
    service.refresh_snapshot(items=_items())

    result = service.refresh_snapshot(items=[])

    assert result.symbol_count == 0
    assert repo.list_all() == []


def test_refresh_snapshot_rejects_wrong_item_type(conn):
    repo = MarketMasterRepository(conn)
    service = MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
        now_fn=_fixed_now(),
    )

    with pytest.raises(ValueError, match="MarketMasterRefreshItem"):
        service.refresh_snapshot(items=[{"symbol": "005930"}])  # type: ignore[list-item]
