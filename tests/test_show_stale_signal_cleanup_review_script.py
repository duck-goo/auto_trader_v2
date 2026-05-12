from __future__ import annotations

import json
from pathlib import Path

import scripts.show_stale_signal_cleanup_review as target


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_review_document_prefers_execute_and_collects_counts(test_db_path):
    ops_dir = test_db_path.with_name(f"{test_db_path.stem}_stale_signal_cleanup_review")

    _write_json(
        ops_dir / "order_maintenance.preview.json",
        {
            "result": {
                "stale_buy_signal_cleanup_result": {
                    "candidates": [
                        {
                            "signal_id": 1,
                            "symbol": "111111",
                            "strategy_name": "preview_buy",
                            "scanned_at": "2026-05-06T09:00:00+09:00",
                            "age_seconds": 600,
                            "outcome": "PREVIEW_READY",
                            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                            "reason_message": "preview only",
                            "acted": False,
                        }
                    ]
                }
            }
        },
    )
    _write_json(
        ops_dir / "order_maintenance.execute.json",
        {
            "result": {
                "stale_buy_signal_cleanup_result": {
                    "candidates": [
                        {
                            "signal_id": 10,
                            "symbol": "005930",
                            "strategy_name": "buy_timing1_intraday_trigger",
                            "scanned_at": "2026-05-06T09:01:00+09:00",
                            "age_seconds": 610,
                            "outcome": "BLOCKED",
                            "reason_code": "INVALID_SIGNAL_SCANNED_AT",
                            "reason_message": "invalid timestamp",
                            "acted": False,
                        }
                    ]
                },
                "stale_sell_signal_cleanup_result": {
                    "candidates": [
                        {
                            "signal_id": 11,
                            "symbol": "000660",
                            "strategy_name": "sell_stop_loss",
                            "scanned_at": "2026-05-06T09:02:00+09:00",
                            "age_seconds": 620,
                            "outcome": "PREVIEW_READY",
                            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                            "reason_message": "ready",
                            "acted": False,
                        },
                        {
                            "signal_id": 12,
                            "symbol": "035420",
                            "strategy_name": "sell_take_profit",
                            "scanned_at": "2026-05-06T09:03:00+09:00",
                            "age_seconds": 630,
                            "outcome": "CLEANED",
                            "reason_code": "STALE_SIGNAL_AGE_EXCEEDED",
                            "reason_message": "cleaned",
                            "acted": True,
                        },
                    ]
                },
            }
        },
    )

    payload, source_path = target.build_stale_signal_cleanup_review_document(
        trade_date="2026-05-06",
        ops_dir=ops_dir,
    )

    assert payload["available"] is True
    assert payload["source_label"] == "order_maintenance.execute"
    assert source_path == ops_dir / "order_maintenance.execute.json"
    assert payload["review_item_count"] == 3
    assert payload["blocked_item_count"] == 1
    assert payload["preview_ready_item_count"] == 1
    assert payload["cleaned_item_count"] == 1
    assert payload["top_symbols"] == "005930, 000660, 035420"
    assert [row["outcome"] for row in payload["items"]] == [
        "BLOCKED",
        "PREVIEW_READY",
        "CLEANED",
    ]


def test_build_review_document_returns_unavailable_when_source_missing(test_db_path):
    ops_dir = test_db_path.with_name(
        f"{test_db_path.stem}_stale_signal_cleanup_review_missing"
    )

    payload, source_path = target.build_stale_signal_cleanup_review_document(
        trade_date="2026-05-06",
        ops_dir=ops_dir,
    )

    assert payload["available"] is False
    assert payload["review_item_count"] == 0
    assert payload["items"] == []
    assert source_path is None
