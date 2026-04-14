"""Market master health-check service."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

import pytz

from services.market_master_query_service import (
    MarketMasterQueryService,
    MarketMasterSnapshotResult,
)

_KST = pytz.timezone("Asia/Seoul")


class MarketMasterHealthOutcome(str, enum.Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MarketMasterHealthResult:
    outcome: MarketMasterHealthOutcome
    exists: bool
    symbol_count: int
    refreshed_at: str | None
    refreshed_trade_date: str | None
    required_trade_date: str | None
    is_same_trade_date: bool | None
    min_symbol_count: int | None
    meets_min_symbol_count: bool | None
    rows: tuple
    reasons: tuple[str, ...]
    reason: str | None


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


def _normalize_min_symbol_count(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"min_symbol_count must be an integer or None: {value!r}"
        )
    if value < 1:
        raise ValueError(
            f"min_symbol_count must be >= 1 when provided: {value!r}"
        )
    return value


def _to_trade_date(timestamp_text: str) -> str:
    parsed = datetime.fromisoformat(timestamp_text)
    if parsed.tzinfo is None:
        raise ValueError(
            f"refreshed_at must be timezone-aware ISO8601: {timestamp_text!r}"
        )
    return parsed.astimezone(_KST).date().isoformat()


class MarketMasterHealthService:
    """Evaluate whether the current market master snapshot is safe to use."""

    def __init__(
        self,
        *,
        query_service: MarketMasterQueryService,
    ) -> None:
        self._query_service = query_service

    def check_snapshot(
        self,
        *,
        trade_date: str | None = None,
        require_same_trade_date: bool = False,
        min_symbol_count: int | None = None,
    ) -> MarketMasterHealthResult:
        if not isinstance(require_same_trade_date, bool):
            raise ValueError(
                "require_same_trade_date must be a bool: "
                f"{require_same_trade_date!r}"
            )
        min_symbol_count = _normalize_min_symbol_count(min_symbol_count)
        if trade_date is not None:
            trade_date = _require_trade_date(trade_date)
        if require_same_trade_date and trade_date is None:
            raise ValueError(
                "trade_date is required when require_same_trade_date=True."
            )

        snapshot = self._query_service.get_snapshot()
        return self._evaluate_snapshot(
            snapshot=snapshot,
            trade_date=trade_date,
            require_same_trade_date=require_same_trade_date,
            min_symbol_count=min_symbol_count,
        )

    def _evaluate_snapshot(
        self,
        *,
        snapshot: MarketMasterSnapshotResult,
        trade_date: str | None,
        require_same_trade_date: bool,
        min_symbol_count: int | None,
    ) -> MarketMasterHealthResult:
        reasons: list[str] = []
        refreshed_trade_date = None
        is_same_trade_date = None
        meets_min_symbol_count = None

        if not snapshot.exists:
            reasons.append("Market master snapshot is missing.")
        else:
            refreshed_trade_date = _to_trade_date(snapshot.refreshed_at)
            if trade_date is not None:
                is_same_trade_date = refreshed_trade_date == trade_date
            if require_same_trade_date and is_same_trade_date is False:
                reasons.append(
                    "Market master snapshot is stale for "
                    f"trade_date={trade_date}: "
                    f"refreshed_trade_date={refreshed_trade_date}"
                )
            if min_symbol_count is not None:
                meets_min_symbol_count = snapshot.symbol_count >= min_symbol_count
                if not meets_min_symbol_count:
                    reasons.append(
                        "Market master snapshot symbol_count is below minimum: "
                        f"actual={snapshot.symbol_count}, "
                        f"minimum={min_symbol_count}"
                    )

        outcome = (
            MarketMasterHealthOutcome.READY
            if not reasons
            else MarketMasterHealthOutcome.BLOCKED
        )
        reason = None if not reasons else "; ".join(reasons)

        return MarketMasterHealthResult(
            outcome=outcome,
            exists=snapshot.exists,
            symbol_count=snapshot.symbol_count,
            refreshed_at=snapshot.refreshed_at,
            refreshed_trade_date=refreshed_trade_date,
            required_trade_date=trade_date,
            is_same_trade_date=is_same_trade_date,
            min_symbol_count=min_symbol_count,
            meets_min_symbol_count=meets_min_symbol_count,
            rows=snapshot.rows,
            reasons=tuple(reasons),
            reason=reason,
        )
