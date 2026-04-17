"""Read-only evaluator for real-time sell exit rules."""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass


def _require_non_empty_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"symbol must be a string: {value!r}")
    normalized = value.strip()
    if not normalized:
        raise ValueError("symbol cannot be empty.")
    return normalized


def _require_positive_price(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer price: {value!r}")
    return value


def _require_positive_ratio(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive float: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{name} must be a finite positive float: {value!r}")
    return normalized


class SellExitRule(str, enum.Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


@dataclass(frozen=True)
class SellExitSettings:
    """Internal ratios. Example: 0.03 means 3 percent."""

    stop_loss_ratio: float = 0.03
    take_profit_ratio: float = 0.05

    def validated(self) -> "SellExitSettings":
        return SellExitSettings(
            stop_loss_ratio=_require_positive_ratio(
                "stop_loss_ratio",
                self.stop_loss_ratio,
            ),
            take_profit_ratio=_require_positive_ratio(
                "take_profit_ratio",
                self.take_profit_ratio,
            ),
        )


@dataclass(frozen=True)
class SellExitMatch:
    symbol: str
    rule: SellExitRule
    avg_price: int
    current_price: int
    trigger_price: int
    stop_loss_ratio: float
    take_profit_ratio: float


class SellExitEvaluator:
    """
    Evaluate only the real-time stop-loss / take-profit part of the sell logic.

    Priority from the project spec:
    - STOP_LOSS first
    - TAKE_PROFIT second
    - MACD later in a separate step
    """

    def evaluate(
        self,
        *,
        symbol: str,
        avg_price: int,
        current_price: int,
        settings: SellExitSettings,
    ) -> SellExitMatch | None:
        normalized_symbol = _require_non_empty_symbol(symbol)
        normalized_avg_price = _require_positive_price("avg_price", avg_price)
        normalized_current_price = _require_positive_price(
            "current_price",
            current_price,
        )
        normalized_settings = settings.validated()

        stop_loss_trigger_price = max(
            1,
            int(
                math.floor(
                    normalized_avg_price
                    * (1.0 - normalized_settings.stop_loss_ratio)
                )
            ),
        )
        if normalized_current_price <= stop_loss_trigger_price:
            return SellExitMatch(
                symbol=normalized_symbol,
                rule=SellExitRule.STOP_LOSS,
                avg_price=normalized_avg_price,
                current_price=normalized_current_price,
                trigger_price=stop_loss_trigger_price,
                stop_loss_ratio=normalized_settings.stop_loss_ratio,
                take_profit_ratio=normalized_settings.take_profit_ratio,
            )

        take_profit_trigger_price = max(
            1,
            int(
                math.ceil(
                    normalized_avg_price
                    * (1.0 + normalized_settings.take_profit_ratio)
                )
            ),
        )
        if normalized_current_price >= take_profit_trigger_price:
            return SellExitMatch(
                symbol=normalized_symbol,
                rule=SellExitRule.TAKE_PROFIT,
                avg_price=normalized_avg_price,
                current_price=normalized_current_price,
                trigger_price=take_profit_trigger_price,
                stop_loss_ratio=normalized_settings.stop_loss_ratio,
                take_profit_ratio=normalized_settings.take_profit_ratio,
            )

        return None
