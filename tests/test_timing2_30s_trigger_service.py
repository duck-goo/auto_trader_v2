"""Tests for Timing2ThirtySecondTriggerService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from services import (
    STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
    STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
    STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
    STRATEGY_NAME_TIMING2_SETUP,
    Timing2ThirtySecondTriggerOutcome,
    Timing2ThirtySecondTriggerService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar30s,
    IntradayBar30sRepository,
    SignalRepository,
)
from strategy import Timing2ThirtySecondTriggerSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-16"
SCANNED_AT = "2026-04-16T09:00:31+09:00"


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _kst_datetime(hour: int, minute: int, second: int = 0) -> datetime:
    return KST.localize(datetime(2026, 4, 16, hour, minute, second))


def _bar(
    *,
    start: str,
    end: str,
    open_price: int,
    close: int,
    volume: int = 100,
) -> IntradayBar30s:
    return IntradayBar30s(
        bar_start_at=start,
        bar_end_at=end,
        open=open_price,
        high=max(open_price, close),
        low=min(open_price, close),
        close=close,
        volume=volume,
    )


def _seed_setup_signal(conn) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            scanned_at="2026-04-16T08:55:00+09:00",
            payload={
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )


def _seed_signal(
    conn,
    *,
    strategy_name: str,
    scanned_at: str,
    payload: dict | None = None,
) -> None:
    signal_repo = SignalRepository(conn)
    with transaction(conn):
        signal_repo.record(
            symbol="005930",
            strategy_name=strategy_name,
            scanned_at=scanned_at,
            payload=payload
            or {
                "trade_date": TRADE_DATE,
                "symbol": "005930",
                "name": "Samsung Electronics",
                "market": "KOSPI",
            },
        )


def _upsert_bars(conn, bars: list[IntradayBar30s]) -> None:
    repo = IntradayBar30sRepository(conn)
    with transaction(conn):
        repo.upsert_many_for_symbol_and_date(
            trade_date=TRADE_DATE,
            symbol="005930",
            bars=bars,
            refreshed_at=SCANNED_AT,
        )


def _service(conn, *, now: datetime) -> Timing2ThirtySecondTriggerService:
    return Timing2ThirtySecondTriggerService(
        conn=conn,
        signal_repo=SignalRepository(conn),
        intraday_bar_repo=IntradayBar30sRepository(conn),
        now_fn=lambda: now,
    )


def test_scan_records_morning_dip_state_without_buy_trigger(conn):
    _seed_setup_signal(conn)
    _upsert_bars(
        conn,
        [
            _bar(
                start="2026-04-16T09:00:00+09:00",
                end="2026-04-16T09:00:30+09:00",
                open_price=1000,
                close=990,
            )
        ],
    )

    result = _service(conn, now=_kst_datetime(9, 0, 31)).scan(
        trade_date=TRADE_DATE,
        settings=Timing2ThirtySecondTriggerSettings(),
        write_signals=True,
    )

    assert result.evaluated_count == 1
    assert result.transition_count == 1
    assert result.buy_triggered_count == 0
    assert result.recorded_count == 1
    candidate = result.candidates[0]
    assert candidate.outcome == Timing2ThirtySecondTriggerOutcome.EVALUATED
    assert candidate.transition_strategy_name == STRATEGY_NAME_TIMING2_30S_MORNING_DIP
    assert candidate.decision is not None
    assert candidate.decision.state_after.morning_dipped_below_open is True


def test_scan_records_morning_open_reclaim_buy_after_prior_dip(conn):
    _seed_setup_signal(conn)
    _seed_signal(
        conn,
        strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
        scanned_at="2026-04-16T09:00:31+09:00",
    )
    _upsert_bars(
        conn,
        [
            _bar(
                start="2026-04-16T09:00:00+09:00",
                end="2026-04-16T09:00:30+09:00",
                open_price=1000,
                close=990,
            ),
            _bar(
                start="2026-04-16T09:00:30+09:00",
                end="2026-04-16T09:01:00+09:00",
                open_price=990,
                close=1001,
            ),
        ],
    )

    result = _service(conn, now=_kst_datetime(9, 1, 1)).scan(
        trade_date=TRADE_DATE,
        settings=Timing2ThirtySecondTriggerSettings(),
        write_signals=True,
    )

    assert result.transition_count == 1
    assert result.buy_triggered_count == 1
    assert result.recorded_count == 1
    candidate = result.candidates[0]
    assert (
        candidate.transition_strategy_name
        == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
    )
    assert candidate.decision is not None
    assert candidate.decision.buy_triggered is True

    rows = SignalRepository(conn).list_by_symbol("005930")
    assert any(
        row.strategy_name == STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER
        and row.payload
        and row.payload.get("buy_triggered") is True
        for row in rows
    )


def test_scan_records_after_10_range_breakout_even_after_morning_trigger(conn):
    _seed_setup_signal(conn)
    _seed_signal(
        conn,
        strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_DIP,
        scanned_at="2026-04-16T09:00:31+09:00",
    )
    _seed_signal(
        conn,
        strategy_name=STRATEGY_NAME_TIMING2_30S_MORNING_TRIGGER,
        scanned_at="2026-04-16T09:01:01+09:00",
    )
    _upsert_bars(
        conn,
        [
            _bar(
                start="2026-04-16T09:00:00+09:00",
                end="2026-04-16T09:00:30+09:00",
                open_price=1000,
                close=1000,
            ),
            _bar(
                start="2026-04-16T09:59:30+09:00",
                end="2026-04-16T10:00:00+09:00",
                open_price=1090,
                close=1100,
            ),
            _bar(
                start="2026-04-16T10:00:00+09:00",
                end="2026-04-16T10:00:30+09:00",
                open_price=1100,
                close=1101,
            ),
        ],
    )

    result = _service(conn, now=_kst_datetime(10, 0, 31)).scan(
        trade_date=TRADE_DATE,
        settings=Timing2ThirtySecondTriggerSettings(),
        write_signals=True,
    )

    assert result.transition_count == 1
    assert result.buy_triggered_count == 1
    assert result.recorded_count == 1
    candidate = result.candidates[0]
    assert candidate.transition_strategy_name == STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER
    assert candidate.decision is not None
    assert candidate.decision.morning_high_close == 1100
    assert candidate.decision.state_before.morning_triggered is True
    assert candidate.decision.state_after.range_triggered is True


def test_scan_does_not_repeat_after_range_trigger_recorded(conn):
    _seed_setup_signal(conn)
    _seed_signal(
        conn,
        strategy_name=STRATEGY_NAME_TIMING2_30S_RANGE_TRIGGER,
        scanned_at="2026-04-16T10:00:31+09:00",
    )
    _upsert_bars(
        conn,
        [
            _bar(
                start="2026-04-16T09:59:30+09:00",
                end="2026-04-16T10:00:00+09:00",
                open_price=1090,
                close=1100,
            ),
            _bar(
                start="2026-04-16T10:00:00+09:00",
                end="2026-04-16T10:00:30+09:00",
                open_price=1100,
                close=1110,
            ),
        ],
    )

    result = _service(conn, now=_kst_datetime(10, 0, 31)).scan(
        trade_date=TRADE_DATE,
        settings=Timing2ThirtySecondTriggerSettings(),
        write_signals=True,
    )

    assert result.evaluated_count == 1
    assert result.transition_count == 0
    assert result.buy_triggered_count == 0
    assert result.recorded_count == 0


def test_scan_skips_when_no_30s_bar_is_available(conn):
    _seed_setup_signal(conn)

    result = _service(conn, now=_kst_datetime(9, 0, 31)).scan(
        trade_date=TRADE_DATE,
        settings=Timing2ThirtySecondTriggerSettings(),
        write_signals=False,
    )

    assert result.skipped_count == 1
    assert result.candidates[0].outcome == (
        Timing2ThirtySecondTriggerOutcome.SKIPPED_NO_30S_BAR
    )


def test_scan_requires_timing2_setup_signals(conn):
    with pytest.raises(Exception, match="Timing2 setup signals are missing"):
        _service(conn, now=_kst_datetime(9, 0, 31)).scan(
            trade_date=TRADE_DATE,
            settings=Timing2ThirtySecondTriggerSettings(),
            write_signals=False,
        )
