"""
Manual universe snapshot viewer.

Reads one trade_date snapshot from SQLite and prints a safe summary.
Returns 0 when snapshot exists, 4 when it does not exist.
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
from services import UniverseQueryService
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import UniverseCandidateRepository

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
        description="Show one stored universe snapshot."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many rows to print. Default: 20",
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

    _section("Show Universe Snapshot")
    _ok("mode", settings.mode)
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))
    _ok("limit", str(args.limit))

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
        repo = UniverseCandidateRepository(conn)
        service = UniverseQueryService(universe_repo=repo)
        result = service.get_snapshot(trade_date=args.trade_date)

        _section("Snapshot Result")
        _ok("exists", str(result.exists))
        _ok("candidate_count", str(result.candidate_count))
        _ok("refreshed_at", str(result.refreshed_at))

        if not result.exists:
            _warn(
                "snapshot",
                "No universe snapshot found for this trade_date.",
            )
        else:
            _section("Rows")
            visible_rows = result.rows[: max(0, args.limit)]
            for row in visible_rows:
                print(
                    f"{row.symbol} name={row.name} market={row.market} "
                    f"close_price={row.close_price} "
                    f"prev_day_trade_value={row.prev_day_trade_value}"
                )

            hidden_count = len(result.rows) - len(visible_rows)
            if hidden_count > 0:
                _warn("rows", f"{hidden_count} more rows omitted from console output.")

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "exists": result.exists,
                "candidate_count": result.candidate_count,
                "refreshed_at": result.refreshed_at,
                                "rows": [
                    {
                        "symbol": row.symbol,
                        "name": row.name,
                        "market": row.market,
                        "close_price": row.close_price,
                        "prev_day_trade_value": row.prev_day_trade_value,
                        "refreshed_at": row.refreshed_at,
                    }
                    for row in result.rows
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0 if result.exists else 4

    except Exception as exc:
        _fail("query", f"{type(exc).__name__}: {exc}")
        return 5

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
