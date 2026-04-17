"""Read-only scan service for real-time sell exit signals."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from logger import get_logger
from services.errors import ServiceError
from storage.db import transaction
from storage.repositories import PositionRepository, SignalRepository, SignalRow
from strategy import SellExitEvaluator, SellExitMatch, SellExitRule, SellExitSettings


STRATEGY_NAME_SELL_STOP_LOSS = "sell_stop_loss"
STRATEGY_NAME_SELL_TAKE_PROFIT = "sell_take_profit"

_log = get_logger("scan")
_KST = pytz.timezone("Asia/Seoul")


@dataclass(frozen=True)
class SellExitScanCandidate:
    symbol: str
    name: str
    qty: int
    avg_price: int
    current_price: int
    strategy_name: str
    match: SellExitMatch
    already_recorded: bool


@dataclass(frozen=True)
class SellExitScanResult:
    trade_date: str
    scanned_at: str
    position_count: int
    matched_count: int
    stop_loss_count: int
    take_profit_count: int
    recorded_count: int
    skipped_existing_count: int
    candidates: tuple[SellExitScanCandidate, ...]
    recorded_signals: tuple[SignalRow, ...]


def _default_now() -> datetime:
    return datetime.now(_KST)


class SellExitScanService:
    """
    Scan current live positions and emit sell-side signals only.

    Safety rules:
    - reads current live positions from SQLite
    - fetches broker current prices outside DB transactions
    - records only append-only signals when write_signals=True
    - never places orders
    """

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        position_repo: PositionRepository,
        signal_repo: SignalRepository,
        now_fn: Callable[[], datetime] | None = None,
        evaluator: SellExitEvaluator | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._position_repo = position_repo
        self._signal_repo = signal_repo
        self._now_fn = now_fn or _default_now
        self._evaluator = evaluator or SellExitEvaluator()

    def scan(
        self,
        *,
        trade_date: str,
        settings: SellExitSettings,
        write_signals: bool = False,
    ) -> SellExitScanResult:
        normalized_settings = settings.validated()
        positions = tuple(self._position_repo.list_all())
        scanned_at = self._now_fn().astimezone(_KST).isoformat()

        candidates: list[SellExitScanCandidate] = []
        payloads_to_record: list[tuple[str, dict]] = []

        _log.info(
            f"[sell_exit_scan:start] trade_date={trade_date} "
            f"position_count={len(positions)} write_signals={write_signals}"
        )

        for row in positions:
            if row.avg_price <= 0:
                raise ServiceError(
                    "Live position has invalid avg_price. "
                    f"symbol={row.symbol}, qty={row.qty}, avg_price={row.avg_price}"
                )

            try:
                snapshot = self._broker.get_current_price(row.symbol)
            except Exception as exc:
                raise ServiceError(
                    f"Failed to load current price for symbol={row.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            try:
                match = self._evaluator.evaluate(
                    symbol=row.symbol,
                    avg_price=row.avg_price,
                    current_price=int(snapshot.price),
                    settings=normalized_settings,
                )
            except Exception as exc:
                raise ServiceError(
                    f"Failed to evaluate sell exit rules for symbol={row.symbol}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if match is None:
                continue

            strategy_name = self._strategy_name_for_rule(match.rule)
            already_recorded = self._has_existing_signal(
                symbol=row.symbol,
                trade_date=trade_date,
                strategy_name=strategy_name,
            )
            candidate_name = self._normalize_name(snapshot.name, row.symbol)
            candidates.append(
                SellExitScanCandidate(
                    symbol=row.symbol,
                    name=candidate_name,
                    qty=row.qty,
                    avg_price=row.avg_price,
                    current_price=int(snapshot.price),
                    strategy_name=strategy_name,
                    match=match,
                    already_recorded=already_recorded,
                )
            )

            if not already_recorded:
                payloads_to_record.append(
                    (
                        strategy_name,
                        self._build_payload(
                            trade_date=trade_date,
                            name=candidate_name,
                            qty=row.qty,
                            match=match,
                        ),
                    )
                )

        recorded_signals: list[SignalRow] = []
        if write_signals and payloads_to_record:
            with transaction(self._conn):
                for strategy_name, payload in payloads_to_record:
                    recorded_signals.append(
                        self._signal_repo.record(
                            symbol=payload["symbol"],
                            strategy_name=strategy_name,
                            scanned_at=scanned_at,
                            payload=payload,
                        )
                    )

        stop_loss_count = sum(
            1 for candidate in candidates if candidate.match.rule == SellExitRule.STOP_LOSS
        )
        take_profit_count = sum(
            1
            for candidate in candidates
            if candidate.match.rule == SellExitRule.TAKE_PROFIT
        )
        skipped_existing_count = sum(
            1 for candidate in candidates if candidate.already_recorded
        )

        _log.info(
            f"[sell_exit_scan:done] trade_date={trade_date} "
            f"matched_count={len(candidates)} recorded_count={len(recorded_signals)} "
            f"stop_loss_count={stop_loss_count} "
            f"take_profit_count={take_profit_count}"
        )

        return SellExitScanResult(
            trade_date=trade_date,
            scanned_at=scanned_at,
            position_count=len(positions),
            matched_count=len(candidates),
            stop_loss_count=stop_loss_count,
            take_profit_count=take_profit_count,
            recorded_count=len(recorded_signals),
            skipped_existing_count=skipped_existing_count,
            candidates=tuple(candidates),
            recorded_signals=tuple(recorded_signals),
        )

    def _has_existing_signal(
        self,
        *,
        symbol: str,
        trade_date: str,
        strategy_name: str,
    ) -> bool:
        existing = self._signal_repo.list_by_symbol(symbol, limit=100)
        for row in existing:
            if row.strategy_name != strategy_name:
                continue
            if not row.payload:
                continue
            if row.payload.get("trade_date") == trade_date:
                return True
        return False

    @staticmethod
    def _strategy_name_for_rule(rule: SellExitRule) -> str:
        if rule == SellExitRule.STOP_LOSS:
            return STRATEGY_NAME_SELL_STOP_LOSS
        if rule == SellExitRule.TAKE_PROFIT:
            return STRATEGY_NAME_SELL_TAKE_PROFIT
        raise ValueError(f"Unsupported sell exit rule: {rule!r}")

    @staticmethod
    def _normalize_name(value: object, fallback_symbol: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback_symbol

    def _build_payload(
        self,
        *,
        trade_date: str,
        name: str,
        qty: int,
        match: SellExitMatch,
    ) -> dict:
        return {
            "trade_date": trade_date,
            "symbol": match.symbol,
            "name": name,
            "rule": match.rule.value,
            "position_qty": qty,
            "avg_price": match.avg_price,
            "current_price": match.current_price,
            "trigger_price": match.trigger_price,
            "stop_loss_ratio": round(match.stop_loss_ratio, 6),
            "take_profit_ratio": round(match.take_profit_ratio, 6),
        }
