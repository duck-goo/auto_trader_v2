"""Tests for IntradayBar15mRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar15m,
    IntradayBar15mRepository,
    RepositoryError,
)


REFRESHED_AT1 = "2026-04-16T15:31:00+09:00"
REFRESHED_AT2 = "2026-04-16T15:32:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _bars_for_date(trade_date: str) -> list[IntradayBar15m]:
    if trade_date == "2026-04-15":
        return [
            IntradayBar15m(
                bar_start_at="2026-04-15T15:00:00+09:00",
                bar_end_at="2026-04-15T15:15:00+09:00",
                open=99,
                high=100,
                low=98,
                close=99,
                volume=150,
            )
        ]
    return [
        IntradayBar15m(
            bar_start_at="2026-04-16T09:00:00+09:00",
            bar_end_at="2026-04-16T09:15:00+09:00",
            open=100,
            high=101,
            low=99,
            close=100,
            volume=150,
        ),
        IntradayBar15m(
            bar_start_at="2026-04-16T09:15:00+09:00",
            bar_end_at="2026-04-16T09:30:00+09:00",
            open=101,
            high=102,
            low=100,
            close=101,
            volume=150,
        ),
    ]


def test_write_methods_require_transaction(conn):
    repo = IntradayBar15mRepository(conn)

    with pytest.raises(RepositoryError):
        repo.replace_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_date("2026-04-16"),
            refreshed_at=REFRESHED_AT1,
        )


def test_replace_for_symbol_and_date_inserts_and_lists_recent(conn):
    repo = IntradayBar15mRepository(conn)

    with transaction(conn):
        rows_day1 = repo.replace_for_symbol_and_date(
            trade_date="2026-04-15",
            symbol="005930",
            bars=_bars_for_date("2026-04-15"),
            refreshed_at=REFRESHED_AT1,
        )
        rows_day2 = repo.replace_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_date("2026-04-16"),
            refreshed_at=REFRESHED_AT2,
        )

    assert [row.trade_date for row in rows_day1] == ["2026-04-15"]
    assert [row.trade_date for row in rows_day2] == ["2026-04-16", "2026-04-16"]

    fetched = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.bar_start_at for row in fetched] == [
        "2026-04-16T09:00:00+09:00",
        "2026-04-16T09:15:00+09:00",
    ]

    recent = repo.list_recent_for_symbol(
        symbol="005930",
        end_at="2026-04-16T23:59:59+09:00",
        limit=10,
    )
    assert [row.trade_date for row in recent] == [
        "2026-04-15",
        "2026-04-16",
        "2026-04-16",
    ]


def test_replace_for_symbol_and_date_replaces_existing_rows(conn):
    repo = IntradayBar15mRepository(conn)

    with transaction(conn):
        repo.replace_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_date("2026-04-16"),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        repo.replace_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=[
                IntradayBar15m(
                    bar_start_at="2026-04-16T09:30:00+09:00",
                    bar_end_at="2026-04-16T09:45:00+09:00",
                    open=102,
                    high=104,
                    low=101,
                    close=102,
                    volume=150,
                )
            ],
            refreshed_at=REFRESHED_AT2,
        )

    fetched = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.bar_start_at for row in fetched] == [
        "2026-04-16T09:30:00+09:00"
    ]
    assert fetched[0].refreshed_at == REFRESHED_AT2


def test_replace_for_symbol_and_date_rejects_duplicate_bars_and_rolls_back(conn):
    repo = IntradayBar15mRepository(conn)

    with transaction(conn):
        repo.replace_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_date("2026-04-16"),
            refreshed_at=REFRESHED_AT1,
        )

    with pytest.raises(ValueError, match="Duplicate 15-minute bar_start_at"):
        with transaction(conn):
            repo.replace_for_symbol_and_date(
                trade_date="2026-04-16",
                symbol="005930",
                bars=[
                    IntradayBar15m(
                        bar_start_at="2026-04-16T09:00:00+09:00",
                        bar_end_at="2026-04-16T09:15:00+09:00",
                        open=100,
                        high=101,
                        low=99,
                        close=100,
                        volume=150,
                    ),
                    IntradayBar15m(
                        bar_start_at="2026-04-16T09:00:00+09:00",
                        bar_end_at="2026-04-16T09:15:00+09:00",
                        open=100,
                        high=101,
                        low=99,
                        close=100,
                        volume=150,
                    ),
                ],
                refreshed_at=REFRESHED_AT2,
            )

    fetched = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.bar_start_at for row in fetched] == [
        "2026-04-16T09:00:00+09:00",
        "2026-04-16T09:15:00+09:00",
    ]
    assert all(row.refreshed_at == REFRESHED_AT1 for row in fetched)
