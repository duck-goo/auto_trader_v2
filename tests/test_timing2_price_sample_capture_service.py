"""Tests for Timing2PriceSampleCaptureService."""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from broker.kis.models import PriceSnapshot
from services import (
    STRATEGY_NAME_TIMING2_SETUP,
    Timing2PriceSampleCaptureOutcome,
    Timing2PriceSampleCaptureService,
)
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import (
    CurrentPriceSampleRepository,
    SignalRepository,
)


KST = pytz.timezone("Asia/Seoul")
SCAN_TIME = "2026-04-16T08:55:00+09:00"
CAPTURE_TIME = KST.localize(datetime(2026, 4, 16, 9, 0, 31))


class _FakeBroker:
    def __init__(
        self,
        snapshots: dict[str, PriceSnapshot] | None = None,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self._snapshots = snapshots or {}
        self._failures = failures or {}
        self.calls: list[str] = []

    def get_current_price(self, code: str) -> PriceSnapshot:
        self.calls.append(code)
        if code in self._failures:
            raise self._failures[code]
        return self._snapshots[code]


@pytest.fixture
def conn(test_db_path):
    run_migrations(test_db_path)
    connection = get_connection(test_db_path)
    try:
        yield connection
    finally:
        connection.close()


def _snapshot(
    symbol: str,
    *,
    observed_at: datetime = CAPTURE_TIME,
    price: int = 1001,
) -> PriceSnapshot:
    return PriceSnapshot(
        code=symbol,
        name="",
        price=price,
        open=1000,
        high=max(1000, price),
        low=min(1000, price),
        prev_close=950,
        change=price - 950,
        change_rate=((price / 950) - 1.0) * 100,
        volume=1000,
        timestamp=observed_at,
    )


def _record_setup_signal(
    conn,
    signal_repo: SignalRepository,
    *,
    symbol: str,
    trade_date: str = "2026-04-16",
) -> None:
    with transaction(conn):
        signal_repo.record(
            symbol=symbol,
            strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            scanned_at=SCAN_TIME,
            payload={
                "trade_date": trade_date,
                "symbol": symbol,
                "name": f"name-{symbol}",
                "market": "KOSPI",
            },
        )


def test_capture_preview_reads_setup_signals_without_writing_samples(conn):
    signal_repo = SignalRepository(conn)
    sample_repo = CurrentPriceSampleRepository(conn)
    _record_setup_signal(conn, signal_repo, symbol="005930")

    service = Timing2PriceSampleCaptureService(
        broker=_FakeBroker({"005930": _snapshot("005930")}),
        conn=conn,
        signal_repo=signal_repo,
        sample_repo=sample_repo,
        now_fn=lambda: CAPTURE_TIME,
    )

    result = service.capture(trade_date="2026-04-16", write_samples=False)

    assert result.preview_ready_count == 1
    assert result.captured_count == 0
    assert result.failed_count == 0
    assert result.candidates[0].outcome == Timing2PriceSampleCaptureOutcome.PREVIEW_READY
    assert sample_repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    ) == []


def test_capture_write_persists_current_price_sample(conn):
    signal_repo = SignalRepository(conn)
    sample_repo = CurrentPriceSampleRepository(conn)
    _record_setup_signal(conn, signal_repo, symbol="005930")

    service = Timing2PriceSampleCaptureService(
        broker=_FakeBroker({"005930": _snapshot("005930", price=1005)}),
        conn=conn,
        signal_repo=signal_repo,
        sample_repo=sample_repo,
        now_fn=lambda: CAPTURE_TIME,
    )

    result = service.capture(trade_date="2026-04-16", write_samples=True)

    assert result.captured_count == 1
    assert result.failed_count == 0
    assert result.candidates[0].outcome == Timing2PriceSampleCaptureOutcome.CAPTURED
    assert result.candidates[0].stored_row is not None

    rows = sample_repo.list_for_symbol_and_date(
        trade_date="2026-04-16",
        symbol="005930",
    )
    assert len(rows) == 1
    assert rows[0].price == 1005
    assert rows[0].source == "kis_current_price"
    assert rows[0].captured_at == CAPTURE_TIME.isoformat()


def test_capture_isolates_per_symbol_broker_failure(conn):
    signal_repo = SignalRepository(conn)
    sample_repo = CurrentPriceSampleRepository(conn)
    _record_setup_signal(conn, signal_repo, symbol="005930")
    _record_setup_signal(conn, signal_repo, symbol="000660")

    service = Timing2PriceSampleCaptureService(
        broker=_FakeBroker(
            {"005930": _snapshot("005930")},
            {"000660": RuntimeError("temporary API failure")},
        ),
        conn=conn,
        signal_repo=signal_repo,
        sample_repo=sample_repo,
        now_fn=lambda: CAPTURE_TIME,
    )

    result = service.capture(trade_date="2026-04-16", write_samples=True)

    assert result.captured_count == 1
    assert result.failed_count == 1
    failed = [
        candidate
        for candidate in result.candidates
        if candidate.outcome == Timing2PriceSampleCaptureOutcome.FAILED
    ][0]
    assert failed.symbol == "000660"
    assert "temporary API failure" in str(failed.reason)
    assert len(
        sample_repo.list_for_symbol_and_date(
            trade_date="2026-04-16",
            symbol="005930",
        )
    ) == 1


def test_capture_respects_max_symbols_without_fetching_skipped_symbols(conn):
    signal_repo = SignalRepository(conn)
    sample_repo = CurrentPriceSampleRepository(conn)
    _record_setup_signal(conn, signal_repo, symbol="111111")
    _record_setup_signal(conn, signal_repo, symbol="222222")
    _record_setup_signal(conn, signal_repo, symbol="333333")

    broker = _FakeBroker(
        {
            "111111": _snapshot("111111"),
            "222222": _snapshot("222222"),
            "333333": _snapshot("333333"),
        }
    )
    service = Timing2PriceSampleCaptureService(
        broker=broker,
        conn=conn,
        signal_repo=signal_repo,
        sample_repo=sample_repo,
        now_fn=lambda: CAPTURE_TIME,
    )

    result = service.capture(
        trade_date="2026-04-16",
        write_samples=True,
        max_symbols=2,
    )

    assert result.setup_signal_count == 3
    assert result.candidate_count == 2
    assert result.skipped_by_limit_count == 1
    assert result.captured_count == 2
    assert len(broker.calls) == 2


def test_capture_rejects_snapshot_for_different_trade_date(conn):
    signal_repo = SignalRepository(conn)
    sample_repo = CurrentPriceSampleRepository(conn)
    _record_setup_signal(conn, signal_repo, symbol="005930")

    service = Timing2PriceSampleCaptureService(
        broker=_FakeBroker(
            {
                "005930": _snapshot(
                    "005930",
                    observed_at=KST.localize(datetime(2026, 4, 17, 9, 0, 31)),
                )
            }
        ),
        conn=conn,
        signal_repo=signal_repo,
        sample_repo=sample_repo,
        now_fn=lambda: CAPTURE_TIME,
    )

    result = service.capture(trade_date="2026-04-16", write_samples=True)

    assert result.captured_count == 0
    assert result.failed_count == 1
    assert "trade_date mismatch" in str(result.candidates[0].reason)


def test_capture_requires_setup_signals(conn):
    service = Timing2PriceSampleCaptureService(
        broker=_FakeBroker(),
        conn=conn,
        signal_repo=SignalRepository(conn),
        sample_repo=CurrentPriceSampleRepository(conn),
        now_fn=lambda: CAPTURE_TIME,
    )

    with pytest.raises(Exception, match="Timing2 setup signals are missing"):
        service.capture(trade_date="2026-04-16", write_samples=False)
