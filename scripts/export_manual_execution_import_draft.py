"""
Export manual execution import draft items from a review JSON file.

This script is intentionally read-only. It converts the output of
`review_execution_recovery.py --output ...` into a draft JSON that the user
can edit and then pass to `import_manual_executions.py`.
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

from services import ManualExecutionImportDraftService


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
        description="Export manual execution import draft from review JSON."
    )
    parser.add_argument(
        "--review-input",
        required=True,
        help="Path to review_execution_recovery JSON output.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the import draft JSON.",
    )
    parser.add_argument(
        "--include-reconcile-qty",
        action="store_true",
        help=(
            "Also export RECONCILE_EXECUTION_QTY items. Default exports only "
            "IMPORT_MISSING_EXECUTIONS."
        ),
    )
    return parser.parse_args()


def _resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Review input JSON must be an object.")
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _build_payload(*, review_input: str, result) -> dict[str, Any]:
    return {
        "review_input": review_input,
        "generated_at": result.generated_at,
        "source_trade_date": result.source_trade_date,
        "source_review_item_count": result.source_review_item_count,
        "exported_item_count": result.exported_item_count,
        "items": [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "recommendation": item.recommendation,
                "broker_filled_qty": item.broker_filled_qty,
                "local_filled_qty": item.local_filled_qty,
                "missing_qty_estimate": item.missing_qty_estimate,
                "import_item_template": item.import_item_template,
                "notes": list(item.notes),
            }
            for item in result.items
        ],
    }


def main() -> int:
    args = _parse_args()
    review_input_path = _resolve_path(args.review_input)
    output_path = _resolve_path(args.output)

    _section("Export Manual Execution Import Draft")
    _ok("review_input", str(review_input_path))
    _ok("output", str(output_path))
    _ok("include_reconcile_qty", str(args.include_reconcile_qty))

    try:
        review_payload = _load_json(review_input_path)
        result = ManualExecutionImportDraftService().build_from_review_payload(
            review_payload=review_payload,
            include_reconcile_qty=args.include_reconcile_qty,
        )
    except Exception as exc:
        _fail("draft", f"{type(exc).__name__}: {exc}")
        return 5

    _save_json(
        output_path,
        _build_payload(review_input=str(review_input_path), result=result),
    )

    _section("Draft Result")
    _ok("source_review_item_count", str(result.source_review_item_count))
    _ok("exported_item_count", str(result.exported_item_count))
    _ok("json_saved", str(output_path))

    if result.items:
        _section("Items")
        for item in result.items[:20]:
            print(
                f"{item.client_order_id} symbol={item.symbol} "
                f"recommendation={item.recommendation} "
                f"missing_qty_estimate={item.missing_qty_estimate} "
                f"template_exec_no={item.import_item_template['kis_exec_no']}"
            )
    else:
        _warn("items", "No draft items matched the export rules.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
