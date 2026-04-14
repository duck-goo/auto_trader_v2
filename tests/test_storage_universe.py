"""Tests for UniverseCandidateRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    RepositoryError,
    UniverseCandidate,
    UniverseCandidateRepository,
)


REFRESHED_AT1 = "2026-04-14T08:30:00+09:00"
REFRESHED_AT2 = "2026-04-14T08:35:00+09:00"
TRADE_DATE = "2026-04-14"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _sample_candidates() -> list[UniverseCandidate]:
    return [
        UniverseCandidate(
            symbol="000660",
            name="SK hynix",
            market="KOSPI",
            close_price=206000,
            prev_day_trade_value=320_000_000_000,
        ),
        UniverseCandidate(
            symbol="005930",
            name="Samsung Electronics",
            market="KOSPI",
            close_price=70500,
            prev_day_trade_value=950_000_000_000,
        ),
    ]


def test_write_methods_require_transaction(conn):
    repo = UniverseCandidateRepository(conn)

    with pytest.raises(RepositoryError):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=_sample_candidates(),
            refreshed_at=REFRESHED_AT1,
        )


def test_replace_for_date_inserts_full_snapshot(conn):
    repo = UniverseCandidateRepository(conn)

    with transaction(conn):
        rows = repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=_sample_candidates(),
            refreshed_at=REFRESHED_AT1,
        )

    assert [row.symbol for row in rows] == ["000660", "005930"]
    assert rows[0].trade_date == TRADE_DATE
    assert rows[0].refreshed_at == REFRESHED_AT1

    fetched = repo.get(trade_date=TRADE_DATE, symbol="005930")
    assert fetched is not None
    assert fetched.name == "Samsung Electronics"
    assert fetched.market == "KOSPI"
    assert fetched.close_price == 70500
    assert fetched.prev_day_trade_value == 950_000_000_000


def test_replace_for_date_replaces_existing_snapshot(conn):
    repo = UniverseCandidateRepository(conn)

    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=_sample_candidates(),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        rows = repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="035420",
                    name="NAVER",
                    market="KOSPI",
                    close_price=180000,
                    prev_day_trade_value=410_000_000_000,
                )
            ],
            refreshed_at=REFRESHED_AT2,
        )

    assert [row.symbol for row in rows] == ["035420"]
    assert repo.get(trade_date=TRADE_DATE, symbol="005930") is None


def test_get_returns_none_for_unknown_symbol(conn):
    repo = UniverseCandidateRepository(conn)
    assert repo.get(trade_date=TRADE_DATE, symbol="005930") is None


def test_replace_for_date_rejects_duplicate_symbols_and_rolls_back(conn):
    repo = UniverseCandidateRepository(conn)

    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=950_000_000_000,
                )
            ],
            refreshed_at=REFRESHED_AT1,
        )

    with pytest.raises(ValueError, match="Duplicate symbol"):
        with transaction(conn):
            repo.replace_for_date(
                trade_date=TRADE_DATE,
                candidates=[
                    UniverseCandidate(
                        symbol="005930",
                        name="Samsung Electronics",
                        market="KOSPI",
                        close_price=70500,
                        prev_day_trade_value=950_000_000_000,
                    ),
                    UniverseCandidate(
                        symbol="005930",
                        name="Samsung Electronics Duplicate",
                        market="KOSPI",
                        close_price=70500,
                        prev_day_trade_value=950_000_000_000,
                    ),
                ],
                refreshed_at=REFRESHED_AT2,
            )

    rows = repo.list_for_date(TRADE_DATE)
    assert [row.symbol for row in rows] == ["005930"]
    assert rows[0].refreshed_at == REFRESHED_AT1


def test_replace_for_date_rejects_bad_trade_date(conn):
    repo = UniverseCandidateRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.replace_for_date(
                trade_date="20260414",
                candidates=_sample_candidates(),
                refreshed_at=REFRESHED_AT1,
            )


def test_replace_for_date_rejects_naive_refreshed_at(conn):
    repo = UniverseCandidateRepository(conn)

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.replace_for_date(
                trade_date=TRADE_DATE,
                candidates=_sample_candidates(),
                refreshed_at="2026-04-14T08:30:00",
            )


def test_replace_for_date_allows_empty_snapshot_and_clears_date(conn):
    repo = UniverseCandidateRepository(conn)

    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=_sample_candidates(),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        rows = repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[],
            refreshed_at=REFRESHED_AT2,
        )

    assert rows == []
    assert repo.list_for_date(TRADE_DATE) == []
