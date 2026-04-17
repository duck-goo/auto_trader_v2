"""Trading risk guard service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pytz

from storage.repositories import OrderRepository, TradingControlRepository

_KST = pytz.timezone("Asia/Seoul")


def _default_now() -> datetime:
    return datetime.now(_KST)


def _validate_optional_positive_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer or None: {value!r}")
    return value


@dataclass(frozen=True)
class TradingRiskGuardResult:
    trade_date: str
    evaluated_at: str
    kill_switch_enabled: bool
    kill_switch_note: str | None
    today_order_count: int
    max_daily_order_count: int | None
    buy_allowed: bool
    buy_block_reason_code: str | None
    buy_block_reason_message: str | None
    sell_allowed: bool
    sell_block_reason_code: str | None
    sell_block_reason_message: str | None


class TradingRiskGuardService:
    """Evaluate persisted trading controls and day-level order guards."""

    def __init__(
        self,
        *,
        order_repo: OrderRepository,
        trading_control_repo: TradingControlRepository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._order_repo = order_repo
        self._trading_control_repo = trading_control_repo
        self._now_fn = now_fn or _default_now

    def evaluate(
        self,
        *,
        trade_date: str,
        max_daily_order_count: int | None = None,
    ) -> TradingRiskGuardResult:
        normalized_max_daily_order_count = _validate_optional_positive_int(
            "max_daily_order_count",
            max_daily_order_count,
        )
        evaluated_at = self._now_fn().astimezone(_KST).isoformat()
        kill_switch_row = self._trading_control_repo.get_kill_switch()
        kill_switch_enabled = (
            False if kill_switch_row is None else kill_switch_row.is_enabled
        )
        kill_switch_note = None if kill_switch_row is None else kill_switch_row.note
        today_order_count = self._order_repo.count_requested_for_trade_date(
            trade_date=trade_date
        )

        if kill_switch_enabled:
            reason_code = "KILL_SWITCH_ENABLED"
            reason_message = "Kill Switch is enabled. Automated trading is blocked."
            if kill_switch_note:
                reason_message = f"{reason_message} note={kill_switch_note}"
            return TradingRiskGuardResult(
                trade_date=trade_date,
                evaluated_at=evaluated_at,
                kill_switch_enabled=True,
                kill_switch_note=kill_switch_note,
                today_order_count=today_order_count,
                max_daily_order_count=normalized_max_daily_order_count,
                buy_allowed=False,
                buy_block_reason_code=reason_code,
                buy_block_reason_message=reason_message,
                sell_allowed=False,
                sell_block_reason_code=reason_code,
                sell_block_reason_message=reason_message,
            )

        if (
            normalized_max_daily_order_count is not None
            and today_order_count >= normalized_max_daily_order_count
        ):
            return TradingRiskGuardResult(
                trade_date=trade_date,
                evaluated_at=evaluated_at,
                kill_switch_enabled=False,
                kill_switch_note=kill_switch_note,
                today_order_count=today_order_count,
                max_daily_order_count=normalized_max_daily_order_count,
                buy_allowed=False,
                buy_block_reason_code="MAX_DAILY_ORDER_COUNT_REACHED",
                buy_block_reason_message=(
                    "Daily order count limit reached for new buy orders: "
                    f"today_order_count={today_order_count}, "
                    f"max_daily_order_count={normalized_max_daily_order_count}"
                ),
                sell_allowed=True,
                sell_block_reason_code=None,
                sell_block_reason_message=None,
            )

        return TradingRiskGuardResult(
            trade_date=trade_date,
            evaluated_at=evaluated_at,
            kill_switch_enabled=False,
            kill_switch_note=kill_switch_note,
            today_order_count=today_order_count,
            max_daily_order_count=normalized_max_daily_order_count,
            buy_allowed=True,
            buy_block_reason_code=None,
            buy_block_reason_message=None,
            sell_allowed=True,
            sell_block_reason_code=None,
            sell_block_reason_message=None,
        )
