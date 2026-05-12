"""Tests for IntradayBar15mRefreshService."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pytz

from services import (
    IntradayBar15mRefreshOutcome,
    IntradayBar15mRefreshService,
)


KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class _FakePosition:
    symbol: str
    qty: int
    avg_price: int


class _FakeBroker:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.calls: list[dict[str, object]] = []

    def get_same_day_minute_candles(self, code: str, *, end_time: str | None = None):
        self.calls.append({"code": code, "end_time": end_time})
        return self._df.copy(deep=True)


class _FakeRetryBroker:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get_same_day_minute_candles(self, code: str, *, end_time: str | None = None):
        self.calls.append({"code": code, "end_time": end_time})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response.copy(deep=True)


class _FakePositionRepo:
    def __init__(self, rows: list[_FakePosition]) -> None:
        self._rows = rows

    def list_all(self) -> list[_FakePosition]:
        return list(self._rows)


class _FakeIntradayBarRepo:
    def __init__(self, existing_rows: list[object]) -> None:
        self._existing_rows = existing_rows
        self.replace_calls: list[dict[str, object]] = []

    def list_for_symbol_and_date(self, *, trade_date: str, symbol: str):
        return list(self._existing_rows)

    def replace_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
        bars,
        refreshed_at: str,
    ):
        self.replace_calls.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "bars": list(bars),
                "refreshed_at": refreshed_at,
            }
        )
        return list(bars)


def _minute_df() -> pd.DataFrame:
    rows = []
    for minute in range(15):
        rows.append(
            {
                "datetime": KST.localize(datetime(2026, 4, 17, 9, minute, 0)),
                "open": 100 + minute,
                "high": 101 + minute,
                "low": 99 + minute,
                "close": 100 + minute,
                "volume": 10,
            }
        )
    return pd.DataFrame(rows)


def test_refresh_live_positions_preview_ready_for_completed_bar():
    conn = sqlite3.connect(":memory:")
    try:
        service = IntradayBar15mRefreshService(
            broker=_FakeBroker(_minute_df()),
            conn=conn,
            position_repo=_FakePositionRepo(
                [_FakePosition(symbol="005930", qty=1, avg_price=70000)]
            ),
            intraday_bar_repo=_FakeIntradayBarRepo([]),
            now_fn=lambda: KST.localize(datetime(2026, 4, 17, 9, 20, 0)),
        )

        result = service.refresh_live_positions(
            trade_date="2026-04-17",
            write=False,
        )

        assert result.preview_ready_count == 1
        assert result.refreshed_symbol_count == 0
        assert result.candidates[0].outcome == IntradayBar15mRefreshOutcome.PREVIEW_READY
        assert result.candidates[0].completed_bar_count == 1
    finally:
        conn.close()


def test_refresh_live_positions_skips_regression_without_replacing_rows():
    conn = sqlite3.connect(":memory:")
    repo = _FakeIntradayBarRepo(existing_rows=[object(), object()])
    try:
        service = IntradayBar15mRefreshService(
            broker=_FakeBroker(_minute_df()),
            conn=conn,
            position_repo=_FakePositionRepo(
                [_FakePosition(symbol="005930", qty=1, avg_price=70000)]
            ),
            intraday_bar_repo=repo,
            now_fn=lambda: KST.localize(datetime(2026, 4, 17, 9, 20, 0)),
        )

        result = service.refresh_live_positions(
            trade_date="2026-04-17",
            write=True,
        )

        assert result.skipped_count == 1
        assert result.candidates[0].outcome == (
            IntradayBar15mRefreshOutcome.SKIPPED_REGRESSION
        )
        assert repo.replace_calls == []
    finally:
        conn.close()


def test_refresh_live_positions_retries_rate_limit_then_recovers():
    conn = sqlite3.connect(":memory:")
    sleep_calls: list[float] = []
    broker = _FakeRetryBroker(
        responses=[
            RuntimeError("HTTP 500 | msg_cd=EGW00201 | 초당 거래건수를 초과하였습니다."),
            _minute_df(),
        ]
    )
    try:
        service = IntradayBar15mRefreshService(
            broker=broker,
            conn=conn,
            position_repo=_FakePositionRepo(
                [_FakePosition(symbol="005930", qty=1, avg_price=70000)]
            ),
            intraday_bar_repo=_FakeIntradayBarRepo([]),
            now_fn=lambda: KST.localize(datetime(2026, 4, 17, 9, 20, 0)),
            minute_candle_retry_count=2,
            minute_candle_retry_delay_seconds=0.5,
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        result = service.refresh_live_positions(
            trade_date="2026-04-17",
            write=False,
        )

        assert result.failed_count == 0
        assert result.preview_ready_count == 1
        assert result.candidates[0].outcome == IntradayBar15mRefreshOutcome.PREVIEW_READY
        assert len(broker.calls) == 2
        assert sleep_calls == [0.5]
    finally:
        conn.close()


def test_refresh_live_positions_does_not_retry_non_rate_limit_error():
    conn = sqlite3.connect(":memory:")
    sleep_calls: list[float] = []
    broker = _FakeRetryBroker(
        responses=[
            RuntimeError("upstream parse failure"),
        ]
    )
    try:
        service = IntradayBar15mRefreshService(
            broker=broker,
            conn=conn,
            position_repo=_FakePositionRepo(
                [_FakePosition(symbol="005930", qty=1, avg_price=70000)]
            ),
            intraday_bar_repo=_FakeIntradayBarRepo([]),
            now_fn=lambda: KST.localize(datetime(2026, 4, 17, 9, 20, 0)),
            minute_candle_retry_count=2,
            minute_candle_retry_delay_seconds=0.5,
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        result = service.refresh_live_positions(
            trade_date="2026-04-17",
            write=False,
        )

        assert result.failed_count == 1
        assert result.candidates[0].outcome == IntradayBar15mRefreshOutcome.FAILED
        assert "upstream parse failure" in (result.candidates[0].reason or "")
        assert len(broker.calls) == 1
        assert sleep_calls == []
    finally:
        conn.close()
