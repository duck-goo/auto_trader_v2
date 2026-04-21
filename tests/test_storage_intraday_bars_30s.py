"""Tests for IntradayBar30sRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar30s,
    IntradayBar30sRepository,
    RepositoryError,
)


REFRESHED_AT1 = "2026-04-16T09:01:00+09:00"
REFRESHED_AT2 = "2026-04-16T09:02:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _bar(
    *,
    start: str,
    end: str,
    open_price: int,
    close: int,
    high: int | None = None,
    low: int | None = None,
) -> IntradayBar30s:
    high_price = max(open_price, close) if high is None else high
    low_price = min(open_price, close) if low is None else low
    return IntradayBar30s(
        bar_start_at=start,
        bar_end_at=end,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close,
        volume=100,
    )


def _bars_for_morning() -> list[IntradayBar30s]:
    return [
        _bar(
            start="2026-04-16T09:00:00+09:00",
            end="2026-04-16T09:00:30+09:00",
            open_price=1000,
            close=1005,
        ),
        _bar(
            start="2026-04-16T09:00:30+09:00",
            end="2026-04-16T09:01:00+09:00",
            open_price=1005,
            close=990,
        ),
        _bar(
            start="2026-04-16T09:59:30+09:00",
            end="2026-04-16T10:00:00+09:00",
            open_price=1008,
            close=1100,
        ),
    ]


def test_write_methods_require_transaction(conn):
    repo = IntradayBar30sRepository(conn)

    with pytest.raises(RepositoryError):
        repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_morning(),
            refreshed_at=REFRESHED_AT1,
        )


def test_upsert_many_inserts_and_exposes_timing2_references(conn):
    repo = IntradayBar30sRepository(conn)

    with transaction(conn):
        rows = repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_morning(),
            refreshed_at=REFRESHED_AT1,
        )

    assert [row.bar_start_at for row in rows] == [
        "2026-04-16T09:00:00+09:00",
        "2026-04-16T09:00:30+09:00",
        "2026-04-16T09:59:30+09:00",
    ]
    assert repo.get_session_open_price(
        trade_date="2026-04-16",
        symbol="005930",
    ) == 1000
    assert repo.get_max_close_between(
        trade_date="2026-04-16",
        symbol="005930",
        start_time="09:00:00",
        end_time="10:00:00",
    ) == 1100

    latest = repo.get_latest_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert latest is not None
    assert latest.bar_end_at == "2026-04-16T10:00:00+09:00"


def test_upsert_updates_existing_bar_without_deleting_other_rows(conn):
    repo = IntradayBar30sRepository(conn)

    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_morning(),
            refreshed_at=REFRESHED_AT1,
        )

    with transaction(conn):
        rows = repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=[
                _bar(
                    start="2026-04-16T09:00:30+09:00",
                    end="2026-04-16T09:01:00+09:00",
                    open_price=1005,
                    close=995,
                )
            ],
            refreshed_at=REFRESHED_AT2,
        )

    assert len(rows) == 3
    updated = [
        row for row in rows if row.bar_start_at == "2026-04-16T09:00:30+09:00"
    ][0]
    assert updated.close == 995
    assert updated.refreshed_at == REFRESHED_AT2
    assert repo.get_max_close_between(
        trade_date="2026-04-16",
        symbol="005930",
    ) == 1100


def test_list_recent_for_symbol_returns_oldest_to_newest(conn):
    repo = IntradayBar30sRepository(conn)

    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_morning(),
            refreshed_at=REFRESHED_AT1,
        )

    recent = repo.list_recent_for_symbol(
        symbol="005930",
        end_at="2026-04-16T10:00:00+09:00",
        limit=2,
    )

    assert [row.bar_start_at for row in recent] == [
        "2026-04-16T09:00:30+09:00",
        "2026-04-16T09:59:30+09:00",
    ]


def test_upsert_rejects_non_30_second_bar_and_rolls_back(conn):
    repo = IntradayBar30sRepository(conn)

    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
            bars=_bars_for_morning(),
            refreshed_at=REFRESHED_AT1,
        )

    with pytest.raises(ValueError, match="30-second bar duration mismatch"):
        with transaction(conn):
            repo.upsert_many_for_symbol_and_date(
                trade_date="2026-04-16",
                symbol="005930",
                bars=[
                    _bar(
                        start="2026-04-16T09:01:00+09:00",
                        end="2026-04-16T09:02:00+09:00",
                        open_price=990,
                        close=991,
                    )
                ],
                refreshed_at=REFRESHED_AT2,
            )

    rows = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.bar_start_at for row in rows] == [
        "2026-04-16T09:00:00+09:00",
        "2026-04-16T09:00:30+09:00",
        "2026-04-16T09:59:30+09:00",
    ]


def test_upsert_rejects_duplicate_bars_in_input(conn):
    repo = IntradayBar30sRepository(conn)

    with pytest.raises(ValueError, match="Duplicate 30-second bar_start_at"):
        with transaction(conn):
            repo.upsert_many_for_symbol_and_date(
                trade_date="2026-04-16",
                symbol="005930",
                bars=[
                    _bar(
                        start="2026-04-16T09:00:00+09:00",
                        end="2026-04-16T09:00:30+09:00",
                        open_price=1000,
                        close=1001,
                    ),
                    _bar(
                        start="2026-04-16T09:00:00+09:00",
                        end="2026-04-16T09:00:30+09:00",
                        open_price=1000,
                        close=1001,
                    ),
                ],
                refreshed_at=REFRESHED_AT1,
            )
