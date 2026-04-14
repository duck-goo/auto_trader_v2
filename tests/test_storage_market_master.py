"""Tests for MarketMasterRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterEntry,
    MarketMasterRepository,
    RepositoryError,
)


REFRESHED_AT1 = "2026-04-14T08:00:00+09:00"
REFRESHED_AT2 = "2026-04-14T08:05:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _sample_entries() -> list[MarketMasterEntry]:
    return [
        MarketMasterEntry(
            symbol="005930",
            name="Samsung Electronics",
            market="KOSPI",
        ),
        MarketMasterEntry(
            symbol="069500",
            name="KODEX 200",
            market="ETF",
            is_etf=True,
        ),
    ]


def test_write_methods_require_transaction(conn):
    repo = MarketMasterRepository(conn)

    with pytest.raises(RepositoryError):
        repo.replace_all(
            entries=_sample_entries(),
            refreshed_at=REFRESHED_AT1,
        )


def test_replace_all_inserts_snapshot(conn):
    repo = MarketMasterRepository(conn)

    with transaction(conn):
        rows = repo.replace_all(
            entries=_sample_entries(),
            refreshed_at=REFRESHED_AT1,
        )

    assert [row.symbol for row in rows] == ["005930", "069500"]
    assert rows[1].is_etf is True
    assert rows[0].refreshed_at == REFRESHED_AT1

    fetched = repo.get(symbol="069500")
    assert fetched is not None
    assert fetched.name == "KODEX 200"
    assert fetched.is_etf is True


def test_replace_all_replaces_existing_snapshot(conn):
    repo = MarketMasterRepository(conn)

    with transaction(conn):
        repo.replace_all(
            entries=_sample_entries(),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        rows = repo.replace_all(
            entries=[
                MarketMasterEntry(
                    symbol="035420",
                    name="NAVER",
                    market="KOSPI",
                )
            ],
            refreshed_at=REFRESHED_AT2,
        )

    assert [row.symbol for row in rows] == ["035420"]
    assert repo.get(symbol="005930") is None


def test_replace_all_rejects_duplicate_symbols_and_rolls_back(conn):
    repo = MarketMasterRepository(conn)

    with transaction(conn):
        repo.replace_all(
            entries=[
                MarketMasterEntry(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                )
            ],
            refreshed_at=REFRESHED_AT1,
        )

    with pytest.raises(ValueError, match="Duplicate symbol"):
        with transaction(conn):
            repo.replace_all(
                entries=[
                    MarketMasterEntry(
                        symbol="005930",
                        name="Samsung Electronics",
                        market="KOSPI",
                    ),
                    MarketMasterEntry(
                        symbol="005930",
                        name="Samsung Electronics Duplicate",
                        market="KOSPI",
                    ),
                ],
                refreshed_at=REFRESHED_AT2,
            )

    rows = repo.list_all()
    assert [row.symbol for row in rows] == ["005930"]
    assert rows[0].refreshed_at == REFRESHED_AT1


def test_replace_all_rejects_naive_refreshed_at(conn):
    repo = MarketMasterRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.replace_all(
                entries=_sample_entries(),
                refreshed_at="2026-04-14T08:00:00",
            )


def test_replace_all_allows_empty_snapshot_and_clears_all(conn):
    repo = MarketMasterRepository(conn)

    with transaction(conn):
        repo.replace_all(
            entries=_sample_entries(),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        rows = repo.replace_all(
            entries=[],
            refreshed_at=REFRESHED_AT2,
        )

    assert rows == []
    assert repo.list_all() == []
