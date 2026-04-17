"""Intraday trigger state machine for buy timing 2."""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from datetime import datetime, time

import pytz


_KST = pytz.timezone("Asia/Seoul")


def _require_trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"trade_date must be a string: {value!r}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"trade_date must be YYYY-MM-DD: {value!r}") from exc
    return value


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _require_rate(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a float: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or not (0 < normalized < 1):
        raise ValueError(f"{name} must be between 0 and 1: {value!r}")
    return normalized


def _require_time_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string: {value!r}")
    try:
        datetime.strptime(value, "%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{name} must be HH:MM:SS: {value!r}") from exc
    return value


def _parse_time_text(value: str) -> time:
    return datetime.strptime(value, "%H:%M:%S").time()


class Timing2IntradayStage(str, enum.Enum):
    WAIT_BREAKOUT = "WAIT_BREAKOUT"
    WAIT_PULLBACK = "WAIT_PULLBACK"
    WAIT_REBOUND = "WAIT_REBOUND"
    TRIGGERED = "TRIGGERED"
    EXPIRED = "EXPIRED"


class Timing2IntradayTransition(str, enum.Enum):
    NONE = "NONE"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    PULLBACK_CONFIRMED = "PULLBACK_CONFIRMED"
    REBOUND_TRIGGERED = "REBOUND_TRIGGERED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Timing2IntradayTriggerSettings:
    tolerance_rate: float = 0.003
    start_time: str = "09:00:00"
    cutoff_time: str = "12:00:00"

    def validated(self) -> "Timing2IntradayTriggerSettings":
        tolerance_rate = _require_rate("tolerance_rate", self.tolerance_rate)
        start_time = _require_time_text("start_time", self.start_time)
        cutoff_time = _require_time_text("cutoff_time", self.cutoff_time)
        if _parse_time_text(start_time) >= _parse_time_text(cutoff_time):
            raise ValueError(
                "cutoff_time must be later than start_time: "
                f"start={start_time}, cutoff={cutoff_time}"
            )
        return Timing2IntradayTriggerSettings(
            tolerance_rate=tolerance_rate,
            start_time=start_time,
            cutoff_time=cutoff_time,
        )


@dataclass(frozen=True)
class Timing2IntradayTriggerDecision:
    symbol: str
    trade_date: str
    observed_at: str
    stage_before: Timing2IntradayStage
    stage_after: Timing2IntradayStage
    transition: Timing2IntradayTransition
    base_open_price: int
    current_price: int
    breakout_trigger_price: int
    pullback_trigger_price: int


class Timing2IntradayTriggerEvaluator:
    """
    Evaluate timing2 intraday trigger progression.

    Sequence from the project spec:
    1. breakout: price >= open * (1 + tolerance)
    2. pullback: price <= open * (1 - tolerance)
    3. rebound:  price >= open
    """

    def evaluate(
        self,
        *,
        symbol: str,
        trade_date: str,
        observed_at: datetime,
        base_open_price: int,
        current_price: int,
        stage_before: Timing2IntradayStage,
        settings: Timing2IntradayTriggerSettings,
    ) -> Timing2IntradayTriggerDecision:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        normalized_trade_date = _require_trade_date(trade_date)
        if not isinstance(stage_before, Timing2IntradayStage):
            raise ValueError(
                "stage_before must be a Timing2IntradayStage instance."
            )
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise ValueError("observed_at must be a timezone-aware datetime.")

        normalized_settings = settings.validated()
        normalized_open = _require_positive_int("base_open_price", base_open_price)
        normalized_price = _require_positive_int("current_price", current_price)

        observed_at_kst = observed_at.astimezone(_KST)
        if observed_at_kst.strftime("%Y-%m-%d") != normalized_trade_date:
            raise ValueError(
                "observed_at date must match trade_date in KST: "
                f"trade_date={normalized_trade_date}, "
                f"observed_at={observed_at_kst.isoformat()}"
            )

        breakout_trigger_price = math.ceil(
            normalized_open * (1 + normalized_settings.tolerance_rate)
        )
        pullback_trigger_price = math.floor(
            normalized_open * (1 - normalized_settings.tolerance_rate)
        )

        stage_after = stage_before
        transition = Timing2IntradayTransition.NONE
        current_time = observed_at_kst.time()
        start_time = _parse_time_text(normalized_settings.start_time)
        cutoff_time = _parse_time_text(normalized_settings.cutoff_time)

        if stage_before not in (
            Timing2IntradayStage.TRIGGERED,
            Timing2IntradayStage.EXPIRED,
        ):
            if current_time >= cutoff_time:
                stage_after = Timing2IntradayStage.EXPIRED
                transition = Timing2IntradayTransition.EXPIRED
            elif current_time >= start_time:
                if (
                    stage_before == Timing2IntradayStage.WAIT_BREAKOUT
                    and normalized_price >= breakout_trigger_price
                ):
                    stage_after = Timing2IntradayStage.WAIT_PULLBACK
                    transition = Timing2IntradayTransition.BREAKOUT_CONFIRMED
                elif (
                    stage_before == Timing2IntradayStage.WAIT_PULLBACK
                    and normalized_price <= pullback_trigger_price
                ):
                    stage_after = Timing2IntradayStage.WAIT_REBOUND
                    transition = Timing2IntradayTransition.PULLBACK_CONFIRMED
                elif (
                    stage_before == Timing2IntradayStage.WAIT_REBOUND
                    and normalized_price >= normalized_open
                ):
                    stage_after = Timing2IntradayStage.TRIGGERED
                    transition = Timing2IntradayTransition.REBOUND_TRIGGERED

        return Timing2IntradayTriggerDecision(
            symbol=symbol.strip(),
            trade_date=normalized_trade_date,
            observed_at=observed_at_kst.isoformat(),
            stage_before=stage_before,
            stage_after=stage_after,
            transition=transition,
            base_open_price=normalized_open,
            current_price=normalized_price,
            breakout_trigger_price=breakout_trigger_price,
            pullback_trigger_price=pullback_trigger_price,
        )
