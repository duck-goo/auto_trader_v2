"""Build timing2 30-second bars from captured current-price samples."""

from __future__ import annotations

import enum
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pytz

from logger import get_logger
from services.errors import ServiceError
from services.timing2_setup_scan_service import STRATEGY_NAME_TIMING2_SETUP
from storage.db import transaction
from storage.repositories import (
    CurrentPriceSampleRepository,
    CurrentPriceSampleRow,
    IntradayBar30s,
    IntradayBar30sRepository,
    IntradayBar30sRow,
    SignalRepository,
    SignalRow,
)


_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")
_BAR_SECONDS = 30


class Timing2ThirtySecondBarBuildOutcome(str, enum.Enum):
    PREVIEW_READY = "PREVIEW_READY"
    BUILT = "BUILT"
    SKIPPED_NO_SAMPLES = "SKIPPED_NO_SAMPLES"
    SKIPPED_NO_BUILDABLE_BAR = "SKIPPED_NO_BUILDABLE_BAR"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Timing2ThirtySecondBarBuildCandidate:
    symbol: str
    name: str
    market: str
    setup_signal_id: int
    sample_count: int
    complete_bucket_count: int
    buildable_bar_count: int
    skipped_insufficient_sample_count: int
    skipped_bad_volume_count: int
    stored_bar_count: int
    outcome: Timing2ThirtySecondBarBuildOutcome
    reason: str | None


@dataclass(frozen=True)
class Timing2ThirtySecondBarBuildResult:
    trade_date: str
    built_at: str
    setup_signal_count: int
    candidate_count: int
    preview_ready_count: int
    built_symbol_count: int
    skipped_count: int
    failed_count: int
    candidates: tuple[Timing2ThirtySecondBarBuildCandidate, ...]


@dataclass(frozen=True)
class _BuildSummary:
    bars: tuple[IntradayBar30s, ...]
    complete_bucket_count: int
    skipped_insufficient_sample_count: int
    skipped_bad_volume_count: int


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2ThirtySecondBarBuildService:
    """
    Build completed 30-second bars from captured current-price samples.

    The service refuses to invent missing candles. A bucket is buildable only
    when it has at least `min_samples_per_bar` samples inside the completed
    30-second window.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        signal_repo: SignalRepository,
        sample_repo: CurrentPriceSampleRepository,
        intraday_bar_repo: IntradayBar30sRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._signal_repo = signal_repo
        self._sample_repo = sample_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._now_fn = now_fn or _default_now

    def build(
        self,
        *,
        trade_date: str,
        min_samples_per_bar: int = 2,
        write_bars: bool = False,
    ) -> Timing2ThirtySecondBarBuildResult:
        normalized_min_samples = self._require_positive_int(
            "min_samples_per_bar",
            min_samples_per_bar,
        )
        setup_signals = self._load_setup_signal_map(trade_date=trade_date)
        if not setup_signals:
            raise ServiceError(
                f"Timing2 setup signals are missing for trade_date={trade_date!r}."
            )

        observed_now = self._now_fn().astimezone(_KST)
        built_at = observed_now.isoformat()
        build_until = observed_now
        self._validate_trade_date_matches_now(
            trade_date=trade_date,
            observed_at=build_until,
        )
        candidates: list[Timing2ThirtySecondBarBuildCandidate] = []

        _log.info(
            f"[timing2_30s_bar_build:start] trade_date={trade_date} "
            f"setup_signal_count={len(setup_signals)} "
            f"min_samples_per_bar={normalized_min_samples} write_bars={write_bars}"
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
                samples = self._sample_repo.list_for_symbol_and_date(
                    trade_date=trade_date,
                    symbol=symbol,
                )
                if not samples:
                    candidates.append(
                        self._candidate(
                            symbol=symbol,
                            name=name,
                            market=market,
                            setup_signal_id=setup_signal.id,
                            sample_count=0,
                            complete_bucket_count=0,
                            buildable_bar_count=0,
                            skipped_insufficient_sample_count=0,
                            skipped_bad_volume_count=0,
                            stored_bar_count=0,
                            outcome=Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_SAMPLES,
                            reason="No current-price samples are available.",
                        )
                    )
                    continue

                summary = self._build_bars_from_samples(
                    trade_date=trade_date,
                    samples=samples,
                    build_until=build_until,
                    min_samples_per_bar=normalized_min_samples,
                )
            except Exception as exc:
                candidates.append(
                    self._candidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        sample_count=0,
                        complete_bucket_count=0,
                        buildable_bar_count=0,
                        skipped_insufficient_sample_count=0,
                        skipped_bad_volume_count=0,
                        stored_bar_count=0,
                        outcome=Timing2ThirtySecondBarBuildOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            if not summary.bars:
                candidates.append(
                    self._candidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        sample_count=len(samples),
                        complete_bucket_count=summary.complete_bucket_count,
                        buildable_bar_count=0,
                        skipped_insufficient_sample_count=(
                            summary.skipped_insufficient_sample_count
                        ),
                        skipped_bad_volume_count=summary.skipped_bad_volume_count,
                        stored_bar_count=0,
                        outcome=(
                            Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_BUILDABLE_BAR
                        ),
                        reason=(
                            "No completed 30-second bucket had enough valid samples."
                        ),
                    )
                )
                continue

            if not write_bars:
                candidates.append(
                    self._candidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        sample_count=len(samples),
                        complete_bucket_count=summary.complete_bucket_count,
                        buildable_bar_count=len(summary.bars),
                        skipped_insufficient_sample_count=(
                            summary.skipped_insufficient_sample_count
                        ),
                        skipped_bad_volume_count=summary.skipped_bad_volume_count,
                        stored_bar_count=0,
                        outcome=Timing2ThirtySecondBarBuildOutcome.PREVIEW_READY,
                        reason=None,
                    )
                )
                continue

            try:
                with transaction(self._conn):
                    stored_rows = (
                        self._intraday_bar_repo.upsert_many_for_symbol_and_date(
                            trade_date=trade_date,
                            symbol=symbol,
                            bars=summary.bars,
                            refreshed_at=built_at,
                        )
                    )
            except Exception as exc:
                candidates.append(
                    self._candidate(
                        symbol=symbol,
                        name=name,
                        market=market,
                        setup_signal_id=setup_signal.id,
                        sample_count=len(samples),
                        complete_bucket_count=summary.complete_bucket_count,
                        buildable_bar_count=len(summary.bars),
                        skipped_insufficient_sample_count=(
                            summary.skipped_insufficient_sample_count
                        ),
                        skipped_bad_volume_count=summary.skipped_bad_volume_count,
                        stored_bar_count=0,
                        outcome=Timing2ThirtySecondBarBuildOutcome.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            candidates.append(
                self._candidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    setup_signal_id=setup_signal.id,
                    sample_count=len(samples),
                    complete_bucket_count=summary.complete_bucket_count,
                    buildable_bar_count=len(summary.bars),
                    skipped_insufficient_sample_count=(
                        summary.skipped_insufficient_sample_count
                    ),
                    skipped_bad_volume_count=summary.skipped_bad_volume_count,
                    stored_bar_count=len(stored_rows),
                    outcome=Timing2ThirtySecondBarBuildOutcome.BUILT,
                    reason=None,
                )
            )

        preview_ready_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2ThirtySecondBarBuildOutcome.PREVIEW_READY
        )
        built_symbol_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2ThirtySecondBarBuildOutcome.BUILT
        )
        skipped_count = sum(
            1
            for candidate in candidates
            if candidate.outcome
            in (
                Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_SAMPLES,
                Timing2ThirtySecondBarBuildOutcome.SKIPPED_NO_BUILDABLE_BAR,
            )
        )
        failed_count = sum(
            1
            for candidate in candidates
            if candidate.outcome == Timing2ThirtySecondBarBuildOutcome.FAILED
        )

        _log.info(
            f"[timing2_30s_bar_build:done] trade_date={trade_date} "
            f"candidate_count={len(candidates)} "
            f"preview_ready_count={preview_ready_count} "
            f"built_symbol_count={built_symbol_count} "
            f"skipped_count={skipped_count} failed_count={failed_count}"
        )
        return Timing2ThirtySecondBarBuildResult(
            trade_date=trade_date,
            built_at=built_at,
            setup_signal_count=len(setup_signals),
            candidate_count=len(candidates),
            preview_ready_count=preview_ready_count,
            built_symbol_count=built_symbol_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            candidates=tuple(candidates),
        )

    def _build_bars_from_samples(
        self,
        *,
        trade_date: str,
        samples: list[CurrentPriceSampleRow],
        build_until: datetime,
        min_samples_per_bar: int,
    ) -> _BuildSummary:
        session_start = _KST.localize(
            datetime.strptime(f"{trade_date} 09:00:00", "%Y-%m-%d %H:%M:%S")
        )
        session_end = _KST.localize(
            datetime.strptime(f"{trade_date} 15:30:00", "%Y-%m-%d %H:%M:%S")
        )
        effective_until = min(build_until.astimezone(_KST), session_end)
        grouped: dict[int, list[tuple[datetime, CurrentPriceSampleRow]]] = defaultdict(
            list
        )

        for sample in samples:
            observed_at = self._parse_kst_iso("observed_at", sample.observed_at)
            if observed_at < session_start or observed_at >= effective_until:
                continue
            seconds_from_open = int((observed_at - session_start).total_seconds())
            bucket_index = seconds_from_open // _BAR_SECONDS
            bar_start_at = session_start + timedelta(
                seconds=bucket_index * _BAR_SECONDS
            )
            bar_end_at = bar_start_at + timedelta(seconds=_BAR_SECONDS)
            if bar_end_at > effective_until:
                continue
            grouped[bucket_index].append((observed_at, sample))

        bars: list[IntradayBar30s] = []
        skipped_insufficient = 0
        skipped_bad_volume = 0

        for bucket_index in sorted(grouped):
            rows = sorted(grouped[bucket_index], key=lambda item: item[0])
            if len(rows) < min_samples_per_bar:
                skipped_insufficient += 1
                continue

            prices = [row.price for _, row in rows]
            volumes = [row.volume for _, row in rows]
            volume = max(volumes) - min(volumes)
            if volume < 0:
                skipped_bad_volume += 1
                continue

            bar_start_at = session_start + timedelta(
                seconds=bucket_index * _BAR_SECONDS
            )
            bar_end_at = bar_start_at + timedelta(seconds=_BAR_SECONDS)
            bars.append(
                IntradayBar30s(
                    bar_start_at=bar_start_at.isoformat(),
                    bar_end_at=bar_end_at.isoformat(),
                    open=int(prices[0]),
                    high=int(max(prices)),
                    low=int(min(prices)),
                    close=int(prices[-1]),
                    volume=int(volume),
                )
            )

        return _BuildSummary(
            bars=tuple(bars),
            complete_bucket_count=len(grouped),
            skipped_insufficient_sample_count=skipped_insufficient,
            skipped_bad_volume_count=skipped_bad_volume,
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
    def _candidate(
        *,
        symbol: str,
        name: str,
        market: str,
        setup_signal_id: int,
        sample_count: int,
        complete_bucket_count: int,
        buildable_bar_count: int,
        skipped_insufficient_sample_count: int,
        skipped_bad_volume_count: int,
        stored_bar_count: int,
        outcome: Timing2ThirtySecondBarBuildOutcome,
        reason: str | None,
    ) -> Timing2ThirtySecondBarBuildCandidate:
        return Timing2ThirtySecondBarBuildCandidate(
            symbol=symbol,
            name=name,
            market=market,
            setup_signal_id=setup_signal_id,
            sample_count=sample_count,
            complete_bucket_count=complete_bucket_count,
            buildable_bar_count=buildable_bar_count,
            skipped_insufficient_sample_count=skipped_insufficient_sample_count,
            skipped_bad_volume_count=skipped_bad_volume_count,
            stored_bar_count=stored_bar_count,
            outcome=outcome,
            reason=reason,
        )

    @staticmethod
    def _require_positive_int(name: str, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer: {value!r}")
        return value

    @staticmethod
    def _validate_trade_date_matches_now(
        *,
        trade_date: str,
        observed_at: datetime,
    ) -> None:
        if observed_at.astimezone(_KST).strftime("%Y-%m-%d") != trade_date:
            raise ServiceError(
                "30-second bar build supports only the current KST trade_date: "
                f"trade_date={trade_date}, "
                f"runtime_trade_date={observed_at.astimezone(_KST).strftime('%Y-%m-%d')}"
            )

    @staticmethod
    def _parse_kst_iso(name: str, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be ISO8601: {value!r}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone offset: {value!r}")
        return parsed.astimezone(_KST)

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
