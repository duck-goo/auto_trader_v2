"""
Manual universe refresh from a JSON file.

Input format:
[
  {
    "symbol": "005930",
    "name": "Samsung Electronics",
    "market": "KOSPI",
    "close_price": 70500,
    "prev_day_trade_value": 950000000000
  }
]
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
from services import (
    UniverseRefreshItem,
    UniverseRefreshService,
)
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


def _fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f" - {detail}" if detail else ""))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh universe snapshot from a JSON file."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to JSON array of universe candidates.",
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--refreshed-at",
        default=None,
        help="Optional aware ISO8601 timestamp. Default: now in KST",
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


def _load_candidates(path: Path) -> list[UniverseRefreshItem]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise ValueError("Input JSON must be a list.")

    items: list[UniverseRefreshItem] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ValueError(f"Item {index} must be an object.")

        try:
            items.append(
                UniverseRefreshItem(
                    symbol=str(raw["symbol"]),
                    name=str(raw["name"]),
                    market=str(raw["market"]),
                    close_price=int(raw["close_price"]),
                    prev_day_trade_value=int(raw["prev_day_trade_value"]),
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


def main() -> int:
    args = _parse_args()

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        return 5

    input_path = _resolve_path(args.input)
    if not input_path.exists():
        _fail("input", f"File not found: {input_path}")
        return 2

    output_path = _resolve_path(args.output) if args.output else None
    db_path = args.db_path or settings.db_path

    _section("Refresh Universe From JSON")
    _ok("mode", settings.mode)
    _ok("input", str(input_path))
    _ok("trade_date", args.trade_date)
    _ok("db_path", str(db_path))

    try:
        items = _load_candidates(input_path)
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
    except Exception as exc:
        _fail("prepare", f"{type(exc).__name__}: {exc}")
        return 5

    try:
        repo = UniverseCandidateRepository(conn)
        service = UniverseRefreshService(
            conn=conn,
            universe_repo=repo,
        )

        result = service.refresh_snapshot(
            trade_date=args.trade_date,
            candidates=items,
            refreshed_at=args.refreshed_at,
        )

        _section("Refresh Result")
        _ok("candidate_count", str(result.candidate_count))
        _ok("refreshed_at", result.refreshed_at)

        for row in result.rows:
            print(
                f"{row.symbol} name={row.name} market={row.market} "
                f"close_price={row.close_price} "
                f"prev_day_trade_value={row.prev_day_trade_value}"
            )

        if output_path is not None:
            payload = {
                "trade_date": result.trade_date,
                "refreshed_at": result.refreshed_at,
                "candidate_count": result.candidate_count,
                "rows": [
                    {
                        "symbol": row.symbol,
                        "name": row.name,
                        "market": row.market,
                        "close_price": row.close_price,
                        "prev_day_trade_value": row.prev_day_trade_value,
                    }
                    for row in result.rows
                ],
            }
            _save_json(output_path, payload)
            _ok("json_saved", str(output_path))

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
