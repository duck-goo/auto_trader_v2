"""Tests for UniverseBuildService."""

from __future__ import annotations

from datetime import datetime

import pytz

from services import (
    UniverseBuildOutcome,
    UniverseBuildResult,
    UniverseBuildService,
    UniverseFilterInput,
    UniverseFilterService,
    UniverseFilterSettings,
    UniverseRefreshService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import UniverseCandidate, UniverseCandidateRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-14"


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 14, 8, 30, 0))
    return lambda: fixed


def _settings() -> UniverseFilterSettings:
    return UniverseFilterSettings(
        min_price=5_000,
        max_price=200_000,
        min_avg_trade_value_20=100_000_000,
    )


def _accepted_items() -> list[UniverseFilterInput]:
    return [
        UniverseFilterInput(
            symbol="005930",
            name="Samsung Electronics",
            market="KOSPI",
            close_price=70500,
            prev_day_trade_value=950_000_000_000,
            avg_trade_value_20=880_000_000_000,
        ),
        UniverseFilterInput(
            symbol="035420",
            name="NAVER",
            market="KOSPI",
            close_price=180000,
            prev_day_trade_value=410_000_000_000,
            avg_trade_value_20=350_000_000_000,
        ),
    ]


def _rejected_items() -> list[UniverseFilterInput]:
    return [
        UniverseFilterInput(
            symbol="069500",
            name="KODEX 200",
            market="ETF",
            close_price=36250,
            prev_day_trade_value=120_000_000_000,
            avg_trade_value_20=110_000_000_000,
            is_etf=True,
        )
    ]


def _seed_existing_snapshot(conn, repo):
    with transaction(conn):
        repo.replace_for_date(
            trade_date=TRADE_DATE,
            candidates=[
                UniverseCandidate(
                    symbol="000660",
                    name="SK hynix",
                    market="KOSPI",
                    close_price=206000,
                    prev_day_trade_value=320_000_000_000,
                )
            ],
            refreshed_at="2026-04-14T08:00:00+09:00",
        )


def _make_conn(test_db_path):
    run_migrations(test_db_path)
    return get_connection(test_db_path)


def test_build_snapshot_dry_run_does_not_save(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        repo = UniverseCandidateRepository(conn)
        build_service = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=None,
        )

        result = build_service.build_snapshot(
            trade_date=TRADE_DATE,
            items=_accepted_items(),
            settings=_settings(),
            write=False,
        )

        assert isinstance(result, UniverseBuildResult)
        assert result.outcome == UniverseBuildOutcome.DRY_RUN
        assert result.filter_result.accepted_count == 2
        assert result.refresh_result is None
        assert repo.list_for_date(TRADE_DATE) == []
    finally:
        conn.close()


def test_build_snapshot_write_saves_snapshot(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        repo = UniverseCandidateRepository(conn)
        build_service = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=UniverseRefreshService(
                conn=conn,
                universe_repo=repo,
                now_fn=_fixed_now(),
            ),
        )

        result = build_service.build_snapshot(
            trade_date=TRADE_DATE,
            items=_accepted_items(),
            settings=_settings(),
            write=True,
        )

        assert result.outcome == UniverseBuildOutcome.SAVED
        assert result.refresh_result is not None
        assert result.refresh_result.candidate_count == 2

        rows = repo.list_for_date(TRADE_DATE)
        assert [row.symbol for row in rows] == ["005930", "035420"]
    finally:
        conn.close()


def test_build_snapshot_skips_empty_save_by_default(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        repo = UniverseCandidateRepository(conn)
        _seed_existing_snapshot(conn, repo)

        build_service = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=UniverseRefreshService(
                conn=conn,
                universe_repo=repo,
                now_fn=_fixed_now(),
            ),
        )

        result = build_service.build_snapshot(
            trade_date=TRADE_DATE,
            items=_rejected_items(),
            settings=_settings(),
            write=True,
            allow_empty_save=False,
        )

        assert result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY
        assert result.refresh_result is None

        rows = repo.list_for_date(TRADE_DATE)
        assert [row.symbol for row in rows] == ["000660"]
    finally:
        conn.close()


def test_build_snapshot_allows_empty_save_when_enabled(test_db_path):
    conn = _make_conn(test_db_path)
    try:
        repo = UniverseCandidateRepository(conn)
        _seed_existing_snapshot(conn, repo)

        build_service = UniverseBuildService(
            filter_service=UniverseFilterService(),
            refresh_service=UniverseRefreshService(
                conn=conn,
                universe_repo=repo,
                now_fn=_fixed_now(),
            ),
        )

        result = build_service.build_snapshot(
            trade_date=TRADE_DATE,
            items=_rejected_items(),
            settings=_settings(),
            write=True,
            allow_empty_save=True,
        )

        assert result.outcome == UniverseBuildOutcome.SAVED
        assert result.refresh_result is not None
        assert result.refresh_result.candidate_count == 0
        assert repo.list_for_date(TRADE_DATE) == []
    finally:
        conn.close()
