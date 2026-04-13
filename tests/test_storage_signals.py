"""Tests for SignalRepository."""

from __future__ import annotations

import pytest

from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    RepositoryError,
    RepositoryInvariantError,
    SignalRepository,
    SignalRow,
)


AT1 = "2026-04-13T09:00:00+09:00"
AT2 = "2026-04-13T09:05:00+09:00"
AT3 = "2026-04-13T09:10:00+09:00"
AT4 = "2026-04-13T10:00:00+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------
# Transaction guard
# ---------------------------------------------------------------------
def test_write_methods_require_transaction(conn):
    repo = SignalRepository(conn)

    with pytest.raises(RepositoryError):
        repo.record(
            symbol="005930",
            strategy_name="momo",
            scanned_at=AT1,
        )
    with pytest.raises(RepositoryError):
        repo.mark_acted(1)


# ---------------------------------------------------------------------
# record / get
# ---------------------------------------------------------------------
def test_record_minimal_fields(conn):
    repo = SignalRepository(conn)

    with transaction(conn):
        row = repo.record(
            symbol="005930",
            strategy_name="momo",
            scanned_at=AT1,
        )

    assert row.symbol == "005930"
    assert row.strategy_name == "momo"
    assert row.scanned_at == AT1
    assert row.score is None
    assert row.payload is None
    assert row.acted is False
    assert row.id > 0


def test_record_with_score_and_payload(conn):
    repo = SignalRepository(conn)
    payload = {"rsi": 28.5, "vol_spike": True, "note": "과매도", "nested": {"x": 1}}

    with transaction(conn):
        row = repo.record(
            symbol="000660",
            strategy_name="rsi_reversal",
            scanned_at=AT2,
            score=0.87,
            payload=payload,
        )

    assert row.score == 0.87
    assert row.payload == payload   # dict round-trip
    assert row.acted is False


def test_record_payload_is_deep_copy_safe(conn):
    """payload mutation after record must not affect stored row."""
    repo = SignalRepository(conn)
    payload = {"k": [1, 2, 3]}

    with transaction(conn):
        row = repo.record(
            symbol="005930",
            strategy_name="s",
            scanned_at=AT1,
            payload=payload,
        )

    payload["k"].append(999)
    reloaded = repo.get(row.id)
    assert reloaded.payload == {"k": [1, 2, 3]}


# ---------------------------------------------------------------------
# record: validation
# ---------------------------------------------------------------------
def test_record_rejects_non_finite_score(conn):
    repo = SignalRepository(conn)
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            with transaction(conn):
                repo.record(
                    symbol="005930",
                    strategy_name="s",
                    scanned_at=AT1,
                    score=bad,
                )


def test_record_rejects_non_serializable_payload(conn):
    repo = SignalRepository(conn)

    class NotSerializable:
        pass

    with pytest.raises(ValueError):
        with transaction(conn):
            repo.record(
                symbol="005930",
                strategy_name="s",
                scanned_at=AT1,
                payload={"obj": NotSerializable()},
            )


def test_record_rejects_non_dict_payload(conn):
    repo = SignalRepository(conn)
    with pytest.raises(ValueError):
        with transaction(conn):
            repo.record(
                symbol="005930",
                strategy_name="s",
                scanned_at=AT1,
                payload=[1, 2, 3],  # type: ignore[arg-type]
            )


def test_record_rejects_naive_timestamp(conn):
    repo = SignalRepository(conn)
    with pytest.raises(ValueError):
        with transaction(conn):
            repo.record(
                symbol="005930",
                strategy_name="s",
                scanned_at="2026-04-13T09:00:00",  # no tz
            )


def test_record_rejects_empty_strategy_name(conn):
    repo = SignalRepository(conn)
    with pytest.raises(ValueError):
        with transaction(conn):
            repo.record(
                symbol="005930",
                strategy_name="   ",
                scanned_at=AT1,
            )


# ---------------------------------------------------------------------
# mark_acted
# ---------------------------------------------------------------------
def test_mark_acted_transitions_flag(conn):
    repo = SignalRepository(conn)

    with transaction(conn):
        row = repo.record(
            symbol="005930", strategy_name="s", scanned_at=AT1,
        )
    with transaction(conn):
        updated = repo.mark_acted(row.id)

    assert updated.acted is True


def test_mark_acted_is_idempotent(conn):
    repo = SignalRepository(conn)

    with transaction(conn):
        row = repo.record(
            symbol="005930", strategy_name="s", scanned_at=AT1,
        )
    with transaction(conn):
        first = repo.mark_acted(row.id)
    with transaction(conn):
        second = repo.mark_acted(row.id)

    assert first.acted is True
    assert second.acted is True
    assert first.id == second.id


def test_mark_acted_raises_for_unknown_id(conn):
    repo = SignalRepository(conn)
    with pytest.raises(RepositoryInvariantError):
        with transaction(conn):
            repo.mark_acted(99999)


# ---------------------------------------------------------------------
# Read: filtering and ordering
# ---------------------------------------------------------------------
def _seed(repo: SignalRepository, conn):
    with transaction(conn):
        a = repo.record(symbol="005930", strategy_name="momo", scanned_at=AT1)
        b = repo.record(symbol="005930", strategy_name="rsi",  scanned_at=AT2)
        c = repo.record(symbol="000660", strategy_name="momo", scanned_at=AT3)
        d = repo.record(symbol="005930", strategy_name="momo", scanned_at=AT4)
    return a, b, c, d


def test_list_by_symbol_returns_desc_order(conn):
    repo = SignalRepository(conn)
    a, b, _c, d = _seed(repo, conn)

    result = repo.list_by_symbol("005930")
    ids = [r.id for r in result]
    # scanned_at DESC: AT4 > AT2 > AT1
    assert ids == [d.id, b.id, a.id]


def test_list_by_strategy_filters_and_orders(conn):
    repo = SignalRepository(conn)
    a, _b, c, d = _seed(repo, conn)

    result = repo.list_by_strategy("momo")
    ids = [r.id for r in result]
    assert ids == [d.id, c.id, a.id]


def test_list_between_inclusive_bounds(conn):
    repo = SignalRepository(conn)
    a, b, c, _d = _seed(repo, conn)

    result = repo.list_between(start_at=AT1, end_at=AT3)
    ids = [r.id for r in result]
    assert ids == [a.id, b.id, c.id]


def test_list_between_rejects_inverted_range(conn):
    repo = SignalRepository(conn)
    with pytest.raises(ValueError):
        repo.list_between(start_at=AT3, end_at=AT1)


def test_list_unacted_excludes_acted(conn):
    repo = SignalRepository(conn)
    a, b, c, d = _seed(repo, conn)

    with transaction(conn):
        repo.mark_acted(b.id)
        repo.mark_acted(d.id)

    result = repo.list_unacted()
    ids = {r.id for r in result}
    assert ids == {a.id, c.id}


def test_list_respects_limit(conn):
    repo = SignalRepository(conn)
    _seed(repo, conn)

    result = repo.list_by_symbol("005930", limit=1)
    assert len(result) == 1


def test_get_returns_none_for_unknown_id(conn):
    repo = SignalRepository(conn)
    assert repo.get(99999) is None