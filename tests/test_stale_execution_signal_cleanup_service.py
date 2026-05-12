"""Tests for StaleExecutionSignalCleanupService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from services import (
    STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
    StaleExecutionSignalCleanupOutcome,
    StaleExecutionSignalCleanupService,
    StaleExecutionSignalCleanupSettings,
)
from services.timing2_30s_trigger_service import (
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import SignalRepository


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, second))


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _record_buy_signal(
    conn,
    signal_repo: SignalRepository,
    *,
    scanned_at: str,
) -> int:
    with transaction(conn):
        row = signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
            scanned_at=scanned_at,
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )
    return row.id


def test_cleanup_marks_stale_signal_acted_and_records_audit(conn):
    signal_repo = SignalRepository(conn)
    signal_id = _record_buy_signal(
        conn,
        signal_repo,
        scanned_at="2026-04-16T09:00:00+09:00",
    )
    service = StaleExecutionSignalCleanupService(
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 10, 1),
    )

    result = service.cleanup_stale_signals(
        trade_date=TRADE_DATE,
        strategy_names=(STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,),
        audit_strategy_name=STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        settings=StaleExecutionSignalCleanupSettings(
            max_signal_age_seconds=300,
            signal_limit=50,
        ),
        execute_cleanup=True,
    )

    assert result.matched_signal_count == 1
    assert result.cleaned_count == 1
    assert result.acted_count == 1
    assert result.audit_record_count == 1
    assert result.candidates[0].signal_id == signal_id
    assert result.candidates[0].outcome == StaleExecutionSignalCleanupOutcome.CLEANED
    assert result.candidates[0].reason_code == "STALE_SIGNAL_AGE_EXCEEDED"
    assert result.candidates[0].acted is True
    assert signal_repo.get(signal_id).acted is True

    audit_rows = signal_repo.list_by_strategy(
        STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        limit=10,
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload is not None
    assert audit_rows[0].payload["source_signal_id"] == signal_id
    assert (
        audit_rows[0].payload["source_strategy_name"]
        == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
    )
    assert audit_rows[0].payload["max_signal_age_seconds"] == 300


def test_cleanup_preview_leaves_stale_signal_unacted(conn):
    signal_repo = SignalRepository(conn)
    signal_id = _record_buy_signal(
        conn,
        signal_repo,
        scanned_at="2026-04-16T09:00:00+09:00",
    )
    service = StaleExecutionSignalCleanupService(
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 10, 1),
    )

    result = service.cleanup_stale_signals(
        trade_date=TRADE_DATE,
        strategy_names=(STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,),
        audit_strategy_name=STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        settings=StaleExecutionSignalCleanupSettings(
            max_signal_age_seconds=300,
            signal_limit=50,
        ),
        execute_cleanup=False,
    )

    assert result.preview_ready_count == 1
    assert result.cleaned_count == 0
    assert result.acted_count == 0
    assert (
        result.candidates[0].outcome
        == StaleExecutionSignalCleanupOutcome.PREVIEW_READY
    )
    assert signal_repo.get(signal_id).acted is False
    assert signal_repo.list_by_strategy(
        STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        limit=10,
    ) == []


def test_cleanup_skips_fresh_signal(conn):
    signal_repo = SignalRepository(conn)
    signal_id = _record_buy_signal(
        conn,
        signal_repo,
        scanned_at="2026-04-16T09:08:00+09:00",
    )
    service = StaleExecutionSignalCleanupService(
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 10, 1),
    )

    result = service.cleanup_stale_signals(
        trade_date=TRADE_DATE,
        strategy_names=(STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,),
        audit_strategy_name=STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        settings=StaleExecutionSignalCleanupSettings(
            max_signal_age_seconds=300,
            signal_limit=50,
        ),
        execute_cleanup=True,
    )

    assert result.skipped_count == 1
    assert result.cleaned_count == 0
    assert result.candidates[0].reason_code == "NOT_STALE_YET"
    assert signal_repo.get(signal_id).acted is False


def test_cleanup_blocks_future_signal_timestamp(conn):
    signal_repo = SignalRepository(conn)
    signal_id = _record_buy_signal(
        conn,
        signal_repo,
        scanned_at="2026-04-16T09:15:00+09:00",
    )
    service = StaleExecutionSignalCleanupService(
        conn=conn,
        signal_repo=signal_repo,
        now_fn=lambda: _kst_datetime(9, 10, 1),
    )

    result = service.cleanup_stale_signals(
        trade_date=TRADE_DATE,
        strategy_names=(STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,),
        audit_strategy_name=STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT,
        settings=StaleExecutionSignalCleanupSettings(
            max_signal_age_seconds=300,
            signal_limit=50,
        ),
        execute_cleanup=True,
    )

    assert result.blocked_count == 1
    assert result.cleaned_count == 0
    assert result.candidates[0].reason_code == "SIGNAL_TIMESTAMP_IN_FUTURE"
    assert signal_repo.get(signal_id).acted is False
