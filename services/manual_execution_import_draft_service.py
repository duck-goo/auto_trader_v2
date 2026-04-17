"""Convert execution-recovery review JSON into manual import draft items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytz

from logger import get_logger
from services.errors import ServiceError
from services.manual_execution_recovery_review_service import (
    ManualExecutionRecoveryReviewResult,
)


_KST = pytz.timezone("Asia/Seoul")
_log = get_logger("order")


@dataclass(frozen=True)
class ManualExecutionImportDraftItem:
    client_order_id: str
    symbol: str
    recommendation: str
    broker_filled_qty: int | None
    local_filled_qty: int
    missing_qty_estimate: int | None
    import_item_template: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ManualExecutionImportDraftResult:
    generated_at: str
    source_trade_date: str | None
    source_review_item_count: int
    exported_item_count: int
    items: tuple[ManualExecutionImportDraftItem, ...]


class ManualExecutionImportDraftService:
    """Build import draft items from a review-output JSON payload."""

    _ALLOWED_RECOMMENDATIONS = frozenset(
        {
            "IMPORT_MISSING_EXECUTIONS",
            "RECONCILE_EXECUTION_QTY",
        }
    )

    def build_from_review_result(
        self,
        *,
        review_result: ManualExecutionRecoveryReviewResult,
        include_reconcile_qty: bool = False,
    ) -> ManualExecutionImportDraftResult:
        if not isinstance(review_result, ManualExecutionRecoveryReviewResult):
            raise ValueError(
                "review_result must be a ManualExecutionRecoveryReviewResult instance."
            )

        rows = [
            {
                "client_order_id": item.client_order_id,
                "symbol": item.symbol,
                "recommendation": item.recommendation.value,
                "broker_filled_qty": item.broker_filled_qty,
                "local_filled_qty": item.local_filled_qty,
                "reason_code": item.reason_code,
            }
            for item in review_result.items
        ]
        return self._build_from_rows(
            source_trade_date=review_result.trade_date,
            review_rows=rows,
            include_reconcile_qty=include_reconcile_qty,
        )

    def build_from_review_payload(
        self,
        *,
        review_payload: dict[str, Any],
        include_reconcile_qty: bool = False,
    ) -> ManualExecutionImportDraftResult:
        if not isinstance(review_payload, dict):
            raise ValueError("review_payload must be a dict.")

        result_payload = review_payload.get("result")
        if not isinstance(result_payload, dict):
            raise ServiceError(
                "Review payload does not contain a successful result object."
            )

        review_items = result_payload.get("items")
        if not isinstance(review_items, list):
            raise ServiceError("Review payload result.items must be a list.")

        source_trade_date = self._optional_text(result_payload.get("trade_date"))
        return self._build_from_rows(
            source_trade_date=source_trade_date,
            review_rows=review_items,
            include_reconcile_qty=include_reconcile_qty,
        )

    def _build_from_rows(
        self,
        *,
        source_trade_date: str | None,
        review_rows: list[dict[str, Any]],
        include_reconcile_qty: bool,
    ) -> ManualExecutionImportDraftResult:
        exported_items: list[ManualExecutionImportDraftItem] = []
        for row in review_rows:
            if not isinstance(row, dict):
                raise ServiceError("Each review item must be an object.")

            recommendation = self._require_text(row, "recommendation")
            if recommendation not in self._ALLOWED_RECOMMENDATIONS:
                continue
            if (
                recommendation == "RECONCILE_EXECUTION_QTY"
                and not include_reconcile_qty
            ):
                continue

            broker_filled_qty = self._optional_int(row.get("broker_filled_qty"))
            local_filled_qty = self._require_int(row, "local_filled_qty")
            missing_qty_estimate = self._compute_missing_qty_estimate(
                broker_filled_qty=broker_filled_qty,
                local_filled_qty=local_filled_qty,
            )
            notes = self._build_notes(
                recommendation=recommendation,
                broker_filled_qty=broker_filled_qty,
                local_filled_qty=local_filled_qty,
                reason_code=self._optional_text(row.get("reason_code")),
            )

            exported_items.append(
                ManualExecutionImportDraftItem(
                    client_order_id=self._require_text(row, "client_order_id"),
                    symbol=self._require_text(row, "symbol"),
                    recommendation=recommendation,
                    broker_filled_qty=broker_filled_qty,
                    local_filled_qty=local_filled_qty,
                    missing_qty_estimate=missing_qty_estimate,
                    import_item_template={
                        "client_order_id": self._require_text(
                            row, "client_order_id"
                        ),
                        "kis_exec_no": "REPLACE_ME_EXEC_NO",
                        "qty": missing_qty_estimate,
                        "price": None,
                        "executed_at": "REPLACE_ME_EXECUTED_AT",
                    },
                    notes=tuple(notes),
                )
            )

        generated_at = datetime.now(_KST).isoformat()

        _log.info(
            f"[manual_execution_import_draft:done] "
            f"source_review_item_count={len(review_rows)} "
            f"exported_item_count={len(exported_items)}"
        )

        return ManualExecutionImportDraftResult(
            generated_at=generated_at,
            source_trade_date=source_trade_date,
            source_review_item_count=len(review_rows),
            exported_item_count=len(exported_items),
            items=tuple(exported_items),
        )

    @staticmethod
    def _compute_missing_qty_estimate(
        *,
        broker_filled_qty: int | None,
        local_filled_qty: int,
    ) -> int | None:
        if broker_filled_qty is None:
            return None
        missing_qty = broker_filled_qty - local_filled_qty
        if missing_qty <= 0:
            return None
        return missing_qty

    @staticmethod
    def _build_notes(
        *,
        recommendation: str,
        broker_filled_qty: int | None,
        local_filled_qty: int,
        reason_code: str | None,
    ) -> list[str]:
        notes: list[str] = [
            "price and executed_at must be filled manually before import",
            "kis_exec_no must be unique for the target order",
        ]
        if recommendation == "IMPORT_MISSING_EXECUTIONS":
            notes.append(
                "this item came from LOCAL_EXECUTIONS_MISSING; verify the missing execution rows first"
            )
        if recommendation == "RECONCILE_EXECUTION_QTY":
            notes.append(
                "this item came from LOCAL_BROKER_FILLED_QTY_MISMATCH; reconcile quantities before import"
            )
        if broker_filled_qty is not None:
            notes.append(
                f"broker_filled_qty={broker_filled_qty}, local_filled_qty={local_filled_qty}"
            )
        if reason_code:
            notes.append(f"reason_code={reason_code}")
        return notes

    @staticmethod
    def _require_text(row: dict[str, Any], key: str) -> str:
        value = row.get(key)
        if not isinstance(value, str):
            raise ServiceError(f"Review field {key!r} must be a string.")
        stripped = value.strip()
        if not stripped:
            raise ServiceError(f"Review field {key!r} cannot be empty.")
        return stripped

    @staticmethod
    def _require_int(row: dict[str, Any], key: str) -> int:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ServiceError(f"Review field {key!r} must be an integer.")
        return value

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ServiceError("Optional integer review field is invalid.")
        return value

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ServiceError("Optional text review field is invalid.")
        stripped = value.strip()
        return stripped or None
