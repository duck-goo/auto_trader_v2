"""Pre-open readiness orchestration service."""

from __future__ import annotations

import enum
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from services.market_master_refresh_service import MarketMasterRefreshItem
from services.preopen_universe_service import (
    PreopenUniverseResult,
    PreopenUniverseService,
)
from services.startup_service import (
    StartupCheckResult,
    StartupOutcome,
    StartupService,
)
from services.universe_build_service import UniverseBuildOutcome
from services.universe_filter_service import (
    UniverseFilterService,
    UniverseFilterSettings,
)
from storage.repositories import (
    MarketMasterRepository,
    OrderRepository,
    PositionRepository,
    UniverseCandidateRepository,
)

_KST = pytz.timezone("Asia/Seoul")


class PreopenReadinessOutcome(str, enum.Enum):
    PREPARED_ONLY = "PREPARED_ONLY"
    READY = "READY"
    BLOCKED = "BLOCKED"
    STARTUP_SKIPPED = "STARTUP_SKIPPED"


@dataclass(frozen=True)
class PreopenReadinessResult:
    outcome: PreopenReadinessOutcome
    trade_date: str
    preopen_universe_result: PreopenUniverseResult
    startup_check_result: StartupCheckResult | None
    reason: str | None


def _default_now() -> datetime:
    return datetime.now(_KST)


class PreopenReadinessService:
    """Refresh master, build universe, then optionally run startup gate."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        market_master_repo: MarketMasterRepository,
        universe_repo: UniverseCandidateRepository,
        order_repo: OrderRepository,
        position_repo: PositionRepository,
        filter_service: UniverseFilterService | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._market_master_repo = market_master_repo
        self._universe_repo = universe_repo
        self._order_repo = order_repo
        self._position_repo = position_repo
        self._filter_service = filter_service or UniverseFilterService()
        self._now_fn = now_fn or _default_now

    def prepare_and_check(
        self,
        *,
        broker: BrokerInterface,
        trade_date: str,
        master_items: Sequence[MarketMasterRefreshItem] | None = None,
        use_existing_market_master: bool = False,
        require_same_day_market_master: bool = False,
        min_market_master_count: int | None = None,
        required_markets: Sequence[str] | None = None,
        filter_settings: UniverseFilterSettings,
        daily_count: int = 40,
        write_universe: bool = False,
        allow_empty_save: bool = False,
        skip_symbol_errors: bool = False,
        run_startup_check: bool = False,
        allow_unresolved_orders: bool = False,
    ) -> PreopenReadinessResult:
        if not isinstance(write_universe, bool):
            raise ValueError(f"write_universe must be a bool: {write_universe!r}")
        if not isinstance(allow_empty_save, bool):
            raise ValueError(
                f"allow_empty_save must be a bool: {allow_empty_save!r}"
            )
        if not isinstance(skip_symbol_errors, bool):
            raise ValueError(
                f"skip_symbol_errors must be a bool: {skip_symbol_errors!r}"
            )
        if not isinstance(run_startup_check, bool):
            raise ValueError(
                f"run_startup_check must be a bool: {run_startup_check!r}"
            )
        if not isinstance(allow_unresolved_orders, bool):
            raise ValueError(
                "allow_unresolved_orders must be a bool: "
                f"{allow_unresolved_orders!r}"
            )
        if not isinstance(use_existing_market_master, bool):
            raise ValueError(
                "use_existing_market_master must be a bool: "
                f"{use_existing_market_master!r}"
            )
        if not isinstance(require_same_day_market_master, bool):
            raise ValueError(
                "require_same_day_market_master must be a bool: "
                f"{require_same_day_market_master!r}"
            )

        preopen_universe_result = PreopenUniverseService(
            conn=self._conn,
            market_master_repo=self._market_master_repo,
            universe_repo=self._universe_repo,
            filter_service=self._filter_service,
            now_fn=self._now_fn,
        ).prepare(
            broker=broker,
            trade_date=trade_date,
            master_items=master_items,
            use_existing_market_master=use_existing_market_master,
            require_same_day_market_master=require_same_day_market_master,
            min_market_master_count=min_market_master_count,
            required_markets=required_markets,
            filter_settings=filter_settings,
            daily_count=daily_count,
            write_universe=write_universe,
            allow_empty_save=allow_empty_save,
            skip_symbol_errors=skip_symbol_errors,
        )

        if not run_startup_check:
            return PreopenReadinessResult(
                outcome=PreopenReadinessOutcome.PREPARED_ONLY,
                trade_date=trade_date,
                preopen_universe_result=preopen_universe_result,
                startup_check_result=None,
                reason=None,
            )

        build_outcome = preopen_universe_result.universe_build_result.outcome
        if build_outcome != UniverseBuildOutcome.SAVED:
            return PreopenReadinessResult(
                outcome=PreopenReadinessOutcome.STARTUP_SKIPPED,
                trade_date=trade_date,
                preopen_universe_result=preopen_universe_result,
                startup_check_result=None,
                reason=(
                    "Startup check skipped because universe snapshot was not saved. "
                    f"build_outcome={build_outcome.value}"
                ),
            )

        startup_check_result = StartupService(
            broker=broker,
            conn=self._conn,
            order_repo=self._order_repo,
            position_repo=self._position_repo,
            universe_repo=self._universe_repo,
            now_fn=self._now_fn,
        ).run_startup_check(
            trade_date=trade_date,
            allow_unresolved_orders=allow_unresolved_orders,
        )

        if startup_check_result.outcome == StartupOutcome.READY:
            return PreopenReadinessResult(
                outcome=PreopenReadinessOutcome.READY,
                trade_date=trade_date,
                preopen_universe_result=preopen_universe_result,
                startup_check_result=startup_check_result,
                reason=None,
            )

        return PreopenReadinessResult(
            outcome=PreopenReadinessOutcome.BLOCKED,
            trade_date=trade_date,
            preopen_universe_result=preopen_universe_result,
            startup_check_result=startup_check_result,
            reason=startup_check_result.reason,
        )
