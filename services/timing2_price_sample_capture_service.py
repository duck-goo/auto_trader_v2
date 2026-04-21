"""Capture current-price samples for timing2 intraday processing."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from broker.kis.models import PriceSnapshot
from logger import get_logger
from services.errors import ServiceError
from services.timing2_setup_scan_service import STRATEGY_NAME_TIMING2_SETUP
from storage.db import transaction
from storage.repositories import (
    CurrentPriceSample,
    CurrentPriceSampleRepository,
    CurrentPriceSampleRow,
    SignalRepository,
    SignalRow,
)


_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


class Timing2PriceSampleCaptureOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Timing2PriceSampleCaptureCandidate:
    symbol: str
    name: str
    market: str
    setup_signal_id: int
    outcome: Timing2PriceSampleCaptureOutcome
    reason: str | None
    sample: CurrentPriceSample | None
    stored_row: CurrentPriceSampleRow | None


@dataclass(frozen=True)
class Timing2PriceSampleCaptureResult:
    trade_date: str
    captured_at: str
    setup_signal_count: int
    candidate_count: int
    preview_ready_count: int
    captured_count: int
    failed_count: int
    candidates: tuple[Timing2PriceSampleCaptureCandidate, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2PriceSampleCaptureService:
    """
    Capture raw current-price samples for timing2 setup symbols.

    This service intentionally does not create 30-second bars directly. It
    stores auditable raw samples first, so later bar-building logic can reject
    weak sample coverage instead of silently inventing candle closes.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        sample_repo: CurrentPriceSampleRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._signal_repo = signal_repo
        self._sample_repo = sample_repo
        self._now_fn = now_fn or _default_now

    def capture(
        self,
        *,
        trade_date: str,
        write_samples: bool = False,
    ) -> Timing2PriceSampleCaptureResult:
        setup_signals = self._load_setup_signal_map(trade_date=trade_date)
        if not setup_signals:
            raise ServiceError(
                f"Timing2 setup signals are missing for trade_date={trade_date!r}."
            )

        captured_at = self._now_fn().astimezone(_KST).isoformat()
        candidates: list[Timing2PriceSampleCaptureCandidate] = []
        _log.info(
            f"[timing2_price_sample_capture:start] trade_date={trade_date} "
            f"setup_signal_count={len(setup_signals)} write_samples={write_samples}"
        )

        for symbol, setup_signal in setup_signals.items():
            setup_payload = self._require_payload_dict(
                setup_signal,
                strategy_name=STRATEGY_NAME_TIMING2_SETUP,
            )
            name = self._require_payload_text(setup_payload, "name", setup_signal.id)
            market = self._require_payload_text(
                setup_payload,
                "market",
                setup_signal.id,
            )

            try:
                snapshot = self._broker.get_current_price(symbol)
                sample = self._sample_from_snapshot(
                    trade_date=trade_date,
                    snapshot=snapshot,
                )
            except Exception as exc:
                candidates.append(
                    Timing2PriceSampleCaptureCandidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        outcome=Timing2PriceSampleCaptureOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                        sample=None,
                        stored_row=None,
                    )
                )
                continue

            if not write_samples:
                candidates.append(
                    Timing2PriceSampleCaptureCandidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        outcome=Timing2PriceSampleCaptureOutcome.PREVIEW_READY,
                        reason=None,
                        sample=sample,
                        stored_row=None,
                    )
                )
                continue

            try:
                with transaction(self._conn):
                    rows = self._sample_repo.upsert_many(
                        samples=[sample],
                        captured_at=captured_at,
                    )
                stored_row = rows[0] if rows else None
                if stored_row is None:
                    raise ServiceError("Current price sample was not stored.")
            except Exception as exc:
                candidates.append(
                    Timing2PriceSampleCaptureCandidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        outcome=Timing2PriceSampleCaptureOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                        sample=sample,
                        stored_row=None,
                    )
                )
                continue

            candidates.append(
                Timing2PriceSampleCaptureCandidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    setup_signal_id=setup_signal.id,
                    outcome=Timing2PriceSampleCaptureOutcome.CAPTURED,
                    reason=None,
                    sample=sample,
                    stored_row=stored_row,
                )
            )

        preview_ready_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2PriceSampleCaptureOutcome.PREVIEW_READY
        )
        captured_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2PriceSampleCaptureOutcome.CAPTURED
        )
        failed_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2PriceSampleCaptureOutcome.FAILED
        )

        _log.info(
            f"[timing2_price_sample_capture:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} "
            f"preview_ready_count={preview_ready_count} "
            f"captured_count={captured_count} failed_count={failed_count}"
        )
        return Timing2PriceSampleCaptureResult(
            trade_date=trade_date,
            captured_at=captured_at,
            setup_signal_count=len(setup_signals),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            captured_count=captured_count,
            failed_count=failed_count,
            candidates=tuple(candidates),
        )

    def _load_setup_signal_map(self, *, trade_date: str) -> dict[str, SignalRow]:
        rows = self._signal_repo.list_by_strategy(
            STRATEGY_NAME_TIMING2_SETUP,
            limit=5000,
        )
        result: dict[str, SignalRow] = {}
        for row in rows:
            if row.symbol in result:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") != trade_date:
                continue
            result[row.symbol] = row
        return result

    @staticmethod
    def _sample_from_snapshot(
        *,
        trade_date: str,
        snapshot: PriceSnapshot,
    ) -> CurrentPriceSample:
        if not isinstance(snapshot.timestamp, datetime):
            raise ValueError("snapshot.timestamp must be a datetime.")
        if snapshot.timestamp.tzinfo is None or snapshot.timestamp.utcoffset() is None:
            raise ValueError("snapshot.timestamp must include a timezone offset.")

        observed_at = snapshot.timestamp.astimezone(_KST).isoformat()
        observed_trade_date = snapshot.timestamp.astimezone(_KST).strftime("%Y-%m-%d")
        if observed_trade_date != trade_date:
            raise ValueError(
                "snapshot timestamp trade_date mismatch: "
                f"expected={trade_date}, actual={observed_trade_date}"
            )

        return CurrentPriceSample(
            trade_date=trade_date,
            symbol=snapshot.code,
            observed_at=observed_at,
            price=snapshot.price,
            open=snapshot.open,
            high=snapshot.high,
            low=snapshot.low,
            prev_close=snapshot.prev_close,
            change=snapshot.change,
            change_rate=snapshot.change_rate,
            volume=snapshot.volume,
            source="kis_current_price",
        )

    @staticmethod
    def _require_payload_dict(
        signal_row: SignalRow,
        *,
        strategy_name: str,
    ) -> dict:
        if not signal_row.payload:
            raise ServiceError(
                f"Signal payload is missing for strategy={strategy_name} "
                f"id={signal_row.id}."
            )
        return signal_row.payload

    @staticmethod
    def _require_payload_text(
        payload: dict,
        field_name: str,
        signal_id: int,
    ) -> str:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(
                f"Signal payload field is missing or invalid: "
                f"id={signal_id}, field={field_name!r}"
            )
        return value.strip()
