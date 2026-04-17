"""
Show or change the persisted Kill Switch flag.

Safety:
- no change is made unless --enable or --disable is passed
- writes require an explicit flag and run inside one SQLite transaction
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

from config.loader import load_settings
from logger import setup_logging
from storage.db import get_connection, transaction
from storage.migrations.runner import run_migrations
from storage.repositories import TradingControlRepository

KST = pytz.timezone("Asia/Seoul")


def _section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(label: str, detail: str = "") -> None:
    print(f"[ OK ] {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show or change the persisted Kill Switch flag."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--enable",
        action="store_true",
        help="Enable Kill Switch.",
    )
    group.add_argument(
        "--disable",
        action="store_true",
        help="Disable Kill Switch.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Optional note to save with the change.",
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


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    db_path = args.db_path or settings.db_path
    output_path = _resolve_path(args.output) if args.output else None
    action = "STATUS"
    if args.enable:
        action = "ENABLE"
    elif args.disable:
        action = "DISABLE"

    _section("Kill Switch")
    _ok("action", action)
    _ok("db_path", str(db_path))

    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("db setup", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        repo = TradingControlRepository(conn)
        now_text = datetime.now(KST).isoformat()
        if args.enable or args.disable:
            with transaction(conn):
                row = repo.set_kill_switch(
                    is_enabled=bool(args.enable),
                    updated_at=now_text,
                    note=args.note,
                )
        else:
            row = repo.get_kill_switch()
    except Exception as exc:
        conn.close()
        _fail("kill switch", f"{type(exc).__name__}: {exc}")
        return 5

    conn.close()

    enabled = False if row is None else row.is_enabled
    note = None if row is None else row.note
    updated_at = None if row is None else row.updated_at

    _section("Result")
    _ok("enabled", str(enabled))
    _ok("note", "" if note is None else note)
    _ok("updated_at", "" if updated_at is None else updated_at)

    payload = {
        "action": action,
        "enabled": enabled,
        "note": note,
        "updated_at": updated_at,
    }
    if output_path is not None:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
