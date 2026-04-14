"""Startup safety gate service."""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from services.reconcile_service import ReconcileOutcome, ReconcileResult, ReconcileService
from services.universe_query_service import UniverseQueryService, UniverseSnapshotResult
from storage.repositories import (
    OrderRepository,
    PositionRepository,
    PositionRow,
    UniverseCandidateRepository,
)

_KST = pytz.timezone("Asia/Seoul")


class StartupOutcome(str, enum.Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StartupCheckResult:
    outcome: StartupOutcome
    checked_at: str
    trade_date: str
    universe_snapshot: UniverseSnapshotResult
    reconcile_result: ReconcileResult | None
    live_positions: tuple[PositionRow, ...]
    reason: str | None


def _default_now() -> datetime:
    return datetime.now(_KST)


class StartupService:
    """Run startup safety checks before trading begins."""

    def __init__(
        self,
        *,
        broker: BrokerInterface,
        conn: sqlite3.Connection,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        universe_repo: UniverseCandidateRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._broker = broker
        self._conn = conn
        self._order_repo = order_repo
        self._position_repo = position_repo
        self._universe_repo = universe_repo
        self._now_fn = now_fn or _default_now

    def run_startup_check(
        self,
        *,
        allow_unresolved_orders: bool = False,
        trade_date: str | None = None,
    ) -> StartupCheckResult:
        now = self._now_fn()
        checked_at = now.isoformat()
        if trade_date is None:
            trade_date = now.date().isoformat()

        universe_service = UniverseQueryService(
            universe_repo=self._universe_repo,
        )
        universe_snapshot = universe_service.get_snapshot(trade_date=trade_date)

        if not universe_snapshot.exists:
            return StartupCheckResult(
                outcome=StartupOutcome.BLOCKED,
                checked_at=checked_at,
                trade_date=trade_date,
                universe_snapshot=universe_snapshot,
                reconcile_result=None,
                live_positions=(),
                reason=(
                    f"Universe snapshot is missing for trade_date={trade_date}. "
                    "Startup is blocked."
                ),
            )

        reconcile_service = ReconcileService(
            broker=self._broker,
            conn=self._conn,
            order_repo=self._order_repo,
            position_repo=self._position_repo,
            now_fn=self._now_fn,
        )
        reconcile_result = reconcile_service.reconcile_positions(
            allow_unresolved_orders=allow_unresolved_orders
        )

        live_positions = tuple(self._position_repo.list_all())

        if reconcile_result.outcome == ReconcileOutcome.BLOCKED:
            return StartupCheckResult(
                outcome=StartupOutcome.BLOCKED,
                checked_at=checked_at,
                trade_date=trade_date,
                universe_snapshot=universe_snapshot,
                reconcile_result=reconcile_result,
                live_positions=live_positions,
                reason="Unresolved orders exist. Startup is blocked.",
            )

        return StartupCheckResult(
            outcome=StartupOutcome.READY,
            checked_at=checked_at,
            trade_date=trade_date,
            universe_snapshot=universe_snapshot,
            reconcile_result=reconcile_result,
            live_positions=live_positions,
            reason=None,
        )
