"""Scan Timing2 entry lots and emit lot-level sell signals."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import (
    ENTRY_SLOT_TIMING2_MORNING,
    ENTRY_SLOT_TIMING2_RANGE,
    EntryLotRepository,
    EntryLotRow,
    IntradayBar30sRepository,
    IntradayBar30sRow,
    SignalRepository,
    SignalRow,
)
from strategy import (
    Timing2LotExitDecision,
    Timing2LotExitEvaluator,
    Timing2LotExitRule,
    Timing2LotExitSettings,
)


STRATEGY_NAME_TIMING2_LOT_STOP_LOSS = "sell_timing2_lot_stop_loss"
STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK = "sell_timing2_lot_3m_ma_break"
STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL = (
    "sell_timing2_lot_take_profit_partial"
)

_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("scan")
_THREE_MINUTE_SECONDS = 180
_THIRTY_SECOND_SECONDS = 30
_THIRTY_SECOND_BARS_PER_3M = 6
_MA_PERIOD = 5
_TIMING2_LOT_ENTRY_SLOTS = (
    ENTRY_SLOT_TIMING2_MORNING,
    ENTRY_SLOT_TIMING2_RANGE,
)


class Timing2LotExitScanOutcome(str, enum.Enum):
    MATCHED = "MATCHED"
    SKIPPED_EXISTING_SIGNAL = "SKIPPED_EXISTING_SIGNAL"


@dataclass(frozen=True)
class Timing2ThreeMinuteMaSnapshot:
    latest_3m_close: int
    ma5_3m: float
    completed_3m_bar_count: int


@dataclass(frozen=True)
class Timing2LotExitScanCandidate:
    symbol: str
    name: str
    lot_id: int
    entry_slot: str
    remaining_qty: int
    total_buy_qty: int
    avg_buy_price: int
    current_price: int
    strategy_name: str
    decision: Timing2LotExitDecision
    ma_snapshot: Timing2ThreeMinuteMaSnapshot | None
    already_recorded: bool
    outcome: Timing2LotExitScanOutcome


@dataclass(frozen=True)
class Timing2LotExitScanResult:
    trade_date: str
    scanned_at: str
    lot_count: int
    matched_count: int
    stop_loss_count: int
    ma_break_count: int
    partial_take_profit_count: int
    recorded_count: int
    skipped_existing_count: int
    candidates: tuple[Timing2LotExitScanCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class Timing2LotExitScanService:
    """
    Scan open Timing2 lots and record lot-level sell signals only.

    This service does not place orders. It keeps Timing2 lot exits separate
    from the existing position-level sell scanner.
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        entry_lot_repo: EntryLotRepository,
        signal_repo: SignalRepository,
        intraday_bar_repo: IntradayBar30sRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: Timing2LotExitEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._entry_lot_repo = entry_lot_repo
        self._signal_repo = signal_repo
        self._intraday_bar_repo = intraday_bar_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or Timing2LotExitEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: Timing2LotExitSettings,
        write_signals: bool = False,
    ) -> Timing2LotExitScanResult:
        normalized_settings = settings.validated()
        scanned_at_dt = self._now_fn().astimezone(_KST)
        scanned_at = scanned_at_dt.isoformat()
        lots = tuple(
            self._entry_lot_repo.list_open_by_entry_slots(
                entry_slots=_TIMING2_LOT_ENTRY_SLOTS,
            )
        )
        price_cache: dict[str, tuple[str, int]] = {}
        ma_cache: dict[str, Timing2ThreeMinuteMaSnapshot | None] = {}
        candidates: list[Timing2LotExitScanCandidate] = []
        payloads_to_record: list[tuple[Timing2LotExitScanCandidate, dict]] = []

        _log.info(
            f"[timing2_lot_exit_scan:start] trade_date={trade_date} "
            f"lot_count={len(lots)} write_signals={write_signals}"
        )

        for lot in lots:
            self._validate_lot(lot)
            name, current_price = self._load_current_price_cached(
                lot.symbol,
                price_cache,
            )
            ma_snapshot = self._load_ma_snapshot_cached(
                trade_date=trade_date,
                symbol=lot.symbol,
                end_at=scanned_at,
                cache=ma_cache,
            )
            latest_3m_close = (
                ma_snapshot.latest_3m_close if ma_snapshot is not None else None
            )
            ma5_3m = ma_snapshot.ma5_3m if ma_snapshot is not None else None
            partial_take_profit_done = lot.realized_sell_qty > 0

            try:
                decision = self._evaluator.evaluate(
                    symbol=lot.symbol,
                    lot_id=lot.id,
                    remaining_qty=lot.remaining_qty,
                    total_buy_qty=lot.total_buy_qty,
                    avg_buy_price=lot.avg_buy_price,
                    current_price=current_price,
                    partial_take_profit_done=partial_take_profit_done,
                    latest_3m_close=latest_3m_close,
                    ma5_3m=ma5_3m,
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    "Failed to evaluate Timing2 lot exit: "
                    f"lot_id={lot.id}, symbol={lot.symbol}, "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if decision is None:
                continue

            strategy_name = self._strategy_name_for_rule(decision.rule)
            already_recorded = self._has_existing_signal(
                symbol=lot.symbol,
                trade_date=trade_date,
                lot_id=lot.id,
                strategy_name=strategy_name,
            )
            candidate = Timing2LotExitScanCandidate(
                symbol=lot.symbol,
                name=name,
                lot_id=lot.id,
                entry_slot=lot.entry_slot,
                remaining_qty=lot.remaining_qty,
                total_buy_qty=lot.total_buy_qty,
                avg_buy_price=lot.avg_buy_price,
                current_price=current_price,
                strategy_name=strategy_name,
                decision=decision,
                ma_snapshot=ma_snapshot,
                already_recorded=already_recorded,
                outcome=(
                    Timing2LotExitScanOutcome.SKIPPED_EXISTING_SIGNAL
                    if already_recorded
                    else Timing2LotExitScanOutcome.MATCHED
                ),
            )
            candidates.append(candidate)

            if not already_recorded:
                payloads_to_record.append(
                    (
                        candidate,
                        self._build_payload(
                            trade_date=trade_date,
                            name=name,
                            lot=lot,
                            decision=decision,
                            ma_snapshot=ma_snapshot,
                        ),
                    )
                )

        recorded_signals: list[SignalRow] = []
        if write_signals and payloads_to_record:
            with transaction(self._conn):
                for candidate, payload in payloads_to_record:
                    recorded_signals.append(
                        self._signal_repo.record(
                            symbol=candidate.symbol,
                            strategy_name=candidate.strategy_name,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        stop_loss_count = sum(
            1
            for candidate in candidates
            if candidate.decision.rule == Timing2LotExitRule.STOP_LOSS
        )
        ma_break_count = sum(
            1
            for candidate in candidates
            if candidate.decision.rule == Timing2LotExitRule.THREE_MINUTE_MA_BREAK
        )
        partial_take_profit_count = sum(
            1
            for candidate in candidates
            if candidate.decision.rule == Timing2LotExitRule.TAKE_PROFIT_PARTIAL
        )
        skipped_existing_count = sum(
            1 for candidate in candidates if candidate.already_recorded
        )

        _log.info(
            f"[timing2_lot_exit_scan:done] trade_date={trade_date} "
            f"matched_count={len(candidates)} recorded_count={len(recorded_signals)} "
            f"stop_loss_count={stop_loss_count} ma_break_count={ma_break_count} "
            f"partial_take_profit_count={partial_take_profit_count}"
        )
        return Timing2LotExitScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            lot_count=len(lots),
            matched_count=len(candidates),
            stop_loss_count=stop_loss_count,
            ma_break_count=ma_break_count,
            partial_take_profit_count=partial_take_profit_count,
            recorded_count=len(recorded_signals),
            skipped_existing_count=skipped_existing_count,
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _load_current_price_cached(
        self,
        symbol: str,
        cache: dict[str, tuple[str, int]],
    ) -> tuple[str, int]:
        cached = cache.get(symbol)
        if cached is not None:
            return cached

        try:
            snapshot = self._broker.get_current_price(symbol)
        except Exception as exc:
            raise ServiceError(
                f"Failed to load current price for symbol={symbol}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        price = self._require_positive_int("current_price", int(snapshot.price))
        name = self._normalize_name(snapshot.name, symbol)
        cache[symbol] = (name, price)
        return name, price

    def _load_ma_snapshot_cached(
        self,
        *,
        trade_date: str,
        symbol: str,
        end_at: str,
        cache: dict[str, Timing2ThreeMinuteMaSnapshot | None],
    ) -> Timing2ThreeMinuteMaSnapshot | None:
        if symbol in cache:
            return cache[symbol]

        rows = self._intraday_bar_repo.list_recent_for_symbol(
            symbol=symbol,
            end_at=end_at,
            limit=120,
        )
        snapshot = self._build_three_minute_ma_snapshot(
            trade_date=trade_date,
            rows=rows,
        )
        cache[symbol] = snapshot
        return snapshot

    def _build_three_minute_ma_snapshot(
        self,
        *,
        trade_date: str,
        rows: list[IntradayBar30sRow],
    ) -> Timing2ThreeMinuteMaSnapshot | None:
        session_start = _KST.localize(
            datetime.strptime(f"{trade_date} 09:00:00", "%Y-%m-%d %H:%M:%S")
        )
        grouped: dict[int, list[IntradayBar30sRow]] = {}
        for row in rows:
            if row.trade_date != trade_date:
                continue
            start_at = self._parse_kst_iso("bar_start_at", row.bar_start_at)
            end_at = self._parse_kst_iso("bar_end_at", row.bar_end_at)
            if start_at < session_start:
                continue
            seconds_from_open = int((start_at - session_start).total_seconds())
            if seconds_from_open % _THIRTY_SECOND_SECONDS != 0:
                continue
            if end_at - start_at != timedelta(seconds=_THIRTY_SECOND_SECONDS):
                continue
            bucket_index = seconds_from_open // _THREE_MINUTE_SECONDS
            grouped.setdefault(bucket_index, []).append(row)

        completed_closes: list[int] = []
        for bucket_index in sorted(grouped):
            bucket_rows = sorted(
                grouped[bucket_index],
                key=lambda item: item.bar_start_at,
            )
            if not self._is_complete_three_minute_bucket(
                session_start=session_start,
                bucket_index=bucket_index,
                rows=bucket_rows,
            ):
                continue
            completed_closes.append(int(bucket_rows[-1].close))

        if len(completed_closes) < _MA_PERIOD:
            return None

        latest_closes = completed_closes[-_MA_PERIOD:]
        return Timing2ThreeMinuteMaSnapshot(
            latest_3m_close=latest_closes[-1],
            ma5_3m=sum(latest_closes) / _MA_PERIOD,
            completed_3m_bar_count=len(completed_closes),
        )

    def _has_existing_signal(
        self,
        *,
        symbol: str,
        trade_date: str,
        lot_id: int,
        strategy_name: str,
    ) -> bool:
        existing = self._signal_repo.list_by_symbol(symbol, limit=500)
        for row in existing:
            if row.strategy_name != strategy_name:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") != trade_date:
                continue
            if row.payload.get("lot_id") == lot_id:
                return True
        return False

    @staticmethod
    def _is_complete_three_minute_bucket(
        *,
        session_start: datetime,
        bucket_index: int,
        rows: list[IntradayBar30sRow],
    ) -> bool:
        if len(rows) != _THIRTY_SECOND_BARS_PER_3M:
            return False
        bucket_start = session_start + timedelta(
            seconds=bucket_index * _THREE_MINUTE_SECONDS
        )
        for offset, row in enumerate(rows):
            expected_start = bucket_start + timedelta(
                seconds=offset * _THIRTY_SECOND_SECONDS
            )
            expected_end = expected_start + timedelta(seconds=_THIRTY_SECOND_SECONDS)
            if row.bar_start_at != expected_start.isoformat():
                return False
            if row.bar_end_at != expected_end.isoformat():
                return False
        return True

    @staticmethod
    def _strategy_name_for_rule(rule: Timing2LotExitRule) -> str:
        if rule == Timing2LotExitRule.STOP_LOSS:
            return STRATEGY_NAME_TIMING2_LOT_STOP_LOSS
        if rule == Timing2LotExitRule.THREE_MINUTE_MA_BREAK:
            return STRATEGY_NAME_TIMING2_LOT_3M_MA_BREAK
        if rule == Timing2LotExitRule.TAKE_PROFIT_PARTIAL:
            return STRATEGY_NAME_TIMING2_LOT_TAKE_PROFIT_PARTIAL
        raise ValueError(f"Unsupported Timing2 lot exit rule: {rule!r}")

    @staticmethod
    def _build_payload(
        *,
        trade_date: str,
        name: str,
        lot: EntryLotRow,
        decision: Timing2LotExitDecision,
        ma_snapshot: Timing2ThreeMinuteMaSnapshot | None,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": lot.symbol,
            "name": name,
            "lot_id": lot.id,
            "entry_order_id": lot.entry_order_id,
            "entry_signal_id": lot.entry_signal_id,
            "entry_strategy_name": lot.entry_strategy_name,
            "entry_slot": lot.entry_slot,
            "rule": decision.rule.value,
            "sell_qty": decision.sell_qty,
            "remaining_qty": decision.remaining_qty,
            "total_buy_qty": decision.total_buy_qty,
            "avg_buy_price": decision.avg_buy_price,
            "current_price": decision.current_price,
            "trigger_price": decision.trigger_price,
            "net_return_rate": round(decision.net_return_rate, 8),
            "stop_loss_ratio": round(decision.stop_loss_ratio, 6),
            "take_profit_ratio": round(decision.take_profit_ratio, 6),
            "partial_take_profit_ratio": round(
                decision.partial_take_profit_ratio,
                6,
            ),
            "sell_cost_rate": round(decision.sell_cost_rate, 9),
            "partial_take_profit_done": decision.partial_take_profit_done,
            "latest_3m_close": (
                ma_snapshot.latest_3m_close if ma_snapshot is not None else None
            ),
            "ma5_3m": round(ma_snapshot.ma5_3m, 6)
            if ma_snapshot is not None
            else None,
            "completed_3m_bar_count": (
                ma_snapshot.completed_3m_bar_count
                if ma_snapshot is not None
                else 0
            ),
        }

    @staticmethod
    def _validate_lot(lot: EntryLotRow) -> None:
        if lot.remaining_qty <= 0:
            raise ServiceError(
                f"Open Timing2 lot has invalid remaining_qty: lot_id={lot.id}"
            )
        if lot.total_buy_qty <= 0:
            raise ServiceError(
                f"Open Timing2 lot has invalid total_buy_qty: lot_id={lot.id}"
            )
        if lot.remaining_qty > lot.total_buy_qty:
            raise ServiceError(
                "Open Timing2 lot remaining_qty exceeds total_buy_qty: "
                f"lot_id={lot.id}, remaining_qty={lot.remaining_qty}, "
                f"total_buy_qty={lot.total_buy_qty}"
            )
        if lot.avg_buy_price <= 0:
            raise ServiceError(
                f"Open Timing2 lot has invalid avg_buy_price: lot_id={lot.id}"
            )

    @staticmethod
    def _normalize_name(value: object, fallback_symbol: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback_symbol

    @staticmethod
    def _require_positive_int(name: str, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer: {value!r}")
        return value

    @staticmethod
    def _parse_kst_iso(name: str, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be ISO8601: {value!r}") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone offset: {value!r}")
        return parsed.astimezone(_KST)
