"""Tests for UniverseQueryService."""

from __future__ import annotations

import pytest

from services import ServiceError, UniverseQueryService, UniverseSnapshotResult
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    UniverseCandidate,
    UniverseCandidateRepository,
)


TRADE_DATE = "2026-04-14"
REFRESHED_AT = "2026-04-14T08:30:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def repo(conn):
    return UniverseCandidateRepository(conn)


def _make_service(repo):
    return UniverseQueryService(universe_repo=repo)


def _seed_snapshot(conn, repo):
    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="000660",
                    name="SK hynix",
                    market="KOSPI",
                    close_price=206000,
                    prev_day_trade_value=1_200_000,
                ),
                UniverseCandidate(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=15_000_000,
                ),
            ],
            refreshed_at=REFRESHED_AT,
        )


def test_get_snapshot_returns_missing_when_no_rows(repo):
    service = _make_service(repo)

    result = service.get_snapshot(trade_date=TRADE_DATE)

    assert isinstance(result, UniverseSnapshotResult)
    assert result.trade_date == TRADE_DATE
    assert result.exists is False
    assert result.candidate_count == 0
    assert result.refreshed_at is None
    assert result.rows == ()


def test_get_snapshot_returns_summary_when_rows_exist(conn, repo):
    _seed_snapshot(conn, repo)
    service = _make_service(repo)

    result = service.get_snapshot(trade_date=TRADE_DATE)

    assert result.exists is True
    assert result.candidate_count == 2
    assert result.refreshed_at == REFRESHED_AT
    assert [row.symbol for row in result.rows] == ["000660", "005930"]


def test_get_snapshot_rejects_bad_trade_date(repo):
    service = _make_service(repo)

    with pytest.raises(ValueError):
        service.get_snapshot(trade_date="20260414")


def test_get_snapshot_raises_on_inconsistent_refreshed_at(conn, repo):
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO universe_candidates (
                trade_date,
                symbol,
                name,
                market,
                close_price,
                prev_day_trade_value,
                refreshed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TRADE_DATE,
                "000660",
                "SK hynix",
                "KOSPI",
                206000,
                1_200_000,
                "2026-04-14T08:30:00+09:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO universe_candidates (
                trade_date,
                symbol,
                name,
                market,
                close_price,
                prev_day_trade_value,
                refreshed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                TRADE_DATE,
                "005930",
                "Samsung Electronics",
                "KOSPI",
                70500,
                15_000_000,
                "2026-04-14T08:35:00+09:00",
            ),
        )

    service = _make_service(repo)

    with pytest.raises(ServiceError, match="inconsistent refreshed_at"):
        service.get_snapshot(trade_date=TRADE_DATE)
