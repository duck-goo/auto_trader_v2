"""Timing2 lot-level sell exit evaluator."""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass


DEFAULT_TIMING2_SELL_COST_RATE = 0.002140527


def _require_non_empty_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"symbol must be a string: {value!r}")
    normalized = value.strip()
    if not normalized:
        raise ValueError("symbol cannot be empty.")
    return normalized


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _require_positive_price(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer price: {value!r}")
    return value


def _require_positive_number(name: str, value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive number: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{name} must be a finite positive number: {value!r}")
    return normalized


def _require_ratio_between_zero_and_one(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a ratio between 0 and 1: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0 or normalized > 1.0:
        raise ValueError(f"{name} must be > 0 and <= 1: {value!r}")
    return normalized


def _require_non_negative_ratio_less_than_one(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a non-negative ratio: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0 or normalized >= 1.0:
        raise ValueError(f"{name} must be >= 0 and < 1: {value!r}")
    return normalized


def _require_bool(name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean: {value!r}")
    return value


def _normalize_ma_inputs(
    *,
    latest_3m_close: int | None,
    ma5_3m: int | float | None,
) -> tuple[int | None, float | None]:
    if latest_3m_close is None and ma5_3m is None:
        return None, None
    if latest_3m_close is None or ma5_3m is None:
        raise ValueError("latest_3m_close and ma5_3m must be provided together.")
    return (
        _require_positive_price("latest_3m_close", latest_3m_close),
        _require_positive_number("ma5_3m", ma5_3m),
    )


class Timing2LotExitRule(str, enum.Enum):
    STOP_LOSS = "STOP_LOSS"
    THREE_MINUTE_MA_BREAK = "THREE_MINUTE_MA_BREAK"
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"


@dataclass(frozen=True)
class Timing2LotExitSettings:
    """Timing2 lot sell ratios. Example: 0.015 means 1.5 percent."""

    stop_loss_ratio: float = 0.015
    take_profit_ratio: float = 0.05
    partial_take_profit_ratio: float = 0.5
    sell_cost_rate: float = DEFAULT_TIMING2_SELL_COST_RATE

    def validated(self) -> "Timing2LotExitSettings":
        return Timing2LotExitSettings(
            stop_loss_ratio=_require_ratio_between_zero_and_one(
                "stop_loss_ratio",
                self.stop_loss_ratio,
            ),
            take_profit_ratio=_require_ratio_between_zero_and_one(
                "take_profit_ratio",
                self.take_profit_ratio,
            ),
            partial_take_profit_ratio=_require_ratio_between_zero_and_one(
                "partial_take_profit_ratio",
                self.partial_take_profit_ratio,
            ),
            sell_cost_rate=_require_non_negative_ratio_less_than_one(
                "sell_cost_rate",
                self.sell_cost_rate,
            ),
        )


@dataclass(frozen=True)
class Timing2LotExitDecision:
    symbol: str
    lot_id: int
    rule: Timing2LotExitRule
    sell_qty: int
    remaining_qty: int
    total_buy_qty: int
    avg_buy_price: int
    current_price: int
    trigger_price: int | None
    net_return_rate: float
    stop_loss_ratio: float
    take_profit_ratio: float
    partial_take_profit_ratio: float
    sell_cost_rate: float
    partial_take_profit_done: bool
    latest_3m_close: int | None
    ma5_3m: float | None


class Timing2LotExitEvaluator:
    """
    Evaluate Timing2 sell rules for one actual filled entry lot.

    Priority is intentionally explicit:
    - STOP_LOSS uses estimated net return after sell fee/tax.
    - THREE_MINUTE_MA_BREAK sells the full remaining lot.
    - TAKE_PROFIT_PARTIAL sells a rounded-up half only once.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        lot_id: int,
        remaining_qty: int,
        total_buy_qty: int,
        avg_buy_price: int,
        current_price: int,
        partial_take_profit_done: bool,
        latest_3m_close: int | None = None,
        ma5_3m: int | float | None = None,
        settings: Timing2LotExitSettings | None = None,
    ) -> Timing2LotExitDecision | None:
        normalized_symbol = _require_non_empty_symbol(symbol)
        normalized_lot_id = _require_positive_int("lot_id", lot_id)
        normalized_remaining_qty = _require_positive_int(
            "remaining_qty",
            remaining_qty,
        )
        normalized_total_buy_qty = _require_positive_int(
            "total_buy_qty",
            total_buy_qty,
        )
        if normalized_remaining_qty > normalized_total_buy_qty:
            raise ValueError(
                "remaining_qty cannot exceed total_buy_qty: "
                f"remaining_qty={remaining_qty}, total_buy_qty={total_buy_qty}"
            )
        normalized_avg_buy_price = _require_positive_price(
            "avg_buy_price",
            avg_buy_price,
        )
        normalized_current_price = _require_positive_price(
            "current_price",
            current_price,
        )
        normalized_partial_done = _require_bool(
            "partial_take_profit_done",
            partial_take_profit_done,
        )
        normalized_latest_3m_close, normalized_ma5_3m = _normalize_ma_inputs(
            latest_3m_close=latest_3m_close,
            ma5_3m=ma5_3m,
        )
        normalized_settings = (settings or Timing2LotExitSettings()).validated()

        net_return_rate = self._net_return_rate(
            avg_buy_price=normalized_avg_buy_price,
            current_price=normalized_current_price,
            sell_cost_rate=normalized_settings.sell_cost_rate,
        )
        stop_loss_trigger_price = self._stop_loss_trigger_price(
            avg_buy_price=normalized_avg_buy_price,
            stop_loss_ratio=normalized_settings.stop_loss_ratio,
            sell_cost_rate=normalized_settings.sell_cost_rate,
        )
        if normalized_current_price <= stop_loss_trigger_price:
            return self._decision(
                symbol=normalized_symbol,
                lot_id=normalized_lot_id,
                rule=Timing2LotExitRule.STOP_LOSS,
                sell_qty=normalized_remaining_qty,
                remaining_qty=normalized_remaining_qty,
                total_buy_qty=normalized_total_buy_qty,
                avg_buy_price=normalized_avg_buy_price,
                current_price=normalized_current_price,
                trigger_price=stop_loss_trigger_price,
                net_return_rate=net_return_rate,
                settings=normalized_settings,
                partial_take_profit_done=normalized_partial_done,
                latest_3m_close=normalized_latest_3m_close,
                ma5_3m=normalized_ma5_3m,
            )

        if (
            normalized_latest_3m_close is not None
            and normalized_ma5_3m is not None
            and normalized_latest_3m_close < normalized_ma5_3m
        ):
            return self._decision(
                symbol=normalized_symbol,
                lot_id=normalized_lot_id,
                rule=Timing2LotExitRule.THREE_MINUTE_MA_BREAK,
                sell_qty=normalized_remaining_qty,
                remaining_qty=normalized_remaining_qty,
                total_buy_qty=normalized_total_buy_qty,
                avg_buy_price=normalized_avg_buy_price,
                current_price=normalized_current_price,
                trigger_price=None,
                net_return_rate=net_return_rate,
                settings=normalized_settings,
                partial_take_profit_done=normalized_partial_done,
                latest_3m_close=normalized_latest_3m_close,
                ma5_3m=normalized_ma5_3m,
            )

        take_profit_trigger_price = self._take_profit_trigger_price(
            avg_buy_price=normalized_avg_buy_price,
            take_profit_ratio=normalized_settings.take_profit_ratio,
        )
        if (
            not normalized_partial_done
            and normalized_current_price >= take_profit_trigger_price
        ):
            sell_qty = self._partial_take_profit_qty(
                remaining_qty=normalized_remaining_qty,
                partial_take_profit_ratio=normalized_settings.partial_take_profit_ratio,
            )
            return self._decision(
                symbol=normalized_symbol,
                lot_id=normalized_lot_id,
                rule=Timing2LotExitRule.TAKE_PROFIT_PARTIAL,
                sell_qty=sell_qty,
                remaining_qty=normalized_remaining_qty,
                total_buy_qty=normalized_total_buy_qty,
                avg_buy_price=normalized_avg_buy_price,
                current_price=normalized_current_price,
                trigger_price=take_profit_trigger_price,
                net_return_rate=net_return_rate,
                settings=normalized_settings,
                partial_take_profit_done=normalized_partial_done,
                latest_3m_close=normalized_latest_3m_close,
                ma5_3m=normalized_ma5_3m,
            )

        return None

    @staticmethod
    def _net_return_rate(
        *,
        avg_buy_price: int,
        current_price: int,
        sell_cost_rate: float,
    ) -> float:
        return (current_price * (1.0 - sell_cost_rate) / avg_buy_price) - 1.0

    @staticmethod
    def _stop_loss_trigger_price(
        *,
        avg_buy_price: int,
        stop_loss_ratio: float,
        sell_cost_rate: float,
    ) -> int:
        return max(
            1,
            int(
                math.floor(
                    avg_buy_price
                    * (1.0 - stop_loss_ratio)
                    / (1.0 - sell_cost_rate)
                )
            ),
        )

    @staticmethod
    def _take_profit_trigger_price(
        *,
        avg_buy_price: int,
        take_profit_ratio: float,
    ) -> int:
        return max(1, int(math.ceil(avg_buy_price * (1.0 + take_profit_ratio))))

    @staticmethod
    def _partial_take_profit_qty(
        *,
        remaining_qty: int,
        partial_take_profit_ratio: float,
    ) -> int:
        return min(
            remaining_qty,
            max(1, int(math.ceil(remaining_qty * partial_take_profit_ratio))),
        )

    @staticmethod
    def _decision(
        *,
        symbol: str,
        lot_id: int,
        rule: Timing2LotExitRule,
        sell_qty: int,
        remaining_qty: int,
        total_buy_qty: int,
        avg_buy_price: int,
        current_price: int,
        trigger_price: int | None,
        net_return_rate: float,
        settings: Timing2LotExitSettings,
        partial_take_profit_done: bool,
        latest_3m_close: int | None,
        ma5_3m: float | None,
    ) -> Timing2LotExitDecision:
        return Timing2LotExitDecision(
            symbol=symbol,
            lot_id=lot_id,
            rule=rule,
            sell_qty=sell_qty,
            remaining_qty=remaining_qty,
            total_buy_qty=total_buy_qty,
            avg_buy_price=avg_buy_price,
            current_price=current_price,
            trigger_price=trigger_price,
            net_return_rate=net_return_rate,
            stop_loss_ratio=settings.stop_loss_ratio,
            take_profit_ratio=settings.take_profit_ratio,
            partial_take_profit_ratio=settings.partial_take_profit_ratio,
            sell_cost_rate=settings.sell_cost_rate,
            partial_take_profit_done=partial_take_profit_done,
            latest_3m_close=latest_3m_close,
            ma5_3m=ma5_3m,
        )
