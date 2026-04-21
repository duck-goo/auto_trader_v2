"""
Import missing execution rows from a manual JSON file.

Input format:
[
  {
    "client_order_id": "COID_123",
    "kis_exec_no": "EXEC_1",
    "qty": 1,
    "price": 70000,
    "executed_at": "2026-04-17T09:05:00+09:00"
  }
]

Safety:
- preview is the default
- execute mode uses a persisted runtime lock
- symbol/side are inferred from the target order row, not from user input
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_settings
from logger import setup_logging
from services import (
    ManualExecutionImportItem,
    ManualExecutionImportService,
    RuntimeLockBusyError,
    RuntimeLockService,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    EntryLotRepository,
    ExecutionRepository,
    OrderRepository,
    PositionRepository,
    RuntimeLockRepository,
    SignalRepository,
)


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
        description="Preview or import manual execution rows from JSON."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to JSON array of manual execution items.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually import execution rows into SQLite.",
    )
    parser.add_argument(
        "--lock-name",
        default="manual_execution_import",
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


def _load_items(path: Path) -> list[ManualExecutionImportItem]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Input JSON must be a list.")

    items: list[ManualExecutionImportItem] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"Item {index} must be an object.")
        try:
            items.append(
                ManualExecutionImportItem(
                    client_order_id=str(row["client_order_id"]),
                    kis_exec_no=str(row["kis_exec_no"]),
                    qty=row["qty"],
                    price=row["price"],
                    executed_at=str(row["executed_at"]),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"Item {index} is missing key: {exc.args[0]!r}"
            ) from exc
    return items


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _build_payload(
    *,
    input_path: str,
    execute_mode: bool,
    result=None,
    lock_name: str | None = None,
    lock_owner_id: str | None = None,
    lock_acquired: bool = False,
    lock_released: bool = False,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "input_path": input_path,
        "execute_mode": execute_mode,
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
        "imported_at": result.imported_at,
        "execute_import": result.execute_import,
        "item_count": result.item_count,
        "candidate_count": result.candidate_count,
        "preview_ready_count": result.preview_ready_count,
        "imported_count": result.imported_count,
        "skipped_count": result.skipped_count,
        "blocked_count": result.blocked_count,
        "acted_count": result.acted_count,
        "candidates": [
            {
                "client_order_id": item.client_order_id,
                "kis_exec_no": item.kis_exec_no,
                "symbol": item.symbol,
                "side": item.side,
                "status_before": item.status_before,
                "status_after": item.status_after,
                "local_filled_qty_before": item.local_filled_qty_before,
                "local_filled_qty_after": item.local_filled_qty_after,
                "outcome": item.outcome.value,
                "reason_code": item.reason_code,
                "reason_message": item.reason_message,
                "acted": item.acted,
            }
            for item in result.candidates
        ],
    }
    return payload


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    input_path = _resolve_path(args.input)
    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path
    lock_service: RuntimeLockService | None = None
    lock_owner_id: str | None = None
    lock_acquired = False
    lock_released = False

    _section("Import Manual Executions")
    _ok("input", str(input_path))
    _ok("execute", str(args.execute))
    _ok("db_path", str(db_path))

    try:
        items = _load_items(input_path)
    except Exception as exc:
        _fail("input", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            _save_json(
                output_path,
                _build_payload(
                    input_path=str(input_path),
                    execute_mode=args.execute,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                ),
            )
        return 5

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
                    input_path=str(input_path),
                    execute_mode=args.execute,
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
                    lock_name=args.lock_name,
                    lease_seconds=args.lock_lease_seconds,
                )
                lock_acquired = True
            except RuntimeLockBusyError as exc:
                _fail("runtime lock", str(exc))
                if output_path is not None:
                    _save_json(
                        output_path,
                        _build_payload(
                            input_path=str(input_path),
                            execute_mode=True,
                            lock_name=args.lock_name,
                            lock_owner_id=lock_owner_id,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        ),
                    )
                return 4

        service = ManualExecutionImportService(
            conn=conn,
            order_repo=OrderRepository(conn),
            execution_repo=ExecutionRepository(conn),
            position_repo=PositionRepository(conn),
            entry_lot_repo=EntryLotRepository(conn),
            signal_repo=SignalRepository(conn),
        )
        result = service.import_items(
            items=items,
            execute_import=args.execute,
        )

        _section("Import Result")
        _ok("item_count", str(result.item_count))
        _ok("candidate_count", str(result.candidate_count))
        _ok("preview_ready_count", str(result.preview_ready_count))
        _ok("imported_count", str(result.imported_count))
        _ok("skipped_count", str(result.skipped_count))
        _ok("blocked_count", str(result.blocked_count))
        _ok("acted_count", str(result.acted_count))

        visible_candidates = result.candidates[: max(0, args.limit)]
        if visible_candidates:
            _section("Candidates")
            for item in visible_candidates:
                print(
                    f"{item.client_order_id} kis_exec_no={item.kis_exec_no} "
                    f"symbol={item.symbol or '-'} outcome={item.outcome.value} "
                    f"before={item.status_before or '-'} after={item.status_after or '-'} "
                    f"reason={item.reason_code or '-'}"
                )
        else:
            _warn("candidates", "No manual execution items were loaded.")

        if output_path is not None:
            if lock_acquired and lock_service is not None:
                lock_released = lock_service.release(lock_name=args.lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    input_path=str(input_path),
                    execute_mode=args.execute,
                    result=result,
                    lock_name=args.lock_name if args.execute else None,
                    lock_owner_id=lock_owner_id,
                    lock_acquired=lock_acquired,
                    lock_released=lock_released,
                ),
            )
            _ok("json_saved", str(output_path))

        return 0

    except Exception as exc:
        _fail("import", f"{type(exc).__name__}: {exc}")
        if output_path is not None:
            if lock_acquired and lock_service is not None:
                lock_released = lock_service.release(lock_name=args.lock_name)
                lock_acquired = False
            _save_json(
                output_path,
                _build_payload(
                    input_path=str(input_path),
                    execute_mode=args.execute,
                    lock_name=args.lock_name if args.execute else None,
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
            if lock_acquired and lock_service is not None:
                lock_released = lock_service.release(lock_name=args.lock_name)
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
