"""
Run safe order-maintenance steps in a conservative order.

Flow:
1. safely sync unresolved orders from the broker
2. finalize what can be safely recovered from existing local execution rows
3. collect orders that still require manual recovery
4. cancel stale buy orders, excluding the manual-recovery ones
5. cancel stale sell orders, excluding the manual-recovery ones

Safety:
- preview is the default
- execute mode only applies safe sync changes and stale cancellations
- execution recovery finalization assumes local execution rows came from the
  normal ledger path that already applied positions
- execute mode uses a persisted runtime lock
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
    ExecutionRecoveryFinalizeService,
    OrderMaintenanceService,
    OrderService,
    RuntimeLockBusyError,
    RuntimeLockService,
    StaleBuyOrderCancelService,
    StaleBuyOrderCancelSettings,
    StaleSellOrderCancelService,
    UnresolvedOrderSyncService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
    RuntimeLockRepository,
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
        description=(
            "Run safe unresolved-order sync, local execution recovery, "
            "and stale buy/sell cancel."
        )
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        required=True,
        help="Cancel unresolved buy/sell orders older than this many seconds.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually apply safe sync changes, safe local execution recovery, "
            "and stale cancellations."
        ),
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
        help="How many candidate rows to print per section. Default: 20",
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


def _resolve_lock_name(args: argparse.Namespace) -> str:
    if isinstance(args.lock_name, str) and args.lock_name.strip():
        return args.lock_name.strip()
    return f"order_maintenance:{args.trade_date}"


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _serialize_sync_result(result) -> dict[str, Any]:
    return {
        "trade_date": result.trade_date,
        "scanned_at": result.scanned_at,
        "execute_sync": result.execute_sync,
        "unresolved_order_count": result.unresolved_order_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "skipped_count": result.skipped_count,
        "synced_count": result.synced_count,
        "execution_recovery_required_count": (
            result.execution_recovery_required_count
        ),
        "acted_count": result.acted_count,
        "candidates": [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "status_before": item.status_before,
                "status_after": item.status_after,
                "kis_order_no": item.kis_order_no,
                "action": item.action.value,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "broker_status": item.broker_status,
                "broker_filled_qty": item.broker_filled_qty,
                "acted": item.acted,
            }
            for item in result.candidates
        ],
    }


def _serialize_cancel_result(result) -> dict[str, Any]:
    return {
        "trade_date": result.trade_date,
        "scanned_at": result.scanned_at,
        "execute_cancels": result.execute_cancels,
        "unresolved_order_count": result.unresolved_order_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "skipped_count": result.skipped_count,
        "cancelled_count": result.cancelled_count,
        "rejected_count": result.rejected_count,
        "unknown_count": result.unknown_count,
        "blocked_count": result.blocked_count,
        "acted_count": result.acted_count,
        "candidates": [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "status": item.status,
                "requested_at": item.requested_at,
                "age_seconds": item.age_seconds,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "acted": item.acted,
            }
            for item in result.candidates
        ],
    }


def _serialize_execution_recovery_result(result) -> dict[str, Any]:
    return {
        "trade_date": result.trade_date,
        "scanned_at": result.scanned_at,
        "execute_recovery": result.execute_recovery,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "recovered_count": result.recovered_count,
        "manual_recovery_required_count": (
            result.manual_recovery_required_count
        ),
        "skipped_count": result.skipped_count,
        "acted_count": result.acted_count,
        "candidates": [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "status_before": item.status_before,
                "status_after": item.status_after,
                "broker_status": item.broker_status,
                "broker_filled_qty": item.broker_filled_qty,
                "local_execution_count": item.local_execution_count,
                "local_filled_qty": item.local_filled_qty,
                "local_avg_fill_price": item.local_avg_fill_price,
                "action": item.action.value,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "acted": item.acted,
            }
            for item in result.candidates
        ],
    }


def _build_payload(
    *,
    trade_date: str,
    execute_mode: bool,
    settings: StaleBuyOrderCancelSettings,
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
            "timeout_seconds": settings.timeout_seconds,
        },
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
        "execute_changes": result.execute_changes,
        "manual_recovery_required_client_order_ids": list(
            result.manual_recovery_required_client_order_ids
        ),
        "sync_result": _serialize_sync_result(result.sync_result),
        "execution_recovery_result": _serialize_execution_recovery_result(
            result.execution_recovery_result
        ),
        "stale_buy_cancel_result": _serialize_cancel_result(
            result.stale_buy_cancel_result
        ),
        "stale_sell_cancel_result": _serialize_cancel_result(
            result.stale_sell_cancel_result
        ),
    }
    return payload


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
        cancel_settings = StaleBuyOrderCancelSettings(
            timeout_seconds=args.timeout_seconds
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

    _section("Run Order Maintenance")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("execute", str(args.execute))
    _ok("timeout_seconds", str(cancel_settings.timeout_seconds))
    _ok("db_path", str(db_path))

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
                    settings=cancel_settings,
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
                            settings=cancel_settings,
                            lock_name=lock_name,
                            lock_owner_id=lock_owner_id,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        ),
                    )
                return 4

        with KisBroker(settings) as broker:
            order_repo = OrderRepository(conn)
            execution_repo = ExecutionRepository(conn)
            position_repo = PositionRepository(conn)
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
            maintenance_service = OrderMaintenanceService(
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
            )
            result = maintenance_service.run(
                trade_date=args.trade_date,
                stale_cancel_settings=cancel_settings,
                execute_changes=args.execute,
            )

        _section("Maintenance Result")
        _ok(
            "initial_recovery_required_count",
            str(result.sync_result.execution_recovery_required_count),
        )
        _ok(
            "recovered_from_local_execution_count",
            str(result.execution_recovery_result.recovered_count),
        )
        _ok(
            "manual_recovery_required_count",
            str(len(result.manual_recovery_required_client_order_ids)),
        )
        _ok("sync_candidate_count", str(result.sync_result.candidate_count))
        _ok("sync_synced_count", str(result.sync_result.synced_count))
        _ok(
            "recovery_candidate_count",
            str(result.execution_recovery_result.candidate_count),
        )
        _ok(
            "recovery_preview_ready_count",
            str(result.execution_recovery_result.preview_ready_count),
        )
        _ok(
            "recovery_manual_required_count",
            str(result.execution_recovery_result.manual_recovery_required_count),
        )
        _ok(
            "buy_cancel_candidate_count",
            str(result.stale_buy_cancel_result.candidate_count),
        )
        _ok(
            "buy_cancel_preview_ready_count",
            str(result.stale_buy_cancel_result.preview_ready_count),
        )
        _ok(
            "buy_cancel_cancelled_count",
            str(result.stale_buy_cancel_result.cancelled_count),
        )
        _ok(
            "buy_cancel_skipped_count",
            str(result.stale_buy_cancel_result.skipped_count),
        )
        _ok(
            "sell_cancel_candidate_count",
            str(result.stale_sell_cancel_result.candidate_count),
        )
        _ok(
            "sell_cancel_preview_ready_count",
            str(result.stale_sell_cancel_result.preview_ready_count),
        )
        _ok(
            "sell_cancel_cancelled_count",
            str(result.stale_sell_cancel_result.cancelled_count),
        )
        _ok(
            "sell_cancel_skipped_count",
            str(result.stale_sell_cancel_result.skipped_count),
        )

        if result.manual_recovery_required_client_order_ids:
            _section("Manual Recovery Required")
            for client_order_id in result.manual_recovery_required_client_order_ids:
                print(client_order_id)

        visible_sync_candidates = result.sync_result.candidates[: max(0, args.limit)]
        if visible_sync_candidates:
            _section("Sync Candidates")
            for item in visible_sync_candidates:
                print(
                    f"{item.client_order_id} symbol={item.symbol} "
                    f"before={item.status_before} after={item.status_after or '-'} "
                    f"action={item.action.value} outcome={item.outcome.value} "
                    f"reason={item.reason_code or '-'}"
                )

        visible_recovery_candidates = result.execution_recovery_result.candidates[
            : max(0, args.limit)
        ]
        if visible_recovery_candidates:
            _section("Execution Recovery Candidates")
            for item in visible_recovery_candidates:
                print(
                    f"{item.client_order_id} symbol={item.symbol} "
                    f"before={item.status_before} after={item.status_after or '-'} "
                    f"broker_status={item.broker_status or '-'} "
                    f"broker_filled_qty={item.broker_filled_qty if item.broker_filled_qty is not None else '-'} "
                    f"local_filled_qty={item.local_filled_qty} "
                    f"outcome={item.outcome.value} reason={item.reason_code or '-'}"
                )

        visible_buy_cancel_candidates = result.stale_buy_cancel_result.candidates[
            : max(0, args.limit)
        ]
        if visible_buy_cancel_candidates:
            _section("Buy Cancel Candidates")
            for item in visible_buy_cancel_candidates:
                print(
                    f"{item.client_order_id} symbol={item.symbol} "
                    f"status={item.status} age_seconds={item.age_seconds} "
                    f"outcome={item.outcome.value} reason={item.reason_code or '-'}"
                )

        visible_sell_cancel_candidates = result.stale_sell_cancel_result.candidates[
            : max(0, args.limit)
        ]
        if visible_sell_cancel_candidates:
            _section("Sell Cancel Candidates")
            for item in visible_sell_cancel_candidates:
                print(
                    f"{item.client_order_id} symbol={item.symbol} "
                    f"status={item.status} age_seconds={item.age_seconds} "
                    f"outcome={item.outcome.value} reason={item.reason_code or '-'}"
                )

        if output_path is not None:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    settings=cancel_settings,
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
        _fail("maintenance", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            if lock_acquired and lock_service is not None and lock_name is not None:
                lock_released = lock_service.release(lock_name=lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    trade_date=args.trade_date,
                    execute_mode=args.execute,
                    settings=cancel_settings,
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
