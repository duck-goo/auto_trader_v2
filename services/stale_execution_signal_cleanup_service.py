"""Preview or consume stale pending execution signals."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from logger import get_logger
from storage.db import transaction
from storage.repositories import SignalRepository, SignalRow


STRATEGY_NAME_STALE_BUY_SIGNAL_CLEANUP_AUDIT = "stale_buy_signal_cleanup"
STRATEGY_NAME_STALE_SELL_SIGNAL_CLEANUP_AUDIT = "stale_sell_signal_cleanup"

_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _require_non_empty_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string: {value!r}")
    return value.strip()


def _default_now() -> datetime:
    return datetime.now(_KST)


class StaleExecutionSignalCleanupOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    SKIPPED = "SKIPPED"
    CLEANED = "CLEANED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StaleExecutionSignalCleanupSettings:
    max_signal_age_seconds: int
    signal_limit: int = 200

    def validated(self) -> "StaleExecutionSignalCleanupSettings":
        return StaleExecutionSignalCleanupSettings(
            max_signal_age_seconds=_require_positive_int(
                "max_signal_age_seconds",
                self.max_signal_age_seconds,
            ),
            signal_limit=_require_positive_int(
                "signal_limit",
                self.signal_limit,
            ),
        )


@dataclass(frozen=True)
class StaleExecutionSignalCleanupCandidate:
    signal_id: int
    symbol: str
    strategy_name: str
    scanned_at: str
    age_seconds: int | None
    outcome: StaleExecutionSignalCleanupOutcome
    reason_code: str | None
    reason_message: str | None
    acted: bool


@dataclass(frozen=True)
class StaleExecutionSignalCleanupResult:
    trade_date: str
    scanned_at: str
    execute_cleanup: bool
    matched_signal_count: int
    candidate_count: int
    preview_ready_count: int
    skipped_count: int
    cleaned_count: int
    blocked_count: int
    acted_count: int
    audit_record_count: int
    candidates: tuple[StaleExecutionSignalCleanupCandidate, ...]


class StaleExecutionSignalCleanupService:
    """Safely consume stale pending execution signals after a timeout."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now

    def cleanup_stale_signals(
        self,
        *,
        trade_date: str,
        strategy_names: list[str] | tuple[str, ...] | frozenset[str],
        audit_strategy_name: str,
        settings: StaleExecutionSignalCleanupSettings,
        execute_cleanup: bool = False,
    ) -> StaleExecutionSignalCleanupResult:
        normalized_settings = settings.validated()
        normalized_audit_strategy_name = _require_non_empty_text(
            "audit_strategy_name",
            audit_strategy_name,
        )
        now = self._now_fn().astimezone(_KST)
        scanned_at = now.isoformat()
        matched_rows = self._signal_repo.list_unacted_by_strategies(
            strategy_names,
            limit=normalized_settings.signal_limit,
        )
        candidates: list[StaleExecutionSignalCleanupCandidate] = []
        acted_count = 0
        audit_record_count = 0

        _log.info(
            f"[stale_signal_cleanup:start] trade_date={trade_date} "
            f"matched_signal_count={len(matched_rows)} "
            f"execute_cleanup={execute_cleanup} "
            f"audit_strategy_name={normalized_audit_strategy_name}"
        )

        for row in matched_rows:
            candidate = self._evaluate_signal(
                row=row,
                trade_date=trade_date,
                observed_at=now,
                settings=normalized_settings,
                execute_cleanup=execute_cleanup,
                cleanup_scanned_at=scanned_at,
                audit_strategy_name=normalized_audit_strategy_name,
            )
            candidates.append(candidate)
            if candidate.acted:
                acted_count += 1
                audit_record_count += 1

        preview_ready_count = sum(
            1
            for item in candidates
            if item.outcome == StaleExecutionSignalCleanupOutcome.PREVIEW_READY
        )
        skipped_count = sum(
            1
            for item in candidates
            if item.outcome == StaleExecutionSignalCleanupOutcome.SKIPPED
        )
        cleaned_count = sum(
            1
            for item in candidates
            if item.outcome == StaleExecutionSignalCleanupOutcome.CLEANED
        )
        blocked_count = sum(
            1
            for item in candidates
            if item.outcome == StaleExecutionSignalCleanupOutcome.BLOCKED
        )

        _log.info(
            f"[stale_signal_cleanup:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} cleaned_count={cleaned_count} "
            f"blocked_count={blocked_count} skipped_count={skipped_count}"
        )

        return StaleExecutionSignalCleanupResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            execute_cleanup=execute_cleanup,
            matched_signal_count=len(matched_rows),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            skipped_count=skipped_count,
            cleaned_count=cleaned_count,
            blocked_count=blocked_count,
            acted_count=acted_count,
            audit_record_count=audit_record_count,
            candidates=tuple(candidates),
        )

    def _evaluate_signal(
        self,
        *,
        row: SignalRow,
        trade_date: str,
        observed_at: datetime,
        settings: StaleExecutionSignalCleanupSettings,
        execute_cleanup: bool,
        cleanup_scanned_at: str,
        audit_strategy_name: str,
    ) -> StaleExecutionSignalCleanupCandidate:
        try:
            signal_scanned_at = self._parse_signal_scanned_at(row.scanned_at)
        except ValueError as exc:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=None,
                outcome=StaleExecutionSignalCleanupOutcome.BLOCKED,
                reason_code="INVALID_SIGNAL_SCANNED_AT",
                reason_message=str(exc),
                acted=False,
            )
        signal_date = signal_scanned_at.strftime("%Y-%m-%d")
        raw_age_seconds = (observed_at - signal_scanned_at).total_seconds()
        if raw_age_seconds < 0:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=None,
                outcome=StaleExecutionSignalCleanupOutcome.BLOCKED,
                reason_code="SIGNAL_TIMESTAMP_IN_FUTURE",
                reason_message=(
                    "Signal scanned_at is in the future relative to cleanup time: "
                    f"signal_scanned_at={row.scanned_at}, "
                    f"cleanup_scanned_at={cleanup_scanned_at}"
                ),
                acted=False,
            )

        age_seconds = int(raw_age_seconds)
        if signal_date != trade_date:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=age_seconds,
                outcome=StaleExecutionSignalCleanupOutcome.SKIPPED,
                reason_code="TRADE_DATE_MISMATCH",
                reason_message=(
                    "Signal scanned_at date does not match cleanup trade_date: "
                    f"signal_date={signal_date}, trade_date={trade_date}"
                ),
                acted=False,
            )

        try:
            payload_trade_date = self._extract_payload_trade_date(row)
        except ValueError as exc:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=age_seconds,
                outcome=StaleExecutionSignalCleanupOutcome.BLOCKED,
                reason_code="INVALID_SIGNAL_TRADE_DATE",
                reason_message=str(exc),
                acted=False,
            )
        if payload_trade_date is not None and payload_trade_date != trade_date:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=age_seconds,
                outcome=StaleExecutionSignalCleanupOutcome.SKIPPED,
                reason_code="PAYLOAD_TRADE_DATE_MISMATCH",
                reason_message=(
                    "Signal payload trade_date does not match cleanup trade_date: "
                    f"payload_trade_date={payload_trade_date}, trade_date={trade_date}"
                ),
                acted=False,
            )

        if age_seconds <= settings.max_signal_age_seconds:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=age_seconds,
                outcome=StaleExecutionSignalCleanupOutcome.SKIPPED,
                reason_code="NOT_STALE_YET",
                reason_message=(
                    "Signal age is still within max_signal_age_seconds: "
                    f"age_seconds={age_seconds}, "
                    f"max_signal_age_seconds={settings.max_signal_age_seconds}"
                ),
                acted=False,
            )

        reason_code = "STALE_SIGNAL_AGE_EXCEEDED"
        reason_message = (
            "Signal age exceeded max_signal_age_seconds during maintenance cleanup: "
            f"age_seconds={age_seconds}, "
            f"max_signal_age_seconds={settings.max_signal_age_seconds}"
        )
        if not execute_cleanup:
            return StaleExecutionSignalCleanupCandidate(
                signal_id=row.id,
                symbol=row.symbol,
                strategy_name=row.strategy_name,
                scanned_at=row.scanned_at,
                age_seconds=age_seconds,
                outcome=StaleExecutionSignalCleanupOutcome.PREVIEW_READY,
                reason_code=reason_code,
                reason_message=reason_message,
                acted=False,
            )

        with transaction(self._conn):
            self._signal_repo.mark_acted(row.id)
            self._signal_repo.record(
                symbol=row.symbol,
                strategy_name=audit_strategy_name,
                scanned_at=cleanup_scanned_at,
                payload={
                    "trade_date": trade_date,
                    "source_signal_id": row.id,
                    "source_symbol": row.symbol,
                    "source_strategy_name": row.strategy_name,
                    "source_signal_scanned_at": row.scanned_at,
                    "signal_age_seconds": age_seconds,
                    "max_signal_age_seconds": settings.max_signal_age_seconds,
                    "reason_code": reason_code,
                    "reason_message": reason_message,
                },
            )

        return StaleExecutionSignalCleanupCandidate(
            signal_id=row.id,
            symbol=row.symbol,
            strategy_name=row.strategy_name,
            scanned_at=row.scanned_at,
            age_seconds=age_seconds,
            outcome=StaleExecutionSignalCleanupOutcome.CLEANED,
            reason_code=reason_code,
            reason_message=reason_message,
            acted=True,
        )

    def _parse_signal_scanned_at(self, scanned_at_text: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(scanned_at_text)
        except ValueError as exc:
            raise ValueError(
                f"signal scanned_at must be ISO8601: {scanned_at_text!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(
                "signal scanned_at must include timezone information: "
                f"{scanned_at_text!r}"
            )
        return parsed.astimezone(_KST)

    def _extract_payload_trade_date(self, row: SignalRow) -> str | None:
        payload = row.payload
        if payload is None:
            return None
        raw_trade_date = payload.get("trade_date")
        if raw_trade_date is None:
            return None
        if not isinstance(raw_trade_date, str) or not raw_trade_date.strip():
            raise ValueError(
                "signal payload trade_date must be a non-empty string when present: "
                f"signal_id={row.id!r}, payload_trade_date={raw_trade_date!r}"
            )
        return raw_trade_date.strip()
