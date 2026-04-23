"""Readiness helper for persisted Timing2 setup signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.timing2_setup_scan_service import STRATEGY_NAME_TIMING2_SETUP
from storage.repositories import SignalRepository


@dataclass(frozen=True)
class Timing2SetupSignalReadiness:
    trade_date: str
    required: bool
    setup_signal_count: int | None
    ready: bool | None
    reason: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "required": self.required,
            "setup_signal_count": self.setup_signal_count,
            "ready": self.ready,
            "reason": self.reason,
        }


def inspect_timing2_setup_signal_readiness(
    *,
    signal_repo: SignalRepository,
    trade_date: str,
    run_timing2: bool,
    limit: int = 5000,
) -> Timing2SetupSignalReadiness:
    normalized_trade_date = _require_trade_date(trade_date)
    normalized_limit = _require_positive_int("limit", limit)

    if not run_timing2:
        return Timing2SetupSignalReadiness(
            trade_date=normalized_trade_date,
            required=False,
            setup_signal_count=None,
            ready=None,
            reason=None,
        )

    setup_signal_count = count_timing2_setup_signal_symbols(
        signal_repo=signal_repo,
        trade_date=normalized_trade_date,
        limit=normalized_limit,
    )
    reason = None
    if setup_signal_count == 0:
        reason = (
            "Timing2 setup signals are missing for this trade date. "
            "Timing2 intraday buy scans will be skipped, but sell/maintenance "
            "flows should continue."
        )

    return Timing2SetupSignalReadiness(
        trade_date=normalized_trade_date,
        required=True,
        setup_signal_count=setup_signal_count,
        ready=setup_signal_count > 0,
        reason=reason,
    )


def count_timing2_setup_signal_symbols(
    *,
    signal_repo: SignalRepository,
    trade_date: str,
    limit: int = 5000,
) -> int:
    normalized_trade_date = _require_trade_date(trade_date)
    normalized_limit = _require_positive_int("limit", limit)
    rows = signal_repo.list_by_strategy(
        STRATEGY_NAME_TIMING2_SETUP,
        limit=normalized_limit,
    )

    symbols: set[str] = set()
    for row in rows:
        if not row.payload:
            continue
        if row.payload.get("trade_date") != normalized_trade_date:
            continue
        symbols.add(row.symbol)
    return len(symbols)


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"trade_date must be non-empty text: {value!r}")
    return value.strip()


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value
