"""Tests for IntradayTriggerCombinedScanService error classification."""

from __future__ import annotations

from services import (
    IntradayTriggerCombinedScanService,
    MissingTiming1ConvergenceSignalsError,
    MissingTiming2SetupSignalsError,
)


class _FakeTiming1Service:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def scan(self, **kwargs):
        if self._error is not None:
            raise self._error
        return object()


class _FakeTiming2Service:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def scan(self, **kwargs):
        if self._error is not None:
            raise self._error
        return object()


def _service() -> IntradayTriggerCombinedScanService:
    service = object.__new__(IntradayTriggerCombinedScanService)
    service._timing1_service = _FakeTiming1Service()
    service._timing2_service = _FakeTiming2Service()
    return service


def test_scan_timing1_skips_with_missing_convergence_signal_error():
    service = _service()
    service._timing1_service = _FakeTiming1Service(
        error=MissingTiming1ConvergenceSignalsError(trade_date="2026-04-29")
    )

    result = service._scan_timing1(
        trade_date="2026-04-29",
        settings=object(),
        daily_count=30,
        write_signals=False,
    )

    assert result.outcome == "SKIPPED"
    assert "MissingTiming1ConvergenceSignalsError" in str(result.reason)


def test_scan_timing2_skips_with_missing_setup_signal_error():
    service = _service()
    service._timing2_service = _FakeTiming2Service(
        error=MissingTiming2SetupSignalsError(trade_date="2026-04-29")
    )

    result = service._scan_timing2(
        trade_date="2026-04-29",
        settings=object(),
        write_signals=False,
    )

    assert result.outcome == "SKIPPED"
    assert "MissingTiming2SetupSignalsError" in str(result.reason)
