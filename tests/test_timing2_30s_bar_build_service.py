"""Tests for Timing2ThirtySecondBarBuildService."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
import pytz

from services import (
    STRATEGY_NAME_TIMING2_SETUP,
    Timing2ThirtySecondBarBuildOutcome,
    Timing2ThirtySecondBarBuildService,
)
from storage.repositories import (
    CurrentPriceSampleRow,
    IntradayBar30s,
    IntradayBar30sRow,
    SignalRow,
)


KST = pytz.timezone("Asia/Seoul")
BUILT_AT = KST.localize(datetime(2026, 4, 16, 9, 1, 0))


class _FakeSignalRepo:
    def __init__(self, rows: list[SignalRow]) -> None:
        self._rows = rows

    def list_by_strategy(self, strategy_name: str, *, limit: int = 100):
        return [row for row in self._rows if row.strategy_name == strategy_name]


class _FakeSampleRepo:
    def __init__(self, rows_by_symbol: dict[str, list[CurrentPriceSampleRow]]) -> None:
        self._rows_by_symbol = rows_by_symbol

    def list_for_symbol_and_date(self, *, trade_date: str, symbol: str):
        return list(self._rows_by_symbol.get(symbol, []))


class _FakeIntradayBarRepo:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, object]] = []

    def upsert_many_for_symbol_and_date(
        self,
        *,
        trade_date: str,
        symbol: str,
        bars,
        refreshed_at: str,
    ):
        stored_rows = [
            IntradayBar30sRow(
                trade_date=trade_date,
                symbol=symbol,
                bar_start_at=bar.bar_start_at,
                bar_end_at=bar.bar_end_at,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                refreshed_at=refreshed_at,
            )
            for bar in bars
        ]
        self.upsert_calls.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "bars": list(bars),
                "refreshed_at": refreshed_at,
            }
        )
        return stored_rows


def _setup_signal(symbol: str = "005930") -> SignalRow:
    return SignalRow(
        id=1,
        scanned_at="2026-04-16T08:55:00+09:00",
        symbol=symbol,
        strategy_name=STRATEGY_NAME_TIMING2_SETUP,
        score=None,
        payload={
            "trade_date": "2026-04-16",
            "symbol": symbol,
            "name": f"name-{symbol}",
            "market": "KOSPI",
        },
        acted=False,
    )


def _sample(observed_at: str, price: int, volume: int) -> CurrentPriceSampleRow:
    return CurrentPriceSampleRow(
        trade_date="2026-04-16",
        symbol="005930",
        observed_at=observed_at,
        price=price,
        open=1000,
        high=max(1000, price),
        low=min(1000, price),
        prev_close=950,
        change=price - 950,
        change_rate=((price / 950) - 1.0) * 100,
        volume=volume,
        source="kis_current_price",
        captured_at=observed_at,
    )


def _service(
    *,
    samples: list[CurrentPriceSampleRow],
    bar_repo: _FakeIntradayBarRepo | None = None,
    setup_signals: list[SignalRow] | None = None,
    now: datetime = BUILT_AT,
) -> Timing2ThirtySecondBarBuildService:
    signal_rows = [_setup_signal()] if setup_signals is None else setup_signals
    return Timing2ThirtySecondBarBuildService(
        conn=sqlite3.connect(":memory:"),
        signal_repo=_FakeSignalRepo(signal_rows),
        sample_repo=_FakeSampleRepo({"005930": samples}),
        intraday_bar_repo=bar_repo or _FakeIntradayBarRepo(),
        now_fn=lambda: now,
    )


def test_build_preview_creates_completed_30_second_bar_without_writing():
    service = _service(
        samples=[
            _sample("2026-04-16T09:00:00+09:00", 1000, 100),
            _sample("2026-04-16T09:00:10+09:00", 1010, 120),
            _sample("2026-04-16T09:00:20+09:00", 1005, 130),
        ]
    )

    result = service.build(
        trade_date="2026-04-16",
        min_samples_per_bar=2,
        write_bars=False,
    )

    assert result.preview_ready_count == 1
    assert result.built_symbol_count == 0
    candidate = result.candidates[0]
    assert candidate.outcome == Timing2ThirtySecondBarBuildOutcome.PREVIEW_READY
    assert candidate.sample_count == 3
    assert candidate.complete_bucket_count == 1
    assert candidate.buildable_bar_count == 1


def test_build_write_upserts_bars():
    bar_repo = _FakeIntradayBarRepo()
    service = _service(
        samples=[
            _sample("2026-04-16T09:00:00+09:00", 1000, 100),
            _sample("2026-04-16T09:00:20+09:00", 1005, 130),
        ],
        bar_repo=bar_repo,
    )

    result = service.build(
        trade_date="2026-04-16",
        min_samples_per_bar=2,
        write_bars=True,
    )

    assert result.built_symbol_count == 1
    assert result.candidates[0].stored_bar_count == 1
    assert len(bar_repo.upsert_calls) == 1
    bars = bar_repo.upsert_calls[0]["bars"]
    assert isinstance(bars, list)
    assert bars == [
        IntradayBar30s(
            bar_start_at="2026-04-16T09:00:00+09:00",
            bar_end_at="2026-04-16T09:00:30+09:00",
            open=1000,
            high=1005,
            low=1000,
            close=1005,
            volume=30,
        )
    ]


def test_build_skips_bucket_when_samples_are_insufficient():
    service = _service(
        samples=[
            _sample("2026-04-16T09:00:00+09:00", 1000, 100),
        ]
    )

    result = service.build(
        trade_date="2026-04-16",
        min_samples_per_bar=2,
        write_bars=False,
    )

    candidate = result.candidates[0]
    assert candidate.outcome == (
        Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_BUILDABLE_BAR
    )
    assert candidate.complete_bucket_count == 1
    assert candidate.skipped_insufficient_sample_count == 1
    assert candidate.buildable_bar_count == 0


def test_build_ignores_current_incomplete_bucket():
    service = _service(
        samples=[
            _sample("2026-04-16T09:00:00+09:00", 1000, 100),
            _sample("2026-04-16T09:00:20+09:00", 1005, 130),
            _sample("2026-04-16T09:00:35+09:00", 1010, 140),
            _sample("2026-04-16T09:00:40+09:00", 1012, 150),
        ],
        now=KST.localize(datetime(2026, 4, 16, 9, 0, 45)),
    )

    result = service.build(
        trade_date="2026-04-16",
        min_samples_per_bar=2,
        write_bars=False,
    )

    candidate = result.candidates[0]
    assert candidate.outcome == Timing2ThirtySecondBarBuildOutcome.PREVIEW_READY
    assert candidate.complete_bucket_count == 1
    assert candidate.buildable_bar_count == 1


def test_build_skips_symbol_without_samples():
    service = _service(samples=[])

    result = service.build(trade_date="2026-04-16")

    assert result.skipped_count == 1
    assert result.candidates[0].outcome == (
        Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_SAMPLES
    )


def test_build_requires_timing2_setup_signals():
    service = _service(samples=[], setup_signals=[])

    with pytest.raises(Exception, match="Timing2 setup signals are missing"):
        service.build(trade_date="2026-04-16")
