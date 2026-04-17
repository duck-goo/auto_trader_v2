"""Intraday trigger evaluator for buy timing 1."""

from __future__ import annotations

import enum
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


class Timing1IntradayStage(str, enum.Enum):
    WAIT_BREAKOUT = "WAIT_BREAKOUT"
    TRIGGERED = "TRIGGERED"
    EXPIRED = "EXPIRED"


class Timing1IntradayTransition(str, enum.Enum):
    NONE = "NONE"
    BREAKOUT_TRIGGERED = "BREAKOUT_TRIGGERED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class Timing1IntradayTriggerSettings:
    start_time: str = "09:00:00"
    cutoff_time: str = "12:00:00"

    def validated(self) -> "Timing1IntradayTriggerSettings":
        start_time = _require_time_text("start_time", self.start_time)
        cutoff_time = _require_time_text("cutoff_time", self.cutoff_time)
        if _parse_time_text(start_time) >= _parse_time_text(cutoff_time):
            raise ValueError(
                "cutoff_time must be later than start_time: "
                f"start={start_time}, cutoff={cutoff_time}"
            )
        return Timing1IntradayTriggerSettings(
            start_time=start_time,
            cutoff_time=cutoff_time,
        )


@dataclass(frozen=True)
class Timing1IntradayTriggerDecision:
    symbol: str
    trade_date: str
    observed_at: str
    stage_before: Timing1IntradayStage
    stage_after: Timing1IntradayStage
    transition: Timing1IntradayTransition
    target_price: int
    current_price: int


class Timing1IntradayTriggerEvaluator:
    """
    Evaluate timing1 intraday breakout.

    Sequence from the project spec:
    - target price = convergence day daily high
    - monitoring time = 09:00 ~ 12:00
    - first tick at or above target triggers the signal
    - if not triggered by cutoff, the candidate expires for the day
    """

    def evaluate(
        self,
        *,
        symbol: str,
        trade_date: str,
        observed_at: datetime,
        target_price: int,
        current_price: int,
        stage_before: Timing1IntradayStage,
        settings: Timing1IntradayTriggerSettings,
    ) -> Timing1IntradayTriggerDecision:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        normalized_trade_date = _require_trade_date(trade_date)
        if not isinstance(stage_before, Timing1IntradayStage):
            raise ValueError(
                "stage_before must be a Timing1IntradayStage instance."
            )
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise ValueError("observed_at must be a timezone-aware datetime.")

        normalized_settings = settings.validated()
        normalized_target = _require_positive_int("target_price", target_price)
        normalized_price = _require_positive_int("current_price", current_price)

        observed_at_kst = observed_at.astimezone(_KST)
        if observed_at_kst.strftime("%Y-%m-%d") != normalized_trade_date:
            raise ValueError(
                "observed_at date must match trade_date in KST: "
                f"trade_date={normalized_trade_date}, "
                f"observed_at={observed_at_kst.isoformat()}"
            )

        stage_after = stage_before
        transition = Timing1IntradayTransition.NONE
        current_time = observed_at_kst.time()
        start_time = _parse_time_text(normalized_settings.start_time)
        cutoff_time = _parse_time_text(normalized_settings.cutoff_time)

        if stage_before not in (
            Timing1IntradayStage.TRIGGERED,
            Timing1IntradayStage.EXPIRED,
        ):
            if current_time >= cutoff_time:
                stage_after = Timing1IntradayStage.EXPIRED
                transition = Timing1IntradayTransition.EXPIRED
            elif current_time >= start_time and normalized_price >= normalized_target:
                stage_after = Timing1IntradayStage.TRIGGERED
                transition = Timing1IntradayTransition.BREAKOUT_TRIGGERED

        return Timing1IntradayTriggerDecision(
            symbol=symbol.strip(),
            trade_date=normalized_trade_date,
            observed_at=observed_at_kst.isoformat(),
            stage_before=stage_before,
            stage_after=stage_after,
            transition=transition,
            target_price=normalized_target,
            current_price=normalized_price,
        )
