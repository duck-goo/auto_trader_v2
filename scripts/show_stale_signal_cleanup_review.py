"""
Build one read-only review JSON for stale signal cleanup candidates.

Inputs:
- order_maintenance.execute.json or order_maintenance.preview.json

Safety:
- read-only
- never mutates DB or source artifacts
- tolerates missing stale cleanup sections and still writes one stable review JSON
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

KST = pytz.timezone("Asia/Seoul")

SOURCE_CANDIDATES = (
    ("order_maintenance.execute", "order_maintenance.execute.json"),
    ("order_maintenance.preview", "order_maintenance.preview.json"),
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
        description="Build one read-only stale signal cleanup review JSON."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST.",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>",
    )
    parser.add_argument(
        "--input",
        default=None,
        help=(
            "Optional direct path to order_maintenance.preview.json or "
            "order_maintenance.execute.json."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional review JSON output path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many review rows to print. Default: 20.",
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _resolve_ops_dir(args: argparse.Namespace) -> Path:
    if args.ops_dir:
        return _resolve_path(args.ops_dir)
    return (PROJECT_ROOT / "data" / "ops" / args.trade_date).resolve()


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _resolve_source(
    *,
    trade_date: str,
    ops_dir: Path,
    input_path: str | None,
) -> tuple[str | None, Path | None, dict[str, Any] | None]:
    if input_path:
        resolved_path = _resolve_path(input_path)
        payload = _load_optional_json(resolved_path)
        return ("manual_input", resolved_path, payload)

    for source_label, filename in SOURCE_CANDIDATES:
        candidate_path = ops_dir / filename
        payload = _load_optional_json(candidate_path)
        if payload is not None:
            return (source_label, candidate_path, payload)
    return (None, None, None)


def _collect_candidates(
    *,
    scope: str,
    cleanup_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(cleanup_result, dict):
        return []
    candidates = cleanup_result.get("candidates")
    if not isinstance(candidates, list):
        return []

    rows: list[dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "scope": scope,
                "signal_id": row.get("signal_id"),
                "symbol": row.get("symbol"),
                "strategy_name": row.get("strategy_name"),
                "scanned_at": row.get("scanned_at"),
                "age_seconds": row.get("age_seconds"),
                "outcome": row.get("outcome"),
                "reason_code": row.get("reason_code"),
                "reason_message": row.get("reason_message"),
                "acted": row.get("acted"),
            }
        )
    return rows


def _symbol_hint(rows: list[dict[str, Any]], *, limit: int = 3) -> str | None:
    symbols: list[str] = []
    for row in rows:
        symbol = _optional_text(row.get("symbol"))
        if symbol is None or symbol in symbols:
            continue
        symbols.append(symbol)
        if len(symbols) >= limit:
            return ", ".join(symbols)
    if not symbols:
        return None
    return ", ".join(symbols)


def _count_outcome(rows: list[dict[str, Any]], outcome: str) -> int:
    return sum(1 for row in rows if _optional_text(row.get("outcome")) == outcome)


def build_stale_signal_cleanup_review_document(
    *,
    trade_date: str,
    ops_dir: str | Path | None = None,
    input_path: str | Path | None = None,
) -> tuple[dict[str, Any], Path | None]:
    resolved_ops_dir = (
        _resolve_path(str(ops_dir))
        if ops_dir is not None
        else (PROJECT_ROOT / "data" / "ops" / trade_date).resolve()
    )
    source_label, source_path, source_payload = _resolve_source(
        trade_date=trade_date,
        ops_dir=resolved_ops_dir,
        input_path=None if input_path is None else str(input_path),
    )

    if source_payload is None or source_path is None or source_label is None:
        return (
            {
                "trade_date": trade_date,
                "available": False,
                "source_label": None,
                "source_path": None,
                "review_item_count": 0,
                "blocked_item_count": 0,
                "preview_ready_item_count": 0,
                "cleaned_item_count": 0,
                "top_symbols": None,
                "items": [],
            },
            None,
        )

    result = source_payload.get("result")
    stale_buy_signal_cleanup_result = (
        None
        if not isinstance(result, dict)
        else result.get("stale_buy_signal_cleanup_result")
    )
    stale_sell_signal_cleanup_result = (
        None
        if not isinstance(result, dict)
        else result.get("stale_sell_signal_cleanup_result")
    )

    items = [
        *_collect_candidates(
            scope="buy",
            cleanup_result=stale_buy_signal_cleanup_result,
        ),
        *_collect_candidates(
            scope="sell",
            cleanup_result=stale_sell_signal_cleanup_result,
        ),
    ]
    items.sort(
        key=lambda row: (
            {"BLOCKED": 0, "PREVIEW_READY": 1, "CLEANED": 2}.get(
                _optional_text(row.get("outcome")) or "",
                9,
            ),
            _optional_text(row.get("scanned_at")) or "",
            _optional_text(row.get("symbol")) or "",
        )
    )

    payload = {
        "trade_date": trade_date,
        "available": True,
        "source_label": source_label,
        "source_path": str(source_path),
        "review_item_count": len(items),
        "blocked_item_count": _count_outcome(items, "BLOCKED"),
        "preview_ready_item_count": _count_outcome(items, "PREVIEW_READY"),
        "cleaned_item_count": _count_outcome(items, "CLEANED"),
        "top_symbols": _symbol_hint(items),
        "items": items,
    }
    return (payload, source_path)


def main() -> int:
    args = _parse_args()

    try:
        payload, source_path = build_stale_signal_cleanup_review_document(
            trade_date=args.trade_date,
            ops_dir=args.ops_dir,
            input_path=args.input,
        )
        output_path = (
            _resolve_path(args.output)
            if args.output
            else _resolve_ops_dir(args) / "stale_signal_cleanup.review.json"
        )
        _save_json(output_path, payload)
    except Exception as exc:
        _fail("stale_signal_cleanup_review", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Stale Signal Cleanup Review")
    _ok("trade_date", str(payload.get("trade_date")))
    _ok("available", str(payload.get("available")))
    _ok("review_item_count", str(payload.get("review_item_count")))
    _ok("blocked_item_count", str(payload.get("blocked_item_count")))
    _ok("preview_ready_item_count", str(payload.get("preview_ready_item_count")))
    _ok("cleaned_item_count", str(payload.get("cleaned_item_count")))
    if source_path is not None:
        _ok("source_path", str(source_path))
    if payload.get("top_symbols"):
        _ok("top_symbols", str(payload.get("top_symbols")))

    visible_items = payload.get("items", [])[: max(0, args.limit)]
    if visible_items:
        _section("Review Items")
        for item in visible_items:
            print(
                f"{item['scope']} signal_id={item['signal_id']} "
                f"symbol={item['symbol']} outcome={item['outcome']} "
                f"reason={item['reason_code'] or '-'}"
            )
    else:
        _warn("review_items", "No stale signal cleanup review items were found.")

    if not payload.get("available"):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
