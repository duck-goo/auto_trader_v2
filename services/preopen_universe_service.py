"""Pre-open universe preparation service."""

from __future__ import annotations

import enum
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from broker.base import BrokerInterface
from market import UniverseMasterItem
from market.kis_daily_universe_source import (
    KisDailyUniverseSkippedItem,
    KisDailyUniverseSource,
)
from services.errors import ServiceError
from services.market_master_query_service import MarketMasterQueryService
from services.market_master_refresh_service import (
    MarketMasterRefreshItem,
    MarketMasterRefreshService,
)
from services.market_master_validation_service import (
    MarketMasterValidationResult,
    MarketMasterValidationService,
)
from services.universe_build_service import (
    UniverseBuildResult,
    UniverseBuildService,
)
from services.universe_filter_service import (
    UniverseFilterInput,
    UniverseFilterService,
    UniverseFilterSettings,
)
from services.universe_refresh_service import UniverseRefreshService
from storage.repositories import (
    MarketMasterRepository,
    MarketMasterRow,
    UniverseCandidateRepository,
)

_KST = pytz.timezone("Asia/Seoul")


class PreopenMarketMasterSource(str, enum.Enum):
    REFRESHED = "REFRESHED"
    EXISTING_DB = "EXISTING_DB"


@dataclass(frozen=True)
class PreopenMarketMasterResult:
    source: PreopenMarketMasterSource
    symbol_count: int
    refreshed_at: str
    refreshed_trade_date: str
    is_same_trade_date: bool
    validation_result: MarketMasterValidationResult
    rows: tuple[MarketMasterRow, ...]


@dataclass(frozen=True)
class PreopenUniverseResult:
    trade_date: str
    market_master_result: PreopenMarketMasterResult
    source_item_count: int
    universe_build_result: UniverseBuildResult
    source_skipped_count: int = 0
    source_skipped_items: tuple[KisDailyUniverseSkippedItem, ...] = ()


def _default_now() -> datetime:
    return datetime.now(_KST)


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


def _to_trade_date(timestamp_text: str) -> str:
    try:
        parsed = datetime.fromisoformat(timestamp_text)
    except ValueError as exc:
        raise ServiceError(
            f"market master refreshed_at must be ISO8601: {timestamp_text!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise ServiceError(
            f"market master refreshed_at must be timezone-aware: {timestamp_text!r}"
        )

    return parsed.astimezone(_KST).date().isoformat()


def _normalize_min_market_master_count(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "min_market_master_count must be an integer or None: "
            f"{value!r}"
        )
    if value < 1:
        raise ValueError(
            "min_market_master_count must be >= 1 when provided: "
            f"{value!r}"
        )
    return value


def _rows_to_master_items(rows: Sequence[MarketMasterRow]) -> list[UniverseMasterItem]:
    return [
        UniverseMasterItem(
            symbol=row.symbol,
            name=row.name,
            market=row.market,
            is_managed=row.is_managed,
            is_investment_warning=row.is_investment_warning,
            is_investment_risk=row.is_investment_risk,
            is_attention_issue=row.is_attention_issue,
            is_disclosure_violation=row.is_disclosure_violation,
            is_liquidation_trade=row.is_liquidation_trade,
            is_trading_halt=row.is_trading_halt,
            is_rights_ex_date=row.is_rights_ex_date,
            is_preferred_stock=row.is_preferred_stock,
            is_etf=row.is_etf,
            is_etn=row.is_etn,
            is_spac=row.is_spac,
        )
        for row in rows
    ]


def _to_filter_inputs(items) -> list[UniverseFilterInput]:
    return [
        UniverseFilterInput(
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            close_price=item.close_price,
            prev_day_trade_value=item.prev_day_trade_value,
            avg_trade_value_20=item.avg_trade_value_20,
            is_managed=item.is_managed,
            is_investment_warning=item.is_investment_warning,
            is_investment_risk=item.is_investment_risk,
            is_attention_issue=item.is_attention_issue,
            is_disclosure_violation=item.is_disclosure_violation,
            is_liquidation_trade=item.is_liquidation_trade,
            is_trading_halt=item.is_trading_halt,
            is_rights_ex_date=item.is_rights_ex_date,
            is_preferred_stock=item.is_preferred_stock,
            is_etf=item.is_etf,
            is_etn=item.is_etn,
            is_spac=item.is_spac,
        )
        for item in items
    ]


class PreopenUniverseService:
    """Load or refresh market master, then build the first-stage universe."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        market_master_repo: MarketMasterRepository,
        universe_repo: UniverseCandidateRepository,
        filter_service: UniverseFilterService | None = None,
        market_master_validation_service: MarketMasterValidationService | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._market_master_repo = market_master_repo
        self._universe_repo = universe_repo
        self._filter_service = filter_service or UniverseFilterService()
        self._market_master_validation_service = (
            market_master_validation_service or MarketMasterValidationService()
        )
        self._now_fn = now_fn or _default_now

    def prepare(
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
    ) -> PreopenUniverseResult:
        trade_date = _require_trade_date(trade_date)
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
        min_market_master_count = _normalize_min_market_master_count(
            min_market_master_count
        )
        if not isinstance(skip_symbol_errors, bool):
            raise ValueError(
                f"skip_symbol_errors must be a bool: {skip_symbol_errors!r}"
            )

        if use_existing_market_master:
            if master_items is not None:
                raise ValueError(
                    "master_items must be omitted when "
                    "use_existing_market_master=True."
                )
            market_master_result = self._load_existing_market_master(
                trade_date=trade_date,
                require_same_day_market_master=require_same_day_market_master,
                min_market_master_count=min_market_master_count,
                required_markets=required_markets,
            )
        else:
            if master_items is None:
                raise ValueError(
                    "master_items are required when "
                    "use_existing_market_master=False."
                )
            market_master_result = self._refresh_market_master(
                master_items,
                trade_date=trade_date,
                require_same_day_market_master=require_same_day_market_master,
                min_market_master_count=min_market_master_count,
                required_markets=required_markets,
            )
        self._ensure_market_master_count(
            market_master_result=market_master_result,
            min_market_master_count=min_market_master_count,
        )

        source = KisDailyUniverseSource(
            broker=broker,
            master_items=_rows_to_master_items(market_master_result.rows),
            trade_date=trade_date,
            daily_count=daily_count,
            skip_symbol_errors=skip_symbol_errors,
        )
        source_items = source.load()

        universe_build_result = UniverseBuildService(
            filter_service=self._filter_service,
            refresh_service=UniverseRefreshService(
                conn=self._conn,
                universe_repo=self._universe_repo,
                now_fn=self._now_fn,
            ),
        ).build_snapshot(
            trade_date=trade_date,
            items=_to_filter_inputs(source_items),
            settings=filter_settings,
            write=write_universe,
            allow_empty_save=allow_empty_save,
        )

        return PreopenUniverseResult(
            trade_date=trade_date,
            market_master_result=market_master_result,
            source_item_count=len(source_items),
            universe_build_result=universe_build_result,
            source_skipped_count=len(source.skipped_items),
            source_skipped_items=source.skipped_items,
        )

    def _refresh_market_master(
        self,
        master_items: Sequence[MarketMasterRefreshItem],
        *,
        trade_date: str,
        require_same_day_market_master: bool,
        min_market_master_count: int | None,
        required_markets: Sequence[str] | None,
    ) -> PreopenMarketMasterResult:
        refresh_result = MarketMasterRefreshService(
            conn=self._conn,
            market_master_repo=self._market_master_repo,
            now_fn=self._now_fn,
        ).refresh_snapshot(items=master_items)
        return self._build_market_master_result(
            source=PreopenMarketMasterSource.REFRESHED,
            symbol_count=refresh_result.symbol_count,
            refreshed_at=refresh_result.refreshed_at,
            rows=refresh_result.rows,
            trade_date=trade_date,
            require_same_day_market_master=require_same_day_market_master,
            min_market_master_count=min_market_master_count,
            required_markets=required_markets,
        )

    def _load_existing_market_master(
        self,
        *,
        trade_date: str,
        require_same_day_market_master: bool,
        min_market_master_count: int | None,
        required_markets: Sequence[str] | None,
    ) -> PreopenMarketMasterResult:
        snapshot = MarketMasterQueryService(
            market_master_repo=self._market_master_repo,
        ).get_snapshot()
        if not snapshot.exists:
            raise ServiceError(
                "No market master snapshot found in SQLite."
            )
        if snapshot.refreshed_at is None:
            raise ServiceError(
                "Market master snapshot exists but refreshed_at is missing."
            )
        return self._build_market_master_result(
            source=PreopenMarketMasterSource.EXISTING_DB,
            symbol_count=snapshot.symbol_count,
            refreshed_at=snapshot.refreshed_at,
            rows=snapshot.rows,
            trade_date=trade_date,
            require_same_day_market_master=require_same_day_market_master,
            min_market_master_count=min_market_master_count,
            required_markets=required_markets,
        )

    def _build_market_master_result(
        self,
        *,
        source: PreopenMarketMasterSource,
        symbol_count: int,
        refreshed_at: str,
        rows: tuple[MarketMasterRow, ...],
        trade_date: str,
        require_same_day_market_master: bool,
        min_market_master_count: int | None,
        required_markets: Sequence[str] | None,
    ) -> PreopenMarketMasterResult:
        refreshed_trade_date = _to_trade_date(refreshed_at)
        is_same_trade_date = refreshed_trade_date == trade_date
        validation_result = self._market_master_validation_service.validate_items(
            items=_rows_to_master_items(rows),
            min_symbol_count=min_market_master_count,
            required_markets=required_markets,
        )

        if require_same_day_market_master and not is_same_trade_date:
            raise ServiceError(
                "Market master snapshot is stale for "
                f"trade_date={trade_date}: refreshed_trade_date="
                f"{refreshed_trade_date}"
            )

        return PreopenMarketMasterResult(
            source=source,
            symbol_count=symbol_count,
            refreshed_at=refreshed_at,
            refreshed_trade_date=refreshed_trade_date,
            is_same_trade_date=is_same_trade_date,
            validation_result=validation_result,
            rows=rows,
        )

    @staticmethod
    def _ensure_market_master_count(
        *,
        market_master_result: PreopenMarketMasterResult,
        min_market_master_count: int | None,
    ) -> None:
        if min_market_master_count is None:
            return
        if market_master_result.symbol_count < min_market_master_count:
            raise ServiceError(
                "Market master snapshot symbol_count is below minimum: "
                f"actual={market_master_result.symbol_count}, "
                f"minimum={min_market_master_count}"
            )
