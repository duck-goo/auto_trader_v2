"""Tests for SellMacdExitScanService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from services import SellMacdExitScanService, STRATEGY_NAME_SELL_MACD_DECREASE
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    IntradayBar15m,
    IntradayBar15mRepository,
    PositionRepository,
    SignalRepository,
)
from strategy import SellMacdExitMatch, SellMacdExitSettings


KST = pytz.timezone("Asia/Seoul")
TRADE_DATE = "2026-04-17"


class StubEvaluator:
    def evaluate(self, *, symbol, intraday_bars, settings):
        if symbol != "005930":
            return None
        return SellMacdExitMatch(
            symbol=symbol,
            bar_start_at="2026-04-17T10:00:00+09:00",
            bar_end_at="2026-04-17T10:15:00+09:00",
            close_price=98_000,
            macd_value=1.0,
            signal_value=0.8,
            hist_t_minus_2=0.5,
            hist_t_minus_1=0.2,
            hist_t=-0.1,
            consecutive_decline_bars=settings.consecutive_decline_bars,
        )


def _fixed_now():
    fixed = KST.localize(datetime(2026, 4, 17, 10, 20, 0))
    return lambda: fixed


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_scan_records_signal_and_dedupes_second_run(conn):
    position_repo = PositionRepository(conn)
    signal_repo = SignalRepository(conn)
    intraday_repo = IntradayBar15mRepository(conn)

    with transaction(conn):
        position_repo.upsert_from_broker(
            symbol="005930",
            qty=4,
            avg_price=100_000,
            updated_at="2026-04-17T09:00:00+09:00",
        )
        intraday_repo.replace_for_symbol_and_date(
            trade_date=TRADE_DATE,
            symbol="005930",
            bars=[
                IntradayBar15m(
                    bar_start_at="2026-04-17T10:00:00+09:00",
                    bar_end_at="2026-04-17T10:15:00+09:00",
                    open=99_000,
                    high=100_000,
                    low=98_000,
                    close=98_000,
                    volume=100,
                )
            ],
            refreshed_at="2026-04-17T10:16:00+09:00",
        )

    service = SellMacdExitScanService(
        conn=conn,
        position_repo=position_repo,
        intraday_bar_repo=intraday_repo,
        signal_repo=signal_repo,
        now_fn=_fixed_now(),
        evaluator=StubEvaluator(),
    )

    first = service.scan(
        trade_date=TRADE_DATE,
        settings=SellMacdExitSettings(),
        history_limit=100,
        write_signals=True,
    )
    second = service.scan(
        trade_date=TRADE_DATE,
        settings=SellMacdExitSettings(),
        history_limit=100,
        write_signals=True,
    )

    assert first.position_count == 1
    assert first.matched_count == 1
    assert first.recorded_count == 1
    assert first.candidates[0].history_bar_count == 1
    assert first.candidates[0].match.close_price == 98_000

    assert second.matched_count == 1
    assert second.recorded_count == 0
    assert second.skipped_existing_count == 1

    stored = signal_repo.list_by_symbol("005930", limit=10)
    assert len(stored) == 1
    assert stored[0].strategy_name == STRATEGY_NAME_SELL_MACD_DECREASE
    assert stored[0].payload["trade_date"] == TRADE_DATE
