"""
Poll the full intraday trading cycle until the latest configured cutoff.

Safety:
- preview is the default
- execute mode uses one persisted runtime lock for the whole polling loop
- repeated failures stop the loop instead of retrying forever
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisBroker
from config.loader import load_settings
from logger import setup_logging
from services import (
    BuySignalExecutionService,
    BuySignalExecutionSettings,
    ExecutionRecoveryFinalizeService,
    IntradayBar15mRefreshService,
    IntradayTradingCycleService,
    IntradayTriggerCombinedScanService,
    OrderMaintenanceService,
    OrderService,
    RuntimeLockBusyError,
    RuntimeLockService,
    SellExitScanService,
    SellMacdExitScanService,
    SellSignalExecutionService,
    SellSignalExecutionSettings,
    StaleBuyOrderCancelService,
    StaleBuyOrderCancelSettings,
    StaleSellOrderCancelService,
    Timing2LotExitScanService,
    TradingRiskGuardService,
    UnresolvedOrderSyncService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    EntryLotRepository,
    ExecutionRepository,
    IntradayBar15mRepository,
    IntradayBar30sRepository,
    OrderRepository,
    PositionRepository,
    RuntimeLockRepository,
    SignalRepository,
    TradingControlRepository,
)
from strategy import (
    DEFAULT_TIMING2_SELL_COST_RATE,
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2IntradayTriggerSettings,
)

KST = pytz.timezone("Asia/Seoul")


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll the full intraday trading cycle repeatedly."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--scan-timing1",
        action="store_true",
        help="Run timing1 buy trigger scan.",
    )
    parser.add_argument(
        "--scan-timing2",
        action="store_true",
        help="Run timing2 buy trigger scan.",
    )
    parser.add_argument(
        "--per-order-budget",
        type=int,
        required=True,
        help="Max KRW budget per buy order.",
    )
    parser.add_argument(
        "--max-holdings",
        type=int,
        required=True,
        help="Max concurrent holdings/unresolved-buy symbols.",
    )
    parser.add_argument(
        "--max-daily-order-count",
        type=int,
        default=None,
        help="Optional max total order count for the trade date. New buys are blocked once reached.",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=int,
        default=None,
        help="Optional max realized daily loss in KRW. New buys are blocked once reached.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Stale order timeout seconds for maintenance. Default: 300",
    )
    parser.add_argument(
        "--buy-signal-limit",
        type=int,
        default=200,
        help="How many unacted buy signals to inspect. Default: 200",
    )
    parser.add_argument(
        "--sell-signal-limit",
        type=int,
        default=200,
        help="How many unacted sell signals to inspect. Default: 200",
    )
    parser.add_argument(
        "--sell-stop-loss-percent",
        type=float,
        default=3.0,
        help="Sell stop-loss percent. Default: 3.0",
    )
    parser.add_argument(
        "--sell-take-profit-percent",
        type=float,
        default=5.0,
        help="Sell take-profit percent. Default: 5.0",
    )
    parser.add_argument(
        "--sell-macd-fast-window",
        type=int,
        default=12,
        help="Sell MACD fast EMA window. Default: 12",
    )
    parser.add_argument(
        "--sell-macd-slow-window",
        type=int,
        default=26,
        help="Sell MACD slow EMA window. Default: 26",
    )
    parser.add_argument(
        "--sell-macd-signal-window",
        type=int,
        default=9,
        help="Sell MACD signal EMA window. Default: 9",
    )
    parser.add_argument(
        "--sell-macd-consecutive-decline-bars",
        type=int,
        default=2,
        help="Sell MACD consecutive decline bars. Default: 2",
    )
    parser.add_argument(
        "--sell-macd-history-limit",
        type=int,
        default=300,
        help="Persisted 15m bar history limit for sell MACD scan. Default: 300",
    )
    parser.add_argument(
        "--timing2-lot-stop-loss-percent",
        type=float,
        default=1.5,
        help="Timing2 lot stop-loss percent after sell costs. Default: 1.5",
    )
    parser.add_argument(
        "--timing2-lot-take-profit-percent",
        type=float,
        default=5.0,
        help="Timing2 lot partial take-profit percent. Default: 5.0",
    )
    parser.add_argument(
        "--timing2-lot-partial-take-profit-percent",
        type=float,
        default=50.0,
        help="Timing2 lot partial take-profit sell ratio percent. Default: 50.0",
    )
    parser.add_argument(
        "--timing2-lot-sell-cost-rate",
        type=float,
        default=DEFAULT_TIMING2_SELL_COST_RATE,
        help=(
            "Timing2 lot combined sell fee/tax ratio. "
            f"Default: {DEFAULT_TIMING2_SELL_COST_RATE}"
        ),
    )
    parser.add_argument(
        "--buy-start-time",
        default="09:00:00",
        help="Buy execution start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--buy-cutoff-time",
        default="12:00:00",
        help="Buy execution cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--sell-start-time",
        default="09:00:00",
        help="Sell execution start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--sell-cutoff-time",
        default="15:20:00",
        help="Sell execution cutoff time HH:MM:SS. Default: 15:20:00",
    )
    parser.add_argument(
        "--timing1-start-time",
        default="09:00:00",
        help="Timing1 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing1-cutoff-time",
        default="12:00:00",
        help="Timing1 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--timing1-daily-count",
        type=int,
        default=5,
        help="Timing1 daily count for next-trading-day validation. Default: 5",
    )
    parser.add_argument(
        "--timing2-tolerance-rate",
        type=float,
        default=0.003,
        help="Timing2 trigger tolerance rate. Default: 0.003",
    )
    parser.add_argument(
        "--timing2-start-time",
        default="09:00:00",
        help="Timing2 monitoring start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing2-cutoff-time",
        default="12:00:00",
        help="Timing2 monitoring cutoff time HH:MM:SS. Default: 12:00:00",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=20,
        help="Polling interval in seconds. Default: 20",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Maximum cycle count. 0 means keep polling until stop condition. Default: 0",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive failed cycles. Default: 3",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually record scan signals, place orders, and run maintenance changes.",
    )
    parser.add_argument(
        "--lock-name",
        default=None,
        help="Optional runtime lock name.",
    )
    parser.add_argument(
        "--lock-lease-seconds",
        type=int,
        default=0,
        help="Runtime lock lease seconds. 0 means auto (max(interval*3, 90)). Default: 0",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional DB path override. Default: settings.db_path",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _validate_positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer: {value!r}")
    if allow_zero:
        if value < 0:
            raise ValueError(f"{name} must be >= 0: {value!r}")
    elif value <= 0:
        raise ValueError(f"{name} must be > 0: {value!r}")
    return value


def _resolve_scan_selection(args: argparse.Namespace) -> tuple[bool, bool]:
    if not args.scan_timing1 and not args.scan_timing2:
        return True, True
    return args.scan_timing1, args.scan_timing2


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"intraday_trading_polling:{args.trade_date}"


def _resolve_lock_lease_seconds(args: argparse.Namespace) -> int:
    if args.lock_lease_seconds > 0:
        lease_seconds = args.lock_lease_seconds
    else:
        lease_seconds = max(args.interval_seconds * 3, 90)
    if lease_seconds <= args.interval_seconds:
        raise ValueError(
            "lock lease must be longer than interval_seconds: "
            f"lease={lease_seconds}, interval={args.interval_seconds}"
        )
    return lease_seconds


def _parse_time_text(value: str) -> dt_time:
    return datetime.strptime(value, "%H:%M:%S").time()


def _seconds_until(*, now: datetime, target_time: dt_time) -> int:
    target = now.astimezone(KST).replace(
        hour=target_time.hour,
        minute=target_time.minute,
        second=target_time.second,
        microsecond=0,
    )
    delta = target - now.astimezone(KST)
    return max(0, int(delta.total_seconds()))


def _resolve_window(
    *,
    run_timing1: bool,
    run_timing2: bool,
    buy_execution_settings: BuySignalExecutionSettings,
    sell_execution_settings: SellSignalExecutionSettings,
    timing1_settings: Timing1IntradayTriggerSettings,
    timing2_settings: Timing2IntradayTriggerSettings,
) -> tuple[dt_time, dt_time]:
    start_candidates = [
        _parse_time_text(buy_execution_settings.start_time),
        _parse_time_text(sell_execution_settings.start_time),
    ]
    cutoff_candidates = [
        _parse_time_text(buy_execution_settings.cutoff_time),
        _parse_time_text(sell_execution_settings.cutoff_time),
    ]
    if run_timing1:
        start_candidates.append(_parse_time_text(timing1_settings.start_time))
        cutoff_candidates.append(_parse_time_text(timing1_settings.cutoff_time))
    if run_timing2:
        start_candidates.append(_parse_time_text(timing2_settings.start_time))
        cutoff_candidates.append(_parse_time_text(timing2_settings.cutoff_time))
    return min(start_candidates), max(cutoff_candidates)


def _serialize_cycle_step(step) -> dict[str, Any]:
    summary = None
    if step.result is not None:
        if hasattr(step.result, "candidate_count"):
            summary = {
                key: value
                for key, value in step.result.__dict__.items()
                if isinstance(value, (int, str, bool))
            }
        else:
            summary = {
                key: value
                for key, value in step.result.__dict__.items()
                if isinstance(value, (int, str, bool))
            }
    return {
        "outcome": step.outcome,
        "reason": step.reason,
        "summary": summary,
    }


def _build_cycle_payload(cycle_no: int, result) -> dict[str, Any]:
    return {
        "cycle_no": cycle_no,
        "trade_date": result.trade_date,
        "execute_actions": result.execute_actions,
        "record_scan_signals": result.record_scan_signals,
        "maintenance": _serialize_cycle_step(result.maintenance),
        "intraday_bar_refresh": _serialize_cycle_step(result.intraday_bar_refresh),
        "sell_exit_scan": _serialize_cycle_step(result.sell_exit_scan),
        "sell_macd_scan": _serialize_cycle_step(result.sell_macd_scan),
        "timing2_lot_exit_scan": _serialize_cycle_step(
            result.timing2_lot_exit_scan
        ),
        "sell_execution": _serialize_cycle_step(result.sell_execution),
        "buy_trigger_scan": _serialize_cycle_step(result.buy_trigger_scan),
        "buy_execution": _serialize_cycle_step(result.buy_execution),
    }


def _print_cycle_summary(cycle_payload: dict[str, Any]) -> None:
    _section(f"Polling Cycle {cycle_payload['cycle_no']}")
    for step_name in (
        "maintenance",
        "intraday_bar_refresh",
        "sell_exit_scan",
        "sell_macd_scan",
        "timing2_lot_exit_scan",
        "sell_execution",
        "buy_trigger_scan",
        "buy_execution",
    ):
        step = cycle_payload[step_name]
        _ok(f"{step_name}_outcome", str(step["outcome"]))
        if step["reason"]:
            _warn(f"{step_name}_reason", str(step["reason"]))
        if isinstance(step["summary"], dict):
            _ok(f"{step_name}_summary", json.dumps(step["summary"], ensure_ascii=False))


def _cycle_failed(cycle_payload: dict[str, Any]) -> bool:
    return any(
        cycle_payload[step_name]["outcome"] == "FAILED"
        for step_name in (
            "maintenance",
            "intraday_bar_refresh",
            "sell_exit_scan",
            "sell_macd_scan",
            "timing2_lot_exit_scan",
            "sell_execution",
            "buy_trigger_scan",
            "buy_execution",
        )
    )


def main() -> int:
    args = _parse_args()
    run_timing1, run_timing2 = _resolve_scan_selection(args)

    try:
        _validate_positive_int("interval_seconds", args.interval_seconds)
        _validate_positive_int("max_cycles", args.max_cycles, allow_zero=True)
        _validate_positive_int(
            "max_consecutive_failures",
            args.max_consecutive_failures,
        )
        _validate_positive_int("buy_signal_limit", args.buy_signal_limit)
        _validate_positive_int("sell_signal_limit", args.sell_signal_limit)
        _validate_positive_int("timeout_seconds", args.timeout_seconds)
        _validate_positive_int("per_order_budget", args.per_order_budget)
        _validate_positive_int("max_holdings", args.max_holdings)
        _validate_positive_int(
            "sell_macd_history_limit",
            args.sell_macd_history_limit,
        )
        if args.lock_lease_seconds != 0:
            _validate_positive_int("lock_lease_seconds", args.lock_lease_seconds)

        settings = load_settings()
        setup_logging(settings)
        maintenance_settings = StaleBuyOrderCancelSettings(
            timeout_seconds=args.timeout_seconds
        ).validated()
        buy_execution_settings = BuySignalExecutionSettings(
            per_order_budget=args.per_order_budget,
            max_holdings=args.max_holdings,
            max_daily_order_count=args.max_daily_order_count,
            max_daily_loss=args.max_daily_loss,
            start_time=args.buy_start_time,
            cutoff_time=args.buy_cutoff_time,
        ).validated()
        sell_execution_settings = SellSignalExecutionSettings(
            start_time=args.sell_start_time,
            cutoff_time=args.sell_cutoff_time,
        ).validated()
        sell_exit_settings = SellExitSettings(
            stop_loss_ratio=args.sell_stop_loss_percent / 100.0,
            take_profit_ratio=args.sell_take_profit_percent / 100.0,
        ).validated()
        sell_macd_settings = SellMacdExitSettings(
            fast_window=args.sell_macd_fast_window,
            slow_window=args.sell_macd_slow_window,
            signal_window=args.sell_macd_signal_window,
            consecutive_decline_bars=args.sell_macd_consecutive_decrease_bars
            if hasattr(args, "sell_macd_consecutive_decrease_bars")
            else args.sell_macd_consecutive_decline_bars,
        ).validated()
        timing2_lot_exit_settings = Timing2LotExitSettings(
            stop_loss_ratio=args.timing2_lot_stop_loss_percent / 100.0,
            take_profit_ratio=args.timing2_lot_take_profit_percent / 100.0,
            partial_take_profit_ratio=(
                args.timing2_lot_partial_take_profit_percent / 100.0
            ),
            sell_cost_rate=args.timing2_lot_sell_cost_rate,
        ).validated()
        timing1_settings = Timing1IntradayTriggerSettings(
            start_time=args.timing1_start_time,
            cutoff_time=args.timing1_cutoff_time,
        ).validated()
        timing2_settings = Timing2IntradayTriggerSettings(
            tolerance_rate=args.timing2_tolerance_rate,
            start_time=args.timing2_start_time,
            cutoff_time=args.timing2_cutoff_time,
        ).validated()
        lock_name = _resolve_lock_name(args)
        lock_lease_seconds = _resolve_lock_lease_seconds(args)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path
    earliest_start, latest_cutoff = _resolve_window(
        run_timing1=run_timing1,
        run_timing2=run_timing2,
        buy_execution_settings=buy_execution_settings,
        sell_execution_settings=sell_execution_settings,
        timing1_settings=timing1_settings,
        timing2_settings=timing2_settings,
    )

    _section("Run Intraday Trading Polling")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("execute", str(args.execute))
    _ok("run_timing1", str(run_timing1))
    _ok("run_timing2", str(run_timing2))
    _ok("interval_seconds", str(args.interval_seconds))
    _ok("max_cycles", str(args.max_cycles))
    _ok("max_consecutive_failures", str(args.max_consecutive_failures))
    _ok("per_order_budget", str(args.per_order_budget))
    _ok("max_holdings", str(args.max_holdings))
    _ok(
        "max_daily_order_count",
        "-" if buy_execution_settings.max_daily_order_count is None else str(buy_execution_settings.max_daily_order_count),
    )
    _ok(
        "max_daily_loss",
        "-" if buy_execution_settings.max_daily_loss is None else str(buy_execution_settings.max_daily_loss),
    )
    _ok("earliest_start", earliest_start.strftime("%H:%M:%S"))
    _ok("latest_cutoff", latest_cutoff.strftime("%H:%M:%S"))
    _ok("db_path", str(db_path))
    _ok("lock_name", lock_name)
    _ok("lock_lease_seconds", str(lock_lease_seconds))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        return 5

    started_at = datetime.now(KST).isoformat()
    cycles: list[dict[str, Any]] = []
    stop_reason = "UNKNOWN"
    consecutive_failures = 0
    lock_acquired = False
    lock_owner_id: str | None = None
    lock_service: RuntimeLockService | None = None
    released_lock = False

    try:
        lock_service = RuntimeLockService(
            conn=conn,
            lock_repo=RuntimeLockRepository(conn),
        )
        lock_owner_id = lock_service.owner_id
        _ok("lock_owner_id", lock_owner_id)

        try:
            lease = lock_service.acquire(
                lock_name=lock_name,
                lease_seconds=lock_lease_seconds,
            )
            lock_acquired = True
            _ok("lock_acquired_at", lease.acquired_at)
            _ok("lock_expires_at", lease.expires_at)
        except RuntimeLockBusyError as exc:
            stop_reason = "LOCK_BUSY"
            _warn("lock_busy", str(exc))
            if output_path is not None:
                _save_json(
                    output_path,
                    {
                        "trade_date": args.trade_date,
                        "started_at": started_at,
                        "finished_at": datetime.now(KST).isoformat(),
                        "stop_reason": stop_reason,
                        "execute_mode": args.execute,
                        "lock_name": lock_name,
                        "lock_owner_id": lock_owner_id,
                        "lock_lease_seconds": lock_lease_seconds,
                        "lock_acquired": False,
                        "cycles": [],
                    },
                )
            return 4

        with KisBroker(settings) as broker:
            signal_repo = SignalRepository(conn)
            order_repo = OrderRepository(conn)
            position_repo = PositionRepository(conn)
            execution_repo = ExecutionRepository(conn)
            intraday_bar_repo = IntradayBar15mRepository(conn)
            intraday_bar_30s_repo = IntradayBar30sRepository(conn)
            entry_lot_repo = EntryLotRepository(conn)
            daily_stats_repo = DailyStatsRepository(conn)
            trading_control_repo = TradingControlRepository(conn)
            risk_guard_service = TradingRiskGuardService(
                order_repo=order_repo,
                trading_control_repo=trading_control_repo,
                daily_stats_repo=daily_stats_repo,
            )
            order_service = OrderService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
                position_repo=position_repo,
            )
            sync_service = UnresolvedOrderSyncService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
            )

            cycle_service = IntradayTradingCycleService(
                order_maintenance_service=OrderMaintenanceService(
                    sync_service=sync_service,
                    execution_recovery_service=ExecutionRecoveryFinalizeService(
                        conn=conn,
                        order_repo=order_repo,
                        execution_repo=execution_repo,
                        sync_service=sync_service,
                    ),
                    stale_buy_cancel_service=StaleBuyOrderCancelService(
                        order_repo=order_repo,
                        order_service=order_service,
                    ),
                    stale_sell_cancel_service=StaleSellOrderCancelService(
                        order_repo=order_repo,
                        order_service=order_service,
                    ),
                ),
                intraday_bar_refresh_service=IntradayBar15mRefreshService(
                    broker=broker,
                    conn=conn,
                    position_repo=position_repo,
                    intraday_bar_repo=intraday_bar_repo,
                ),
                sell_exit_scan_service=SellExitScanService(
                    broker=broker,
                    conn=conn,
                    position_repo=position_repo,
                    signal_repo=signal_repo,
                ),
                sell_macd_scan_service=SellMacdExitScanService(
                    conn=conn,
                    position_repo=position_repo,
                    intraday_bar_repo=intraday_bar_repo,
                    signal_repo=signal_repo,
                ),
                timing2_lot_exit_scan_service=Timing2LotExitScanService(
                    broker=broker,
                    conn=conn,
                    entry_lot_repo=entry_lot_repo,
                    signal_repo=signal_repo,
                    intraday_bar_repo=intraday_bar_30s_repo,
                ),
                sell_signal_execution_service=SellSignalExecutionService(
                    broker=broker,
                    conn=conn,
                    signal_repo=signal_repo,
                    order_repo=order_repo,
                    position_repo=position_repo,
                    order_service=order_service,
                    risk_guard_service=risk_guard_service,
                    entry_lot_repo=entry_lot_repo,
                ),
                buy_trigger_scan_service=IntradayTriggerCombinedScanService(
                    broker=broker,
                    conn=conn,
                    signal_repo=signal_repo,
                ),
                buy_signal_execution_service=BuySignalExecutionService(
                    broker=broker,
                    conn=conn,
                    signal_repo=signal_repo,
                    order_repo=order_repo,
                    position_repo=position_repo,
                    order_service=order_service,
                    risk_guard_service=risk_guard_service,
                ),
            )

            cycle_no = 0
            while True:
                now = datetime.now(KST)
                lock_service.heartbeat(
                    lock_name=lock_name,
                    lease_seconds=lock_lease_seconds,
                )

                if cycle_no == 0 and now.time() < earliest_start:
                    wait_seconds = min(
                        args.interval_seconds,
                        _seconds_until(now=now, target_time=earliest_start),
                    )
                    _warn(
                        "waiting_for_start",
                        f"sleeping {wait_seconds}s until trading window starts",
                    )
                    time.sleep(wait_seconds)
                    continue

                cycle_no += 1
                cycle_result = cycle_service.run_cycle(
                    trade_date=args.trade_date,
                    execute_actions=args.execute,
                    maintenance_settings=maintenance_settings,
                    sell_exit_settings=sell_exit_settings,
                    sell_macd_settings=sell_macd_settings,
                    sell_macd_history_limit=args.sell_macd_history_limit,
                    timing2_lot_exit_settings=timing2_lot_exit_settings,
                    sell_execution_settings=sell_execution_settings,
                    sell_signal_limit=args.sell_signal_limit,
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    timing1_settings=timing1_settings,
                    timing1_daily_count=args.timing1_daily_count,
                    timing2_settings=timing2_settings,
                    buy_execution_settings=buy_execution_settings,
                    buy_signal_limit=args.buy_signal_limit,
                    record_scan_signals=args.execute,
                )
                cycle_payload = _build_cycle_payload(cycle_no, cycle_result)
                cycles.append(cycle_payload)
                _print_cycle_summary(cycle_payload)
                lock_service.heartbeat(
                    lock_name=lock_name,
                    lease_seconds=lock_lease_seconds,
                )

                if _cycle_failed(cycle_payload):
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= args.max_consecutive_failures:
                    stop_reason = "MAX_CONSECUTIVE_FAILURES"
                    break

                if args.max_cycles > 0 and cycle_no >= args.max_cycles:
                    stop_reason = "MAX_CYCLES_REACHED"
                    break

                if datetime.now(KST).time() >= latest_cutoff:
                    stop_reason = "CUTOFF_REACHED"
                    break

                time.sleep(args.interval_seconds)

    except KeyboardInterrupt:
        stop_reason = "INTERRUPTED"
    except Exception as exc:
        _fail("polling", f"{type(exc).__name__}: {exc}")
        stop_reason = f"FAILED:{type(exc).__name__}"
        if output_path is not None:
            _save_json(
                output_path,
                {
                    "trade_date": args.trade_date,
                    "started_at": started_at,
                    "finished_at": datetime.now(KST).isoformat(),
                    "stop_reason": stop_reason,
                    "execute_mode": args.execute,
                    "lock_name": lock_name,
                    "lock_owner_id": lock_owner_id,
                    "lock_lease_seconds": lock_lease_seconds,
                    "lock_acquired": lock_acquired,
                    "cycles": cycles,
                },
            )
        return 5
    finally:
        if lock_acquired and lock_service is not None:
            try:
                released_lock = lock_service.release(lock_name=lock_name)
            except Exception:
                released_lock = False
        conn.close()

    payload = {
        "trade_date": args.trade_date,
        "started_at": started_at,
        "finished_at": datetime.now(KST).isoformat(),
        "stop_reason": stop_reason,
        "execute_mode": args.execute,
        "interval_seconds": args.interval_seconds,
        "max_cycles": args.max_cycles,
        "max_consecutive_failures": args.max_consecutive_failures,
        "lock_name": lock_name,
        "lock_owner_id": lock_owner_id,
        "lock_lease_seconds": lock_lease_seconds,
        "lock_acquired": lock_acquired,
        "lock_released": released_lock,
        "cycle_count": len(cycles),
        "cycles": cycles,
    }
    if output_path is not None:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    if stop_reason == "INTERRUPTED":
        _warn("polling", "Interrupted by user.")
        return 130
    if stop_reason.startswith("FAILED") or stop_reason == "MAX_CONSECUTIVE_FAILURES":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
