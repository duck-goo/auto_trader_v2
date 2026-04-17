"""Tests for ManualExecutionImportDraftService."""

from __future__ import annotations

from services import ManualExecutionImportDraftService


def test_build_from_review_payload_exports_only_missing_execution_items_by_default():
    payload = {
        "trade_date": "2026-04-17",
        "result": {
            "trade_date": "2026-04-17",
            "review_item_count": 2,
            "items": [
                {
                    "client_order_id": "COID_MISSING",
                    "symbol": "005930",
                    "recommendation": "IMPORT_MISSING_EXECUTIONS",
                    "broker_filled_qty": 3,
                    "local_filled_qty": 1,
                    "reason_code": "LOCAL_EXECUTIONS_MISSING",
                },
                {
                    "client_order_id": "COID_RECON",
                    "symbol": "000660",
                    "recommendation": "RECONCILE_EXECUTION_QTY",
                    "broker_filled_qty": 5,
                    "local_filled_qty": 2,
                    "reason_code": "LOCAL_BROKER_FILLED_QTY_MISMATCH",
                },
            ],
        },
    }

    result = ManualExecutionImportDraftService().build_from_review_payload(
        review_payload=payload,
        include_reconcile_qty=False,
    )

    assert result.source_review_item_count == 2
    assert result.exported_item_count == 1
    item = result.items[0]
    assert item.client_order_id == "COID_MISSING"
    assert item.missing_qty_estimate == 2
    assert item.import_item_template["qty"] == 2
    assert item.import_item_template["price"] is None
