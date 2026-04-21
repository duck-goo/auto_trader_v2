"""Tests for CurrentPriceSampleRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    CurrentPriceSample,
    CurrentPriceSampleRepository,
    RepositoryError,
)


CAPTURED_AT1 = "2026-04-16T09:00:31+09:00"
CAPTURED_AT2 = "2026-04-16T09:00:32+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _sample(
    *,
    observed_at: str,
    price: int,
    symbol: str = "005930",
) -> CurrentPriceSample:
    return CurrentPriceSample(
        trade_date="2026-04-16",
        symbol=symbol,
        observed_at=observed_at,
        price=price,
        open=1000,
        high=max(1000, price),
        low=min(1000, price),
        prev_close=950,
        change=price - 950,
        change_rate=((price / 950) - 1.0) * 100,
        volume=1000,
    )


def test_write_methods_require_transaction(conn):
    repo = CurrentPriceSampleRepository(conn)

    with pytest.raises(RepositoryError):
        repo.upsert_many(
            samples=[_sample(observed_at="2026-04-16T09:00:30+09:00", price=1001)],
            captured_at=CAPTURED_AT1,
        )


def test_upsert_many_inserts_and_lists_samples(conn):
    repo = CurrentPriceSampleRepository(conn)

    with transaction(conn):
        rows = repo.upsert_many(
            samples=[
                _sample(observed_at="2026-04-16T09:00:30+09:00", price=1001),
                _sample(observed_at="2026-04-16T09:00:31+09:00", price=1002),
            ],
            captured_at=CAPTURED_AT1,
        )

    assert [row.price for row in rows] == [1001, 1002]
    assert all(row.captured_at == CAPTURED_AT1 for row in rows)

    fetched = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.observed_at for row in fetched] == [
        "2026-04-16T09:00:30+09:00",
        "2026-04-16T09:00:31+09:00",
    ]

    latest = repo.get_latest_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert latest is not None
    assert latest.price == 1002


def test_upsert_updates_existing_sample(conn):
    repo = CurrentPriceSampleRepository(conn)

    with transaction(conn):
        repo.upsert_many(
            samples=[_sample(observed_at="2026-04-16T09:00:30+09:00", price=1001)],
            captured_at=CAPTURED_AT1,
        )

    with transaction(conn):
        repo.upsert_many(
            samples=[_sample(observed_at="2026-04-16T09:00:30+09:00", price=1005)],
            captured_at=CAPTURED_AT2,
        )

    rows = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert len(rows) == 1
    assert rows[0].price == 1005
    assert rows[0].captured_at == CAPTURED_AT2


def test_list_for_symbol_between_uses_half_open_window(conn):
    repo = CurrentPriceSampleRepository(conn)

    with transaction(conn):
        repo.upsert_many(
            samples=[
                _sample(observed_at="2026-04-16T09:00:29+09:00", price=1000),
                _sample(observed_at="2026-04-16T09:00:30+09:00", price=1001),
                _sample(observed_at="2026-04-16T09:01:00+09:00", price=1002),
            ],
            captured_at=CAPTURED_AT1,
        )

    rows = repo.list_for_symbol_between(
        symbol="005930",
        start_at="2026-04-16T09:00:30+09:00",
        end_at="2026-04-16T09:01:00+09:00",
    )
    assert [row.observed_at for row in rows] == ["2026-04-16T09:00:30+09:00"]


def test_upsert_rejects_date_mismatch_and_rolls_back(conn):
    repo = CurrentPriceSampleRepository(conn)

    with transaction(conn):
        repo.upsert_many(
            samples=[_sample(observed_at="2026-04-16T09:00:30+09:00", price=1001)],
            captured_at=CAPTURED_AT1,
        )

    with pytest.raises(ValueError, match="observed_at trade_date mismatch"):
        with transaction(conn):
            repo.upsert_many(
                samples=[
                    CurrentPriceSample(
                        trade_date="2026-04-16",
                        symbol="005930",
                        observed_at="2026-04-17T09:00:30+09:00",
                        price=1001,
                        open=1000,
                        high=1001,
                        low=1000,
                        prev_close=950,
                        change=51,
                        change_rate=5.3,
                        volume=1000,
                    )
                ],
                captured_at=CAPTURED_AT2,
            )

    rows = repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert [row.price for row in rows] == [1001]


def test_upsert_rejects_duplicate_samples_in_input(conn):
    repo = CurrentPriceSampleRepository(conn)

    with pytest.raises(ValueError, match="Duplicate current price sample"):
        with transaction(conn):
            repo.upsert_many(
                samples=[
                    _sample(observed_at="2026-04-16T09:00:30+09:00", price=1001),
                    _sample(observed_at="2026-04-16T09:00:30+09:00", price=1002),
                ],
                captured_at=CAPTURED_AT1,
            )
