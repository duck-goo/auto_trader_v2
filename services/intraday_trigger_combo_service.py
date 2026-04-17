"""Combined orchestration service for timing1/timing2 intraday trigger scans."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Union

from broker.base import BrokerInterface
from services.timing1_intraday_trigger_service import (
    Timing1IntradayTriggerScanResult,
    Timing1IntradayTriggerService,
)
from services.timing2_intraday_trigger_service import (
    Timing2IntradayTriggerScanResult,
    Timing2IntradayTriggerService,
)
from storage.repositories import SignalRepository
from strategy import (
    Timing1IntradayTriggerSettings,
    Timing2IntradayTriggerSettings,
)


ScanResultType = Union[
    Timing1IntradayTriggerScanResult,
    Timing2IntradayTriggerScanResult,
]


@dataclass(frozen=True)
class IntradayTriggerStrategyStatus:
    outcome: str
    reason: str | None
    result: ScanResultType | None


@dataclass(frozen=True)
class IntradayTriggerCombinedScanResult:
    trade_date: str
    timing1: IntradayTriggerStrategyStatus
    timing2: IntradayTriggerStrategyStatus


class IntradayTriggerCombinedScanService:
    """Run timing1 and timing2 intraday trigger scans together."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
    ) -> None:
        self._timing1_service = Timing1IntradayTriggerService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
        )
        self._timing2_service = Timing2IntradayTriggerService(
            broker=broker,
            conn=conn,
            signal_repo=signal_repo,
        )

    def scan(
        self,
        *,
        trade_date: str,
        run_timing1: bool,
        run_timing2: bool,
        timing1_settings: Timing1IntradayTriggerSettings,
        timing1_daily_count: int,
        write_timing1_signals: bool,
        timing2_settings: Timing2IntradayTriggerSettings,
        write_timing2_signals: bool,
    ) -> IntradayTriggerCombinedScanResult:
        timing1_status = IntradayTriggerStrategyStatus(
            outcome="SKIPPED",
            reason="Disabled by request.",
            result=None,
        )
        timing2_status = IntradayTriggerStrategyStatus(
            outcome="SKIPPED",
            reason="Disabled by request.",
            result=None,
        )

        if run_timing1:
            timing1_status = self._scan_timing1(
                trade_date=trade_date,
                settings=timing1_settings,
                daily_count=timing1_daily_count,
                write_signals=write_timing1_signals,
            )

        if run_timing2:
            timing2_status = self._scan_timing2(
                trade_date=trade_date,
                settings=timing2_settings,
                write_signals=write_timing2_signals,
            )

        return IntradayTriggerCombinedScanResult(
            trade_date=trade_date,
            timing1=timing1_status,
            timing2=timing2_status,
        )

    def _scan_timing1(
        self,
        *,
        trade_date: str,
        settings: Timing1IntradayTriggerSettings,
        daily_count: int,
        write_signals: bool,
    ) -> IntradayTriggerStrategyStatus:
        try:
            result = self._timing1_service.scan(
                trade_date=trade_date,
                settings=settings,
                daily_count=daily_count,
                write_signals=write_signals,
            )
            return IntradayTriggerStrategyStatus(
                outcome="COMPLETED",
                reason=None,
                result=result,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if "Timing1 convergence signals are missing" in str(exc):
                return IntradayTriggerStrategyStatus(
                    outcome="SKIPPED",
                    reason=message,
                    result=None,
                )
            return IntradayTriggerStrategyStatus(
                outcome="FAILED",
                reason=message,
                result=None,
            )

    def _scan_timing2(
        self,
        *,
        trade_date: str,
        settings: Timing2IntradayTriggerSettings,
        write_signals: bool,
    ) -> IntradayTriggerStrategyStatus:
        try:
            result = self._timing2_service.scan(
                trade_date=trade_date,
                settings=settings,
                write_signals=write_signals,
            )
            return IntradayTriggerStrategyStatus(
                outcome="COMPLETED",
                reason=None,
                result=result,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if "Timing2 setup signals are missing" in str(exc):
                return IntradayTriggerStrategyStatus(
                    outcome="SKIPPED",
                    reason=message,
                    result=None,
                )
            return IntradayTriggerStrategyStatus(
                outcome="FAILED",
                reason=message,
                result=None,
            )
