"""Tests for UniverseRefreshService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from services import (
    UniverseRefreshItem,
    UniverseRefreshResult,
    UniverseRefreshService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import UniverseCandidateRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-14"


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


def _fixed_now(year=2026, month=4, day=14, hour=8, minute=30, second=0):
    fixed = KST.localize(datetime(year, month, day, hour, minute, second))
    return lambda: fixed


def _items() -> list[UniverseRefreshItem]:
    return [
        UniverseRefreshItem(
            symbol="000660",
            name="SK하이닉스",
            market="KOSPI",
            close_price=206000,
            prev_day_trade_value=1_200_000,
        ),
        UniverseRefreshItem(
            symbol="005930",
            name="삼성전자",
            market="KOSPI",
            close_price=70500,
            prev_day_trade_value=15_000_000,
        ),
    ]


def _make_service(conn, repo, *, now_fn=None):
    return UniverseRefreshService(
        conn=conn,
        universe_repo=repo,
        now_fn=now_fn or _fixed_now(),
    )


def test_refresh_snapshot_persists_candidates(conn, repo):
    service = _make_service(conn, repo)

    result = service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=_items(),
    )

    assert isinstance(result, UniverseRefreshResult)
    assert result.trade_date == TRADE_DATE
    assert result.refreshed_at == "2026-04-14T08:30:00+09:00"
    assert result.candidate_count == 2
    assert [row.symbol for row in result.rows] == ["000660", "005930"]

    rows = repo.list_for_date(TRADE_DATE)
    assert [row.symbol for row in rows] == ["000660", "005930"]


def test_refresh_snapshot_replaces_existing_snapshot(conn, repo):
    service = _make_service(conn, repo)

    service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=_items(),
    )

    result = service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=[
            UniverseRefreshItem(
                symbol="035420",
                name="NAVER",
                market="KOSPI",
                close_price=180000,
                prev_day_trade_value=900000,
            )
        ],
        refreshed_at="2026-04-14T08:35:00+09:00",
    )

    assert result.candidate_count == 1
    assert [row.symbol for row in result.rows] == ["035420"]
    assert repo.get(trade_date=TRADE_DATE, symbol="005930") is None


def test_refresh_snapshot_allows_empty_snapshot(conn, repo):
    service = _make_service(conn, repo)

    service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=_items(),
    )

    result = service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=[],
        refreshed_at="2026-04-14T08:35:00+09:00",
    )

    assert result.candidate_count == 0
    assert result.rows == ()
    assert repo.list_for_date(TRADE_DATE) == []


def test_refresh_snapshot_rolls_back_on_duplicate_symbol(conn, repo):
    service = _make_service(conn, repo)

    service.refresh_snapshot(
        trade_date=TRADE_DATE,
        candidates=[
            UniverseRefreshItem(
                symbol="005930",
                name="삼성전자",
                market="KOSPI",
                close_price=70500,
                prev_day_trade_value=15_000_000,
            )
        ],
    )

    with pytest.raises(ValueError, match="Duplicate symbol"):
        service.refresh_snapshot(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseRefreshItem(
                    symbol="005930",
                    name="삼성전자",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=15_000_000,
                ),
                UniverseRefreshItem(
                    symbol="005930",
                    name="삼성전자-중복",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=15_000_000,
                ),
            ],
            refreshed_at="2026-04-14T08:35:00+09:00",
        )

    rows = repo.list_for_date(TRADE_DATE)
    assert [row.symbol for row in rows] == ["005930"]


def test_refresh_snapshot_rejects_wrong_item_type(conn, repo):
    service = _make_service(conn, repo)

    with pytest.raises(ValueError, match="UniverseRefreshItem"):
        service.refresh_snapshot(
            trade_date=TRADE_DATE,
            candidates=[{"symbol": "005930"}],  # type: ignore[list-item]
        )
