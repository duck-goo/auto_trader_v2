"""Tests for MarketMasterHealthService."""

from __future__ import annotations

import pytest

from services import (
    MarketMasterHealthOutcome,
    MarketMasterHealthResult,
    MarketMasterHealthService,
    MarketMasterQueryService,
    MarketMasterRefreshItem,
    MarketMasterRefreshService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import MarketMasterRepository


REFRESHED_AT = "2026-04-14T08:00:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _seed_snapshot(conn, repo, *, refreshed_at: str = REFRESHED_AT):
    MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
    ).refresh_snapshot(
        items=[
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
        ],
        refreshed_at=refreshed_at,
    )


def test_check_snapshot_blocks_when_missing(conn):
    service = MarketMasterHealthService(
        query_service=MarketMasterQueryService(
            market_master_repo=MarketMasterRepository(conn),
        )
    )

    result = service.check_snapshot()

    assert isinstance(result, MarketMasterHealthResult)
    assert result.outcome == MarketMasterHealthOutcome.BLOCKED
    assert result.exists is False
    assert result.reason == "Market master snapshot is missing."


def test_check_snapshot_ready_when_same_day_and_min_count_pass(conn):
    repo = MarketMasterRepository(conn)
    _seed_snapshot(conn, repo)
    service = MarketMasterHealthService(
        query_service=MarketMasterQueryService(market_master_repo=repo)
    )

    result = service.check_snapshot(
        trade_date="2026-04-14",
        require_same_trade_date=True,
        min_symbol_count=2,
    )

    assert result.outcome == MarketMasterHealthOutcome.READY
    assert result.refreshed_trade_date == "2026-04-14"
    assert result.is_same_trade_date is True
    assert result.meets_min_symbol_count is True
    assert result.reason is None


def test_check_snapshot_blocks_when_stale(conn):
    repo = MarketMasterRepository(conn)
    _seed_snapshot(conn, repo, refreshed_at="2026-04-13T08:00:00+09:00")
    service = MarketMasterHealthService(
        query_service=MarketMasterQueryService(market_master_repo=repo)
    )

    result = service.check_snapshot(
        trade_date="2026-04-14",
        require_same_trade_date=True,
    )

    assert result.outcome == MarketMasterHealthOutcome.BLOCKED
    assert result.refreshed_trade_date == "2026-04-13"
    assert result.is_same_trade_date is False
    assert "stale" in result.reason


def test_check_snapshot_blocks_when_symbol_count_below_minimum(conn):
    repo = MarketMasterRepository(conn)
    _seed_snapshot(conn, repo)
    service = MarketMasterHealthService(
        query_service=MarketMasterQueryService(market_master_repo=repo)
    )

    result = service.check_snapshot(min_symbol_count=3)

    assert result.outcome == MarketMasterHealthOutcome.BLOCKED
    assert result.meets_min_symbol_count is False
    assert result.reason == (
        "Market master snapshot symbol_count is below minimum: "
        "actual=2, minimum=3"
    )
