"""Tests for MarketMasterQueryService."""

from __future__ import annotations

import pytest

from services import (
    MarketMasterQueryService,
    MarketMasterRefreshItem,
    MarketMasterRefreshService,
    MarketMasterSnapshotResult,
    ServiceError,
)
from storage.db import get_connection, transaction
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


def _seed_snapshot(conn, repo):
    service = MarketMasterRefreshService(
        conn=conn,
        market_master_repo=repo,
    )
    service.refresh_snapshot(
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
        refreshed_at=REFRESHED_AT,
    )


def test_get_snapshot_returns_missing_when_no_rows(conn):
    repo = MarketMasterRepository(conn)
    service = MarketMasterQueryService(market_master_repo=repo)

    result = service.get_snapshot()

    assert isinstance(result, MarketMasterSnapshotResult)
    assert result.exists is False
    assert result.symbol_count == 0
    assert result.refreshed_at is None
    assert result.rows == ()


def test_get_snapshot_returns_summary_when_rows_exist(conn):
    repo = MarketMasterRepository(conn)
    _seed_snapshot(conn, repo)
    service = MarketMasterQueryService(market_master_repo=repo)

    result = service.get_snapshot()

    assert result.exists is True
    assert result.symbol_count == 2
    assert result.refreshed_at == REFRESHED_AT
    assert [row.symbol for row in result.rows] == ["005930", "069500"]


def test_get_snapshot_raises_on_inconsistent_refreshed_at(conn):
    repo = MarketMasterRepository(conn)
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO market_master (
                symbol,
                name,
                market,
                is_managed,
                is_investment_warning,
                is_investment_risk,
                is_attention_issue,
                is_disclosure_violation,
                is_liquidation_trade,
                is_trading_halt,
                is_rights_ex_date,
                is_preferred_stock,
                is_etf,
                is_etn,
                is_spac,
                refreshed_at
            )
            VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?)
            """,
            ("005930", "Samsung Electronics", "KOSPI", "2026-04-14T08:00:00+09:00"),
        )
        conn.execute(
            """
            INSERT INTO market_master (
                symbol,
                name,
                market,
                is_managed,
                is_investment_warning,
                is_investment_risk,
                is_attention_issue,
                is_disclosure_violation,
                is_liquidation_trade,
                is_trading_halt,
                is_rights_ex_date,
                is_preferred_stock,
                is_etf,
                is_etn,
                is_spac,
                refreshed_at
            )
            VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, ?)
            """,
            ("069500", "KODEX 200", "ETF", "2026-04-14T08:05:00+09:00"),
        )

    service = MarketMasterQueryService(market_master_repo=repo)

    with pytest.raises(ServiceError, match="inconsistent refreshed_at"):
        service.get_snapshot()
