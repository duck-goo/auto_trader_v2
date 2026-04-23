"""Tests for Timing2 setup-signal readiness checks."""

from __future__ import annotations

from dataclasses import dataclass

from services import (
    STRATEGY_NAME_TIMING2_SETUP,
    count_timing2_setup_signal_symbols,
    inspect_timing2_setup_signal_readiness,
)


TRADE_DATE = "2026-04-20"


@dataclass(frozen=True)
class _FakeSignalRow:
    symbol: str
    strategy_name: str
    payload: dict | None


class _FakeSignalRepository:
    def __init__(self, rows: list[_FakeSignalRow]) -> None:
        self._rows = rows

    def list_by_strategy(self, strategy_name: str, *, limit: int = 100):
        return [
            row
            for row in self._rows[:limit]
            if row.strategy_name == strategy_name
        ]


def _setup_row(symbol: str, *, trade_date: str = TRADE_DATE) -> _FakeSignalRow:
    return _FakeSignalRow(
        symbol=symbol,
        strategy_name=STRATEGY_NAME_TIMING2_SETUP,
        payload={"trade_date": trade_date, "symbol": symbol},
    )


def test_count_timing2_setup_signal_symbols_counts_unique_trade_date_symbols():
    repo = _FakeSignalRepository(
        [
            _setup_row("005930"),
            _setup_row("005930"),
            _setup_row("000660"),
            _setup_row("035420", trade_date="2026-04-19"),
            _FakeSignalRow(
                symbol="111111",
                strategy_name=STRATEGY_NAME_TIMING2_SETUP,
                payload=None,
            ),
            _FakeSignalRow(
                symbol="222222",
                strategy_name="other_strategy",
                payload={"trade_date": TRADE_DATE},
            ),
        ]
    )

    assert (
        count_timing2_setup_signal_symbols(
            signal_repo=repo,
            trade_date=TRADE_DATE,
        )
        == 2
    )


def test_inspect_timing2_setup_signal_readiness_is_optional_when_not_requested():
    readiness = inspect_timing2_setup_signal_readiness(
        signal_repo=_FakeSignalRepository([]),
        trade_date=TRADE_DATE,
        run_timing2=False,
    )

    assert readiness.to_payload() == {
        "trade_date": TRADE_DATE,
        "required": False,
        "setup_signal_count": None,
        "ready": None,
        "reason": None,
    }


def test_inspect_timing2_setup_signal_readiness_warns_when_required_but_missing():
    readiness = inspect_timing2_setup_signal_readiness(
        signal_repo=_FakeSignalRepository([]),
        trade_date=TRADE_DATE,
        run_timing2=True,
    )

    assert readiness.required is True
    assert readiness.setup_signal_count == 0
    assert readiness.ready is False
    assert "Timing2 setup signals are missing" in str(readiness.reason)
