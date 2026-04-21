"""30-second candle trigger evaluator for buy timing 2."""

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


def _require_optional_positive_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    return _require_positive_int(name, value)


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


class Timing2ThirtySecondTriggerType(str, enum.Enum):
    NONE = "NONE"
    MORNING_OPEN_RECLAIM = "MORNING_OPEN_RECLAIM"
    RANGE_HIGH_BREAKOUT = "RANGE_HIGH_BREAKOUT"


class Timing2ThirtySecondTransition(str, enum.Enum):
    NONE = "NONE"
    MORNING_DIP_CONFIRMED = "MORNING_DIP_CONFIRMED"
    MORNING_OPEN_RECLAIM_TRIGGERED = "MORNING_OPEN_RECLAIM_TRIGGERED"
    RANGE_HIGH_BREAKOUT_TRIGGERED = "RANGE_HIGH_BREAKOUT_TRIGGERED"


@dataclass(frozen=True)
class Timing2ThirtySecondTriggerSettings:
    morning_start_time: str = "09:00:00"
    morning_end_time: str = "10:00:00"
    range_breakout_start_time: str = "10:00:00"

    def validated(self) -> "Timing2ThirtySecondTriggerSettings":
        morning_start_time = _require_time_text(
            "morning_start_time",
            self.morning_start_time,
        )
        morning_end_time = _require_time_text(
            "morning_end_time",
            self.morning_end_time,
        )
        range_breakout_start_time = _require_time_text(
            "range_breakout_start_time",
            self.range_breakout_start_time,
        )

        morning_start = _parse_time_text(morning_start_time)
        morning_end = _parse_time_text(morning_end_time)
        range_start = _parse_time_text(range_breakout_start_time)
        if morning_start >= morning_end:
            raise ValueError(
                "morning_end_time must be later than morning_start_time: "
                f"start={morning_start_time}, end={morning_end_time}"
            )
        if morning_end > range_start:
            raise ValueError(
                "range_breakout_start_time must be at or after morning_end_time: "
                f"morning_end={morning_end_time}, range_start={range_breakout_start_time}"
            )

        return Timing2ThirtySecondTriggerSettings(
            morning_start_time=morning_start_time,
            morning_end_time=morning_end_time,
            range_breakout_start_time=range_breakout_start_time,
        )


@dataclass(frozen=True)
class Timing2ThirtySecondTriggerState:
    morning_dipped_below_open: bool = False
    morning_triggered: bool = False
    range_triggered: bool = False


@dataclass(frozen=True)
class Timing2ThirtySecondTriggerDecision:
    symbol: str
    trade_date: str
    bar_end_at: str
    state_before: Timing2ThirtySecondTriggerState
    state_after: Timing2ThirtySecondTriggerState
    transition: Timing2ThirtySecondTransition
    trigger_type: Timing2ThirtySecondTriggerType
    buy_triggered: bool
    session_open_price: int
    bar_close_price: int
    morning_high_close: int | None


class Timing2ThirtySecondTriggerEvaluator:
    """
    Evaluate buy timing 2 with completed 30-second candle closes.

    The evaluator does not fetch candles or place orders. It only decides
    whether one newly completed 30-second candle creates a new buy trigger.
    This keeps strategy judgment separate from execution and persistence.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        trade_date: str,
        bar_end_at: datetime,
        session_open_price: int,
        bar_close_price: int,
        morning_high_close: int | None,
        state_before: Timing2ThirtySecondTriggerState,
        settings: Timing2ThirtySecondTriggerSettings,
    ) -> Timing2ThirtySecondTriggerDecision:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string: {symbol!r}")
        normalized_trade_date = _require_trade_date(trade_date)
        if not isinstance(bar_end_at, datetime) or bar_end_at.tzinfo is None:
            raise ValueError("bar_end_at must be a timezone-aware datetime.")
        if not isinstance(state_before, Timing2ThirtySecondTriggerState):
            raise ValueError(
                "state_before must be a Timing2ThirtySecondTriggerState instance."
            )

        normalized_settings = settings.validated()
        normalized_open = _require_positive_int(
            "session_open_price",
            session_open_price,
        )
        normalized_close = _require_positive_int(
            "bar_close_price",
            bar_close_price,
        )
        normalized_morning_high = _require_optional_positive_int(
            "morning_high_close",
            morning_high_close,
        )

        bar_end_at_kst = bar_end_at.astimezone(_KST)
        if bar_end_at_kst.strftime("%Y-%m-%d") != normalized_trade_date:
            raise ValueError(
                "bar_end_at date must match trade_date in KST: "
                f"trade_date={normalized_trade_date}, "
                f"bar_end_at={bar_end_at_kst.isoformat()}"
            )

        current_time = bar_end_at_kst.time()
        morning_start = _parse_time_text(normalized_settings.morning_start_time)
        morning_end = _parse_time_text(normalized_settings.morning_end_time)
        range_start = _parse_time_text(normalized_settings.range_breakout_start_time)

        state_after = state_before
        transition = Timing2ThirtySecondTransition.NONE
        trigger_type = Timing2ThirtySecondTriggerType.NONE

        is_morning_window = morning_start <= current_time < morning_end
        if is_morning_window and not state_before.morning_triggered:
            state_after, transition, trigger_type = self._evaluate_morning_reclaim(
                state_before=state_before,
                session_open_price=normalized_open,
                bar_close_price=normalized_close,
            )
        elif current_time >= range_start and not state_before.range_triggered:
            if normalized_morning_high is None:
                raise ValueError(
                    "morning_high_close is required for range breakout evaluation."
                )
            state_after, transition, trigger_type = self._evaluate_range_breakout(
                state_before=state_before,
                morning_high_close=normalized_morning_high,
                bar_close_price=normalized_close,
            )

        return Timing2ThirtySecondTriggerDecision(
            symbol=symbol.strip(),
            trade_date=normalized_trade_date,
            bar_end_at=bar_end_at_kst.isoformat(),
            state_before=state_before,
            state_after=state_after,
            transition=transition,
            trigger_type=trigger_type,
            buy_triggered=trigger_type != Timing2ThirtySecondTriggerType.NONE,
            session_open_price=normalized_open,
            bar_close_price=normalized_close,
            morning_high_close=normalized_morning_high,
        )

    def _evaluate_morning_reclaim(
        self,
        *,
        state_before: Timing2ThirtySecondTriggerState,
        session_open_price: int,
        bar_close_price: int,
    ) -> tuple[
        Timing2ThirtySecondTriggerState,
        Timing2ThirtySecondTransition,
        Timing2ThirtySecondTriggerType,
    ]:
        if not state_before.morning_dipped_below_open:
            if bar_close_price < session_open_price:
                return (
                    Timing2ThirtySecondTriggerState(
                        morning_dipped_below_open=True,
                        morning_triggered=state_before.morning_triggered,
                        range_triggered=state_before.range_triggered,
                    ),
                    Timing2ThirtySecondTransition.MORNING_DIP_CONFIRMED,
                    Timing2ThirtySecondTriggerType.NONE,
                )
            return (
                state_before,
                Timing2ThirtySecondTransition.NONE,
                Timing2ThirtySecondTriggerType.NONE,
            )

        if bar_close_price > session_open_price:
            return (
                Timing2ThirtySecondTriggerState(
                    morning_dipped_below_open=True,
                    morning_triggered=True,
                    range_triggered=state_before.range_triggered,
                ),
                Timing2ThirtySecondTransition.MORNING_OPEN_RECLAIM_TRIGGERED,
                Timing2ThirtySecondTriggerType.MORNING_OPEN_RECLAIM,
            )

        return (
            state_before,
            Timing2ThirtySecondTransition.NONE,
            Timing2ThirtySecondTriggerType.NONE,
        )

    def _evaluate_range_breakout(
        self,
        *,
        state_before: Timing2ThirtySecondTriggerState,
        morning_high_close: int,
        bar_close_price: int,
    ) -> tuple[
        Timing2ThirtySecondTriggerState,
        Timing2ThirtySecondTransition,
        Timing2ThirtySecondTriggerType,
    ]:
        if bar_close_price > morning_high_close:
            return (
                Timing2ThirtySecondTriggerState(
                    morning_dipped_below_open=state_before.morning_dipped_below_open,
                    morning_triggered=state_before.morning_triggered,
                    range_triggered=True,
                ),
                Timing2ThirtySecondTransition.RANGE_HIGH_BREAKOUT_TRIGGERED,
                Timing2ThirtySecondTriggerType.RANGE_HIGH_BREAKOUT,
            )

        return (
            state_before,
            Timing2ThirtySecondTransition.NONE,
            Timing2ThirtySecondTriggerType.NONE,
        )
