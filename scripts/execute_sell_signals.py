"""
Preview or execute sell orders from sell trigger signals.

Safety:
- preview is the default
- real order placement requires --execute
- execute mode uses a persisted runtime lock to avoid duplicate runners
- source signals are consumed only for terminal or structurally blocked cases
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
    OrderService,
    RuntimeLockBusyError,
    RuntimeLockService,
    SellSignalExecutionService,
    SellSignalExecutionSettings,
    TradingRiskGuardService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    DailyStatsRepository,
    EntryLotRepository,
    OrderRepository,
    PositionRepository,
    RuntimeLockRepository,
    SignalRepository,
    TradingControlRepository,
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
        description="Preview or execute sell orders from trigger signals."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--start-time",
        default="09:00:00",
        help="Execution start time HH:MM:SS. Default: 09:00:00",
    )
    parser.add_argument(
        "--cutoff-time",
        default="15:20:00",
        help="Execution cutoff time HH:MM:SS. Default: 15:20:00",
    )
    parser.add_argument(
        "--signal-limit",
        type=int,
        default=200,
        help="How many unacted signals to inspect. Default: 200",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually place market sell orders.",
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
        "--limit",
        type=int,
        default=20,
        help="How many candidate rows to print. Default: 20",
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


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"sell_signal_execution:{args.trade_date}"


def _build_payload(
    *,
    trade_date: str,
    execute_mode: bool,
    settings: SellSignalExecutionSettings,
    signal_limit: int,
    result=None,
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
            "start_time": settings.start_time,
            "cutoff_time": settings.cutoff_time,
        },
        "signal_limit": signal_limit,
        "lock_name": lock_name,
        "lock_owner_id": lock_owner_id,
        "lock_acquired": lock_acquired,
        "lock_released": lock_released,
        "error_type": error_type,
        "error_message": error_message,
        "result": None,
    }
    if result is None:
        return payload

    payload["result"] = {
        "trade_date": result.trade_date,
        "executed_at": result.executed_at,
        "execute_orders": result.execute_orders,
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
        "acted_signal_ids": list(result.acted_signal_ids),
        "candidates": [
            {
                "signal_id": item.signal_id,
                "symbol": item.symbol,
                "name": item.name,
                "source_strategy_name": item.source_strategy_name,
                "lot_id": item.lot_id,
                "requested_sell_qty": item.requested_sell_qty,
                "order_qty": item.order_qty,
                "sell_cost_rate": item.sell_cost_rate,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "current_price": item.current_price,
                "position_qty": item.position_qty,
                "avg_price": item.avg_price,
                "client_order_id": item.client_order_id,
                "order_error_code": item.order_error_code,
                "order_error_message": item.order_error_message,
                "acted": item.acted,
            }
            for item in result.candidates
        ],
    }
    return payload


def main() -> int:
    args = _parse_args()

    try:
        _validate_positive_int("signal_limit", args.signal_limit)
        _validate_positive_int("limit", args.limit)
        if args.execute:
            _validate_positive_int("lock_lease_seconds", args.lock_lease_seconds)

        settings = load_settings()
        setup_logging(settings)
        execution_settings = SellSignalExecutionSettings(
            start_time=args.start_time,
            cutoff_time=args.cutoff_time,
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

    _section("Execute Sell Signals")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("execute", str(args.execute))
    _ok("signal_limit", str(args.signal_limit))
    _ok("start_time", execution_settings.start_time)
    _ok("cutoff_time", execution_settings.cutoff_time)
    _ok("db_path", str(db_path))
    if args.execute:
        _ok("lock_name", str(lock_name))
        _ok("lock_lease_seconds", str(args.lock_lease_seconds))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    settings=execution_settings,
                    signal_limit=args.signal_limit,
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
                            settings=execution_settings,
                            signal_limit=args.signal_limit,
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
            entry_lot_repo = EntryLotRepository(conn)
            daily_stats_repo = DailyStatsRepository(conn)
            trading_control_repo = TradingControlRepository(conn)
            order_service = OrderService(
                broker=broker,
                conn=conn,
                order_repo=order_repo,
                position_repo=position_repo,
            )
            service = SellSignalExecutionService(
                broker=broker,
                conn=conn,
                signal_repo=signal_repo,
                order_repo=order_repo,
                position_repo=position_repo,
                order_service=order_service,
                entry_lot_repo=entry_lot_repo,
                risk_guard_service=TradingRiskGuardService(
                    order_repo=order_repo,
                    trading_control_repo=trading_control_repo,
                    daily_stats_repo=daily_stats_repo,
                ),
            )
            result = service.execute_pending_signals(
                trade_date=args.trade_date,
                settings=execution_settings,
                signal_limit=args.signal_limit,
                execute_orders=args.execute,
            )

        _section("Execution Result")
        _ok("pending_signal_count", str(result.pending_signal_count))
        _ok("candidate_count", str(result.candidate_count))
        _ok("preview_ready_count", str(result.preview_ready_count))
        _ok("blocked_count", str(result.blocked_count))
        _ok("submitted_count", str(result.submitted_count))
        _ok("unknown_count", str(result.unknown_count))
        _ok("rejected_count", str(result.rejected_count))
        _ok("failed_count", str(result.failed_count))
        _ok("acted_count", str(result.acted_count))
        _ok("audit_record_count", str(result.audit_record_count))
        _ok("executed_at", result.executed_at)

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Candidates")
            for item in visible_candidates:
                print(
                    f"{item.symbol} strategy={item.source_strategy_name} "
                    f"outcome={item.outcome.value} "
                    f"qty={item.order_qty or item.position_qty} "
                    f"lot_id={item.lot_id or '-'} price={item.current_price} "
                    f"reason={item.reason_code or '-'} "
                    f"client_order_id={item.client_order_id or '-'}"
                )
        else:
            _warn("candidates", "No pending sell trigger signals found.")

        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    settings=execution_settings,
                    signal_limit=args.signal_limit,
                    result=result,
                    lock_name=lock_name,
                    lock_owner_id=lock_owner_id,
                    lock_acquired=lock_acquired,
                    lock_released=lock_released,
                ),
            )
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("execute", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    settings=execution_settings,
                    signal_limit=args.signal_limit,
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
