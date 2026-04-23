"""
Run one conservative intraday trading cycle.

Safety:
- preview is the default
- execute mode uses a persisted runtime lock
- fresh scan signals are recorded only in execute mode
- if a critical earlier step fails, later order execution is skipped
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
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
    Timing2PriceSampleCaptureService,
    Timing2SetupSignalReadiness,
    Timing2ThirtySecondBarBuildService,
    Timing2ThirtySecondTriggerService,
    Timing2LotExitScanService,
    TradingRiskGuardService,
    UnresolvedOrderSyncService,
    inspect_timing2_setup_signal_readiness,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    CurrentPriceSampleRepository,
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
    BUY_STRATEGY_CHOICES,
    DEFAULT_TIMING2_SELL_COST_RATE,
    SellExitSettings,
    SellMacdExitSettings,
    Timing1IntradayTriggerSettings,
    Timing2LotExitSettings,
    Timing2ThirtySecondTriggerSettings,
    Timing2IntradayTriggerSettings,
    resolve_buy_strategy_selection,
    selection_to_buy_strategy,
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
        description="Run one conservative intraday trading cycle."
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
        "--timing2-30s-min-samples-per-bar",
        type=int,
        default=2,
        help="Minimum samples required to build one Timing2 30s bar. Default: 2",
    )
    parser.add_argument(
        "--timing2-max-sample-symbols-per-cycle",
        type=int,
        default=30,
        help="Max Timing2 symbols to sample per cycle. Default: 30",
    )
    parser.add_argument(
        "--timing2-30s-morning-start-time",
        default="09:00:00",
        help="Timing2 30s morning pattern start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--timing2-30s-morning-end-time",
        default="10:00:00",
        help="Timing2 30s morning pattern end time HH:MM:SS. Default: 10:00:00",
    )
    parser.add_argument(
        "--timing2-30s-range-breakout-start-time",
        default="10:00:00",
        help="Timing2 30s range breakout start time HH:MM:SS. Default: 10:00:00",
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
        "--buy-strategy",
        choices=BUY_STRATEGY_CHOICES,
        default=None,
        help=(
            "Buy strategy selection for future UI controls. "
            "Choices: timing1, timing2, both. Default: legacy scan flags, or both."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually record scan signals, place orders, and run maintenance changes.",
    )
    parser.add_argument(
        "--lock-name",
        default=None,
        help="Optional runtime lock name for execute mode.",
    )
    parser.add_argument(
        "--lock-lease-seconds",
        type=int,
        default=180,
        help="Runtime lock lease seconds in execute mode. Default: 180",
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


def _validate_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    return value


def _resolve_scan_selection(args: argparse.Namespace) -> tuple[bool, bool]:
    return resolve_buy_strategy_selection(
        buy_strategy=args.buy_strategy,
        scan_timing1=args.scan_timing1,
        scan_timing2=args.scan_timing2,
    )


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"intraday_trading_cycle:{args.trade_date}"


def _serialize_maintenance_summary(result) -> dict[str, Any]:
    return {
        "sync_candidate_count": result.sync_result.candidate_count,
        "sync_synced_count": result.sync_result.synced_count,
        "recovered_count": result.execution_recovery_result.recovered_count,
        "manual_recovery_required_count": len(
            result.manual_recovery_required_client_order_ids
        ),
        "buy_cancelled_count": result.stale_buy_cancel_result.cancelled_count,
        "sell_cancelled_count": result.stale_sell_cancel_result.cancelled_count,
    }


def _serialize_sell_exit_scan_summary(result) -> dict[str, Any]:
    return {
        "position_count": result.position_count,
        "matched_count": result.matched_count,
        "stop_loss_count": result.stop_loss_count,
        "take_profit_count": result.take_profit_count,
        "recorded_count": result.recorded_count,
        "skipped_existing_count": result.skipped_existing_count,
        "scanned_at": result.scanned_at,
    }


def _serialize_intraday_bar_refresh_summary(result) -> dict[str, Any]:
    return {
        "position_count": result.position_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "refreshed_symbol_count": result.refreshed_symbol_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "refreshed_at": result.refreshed_at,
    }


def _serialize_timing2_price_sample_capture_summary(result) -> dict[str, Any]:
    return {
        "setup_signal_count": result.setup_signal_count,
        "candidate_count": result.candidate_count,
        "skipped_by_limit_count": result.skipped_by_limit_count,
        "preview_ready_count": result.preview_ready_count,
        "captured_count": result.captured_count,
        "failed_count": result.failed_count,
        "captured_at": result.captured_at,
    }


def _serialize_timing2_30s_bar_build_summary(result) -> dict[str, Any]:
    return {
        "setup_signal_count": result.setup_signal_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "built_symbol_count": result.built_symbol_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "built_at": result.built_at,
    }


def _serialize_sell_macd_scan_summary(result) -> dict[str, Any]:
    return {
        "position_count": result.position_count,
        "matched_count": result.matched_count,
        "recorded_count": result.recorded_count,
        "skipped_existing_count": result.skipped_existing_count,
        "scanned_at": result.scanned_at,
    }


def _serialize_timing2_lot_exit_scan_summary(result) -> dict[str, Any]:
    return {
        "lot_count": result.lot_count,
        "matched_count": result.matched_count,
        "stop_loss_count": result.stop_loss_count,
        "ma_break_count": result.ma_break_count,
        "partial_take_profit_count": result.partial_take_profit_count,
        "recorded_count": result.recorded_count,
        "skipped_existing_count": result.skipped_existing_count,
        "scanned_at": result.scanned_at,
    }


def _serialize_timing2_30s_trigger_scan_summary(result) -> dict[str, Any]:
    return {
        "setup_signal_count": result.setup_signal_count,
        "candidate_count": result.candidate_count,
        "evaluated_count": result.evaluated_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "transition_count": result.transition_count,
        "buy_triggered_count": result.buy_triggered_count,
        "recorded_count": result.recorded_count,
        "scanned_at": result.scanned_at,
    }


def _serialize_sell_execution_summary(result) -> dict[str, Any]:
    return {
        "pending_signal_count": result.pending_signal_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "blocked_count": result.blocked_count,
        "submitted_count": result.submitted_count,
        "unknown_count": result.unknown_count,
        "rejected_count": result.rejected_count,
        "failed_count": result.failed_count,
        "acted_count": result.acted_count,
        "audit_record_count": result.audit_record_count,
        "executed_at": result.executed_at,
    }


def _serialize_buy_trigger_summary(result) -> dict[str, Any]:
    timing1 = result.timing1.result
    timing2 = result.timing2.result
    return {
        "timing1": {
            "outcome": result.timing1.outcome,
            "reason": result.timing1.reason,
            "candidate_count": None if timing1 is None else timing1.candidate_count,
            "transition_count": None if timing1 is None else timing1.transition_count,
            "triggered_count": None if timing1 is None else timing1.triggered_count,
            "expired_count": None if timing1 is None else timing1.expired_count,
            "recorded_count": None if timing1 is None else timing1.recorded_count,
            "scanned_at": None if timing1 is None else timing1.scanned_at,
        },
        "timing2": {
            "outcome": result.timing2.outcome,
            "reason": result.timing2.reason,
            "candidate_count": None if timing2 is None else timing2.candidate_count,
            "transition_count": None if timing2 is None else timing2.transition_count,
            "triggered_count": None if timing2 is None else timing2.triggered_count,
            "expired_count": None if timing2 is None else timing2.expired_count,
            "recorded_count": None if timing2 is None else timing2.recorded_count,
            "scanned_at": None if timing2 is None else timing2.scanned_at,
        },
    }


def _serialize_buy_execution_summary(result) -> dict[str, Any]:
    return {
        "pending_signal_count": result.pending_signal_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "blocked_count": result.blocked_count,
        "submitted_count": result.submitted_count,
        "unknown_count": result.unknown_count,
        "rejected_count": result.rejected_count,
        "failed_count": result.failed_count,
        "acted_count": result.acted_count,
        "audit_record_count": result.audit_record_count,
        "executed_at": result.executed_at,
    }


def _serialize_step(status, summary_builder) -> dict[str, Any]:
    return {
        "outcome": status.outcome,
        "reason": status.reason,
        "summary": None if status.result is None else summary_builder(status.result),
    }


def _readiness_payload(
    readiness: Timing2SetupSignalReadiness | None,
) -> dict[str, Any] | None:
    if readiness is None:
        return None
    return readiness.to_payload()


def _print_timing2_setup_readiness(
    readiness: Timing2SetupSignalReadiness,
) -> None:
    if not readiness.required:
        return
    _ok("timing2_setup_signal_count", str(readiness.setup_signal_count))
    _ok("timing2_setup_ready", str(readiness.ready))
    if readiness.reason:
        _warn("timing2_setup_readiness", readiness.reason)


def _build_payload(
    *,
    trade_date: str,
    execute_mode: bool,
    run_timing1: bool,
    run_timing2: bool,
    timeout_seconds: int,
    buy_execution_settings: BuySignalExecutionSettings,
    sell_execution_settings: SellSignalExecutionSettings,
    sell_exit_settings: SellExitSettings,
    sell_macd_settings: SellMacdExitSettings,
    sell_macd_history_limit: int,
    timing2_lot_exit_settings: Timing2LotExitSettings,
    timing1_settings: Timing1IntradayTriggerSettings,
    timing1_daily_count: int,
    timing2_settings: Timing2IntradayTriggerSettings,
    timing2_30s_trigger_settings: Timing2ThirtySecondTriggerSettings,
    timing2_30s_min_samples_per_bar: int,
    timing2_max_sample_symbols_per_cycle: int,
    buy_signal_limit: int,
    sell_signal_limit: int,
    result=None,
    timing2_setup_readiness: Timing2SetupSignalReadiness | None = None,
    lock_name: str | None = None,
    lock_owner_id: str | None = None,
    lock_acquired: bool = False,
    lock_released: bool = False,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "trade_date": trade_date,
        "execute_mode": execute_mode,
        "settings": {
            "timeout_seconds": timeout_seconds,
            "buy_strategy": selection_to_buy_strategy(
                run_timing1=run_timing1,
                run_timing2=run_timing2,
            ),
            "run_timing1": run_timing1,
            "run_timing2": run_timing2,
            "buy_signal_limit": buy_signal_limit,
            "sell_signal_limit": sell_signal_limit,
            "buy_execution": {
                "per_order_budget": buy_execution_settings.per_order_budget,
                "max_holdings": buy_execution_settings.max_holdings,
                "max_daily_order_count": buy_execution_settings.max_daily_order_count,
                "max_daily_loss": buy_execution_settings.max_daily_loss,
                "start_time": buy_execution_settings.start_time,
                "cutoff_time": buy_execution_settings.cutoff_time,
            },
            "sell_execution": {
                "start_time": sell_execution_settings.start_time,
                "cutoff_time": sell_execution_settings.cutoff_time,
            },
            "sell_exit": {
                "stop_loss_ratio": sell_exit_settings.stop_loss_ratio,
                "take_profit_ratio": sell_exit_settings.take_profit_ratio,
            },
            "sell_macd": {
                "fast_window": sell_macd_settings.fast_window,
                "slow_window": sell_macd_settings.slow_window,
                "signal_window": sell_macd_settings.signal_window,
                "consecutive_decline_bars": (
                    sell_macd_settings.consecutive_decline_bars
                ),
                "history_limit": sell_macd_history_limit,
            },
            "timing2_lot_exit": {
                "stop_loss_ratio": timing2_lot_exit_settings.stop_loss_ratio,
                "take_profit_ratio": timing2_lot_exit_settings.take_profit_ratio,
                "partial_take_profit_ratio": (
                    timing2_lot_exit_settings.partial_take_profit_ratio
                ),
                "sell_cost_rate": timing2_lot_exit_settings.sell_cost_rate,
            },
            "timing1": {
                "start_time": timing1_settings.start_time,
                "cutoff_time": timing1_settings.cutoff_time,
                "daily_count": timing1_daily_count,
            },
            "timing2": {
                "tolerance_rate": timing2_settings.tolerance_rate,
                "start_time": timing2_settings.start_time,
                "cutoff_time": timing2_settings.cutoff_time,
            },
            "timing2_30s": {
                "min_samples_per_bar": timing2_30s_min_samples_per_bar,
                "max_sample_symbols_per_cycle": (
                    timing2_max_sample_symbols_per_cycle
                ),
                "morning_start_time": (
                    timing2_30s_trigger_settings.morning_start_time
                ),
                "morning_end_time": timing2_30s_trigger_settings.morning_end_time,
                "range_breakout_start_time": (
                    timing2_30s_trigger_settings.range_breakout_start_time
                ),
            },
        },
        "lock_name": lock_name,
        "lock_owner_id": lock_owner_id,
        "lock_acquired": lock_acquired,
        "lock_released": lock_released,
        "timing2_setup_readiness": _readiness_payload(timing2_setup_readiness),
        "error_type": error_type,
        "error_message": error_message,
        "result": None,
    }
    if result is None:
        return payload

    payload["result"] = {
        "trade_date": result.trade_date,
        "execute_actions": result.execute_actions,
        "record_scan_signals": result.record_scan_signals,
        "maintenance": _serialize_step(
            result.maintenance,
            _serialize_maintenance_summary,
        ),
        "intraday_bar_refresh": _serialize_step(
            result.intraday_bar_refresh,
            _serialize_intraday_bar_refresh_summary,
        ),
        "timing2_price_sample_capture": _serialize_step(
            result.timing2_price_sample_capture,
            _serialize_timing2_price_sample_capture_summary,
        ),
        "timing2_30s_bar_build": _serialize_step(
            result.timing2_30s_bar_build,
            _serialize_timing2_30s_bar_build_summary,
        ),
        "sell_exit_scan": _serialize_step(
            result.sell_exit_scan,
            _serialize_sell_exit_scan_summary,
        ),
        "sell_macd_scan": _serialize_step(
            result.sell_macd_scan,
            _serialize_sell_macd_scan_summary,
        ),
        "timing2_lot_exit_scan": _serialize_step(
            result.timing2_lot_exit_scan,
            _serialize_timing2_lot_exit_scan_summary,
        ),
        "sell_execution": _serialize_step(
            result.sell_execution,
            _serialize_sell_execution_summary,
        ),
        "timing2_30s_trigger_scan": _serialize_step(
            result.timing2_30s_trigger_scan,
            _serialize_timing2_30s_trigger_scan_summary,
        ),
        "buy_trigger_scan": _serialize_step(
            result.buy_trigger_scan,
            _serialize_buy_trigger_summary,
        ),
        "buy_execution": _serialize_step(
            result.buy_execution,
            _serialize_buy_execution_summary,
        ),
    }
    return payload


def _print_step(title: str, status, summary_builder) -> None:
    _section(title)
    _ok("outcome", status.outcome)
    if status.reason:
        _warn("reason", status.reason)
    if status.result is None:
        return
    summary = summary_builder(status.result)
    for key, value in summary.items():
        if isinstance(value, dict):
            _ok(key, json.dumps(value, ensure_ascii=False))
        else:
            _ok(key, str(value))


def _has_failed_step(result) -> bool:
    return any(
        step.outcome == "FAILED"
        for step in (
            result.maintenance,
            result.intraday_bar_refresh,
            result.timing2_price_sample_capture,
            result.timing2_30s_bar_build,
            result.sell_exit_scan,
            result.sell_macd_scan,
            result.timing2_lot_exit_scan,
            result.sell_execution,
            result.timing2_30s_trigger_scan,
            result.buy_trigger_scan,
            result.buy_execution,
        )
    )


def main() -> int:
    args = _parse_args()

    try:
        run_timing1, run_timing2 = _resolve_scan_selection(args)
        _validate_positive_int("buy_signal_limit", args.buy_signal_limit)
        _validate_positive_int("sell_signal_limit", args.sell_signal_limit)
        _validate_positive_int("timeout_seconds", args.timeout_seconds)
        _validate_positive_int("per_order_budget", args.per_order_budget)
        _validate_positive_int("max_holdings", args.max_holdings)
        _validate_positive_int(
            "sell_macd_history_limit",
            args.sell_macd_history_limit,
        )
        _validate_positive_int(
            "timing2_30s_min_samples_per_bar",
            args.timing2_30s_min_samples_per_bar,
        )
        _validate_positive_int(
            "timing2_max_sample_symbols_per_cycle",
            args.timing2_max_sample_symbols_per_cycle,
        )
        if args.execute:
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
            consecutive_decline_bars=args.sell_macd_consecutive_decline_bars,
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
        timing2_30s_trigger_settings = Timing2ThirtySecondTriggerSettings(
            morning_start_time=args.timing2_30s_morning_start_time,
            morning_end_time=args.timing2_30s_morning_end_time,
            range_breakout_start_time=args.timing2_30s_range_breakout_start_time,
        ).validated()
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None
    lock_name = _resolve_lock_name(args) if args.execute else None
    lock_service: RuntimeLockService | None = None
    lock_owner_id: str | None = None
    lock_acquired = False
    lock_released = False
    timing2_setup_readiness: Timing2SetupSignalReadiness | None = None

    _section("Run Intraday Trading Cycle")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("execute", str(args.execute))
    _ok(
        "buy_strategy",
        selection_to_buy_strategy(
            run_timing1=run_timing1,
            run_timing2=run_timing2,
        ),
    )
    _ok("run_timing1", str(run_timing1))
    _ok("run_timing2", str(run_timing2))
    _ok(
        "timing2_30s_min_samples_per_bar",
        str(args.timing2_30s_min_samples_per_bar),
    )
    _ok(
        "timing2_max_sample_symbols_per_cycle",
        str(args.timing2_max_sample_symbols_per_cycle),
    )
    _ok("timeout_seconds", str(args.timeout_seconds))
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
    _ok("db_path", str(db_path))
    if args.execute:
        _ok("lock_name", str(lock_name))
        _ok("lock_lease_seconds", str(args.lock_lease_seconds))
    else:
        _warn(
            "preview_note",
            "Fresh scan matches and 15-minute bar cache are not persisted "
            "in preview mode, so execution previews use only already-"
            "recorded pending signals and stored bars.",
        )

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
        timing2_setup_readiness = inspect_timing2_setup_signal_readiness(
            signal_repo=SignalRepository(conn),
            trade_date=args.trade_date,
            run_timing2=run_timing2,
        )
        _print_timing2_setup_readiness(timing2_setup_readiness)
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    timeout_seconds=args.timeout_seconds,
                    buy_execution_settings=buy_execution_settings,
                    sell_execution_settings=sell_execution_settings,
                    sell_exit_settings=sell_exit_settings,
                    sell_macd_settings=sell_macd_settings,
                    sell_macd_history_limit=args.sell_macd_history_limit,
                    timing2_lot_exit_settings=timing2_lot_exit_settings,
                    timing1_settings=timing1_settings,
                    timing1_daily_count=args.timing1_daily_count,
                    timing2_settings=timing2_settings,
                    timing2_30s_trigger_settings=timing2_30s_trigger_settings,
                    timing2_30s_min_samples_per_bar=(
                        args.timing2_30s_min_samples_per_bar
                    ),
                    timing2_max_sample_symbols_per_cycle=(
                        args.timing2_max_sample_symbols_per_cycle
                    ),
                    buy_signal_limit=args.buy_signal_limit,
                    sell_signal_limit=args.sell_signal_limit,
                    lock_name=lock_name,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        return 5

    try:
        if args.execute:
            lock_service = RuntimeLockService(
                conn=conn,
                lock_repo=RuntimeLockRepository(conn),
            )
            lock_owner_id = lock_service.owner_id
            try:
                lock_service.acquire(
                    lock_name=lock_name,
                    lease_seconds=args.lock_lease_seconds,
                )
                lock_acquired = True
            except RuntimeLockBusyError as exc:
                _fail("runtime lock", str(exc))
                if output_path is not None:
                    _save_json(
                        output_path,
                        _build_payload(
                            trade_date=args.trade_date,
                            execute_mode=True,
                            run_timing1=run_timing1,
                            run_timing2=run_timing2,
                            timeout_seconds=args.timeout_seconds,
                            buy_execution_settings=buy_execution_settings,
                            sell_execution_settings=sell_execution_settings,
                            sell_exit_settings=sell_exit_settings,
                            sell_macd_settings=sell_macd_settings,
                            sell_macd_history_limit=args.sell_macd_history_limit,
                            timing2_lot_exit_settings=timing2_lot_exit_settings,
                            timing1_settings=timing1_settings,
                            timing1_daily_count=args.timing1_daily_count,
                            timing2_settings=timing2_settings,
                            timing2_30s_trigger_settings=(
                                timing2_30s_trigger_settings
                            ),
                            timing2_30s_min_samples_per_bar=(
                                args.timing2_30s_min_samples_per_bar
                            ),
                            timing2_max_sample_symbols_per_cycle=(
                                args.timing2_max_sample_symbols_per_cycle
                            ),
                            buy_signal_limit=args.buy_signal_limit,
                            sell_signal_limit=args.sell_signal_limit,
                            timing2_setup_readiness=timing2_setup_readiness,
                            lock_name=lock_name,
                            lock_owner_id=lock_owner_id,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        ),
                    )
                return 4

        with KisBroker(settings) as broker:
            signal_repo = SignalRepository(conn)
            order_repo = OrderRepository(conn)
            position_repo = PositionRepository(conn)
            execution_repo = ExecutionRepository(conn)
            current_price_sample_repo = CurrentPriceSampleRepository(conn)
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
                timing2_price_sample_capture_service=(
                    Timing2PriceSampleCaptureService(
                        broker=broker,
                        conn=conn,
                        signal_repo=signal_repo,
                        sample_repo=current_price_sample_repo,
                    )
                ),
                timing2_30s_bar_build_service=(
                    Timing2ThirtySecondBarBuildService(
                        conn=conn,
                        signal_repo=signal_repo,
                        sample_repo=current_price_sample_repo,
                        intraday_bar_repo=intraday_bar_30s_repo,
                    )
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
                timing2_30s_trigger_service=Timing2ThirtySecondTriggerService(
                    conn=conn,
                    signal_repo=signal_repo,
                    intraday_bar_repo=intraday_bar_30s_repo,
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
            result = cycle_service.run_cycle(
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
                timing2_30s_trigger_settings=timing2_30s_trigger_settings,
                timing2_30s_min_samples_per_bar=(
                    args.timing2_30s_min_samples_per_bar
                ),
                timing2_max_sample_symbols_per_cycle=(
                    args.timing2_max_sample_symbols_per_cycle
                ),
                buy_execution_settings=buy_execution_settings,
                buy_signal_limit=args.buy_signal_limit,
                record_scan_signals=args.execute,
            )

        _section("Cycle Result")
        _ok("record_scan_signals", str(result.record_scan_signals))
        _print_step("Order Maintenance", result.maintenance, _serialize_maintenance_summary)
        _print_step(
            "Intraday Bar Refresh",
            result.intraday_bar_refresh,
            _serialize_intraday_bar_refresh_summary,
        )
        _print_step(
            "Timing2 Price Sample Capture",
            result.timing2_price_sample_capture,
            _serialize_timing2_price_sample_capture_summary,
        )
        _print_step(
            "Timing2 30s Bar Build",
            result.timing2_30s_bar_build,
            _serialize_timing2_30s_bar_build_summary,
        )
        _print_step("Sell Exit Scan", result.sell_exit_scan, _serialize_sell_exit_scan_summary)
        _print_step("Sell MACD Scan", result.sell_macd_scan, _serialize_sell_macd_scan_summary)
        _print_step(
            "Timing2 Lot Exit Scan",
            result.timing2_lot_exit_scan,
            _serialize_timing2_lot_exit_scan_summary,
        )
        _print_step("Sell Execution", result.sell_execution, _serialize_sell_execution_summary)
        _print_step(
            "Timing2 30s Trigger Scan",
            result.timing2_30s_trigger_scan,
            _serialize_timing2_30s_trigger_scan_summary,
        )
        _print_step("Buy Trigger Scan", result.buy_trigger_scan, _serialize_buy_trigger_summary)
        _print_step("Buy Execution", result.buy_execution, _serialize_buy_execution_summary)

        if output_path is not None:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    timeout_seconds=args.timeout_seconds,
                    buy_execution_settings=buy_execution_settings,
                    sell_execution_settings=sell_execution_settings,
                    sell_exit_settings=sell_exit_settings,
                    sell_macd_settings=sell_macd_settings,
                    sell_macd_history_limit=args.sell_macd_history_limit,
                    timing2_lot_exit_settings=timing2_lot_exit_settings,
                    timing1_settings=timing1_settings,
                    timing1_daily_count=args.timing1_daily_count,
                    timing2_settings=timing2_settings,
                    timing2_30s_trigger_settings=timing2_30s_trigger_settings,
                    timing2_30s_min_samples_per_bar=(
                        args.timing2_30s_min_samples_per_bar
                    ),
                    timing2_max_sample_symbols_per_cycle=(
                        args.timing2_max_sample_symbols_per_cycle
                    ),
                    buy_signal_limit=args.buy_signal_limit,
                    sell_signal_limit=args.sell_signal_limit,
                    result=result,
                    timing2_setup_readiness=timing2_setup_readiness,
                    lock_name=lock_name,
                    lock_owner_id=lock_owner_id,
                    lock_acquired=lock_acquired,
                    lock_released=lock_released,
                ),
            )
            _ok("json_saved", str(output_path))

        if _has_failed_step(result):
            return 5
        return 0

    except Exception as exc:
        _fail("cycle", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    run_timing1=run_timing1,
                    run_timing2=run_timing2,
                    timeout_seconds=args.timeout_seconds,
                    buy_execution_settings=buy_execution_settings,
                    sell_execution_settings=sell_execution_settings,
                    sell_exit_settings=sell_exit_settings,
                    sell_macd_settings=sell_macd_settings,
                    sell_macd_history_limit=args.sell_macd_history_limit,
                    timing2_lot_exit_settings=timing2_lot_exit_settings,
                    timing1_settings=timing1_settings,
                    timing1_daily_count=args.timing1_daily_count,
                    timing2_settings=timing2_settings,
                    timing2_30s_trigger_settings=timing2_30s_trigger_settings,
                    timing2_30s_min_samples_per_bar=(
                        args.timing2_30s_min_samples_per_bar
                    ),
                    timing2_max_sample_symbols_per_cycle=(
                        args.timing2_max_sample_symbols_per_cycle
                    ),
                    buy_signal_limit=args.buy_signal_limit,
                    sell_signal_limit=args.sell_signal_limit,
                    timing2_setup_readiness=timing2_setup_readiness,
                    lock_name=lock_name,
                    lock_owner_id=lock_owner_id,
                    lock_acquired=lock_acquired,
                    lock_released=lock_released,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        return 5

    finally:
        try:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
