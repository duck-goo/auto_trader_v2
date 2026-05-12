"""
Show one date-level operations report from data/ops artifacts.

Flow:
1. Resolve one ops directory for a trade date.
2. Read known JSON artifacts if they exist.
3. Build one normalized report with attention flags.

Safety:
- read-only
- missing files are tolerated
- report generation succeeds even when some artifacts show blocked/failed states
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

KNOWN_ARTIFACT_FILES = {
    "startup_check": "startup_check.json",
    "trading_session_preview": "run_trading_session.preview.json",
    "trading_session_execute": "run_trading_session.execute.json",
    "execute_buy_signals_preview": "execute_buy_signals.preview.json",
    "execute_buy_signals_execute": "execute_buy_signals.execute.json",
    "execute_sell_signals_preview": "execute_sell_signals.preview.json",
    "execute_sell_signals_execute": "execute_sell_signals.execute.json",
    "after_close_preview": "after_close.preview.json",
    "after_close_write": "after_close.write.json",
    "order_maintenance_preview": "order_maintenance.preview.json",
    "order_maintenance_execute": "order_maintenance.execute.json",
    "stale_signal_cleanup_review": "stale_signal_cleanup.review.json",
    "execution_recovery_review": "execution_recovery.review.json",
    "execution_recovery_draft": "execution_recovery.draft.json",
    "execution_recovery_import_preview": "execution_recovery.import.preview.json",
    "execution_recovery_import_execute": "execution_recovery.import.execute.json",
    "kill_switch_status": "kill_switch.status.json",
    "kill_switch_enable": "kill_switch.enable.json",
    "kill_switch_disable": "kill_switch.disable.json",
}

SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

CRITICAL_ATTENTION_FLAGS = {
    "KILL_SWITCH_ENABLED",
    "TRADING_SESSION_EXECUTE_FAILED",
    "EXECUTE_BUY_SIGNALS_EXECUTE_FAILED",
    "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
    "AFTER_CLOSE_WRITE_FAILED",
    "ORDER_MAINTENANCE_EXECUTE_FAILED",
    "EXECUTION_RECOVERY_IMPORT_EXECUTE_FAILED",
    "EXECUTION_RECOVERY_IMPORT_EXECUTE_HAS_BLOCKED_ITEMS",
}

DEFAULT_ALERT_ACTION_LIMIT = 3
TIMING2_30S_STEP_NAMES = (
    "timing2_price_sample_capture",
    "timing2_30s_bar_build",
    "timing2_30s_trigger_scan",
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
        description="Show one date-level daily operations report."
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--ops-dir",
        default=None,
        help="Optional ops directory override. Default: data/ops/<trade-date>",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional normalized JSON output path.",
    )
    parser.add_argument(
        "--alert-output",
        default=None,
        help="Optional plain-text alert output path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when warning/critical attention flags are detected.",
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


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_non_negative_int(value: Any) -> int | None:
    normalized_value = _optional_int(value)
    if normalized_value is None or normalized_value < 0:
        return None
    return normalized_value


def _sum_optional_ints(*values: Any) -> int | None:
    normalized_values = [
        value
        for value in (_optional_int(raw_value) for raw_value in values)
        if value is not None
    ]
    if not normalized_values:
        return None
    return sum(normalized_values)


def _summarize_intraday_bar_refresh(
    polling_result: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {
        "cycle_count": None,
        "failed_count": None,
        "reason": None,
    }
    if not isinstance(polling_result, dict):
        return summary

    cycles = polling_result.get("cycles")
    if not isinstance(cycles, list):
        return summary

    cycle_count = 0
    failed_count_total = 0
    latest_reason: str | None = None

    for cycle in cycles:
        if not isinstance(cycle, dict):
            continue
        refresh_result = cycle.get("intraday_bar_refresh")
        if not isinstance(refresh_result, dict):
            continue
        cycle_count += 1
        refresh_summary = refresh_result.get("summary")
        failed_count = (
            None
            if not isinstance(refresh_summary, dict)
            else _optional_non_negative_int(refresh_summary.get("failed_count"))
        )
        if failed_count is not None:
            failed_count_total += failed_count

        reason = _optional_text(refresh_result.get("reason"))
        if reason is not None:
            latest_reason = reason

    if cycle_count == 0:
        return summary

    summary["cycle_count"] = cycle_count
    summary["failed_count"] = failed_count_total
    summary["reason"] = latest_reason
    return summary


def _collect_signal_cleanup_symbol_hint(
    *results: dict[str, Any] | None,
    limit: int = 3,
) -> str | None:
    symbols: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            continue
        for row in candidates:
            if not isinstance(row, dict):
                continue
            outcome = _optional_text(row.get("outcome"))
            if outcome not in {"PREVIEW_READY", "CLEANED", "BLOCKED"}:
                continue
            symbol = _optional_text(row.get("symbol"))
            if symbol is None or symbol in symbols:
                continue
            symbols.append(symbol)
            if len(symbols) >= limit:
                return ", ".join(symbols)
    if not symbols:
        return None
    return ", ".join(symbols)


def _collect_signal_cleanup_reason_codes(
    *results: dict[str, Any] | None,
    limit: int = 3,
) -> str | None:
    reason_codes: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            continue
        for row in candidates:
            if not isinstance(row, dict):
                continue
            if _optional_text(row.get("outcome")) != "BLOCKED":
                continue
            reason_code = _optional_text(row.get("reason_code"))
            if reason_code is None or reason_code in reason_codes:
                continue
            reason_codes.append(reason_code)
            if len(reason_codes) >= limit:
                return ", ".join(reason_codes)
    if not reason_codes:
        return None
    return ", ".join(reason_codes)


def _build_stale_signal_cleanup_preview_items(
    items: Any,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    preview_items: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        preview_items.append(
            {
                "scope": _optional_text(row.get("scope")),
                "symbol": _optional_text(row.get("symbol")),
                "strategy_name": _optional_text(row.get("strategy_name")),
                "scanned_at": _optional_text(row.get("scanned_at")),
                "outcome": _optional_text(row.get("outcome")),
                "reason_code": _optional_text(row.get("reason_code")),
                "age_seconds": _optional_non_negative_int(row.get("age_seconds")),
            }
        )
        if len(preview_items) >= limit:
            break
    return preview_items


def _build_source_file_name(path_value: Any) -> str | None:
    normalized_path = _optional_text(path_value)
    if normalized_path is None:
        return None
    return Path(normalized_path).name or None


def _artifact_stub(*, label: str, path: Path) -> dict[str, Any]:
    return {
        "label": label,
        "exists": False,
        "path": str(path),
    }


def _summarize_startup(*, path: Path, payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = _artifact_stub(label="startup_check", path=path)
    if payload is None:
        return summary
    unresolved_orders = payload.get("unresolved_orders")
    live_positions = payload.get("live_positions")
    universe_snapshot = payload.get("universe_snapshot")
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "checked_at": payload.get("checked_at"),
            "outcome": payload.get("outcome"),
            "reason": payload.get("reason"),
            "reconcile_reason_code": payload.get("reconcile_reason_code"),
            "reconcile_reason_message": payload.get("reconcile_reason_message"),
            "reconcile_changed_rows": payload.get("reconcile_changed_rows"),
            "universe_exists": (
                None
                if not isinstance(universe_snapshot, dict)
                else universe_snapshot.get("exists")
            ),
            "universe_candidate_count": (
                None
                if not isinstance(universe_snapshot, dict)
                else universe_snapshot.get("candidate_count")
            ),
            "unresolved_order_count": (
                None if not isinstance(unresolved_orders, list) else len(unresolved_orders)
            ),
            "live_position_count": (
                None if not isinstance(live_positions, list) else len(live_positions)
            ),
        }
    )
    return summary


def _summarize_trading_session(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    preopen_result = payload.get("preopen_result")
    timing2_setup_scan_result = (
        None
        if not isinstance(preopen_result, dict)
        else preopen_result.get("timing2_setup_scan_result")
    )
    polling_result = payload.get("polling_result")
    timing2_setup_readiness = _coerce_timing2_setup_readiness(
        None
        if not isinstance(polling_result, dict)
        else polling_result.get("timing2_setup_readiness")
    )
    intraday_bar_refresh = _summarize_intraday_bar_refresh(polling_result)
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "execute_mode": payload.get("execute_mode"),
            "session_outcome": payload.get("session_outcome"),
            "session_reason": payload.get("session_reason"),
            "preopen_exit_code": payload.get("preopen_exit_code"),
            "preopen_readiness_outcome": (
                None
                if not isinstance(preopen_result, dict)
                else preopen_result.get("readiness_outcome")
            ),
            "preopen_readiness_reason": (
                None
                if not isinstance(preopen_result, dict)
                else (
                    preopen_result.get("readiness_reason")
                    or preopen_result.get("error_message")
                )
            ),
            "timing2_setup_scan_outcome": (
                None
                if not isinstance(preopen_result, dict)
                else preopen_result.get("timing2_setup_scan_outcome")
            ),
            "timing2_setup_scan_reason": (
                None
                if not isinstance(preopen_result, dict)
                else preopen_result.get("timing2_setup_scan_reason")
            ),
            "timing2_setup_scan_matched_count": (
                None
                if not isinstance(timing2_setup_scan_result, dict)
                else timing2_setup_scan_result.get("matched_count")
            ),
            "timing2_setup_scan_recorded_count": (
                None
                if not isinstance(timing2_setup_scan_result, dict)
                else timing2_setup_scan_result.get("recorded_count")
            ),
            "polling_started": payload.get("polling_started"),
            "polling_exit_code": payload.get("polling_exit_code"),
            "polling_stop_reason": (
                None
                if not isinstance(polling_result, dict)
                else polling_result.get("stop_reason")
            ),
            "intraday_bar_refresh_cycle_count": intraday_bar_refresh["cycle_count"],
            "intraday_bar_refresh_failed_count": intraday_bar_refresh[
                "failed_count"
            ],
            "intraday_bar_refresh_reason": intraday_bar_refresh["reason"],
            "timing2_setup_readiness": timing2_setup_readiness,
            "timing2_setup_required": timing2_setup_readiness.get("required"),
            "timing2_setup_ready": timing2_setup_readiness.get("ready"),
            "timing2_setup_signal_count": timing2_setup_readiness.get(
                "setup_signal_count"
            ),
            "timing2_setup_reason": timing2_setup_readiness.get("reason"),
        }
    )
    return summary


def _resolve_trading_session_summary_for_flag(
    flag: str,
    summaries: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not flag.startswith("TRADING_SESSION_"):
        return None
    if "PREVIEW" in flag:
        return summaries.get("trading_session_preview")
    return summaries.get("trading_session_execute")


def _summarize_after_close(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    steps = payload.get("steps")
    step_rows: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for row in steps:
            if not isinstance(row, dict):
                continue
            step_rows.append(
                {
                    "name": row.get("name"),
                    "outcome": row.get("outcome"),
                    "reason": row.get("reason"),
                    "exit_code": row.get("exit_code"),
                }
            )
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "write_mode": payload.get("write_mode"),
            "session_outcome": payload.get("session_outcome"),
            "session_reason": payload.get("session_reason"),
            "lock_acquired": payload.get("lock_acquired"),
            "lock_released": payload.get("lock_released"),
            "steps": step_rows,
        }
    )
    return summary


def _summarize_order_maintenance(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    result = payload.get("result")
    sync_result = None if not isinstance(result, dict) else result.get("sync_result")
    recovery_result = (
        None if not isinstance(result, dict) else result.get("execution_recovery_result")
    )
    stale_buy_cancel_result = (
        None if not isinstance(result, dict) else result.get("stale_buy_cancel_result")
    )
    stale_sell_cancel_result = (
        None if not isinstance(result, dict) else result.get("stale_sell_cancel_result")
    )
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
    manual_ids = (
        None
        if not isinstance(result, dict)
        else result.get("manual_recovery_required_client_order_ids")
    )
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "execute_mode": payload.get("execute_mode"),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
            "manual_recovery_required_count": (
                None if not isinstance(manual_ids, list) else len(manual_ids)
            ),
            "sync_candidate_count": (
                None
                if not isinstance(sync_result, dict)
                else sync_result.get("candidate_count")
            ),
            "sync_synced_count": (
                None
                if not isinstance(sync_result, dict)
                else sync_result.get("synced_count")
            ),
            "sync_execution_recovery_required_count": (
                None
                if not isinstance(sync_result, dict)
                else sync_result.get("execution_recovery_required_count")
            ),
            "recovery_preview_ready_count": (
                None
                if not isinstance(recovery_result, dict)
                else recovery_result.get("preview_ready_count")
            ),
            "recovery_recovered_count": (
                None
                if not isinstance(recovery_result, dict)
                else recovery_result.get("recovered_count")
            ),
            "recovery_manual_required_count": (
                None
                if not isinstance(recovery_result, dict)
                else recovery_result.get("manual_recovery_required_count")
            ),
            "buy_cancel_cancelled_count": (
                None
                if not isinstance(stale_buy_cancel_result, dict)
                else stale_buy_cancel_result.get("cancelled_count")
            ),
            "sell_cancel_cancelled_count": (
                None
                if not isinstance(stale_sell_cancel_result, dict)
                else stale_sell_cancel_result.get("cancelled_count")
            ),
            "buy_signal_cleanup_preview_ready_count": (
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("preview_ready_count")
            ),
            "buy_signal_cleanup_cleaned_count": (
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("cleaned_count")
            ),
            "buy_signal_cleanup_blocked_count": (
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("blocked_count")
            ),
            "sell_signal_cleanup_preview_ready_count": (
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("preview_ready_count")
            ),
            "sell_signal_cleanup_cleaned_count": (
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("cleaned_count")
            ),
            "sell_signal_cleanup_blocked_count": (
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("blocked_count")
            ),
            "stale_signal_preview_ready_count": _sum_optional_ints(
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("preview_ready_count"),
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("preview_ready_count"),
            ),
            "stale_signal_cleaned_count": _sum_optional_ints(
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("cleaned_count"),
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("cleaned_count"),
            ),
            "stale_signal_blocked_count": _sum_optional_ints(
                None
                if not isinstance(stale_buy_signal_cleanup_result, dict)
                else stale_buy_signal_cleanup_result.get("blocked_count"),
                None
                if not isinstance(stale_sell_signal_cleanup_result, dict)
                else stale_sell_signal_cleanup_result.get("blocked_count"),
            ),
            "stale_signal_symbol_hint": _collect_signal_cleanup_symbol_hint(
                stale_buy_signal_cleanup_result,
                stale_sell_signal_cleanup_result,
            ),
            "stale_signal_blocked_reason_codes": (
                _collect_signal_cleanup_reason_codes(
                    stale_buy_signal_cleanup_result,
                    stale_sell_signal_cleanup_result,
                )
            ),
        }
    )
    return summary


def _summarize_execute_buy_signals(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    result = payload.get("result")
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "execute_mode": payload.get("execute_mode"),
            "signal_limit": payload.get("signal_limit"),
            "stop_reason": payload.get("stop_reason"),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
            "candidate_count": (
                None if not isinstance(result, dict) else result.get("candidate_count")
            ),
            "preview_ready_count": (
                None
                if not isinstance(result, dict)
                else result.get("preview_ready_count")
            ),
            "blocked_count": (
                None if not isinstance(result, dict) else result.get("blocked_count")
            ),
            "submitted_count": (
                None if not isinstance(result, dict) else result.get("submitted_count")
            ),
            "acted_count": (
                None if not isinstance(result, dict) else result.get("acted_count")
            ),
        }
    )
    return summary


def _summarize_execute_sell_signals(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    result = payload.get("result")
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "execute_mode": payload.get("execute_mode"),
            "signal_limit": payload.get("signal_limit"),
            "stop_reason": payload.get("stop_reason"),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
            "candidate_count": (
                None if not isinstance(result, dict) else result.get("candidate_count")
            ),
            "preview_ready_count": (
                None
                if not isinstance(result, dict)
                else result.get("preview_ready_count")
            ),
            "blocked_count": (
                None if not isinstance(result, dict) else result.get("blocked_count")
            ),
            "submitted_count": (
                None if not isinstance(result, dict) else result.get("submitted_count")
            ),
            "acted_count": (
                None if not isinstance(result, dict) else result.get("acted_count")
            ),
        }
    )
    return summary


def _summarize_execution_recovery_review(
    *,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label="execution_recovery_review", path=path)
    if payload is None:
        return summary
    review_result = payload.get("review_result")
    recovery_result = (
        None if not isinstance(review_result, dict) else review_result.get("recovery_result")
    )
    draft_result = payload.get("draft_result")
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "review_item_count": (
                None
                if not isinstance(review_result, dict)
                else review_result.get("review_item_count")
            ),
            "manual_recovery_required_count": (
                None
                if not isinstance(recovery_result, dict)
                else recovery_result.get("manual_recovery_required_count")
            ),
            "auto_recoverable_count": (
                None
                if not isinstance(recovery_result, dict)
                else recovery_result.get("preview_ready_count")
            ),
            "draft_item_count": (
                None
                if not isinstance(draft_result, dict)
                else draft_result.get("exported_item_count")
            ),
        }
    )
    return summary


def _summarize_stale_signal_cleanup_review(
    *,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label="stale_signal_cleanup_review", path=path)
    if payload is None:
        return summary
    items = payload.get("items")
    summary.update(
        {
            "exists": True,
            "trade_date": payload.get("trade_date"),
            "source_label": payload.get("source_label"),
            "source_path": payload.get("source_path"),
            "source_file_name": _build_source_file_name(
                payload.get("source_path")
            ),
            "review_item_count": payload.get("review_item_count"),
            "blocked_item_count": payload.get("blocked_item_count"),
            "preview_ready_item_count": payload.get("preview_ready_item_count"),
            "cleaned_item_count": payload.get("cleaned_item_count"),
            "top_symbols": payload.get("top_symbols"),
            "preview_items": _build_stale_signal_cleanup_preview_items(items),
            "item_count": None if not isinstance(items, list) else len(items),
        }
    )
    return summary


def _summarize_execution_recovery_draft(
    *,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label="execution_recovery_draft", path=path)
    if payload is None:
        return summary
    items = payload.get("items")
    summary.update(
        {
            "exists": True,
            "source_trade_date": payload.get("source_trade_date"),
            "exported_item_count": payload.get("exported_item_count"),
            "item_count": None if not isinstance(items, list) else len(items),
        }
    )
    return summary


def _summarize_manual_import(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    result = payload.get("result")
    summary.update(
        {
            "exists": True,
            "input_path": payload.get("input_path"),
            "execute_mode": payload.get("execute_mode"),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
            "item_count": None if not isinstance(result, dict) else result.get("item_count"),
            "candidate_count": None if not isinstance(result, dict) else result.get("candidate_count"),
            "preview_ready_count": None if not isinstance(result, dict) else result.get("preview_ready_count"),
            "imported_count": None if not isinstance(result, dict) else result.get("imported_count"),
            "blocked_count": None if not isinstance(result, dict) else result.get("blocked_count"),
            "acted_count": None if not isinstance(result, dict) else result.get("acted_count"),
        }
    )
    return summary


def _summarize_kill_switch(
    *,
    label: str,
    path: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = _artifact_stub(label=label, path=path)
    if payload is None:
        return summary
    summary.update(
        {
            "exists": True,
            "action": payload.get("action"),
            "enabled": payload.get("enabled"),
            "note": payload.get("note"),
            "updated_at": payload.get("updated_at"),
        }
    )
    return summary


def _coerce_timing2_setup_readiness(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "trade_date": value.get("trade_date"),
        "required": value.get("required"),
        "setup_signal_count": value.get("setup_signal_count"),
        "ready": value.get("ready"),
        "reason": value.get("reason"),
    }


def _coerce_scan_settings(payload: dict[str, Any]) -> dict[str, Any]:
    scan_settings = payload.get("scan_settings")
    if not isinstance(scan_settings, dict):
        return {}
    return {
        "buy_strategy": scan_settings.get("buy_strategy"),
        "effective_buy_strategy": scan_settings.get("effective_buy_strategy"),
        "scan_timing1": scan_settings.get("scan_timing1"),
        "scan_timing2": scan_settings.get("scan_timing2"),
        "preopen_scan_timing2_setup": scan_settings.get(
            "preopen_scan_timing2_setup"
        ),
        "preopen_write_timing2_signals": scan_settings.get(
            "preopen_write_timing2_signals"
        ),
        "preopen_timing2_daily_count": scan_settings.get(
            "preopen_timing2_daily_count"
        ),
        "preopen_timing2_new_high_lookback_days": scan_settings.get(
            "preopen_timing2_new_high_lookback_days"
        ),
        "timing2_30s_min_samples_per_bar": scan_settings.get(
            "timing2_30s_min_samples_per_bar"
        ),
        "timing2_max_sample_symbols_per_cycle": scan_settings.get(
            "timing2_max_sample_symbols_per_cycle"
        ),
    }


def _rehearsal_timing2_30s_verified(payload: dict[str, Any]) -> bool | None:
    scan_settings = _coerce_scan_settings(payload)
    if not _scan_settings_request_timing2_validation(scan_settings):
        return None

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return False

    for step in steps:
        if not isinstance(step, dict) or step.get("name") != "Trading Session Preview":
            continue
        if step.get("outcome") != "COMPLETED":
            return False
        result = step.get("result")
        if not isinstance(result, dict):
            return False
        polling_result = result.get("polling_result")
        if not isinstance(polling_result, dict):
            return False
        cycles = polling_result.get("cycles")
        if not isinstance(cycles, list) or not cycles:
            return False
        first_cycle = cycles[0]
        if not isinstance(first_cycle, dict):
            return False
        return all(
            isinstance(first_cycle.get(step_name), dict)
            for step_name in TIMING2_30S_STEP_NAMES
        )
    return False


def _scan_settings_request_timing2_validation(scan_settings: dict[str, Any]) -> bool:
    return (
        scan_settings.get("scan_timing2") is True
        or scan_settings.get("buy_strategy") in ("timing2", "both")
    )


def _rehearsal_recency_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        _optional_text(row.get("finished_at"))
        or _optional_text(row.get("started_at"))
        or "",
        _optional_text(row.get("name")) or "",
    )


def _rehearsals_by_recency(rehearsals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rehearsals,
        key=_rehearsal_recency_sort_key,
        reverse=True,
    )


def _scan_rehearsals(ops_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not ops_dir.exists():
        return results
    for child in sorted(ops_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        summary_path = child / "rehearsal_summary.json"
        payload = _load_optional_json(summary_path)
        if payload is None:
            continue
        scan_settings = _coerce_scan_settings(payload)
        results.append(
            {
                "name": child.name,
                "path": str(summary_path),
                "trade_date": payload.get("trade_date"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "overall_outcome": payload.get("overall_outcome"),
                "overall_reason": payload.get("overall_reason"),
                "include_after_close": payload.get("include_after_close"),
                "scan_settings": scan_settings,
                "timing2_30s_verified": _rehearsal_timing2_30s_verified(
                    payload
                ),
            }
        )
    return _rehearsals_by_recency(results)


def _latest_kill_switch_state(summaries: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for key in ("kill_switch_status", "kill_switch_enable", "kill_switch_disable"):
        row = summaries.get(key)
        if row is None or not row.get("exists"):
            continue
        candidates.append(row)
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            _optional_text(row.get("updated_at")) or "",
            _optional_text(row.get("label")) or "",
        ),
        reverse=True,
    )
    return candidates[0]


def _collect_attention_flags(
    *,
    summaries: dict[str, dict[str, Any]],
    rehearsals: list[dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    latest_kill_switch = _latest_kill_switch_state(summaries)
    if latest_kill_switch is not None and latest_kill_switch.get("enabled") is True:
        flags.append("KILL_SWITCH_ENABLED")

    startup = summaries["startup_check"]
    if startup.get("exists") and startup.get("outcome") != "READY":
        if (
            _optional_text(startup.get("reconcile_reason_code"))
            == "OPEN_ENTRY_LOT_POSITION_MISMATCH"
        ):
            flags.append("STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH")
        else:
            flags.append("STARTUP_NOT_READY")

    for label in ("trading_session_preview", "trading_session_execute"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        outcome = _optional_text(row.get("session_outcome")) or "UNKNOWN"
        if outcome in ("PREOPEN_BLOCKED", "POLLING_BLOCKED", "POLLING_LOCK_BUSY"):
            flags.append(f"{label.upper()}_BLOCKED")
        elif outcome not in ("COMPLETED",):
            flags.append(f"{label.upper()}_FAILED")
        if row.get("timing2_setup_ready") is False:
            flags.append(f"{label.upper()}_TIMING2_SETUP_NOT_READY")
        intraday_bar_refresh_failed_count = _optional_non_negative_int(
            row.get("intraday_bar_refresh_failed_count")
        )
        if (
            intraday_bar_refresh_failed_count is not None
            and intraday_bar_refresh_failed_count > 0
        ):
            flags.append(f"{label.upper()}_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE")

    for label in ("execute_buy_signals_preview", "execute_buy_signals_execute"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        stop_reason = _optional_text(row.get("stop_reason"))
        error_type = _optional_text(row.get("error_type"))
        if (
            error_type is not None
            or (stop_reason is not None and stop_reason.startswith("FAILED:"))
        ):
            flags.append(f"{label.upper()}_FAILED")
        elif stop_reason is not None:
            flags.append(f"{label.upper()}_BLOCKED")

    for label in ("execute_sell_signals_preview", "execute_sell_signals_execute"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        stop_reason = _optional_text(row.get("stop_reason"))
        error_type = _optional_text(row.get("error_type"))
        if error_type is not None or (
            stop_reason is not None and stop_reason != "LOCK_BUSY"
        ):
            flags.append(f"{label.upper()}_FAILED")
        elif stop_reason == "LOCK_BUSY":
            flags.append(f"{label.upper()}_BLOCKED")

    for label in ("after_close_preview", "after_close_write"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        outcome = _optional_text(row.get("session_outcome")) or "UNKNOWN"
        if outcome == "LOCK_BUSY":
            flags.append(f"{label.upper()}_BLOCKED")
        elif outcome not in ("COMPLETED",):
            flags.append(f"{label.upper()}_FAILED")

    for label in ("order_maintenance_preview", "order_maintenance_execute"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        if _optional_text(row.get("error_type")):
            flags.append(f"{label.upper()}_FAILED")
        manual_count = row.get("manual_recovery_required_count")
        if isinstance(manual_count, int) and manual_count > 0:
            flags.append("MANUAL_RECOVERY_REQUIRED")
        stale_signal_blocked_count = row.get("stale_signal_blocked_count")
        if (
            isinstance(stale_signal_blocked_count, int)
            and stale_signal_blocked_count > 0
        ):
            flags.append("STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS")

    review = summaries["execution_recovery_review"]
    manual_recovery_required_count = review.get("manual_recovery_required_count")
    if review.get("exists") and isinstance(manual_recovery_required_count, int) and manual_recovery_required_count > 0:
        flags.append("EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS")

    for label in ("execution_recovery_import_preview", "execution_recovery_import_execute"):
        row = summaries[label]
        if not row.get("exists"):
            continue
        if _optional_text(row.get("error_type")):
            flags.append(f"{label.upper()}_FAILED")
        blocked_count = row.get("blocked_count")
        if isinstance(blocked_count, int) and blocked_count > 0:
            flags.append(f"{label.upper()}_HAS_BLOCKED_ITEMS")

    latest_rehearsals = _rehearsals_by_recency(rehearsals)
    if latest_rehearsals:
        latest_rehearsal = latest_rehearsals[0]
        outcome = _optional_text(latest_rehearsal.get("overall_outcome")) or "UNKNOWN"
        if outcome.endswith("_FAILED"):
            flags.append("REHEARSAL_FAILED")
        elif outcome.endswith("_BLOCKED"):
            flags.append("REHEARSAL_BLOCKED")
        else:
            scan_settings = latest_rehearsal.get("scan_settings")
            if (
                isinstance(scan_settings, dict)
                and _scan_settings_request_timing2_validation(scan_settings)
                and outcome == "COMPLETED"
                and latest_rehearsal.get("timing2_30s_verified") is not True
            ):
                flags.append("REHEARSAL_TIMING2_30S_NOT_VERIFIED")

    unique_flags: list[str] = []
    for flag in flags:
        if flag not in unique_flags:
            unique_flags.append(flag)
    return unique_flags


def _resolve_report_outcome(*, artifact_count: int, attention_flags: list[str]) -> str:
    if artifact_count == 0:
        return "NO_ARTIFACTS"
    failed_flags = [flag for flag in attention_flags if flag.endswith("_FAILED")]
    if failed_flags:
        return "FAILED"
    if attention_flags:
        return "ATTENTION"
    return "READY"


def _resolve_flag_severity(flag: str) -> str:
    if flag in CRITICAL_ATTENTION_FLAGS:
        return SEVERITY_CRITICAL
    return SEVERITY_WARNING


def _resolve_reference_path_for_flag(
    *,
    flag: str,
    summaries: dict[str, dict[str, Any]],
    rehearsals: list[dict[str, Any]],
) -> str | None:
    if flag == "KILL_SWITCH_ENABLED":
        latest_kill_switch = _latest_kill_switch_state(summaries)
        return None if latest_kill_switch is None else _optional_text(
            latest_kill_switch.get("path")
        )

    if flag in (
        "STARTUP_NOT_READY",
        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
    ):
        return _optional_text(summaries["startup_check"].get("path"))

    if flag.startswith("TRADING_SESSION_"):
        row = (
            summaries["trading_session_preview"]
            if "PREVIEW" in flag
            else summaries["trading_session_execute"]
        )
        return _optional_text(row.get("path"))

    if flag.startswith("EXECUTE_BUY_SIGNALS_"):
        row = (
            summaries["execute_buy_signals_preview"]
            if "PREVIEW" in flag
            else summaries["execute_buy_signals_execute"]
        )
        return _optional_text(row.get("path"))

    if flag.startswith("EXECUTE_SELL_SIGNALS_"):
        row = (
            summaries["execute_sell_signals_preview"]
            if "PREVIEW" in flag
            else summaries["execute_sell_signals_execute"]
        )
        return _optional_text(row.get("path"))

    if flag.startswith("AFTER_CLOSE_"):
        row = (
            summaries["after_close_preview"]
            if "PREVIEW" in flag
            else summaries["after_close_write"]
        )
        return _optional_text(row.get("path"))

    if flag.startswith("ORDER_MAINTENANCE_") or flag == "MANUAL_RECOVERY_REQUIRED":
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries[key]
            if row.get("exists"):
                return _optional_text(row.get("path"))
        return None

    if flag == "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS":
        review_row = summaries.get("stale_signal_cleanup_review")
        if isinstance(review_row, dict) and review_row.get("exists"):
            blocked_count = review_row.get("blocked_item_count")
            if isinstance(blocked_count, int) and blocked_count > 0:
                return _optional_text(review_row.get("path"))
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries[key]
            blocked_count = row.get("stale_signal_blocked_count")
            if row.get("exists") and isinstance(blocked_count, int) and blocked_count > 0:
                return _optional_text(row.get("path"))
        return None

    if flag == "EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS":
        return _optional_text(summaries["execution_recovery_review"].get("path"))

    if flag.startswith("EXECUTION_RECOVERY_IMPORT_"):
        row = (
            summaries["execution_recovery_import_preview"]
            if "PREVIEW" in flag
            else summaries["execution_recovery_import_execute"]
        )
        return _optional_text(row.get("path"))

    if flag.startswith("REHEARSAL_"):
        latest_rehearsals = _rehearsals_by_recency(rehearsals)
        if not latest_rehearsals:
            return None
        row = latest_rehearsals[0]
        outcome = _optional_text(row.get("overall_outcome")) or "UNKNOWN"
        if flag == "REHEARSAL_FAILED" and outcome.endswith("_FAILED"):
            return _optional_text(row.get("path"))
        if flag == "REHEARSAL_BLOCKED" and outcome.endswith("_BLOCKED"):
            return _optional_text(row.get("path"))
        if (
            flag == "REHEARSAL_TIMING2_30S_NOT_VERIFIED"
            and row.get("timing2_30s_verified") is False
        ):
            return _optional_text(row.get("path"))
        return None

    return None


def _build_attention_message(
    *,
    flag: str,
    summaries: dict[str, dict[str, Any]],
    rehearsals: list[dict[str, Any]],
) -> str | None:
    if flag == "KILL_SWITCH_ENABLED":
        latest_kill_switch = _latest_kill_switch_state(summaries)
        if latest_kill_switch is None:
            return "Kill switch is enabled."
        note = _optional_text(latest_kill_switch.get("note"))
        if note:
            return f"Kill switch is enabled. note={note}"
        return "Kill switch is enabled."

    if flag == "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH":
        return (
            _optional_text(summaries["startup_check"].get("reconcile_reason_message"))
            or _optional_text(summaries["startup_check"].get("reason"))
            or (
                "Startup reconciliation is blocked because open entry lots would "
                "change the local position state."
            )
        )

    if flag == "STARTUP_NOT_READY":
        reason = _optional_text(summaries["startup_check"].get("reason"))
        return reason or "Startup check did not finish with READY."

    if flag.startswith("TRADING_SESSION_"):
        row = _resolve_trading_session_summary_for_flag(flag, summaries) or {}
        if flag.endswith("_TIMING2_SETUP_NOT_READY"):
            if (
                row.get("timing2_setup_scan_outcome") == "SCANNED"
                and row.get("timing2_setup_scan_matched_count") == 0
            ):
                return _optional_text(row.get("timing2_setup_reason")) or (
                    "Timing2 setup scan completed, but no matching setup symbols were found for this trade date."
                )
            return _optional_text(row.get("timing2_setup_reason")) or (
                "Timing2 setup signals are missing for the trading session."
            )
        if flag.endswith("_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE"):
            reason = _optional_text(row.get("intraday_bar_refresh_reason"))
            if reason is not None:
                return reason
            failed_count = _optional_non_negative_int(
                row.get("intraday_bar_refresh_failed_count")
            )
            if failed_count is not None:
                return (
                    "Intraday 15-minute bar refresh reported failed symbols. "
                    f"failed_count={failed_count}"
                )
            return "Intraday 15-minute bar refresh reported failed symbols."
        return (
            _optional_text(row.get("session_reason"))
            or _optional_text(row.get("polling_stop_reason"))
            or _optional_text(row.get("session_outcome"))
        )

    if flag.startswith("EXECUTE_BUY_SIGNALS_"):
        row = (
            summaries["execute_buy_signals_preview"]
            if "PREVIEW" in flag
            else summaries["execute_buy_signals_execute"]
        )
        return (
            _optional_text(row.get("stop_reason"))
            or _optional_text(row.get("error_message"))
            or _optional_text(row.get("error_type"))
            or "Buy execution did not complete cleanly."
        )

    if flag.startswith("EXECUTE_SELL_SIGNALS_"):
        row = (
            summaries["execute_sell_signals_preview"]
            if "PREVIEW" in flag
            else summaries["execute_sell_signals_execute"]
        )
        return (
            _optional_text(row.get("stop_reason"))
            or _optional_text(row.get("error_message"))
            or _optional_text(row.get("error_type"))
            or "Sell execution did not complete cleanly."
        )

    if flag.startswith("AFTER_CLOSE_"):
        row = (
            summaries["after_close_preview"]
            if "PREVIEW" in flag
            else summaries["after_close_write"]
        )
        return (
            _optional_text(row.get("session_reason"))
            or _optional_text(row.get("session_outcome"))
        )

    if flag.startswith("ORDER_MAINTENANCE_"):
        row = (
            summaries["order_maintenance_preview"]
            if "PREVIEW" in flag
            else summaries["order_maintenance_execute"]
        )
        return _optional_text(row.get("error_message")) or _optional_text(
            row.get("error_type")
        )

    if flag == "MANUAL_RECOVERY_REQUIRED":
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries[key]
            count = row.get("manual_recovery_required_count")
            if isinstance(count, int) and count > 0:
                return f"Manual recovery required count={count}"
        return "Manual recovery is required."

    if flag == "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS":
        review_row = summaries.get("stale_signal_cleanup_review")
        if isinstance(review_row, dict) and review_row.get("exists"):
            blocked_count = review_row.get("blocked_item_count")
            if isinstance(blocked_count, int) and blocked_count > 0:
                top_symbols = _optional_text(review_row.get("top_symbols"))
                message = f"Stale signal cleanup blocked count={blocked_count}"
                if top_symbols is not None:
                    message += f" symbols={top_symbols}"
                return message
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries[key]
            blocked_count = row.get("stale_signal_blocked_count")
            if not (isinstance(blocked_count, int) and blocked_count > 0):
                continue
            reason_codes = _optional_text(row.get("stale_signal_blocked_reason_codes"))
            symbol_hint = _optional_text(row.get("stale_signal_symbol_hint"))
            message = f"Stale signal cleanup blocked count={blocked_count}"
            if reason_codes is not None:
                message += f" reason_codes={reason_codes}"
            if symbol_hint is not None:
                message += f" symbols={symbol_hint}"
            return message
        return "Stale signal cleanup has blocked items."

    if flag == "EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS":
        count = summaries["execution_recovery_review"].get(
            "manual_recovery_required_count"
        )
        if isinstance(count, int):
            return f"Execution recovery review has manual items count={count}"
        return "Execution recovery review has manual items."

    if flag.startswith("EXECUTION_RECOVERY_IMPORT_"):
        row = (
            summaries["execution_recovery_import_preview"]
            if "PREVIEW" in flag
            else summaries["execution_recovery_import_execute"]
        )
        blocked_count = row.get("blocked_count")
        if isinstance(blocked_count, int) and blocked_count > 0:
            return f"Manual import blocked count={blocked_count}"
        return _optional_text(row.get("error_message")) or _optional_text(
            row.get("error_type")
        )

    if flag.startswith("REHEARSAL_"):
        for row in rehearsals:
            outcome = _optional_text(row.get("overall_outcome")) or "UNKNOWN"
            if flag == "REHEARSAL_FAILED" and outcome.endswith("_FAILED"):
                return _optional_text(row.get("overall_reason")) or outcome
            if flag == "REHEARSAL_BLOCKED" and outcome.endswith("_BLOCKED"):
                return _optional_text(row.get("overall_reason")) or outcome
            if (
                flag == "REHEARSAL_TIMING2_30S_NOT_VERIFIED"
                and row.get("timing2_30s_verified") is False
            ):
                return "Timing2 was requested, but 30-second steps were not verified."
        return None

    return None


def _build_flag_details(
    *,
    attention_flags: list[str],
    summaries: dict[str, dict[str, Any]],
    rehearsals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "flag": flag,
            "severity": _resolve_flag_severity(flag),
            "message": _build_attention_message(
                flag=flag,
                summaries=summaries,
                rehearsals=rehearsals,
            ),
        }
        for flag in attention_flags
    ]


def _build_action_item(
    *,
    action_code: str,
    severity: str,
    flag: str,
    summary: str,
    detail: str | None,
    reference_path: str | None,
    suggested_command: str | None,
) -> dict[str, Any]:
    return {
        "action_code": action_code,
        "severity": severity,
        "flag": flag,
        "summary": summary,
        "detail": detail,
        "reference_path": reference_path,
        "suggested_command": suggested_command,
    }


def _build_action_item_for_flag(
    *,
    flag: str,
    trade_date: str,
    severity: str,
    message: str | None,
    reference_path: str | None,
    summaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if flag == "KILL_SWITCH_ENABLED":
        return _build_action_item(
            action_code="REVIEW_KILL_SWITCH",
            severity=severity,
            flag=flag,
            summary="Kill switch 상태와 note를 먼저 확인하고 자동 실행 재개를 멈추세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\set_kill_switch.py "
                f"--output .\\data\\ops\\{trade_date}\\kill_switch.status.json"
            ),
        )

    if flag == "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH":
        return _build_action_item(
            action_code="REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
            severity=severity,
            flag=flag,
            summary=(
                "Startup was blocked because reconcile would change symbols "
                "that still have open entry lots. Review executions and lot "
                "state before rerunning startup."
            ),
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\startup_check.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\startup_check.json"
            ),
        )

    if flag == "STARTUP_NOT_READY":
        return _build_action_item(
            action_code="REVIEW_STARTUP_CHECK",
            severity=severity,
            flag=flag,
            summary="Startup 차단 원인을 먼저 해결하고 장중 실행은 보류하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\startup_check.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\startup_check.json"
            ),
        )

    if flag in (
        "TRADING_SESSION_PREVIEW_BLOCKED",
        "TRADING_SESSION_EXECUTE_BLOCKED",
    ):
        return _build_action_item(
            action_code="REVIEW_TRADING_SESSION_BLOCK",
            severity=severity,
            flag=flag,
            summary="Trading session 차단 사유를 확인하고 같은 execute 재실행은 잠시 보류하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in (
        "TRADING_SESSION_PREVIEW_FAILED",
        "TRADING_SESSION_EXECUTE_FAILED",
    ):
        return _build_action_item(
            action_code="REVIEW_TRADING_SESSION_FAILURE",
            severity=severity,
            flag=flag,
            summary="Trading session 실패 원인을 먼저 정리하고 execute 재실행은 멈추세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in (
        "TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY",
        "TRADING_SESSION_EXECUTE_TIMING2_SETUP_NOT_READY",
    ):
        trading_session_summary = _resolve_trading_session_summary_for_flag(
            flag,
            summaries,
        ) or {}
        if (
            trading_session_summary.get("timing2_setup_scan_outcome") == "SCANNED"
            and trading_session_summary.get("timing2_setup_scan_matched_count") == 0
        ):
            return _build_action_item(
                action_code="REVIEW_TIMING2_SETUP_ZERO_MATCH",
                severity=severity,
                flag=flag,
                summary=(
                    "Timing2 setup scan ran normally, but no matching symbols were found "
                    "for this trade date. Timing2 buys stayed disabled."
                ),
                detail=message,
                reference_path=reference_path,
                suggested_command=None,
            )
        return _build_action_item(
            action_code="RERUN_TRADING_SESSION_WITH_TIMING2_SETUP",
            severity=severity,
            flag=flag,
            summary="Timing2 setup signals are missing. Rerun the session with Timing2 preopen setup/write enabled before relying on Timing2 buys.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\run_trading_session.py "
                f"--trade-date {trade_date} --use-db-master "
                f"--preopen-scan-timing2-setup --preopen-write-timing2-signals "
                f"--buy-strategy timing2 --per-order-budget 1000000 "
                f"--max-holdings 3 "
                f"--output .\\data\\ops\\{trade_date}\\run_trading_session.preview.json"
            ),
        )

    if flag in (
        "TRADING_SESSION_PREVIEW_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE",
        "TRADING_SESSION_EXECUTE_INTRADAY_BAR_REFRESH_PARTIAL_FAILURE",
    ):
        return _build_action_item(
            action_code="REVIEW_INTRADAY_BAR_REFRESH_FAILURE",
            severity=severity,
            flag=flag,
            summary=(
                "One or more symbols failed the 15-minute bar refresh during the "
                "trading session. Review quote access and refresh coverage before "
                "trusting scan completeness."
            ),
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in (
        "EXECUTE_BUY_SIGNALS_PREVIEW_BLOCKED",
        "EXECUTE_BUY_SIGNALS_EXECUTE_BLOCKED",
    ):
        return _build_action_item(
            action_code="REVIEW_BUY_EXECUTION_BLOCK",
            severity=severity,
            flag=flag,
            summary="Buy execution direct run was blocked. Check the stop reason before retrying another buy pass.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in (
        "EXECUTE_BUY_SIGNALS_PREVIEW_FAILED",
        "EXECUTE_BUY_SIGNALS_EXECUTE_FAILED",
    ):
        return _build_action_item(
            action_code="REVIEW_BUY_EXECUTION_FAILURE",
            severity=severity,
            flag=flag,
            summary="Buy execution direct run failed. Review the JSON error and rerun preview before using execute again.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\execute_buy_signals.py "
                f"--trade-date {trade_date} --per-order-budget 1000000 "
                f"--max-holdings 3 "
                f"--output .\\data\\ops\\{trade_date}\\execute_buy_signals.preview.json"
            ),
        )

    if flag in (
        "EXECUTE_SELL_SIGNALS_PREVIEW_BLOCKED",
        "EXECUTE_SELL_SIGNALS_EXECUTE_BLOCKED",
    ):
        return _build_action_item(
            action_code="REVIEW_SELL_EXECUTION_BLOCK",
            severity=severity,
            flag=flag,
            summary="Sell execution direct run was blocked. Check the stop reason before retrying another sell pass.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in (
        "EXECUTE_SELL_SIGNALS_PREVIEW_FAILED",
        "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
    ):
        return _build_action_item(
            action_code="REVIEW_SELL_EXECUTION_FAILURE",
            severity=severity,
            flag=flag,
            summary="Sell execution direct run failed. Review the JSON error and rerun preview before using execute again.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\execute_sell_signals.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\execute_sell_signals.preview.json"
            ),
        )

    if flag in (
        "AFTER_CLOSE_PREVIEW_FAILED",
        "AFTER_CLOSE_WRITE_FAILED",
        "AFTER_CLOSE_PREVIEW_BLOCKED",
        "AFTER_CLOSE_WRITE_BLOCKED",
    ):
        return _build_action_item(
            action_code="RERUN_AFTER_CLOSE_PREVIEW",
            severity=severity,
            flag=flag,
            summary="After-close 결과를 다시 점검하고 preview부터 재확인하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\run_after_close_session.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\after_close.preview.json"
            ),
        )

    if flag in (
        "ORDER_MAINTENANCE_PREVIEW_FAILED",
        "ORDER_MAINTENANCE_EXECUTE_FAILED",
    ):
        return _build_action_item(
            action_code="RERUN_ORDER_MAINTENANCE_PREVIEW",
            severity=severity,
            flag=flag,
            summary="Order maintenance 실패 원인을 보고 preview부터 다시 실행하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\run_order_maintenance.py "
                f"--trade-date {trade_date} --timeout-seconds 300 "
                f"--output .\\data\\ops\\{trade_date}\\order_maintenance.preview.json"
            ),
        )

    if flag in (
        "MANUAL_RECOVERY_REQUIRED",
        "EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS",
    ):
        return _build_action_item(
            action_code="REVIEW_EXECUTION_RECOVERY",
            severity=severity,
            flag=flag,
            summary="수동 체결 복구 후보를 검토하고 review/draft를 다시 만드세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\run_execution_recovery_workflow.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\execution_recovery.review.json "
                f"--draft-output .\\data\\ops\\{trade_date}\\execution_recovery.draft.json"
            ),
        )

    if flag == "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS":
        return _build_action_item(
            action_code="REVIEW_STALE_SIGNAL_CLEANUP",
            severity=severity,
            flag=flag,
            summary=(
                "Blocked stale signal cleanup items need review before relying on "
                "automatic stale-signal maintenance."
            ),
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe "
                f"scripts\\show_stale_signal_cleanup_review.py "
                f"--trade-date {trade_date} "
                f"--output .\\data\\ops\\{trade_date}\\stale_signal_cleanup.review.json"
            ),
        )

    if flag in (
        "EXECUTION_RECOVERY_IMPORT_PREVIEW_FAILED",
        "EXECUTION_RECOVERY_IMPORT_EXECUTE_FAILED",
        "EXECUTION_RECOVERY_IMPORT_PREVIEW_HAS_BLOCKED_ITEMS",
        "EXECUTION_RECOVERY_IMPORT_EXECUTE_HAS_BLOCKED_ITEMS",
    ):
        return _build_action_item(
            action_code="REVIEW_MANUAL_IMPORT",
            severity=severity,
            flag=flag,
            summary="수동 import 입력 파일과 preview 결과를 다시 대조하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag in ("REHEARSAL_BLOCKED", "REHEARSAL_FAILED"):
        return _build_action_item(
            action_code="REVIEW_REHEARSAL",
            severity=severity,
            flag=flag,
            summary="Mock 리허설 결과를 다시 확인하고 절차가 막힌 위치를 정리하세요.",
            detail=message,
            reference_path=reference_path,
            suggested_command=None,
        )

    if flag == "REHEARSAL_TIMING2_30S_NOT_VERIFIED":
        return _build_action_item(
            action_code="RERUN_TIMING2_REHEARSAL",
            severity=severity,
            flag=flag,
            summary="Rerun mock rehearsal with --scan-timing2 and confirm the 30-second pipeline appears in polling cycles.",
            detail=message,
            reference_path=reference_path,
            suggested_command=(
                f".\\venv\\Scripts\\python.exe scripts\\run_mock_operational_rehearsal.py "
                f"--trade-date {trade_date} --use-db-master "
                f"--preopen-scan-timing2-setup --preopen-write-timing2-signals "
                f"--buy-strategy timing2 "
                f"--per-order-budget 1000000 --max-holdings 3 "
                f"--output-dir .\\data\\ops\\{trade_date}\\rehearsal_timing2"
            ),
        )

    return _build_action_item(
        action_code="REVIEW_DAILY_OPS_REPORT",
        severity=severity,
        flag=flag,
        summary="Daily ops report와 원본 산출물을 다시 확인하세요.",
        detail=message,
        reference_path=reference_path,
        suggested_command=None,
    )


def _build_action_items(
    *,
    trade_date: str,
    flag_details: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    rehearsals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in flag_details:
        flag = _optional_text(row.get("flag"))
        severity = _optional_text(row.get("severity"))
        if flag is None or severity is None:
            continue
        key = (flag, severity)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            _build_action_item_for_flag(
                flag=flag,
                trade_date=trade_date,
                severity=severity,
                message=_optional_text(row.get("message")),
                reference_path=_resolve_reference_path_for_flag(
                    flag=flag,
                    summaries=summaries,
                    rehearsals=rehearsals,
                ),
                summaries=summaries,
            )
        )
    return items


def _resolve_health_outcome(
    *,
    artifact_count: int,
    flag_details: list[dict[str, Any]],
) -> str:
    if artifact_count == 0:
        return "NO_ARTIFACTS"
    severities = {
        _optional_text(row.get("severity")) or SEVERITY_WARNING for row in flag_details
    }
    if SEVERITY_CRITICAL in severities:
        return "CRITICAL"
    if SEVERITY_WARNING in severities:
        return "WARNING"
    return "READY"


def _resolve_highest_severity(flag_details: list[dict[str, Any]]) -> str:
    severities = {
        _optional_text(row.get("severity")) or SEVERITY_WARNING for row in flag_details
    }
    if SEVERITY_CRITICAL in severities:
        return SEVERITY_CRITICAL
    if SEVERITY_WARNING in severities:
        return SEVERITY_WARNING
    return "NONE"


def _resolve_summary_key_for_flag(
    *,
    flag: str,
    summaries: dict[str, dict[str, Any]],
) -> str | None:
    if flag == "KILL_SWITCH_ENABLED":
        latest_kill_switch = _latest_kill_switch_state(summaries)
        return None if latest_kill_switch is None else _optional_text(
            latest_kill_switch.get("label")
        )

    if flag in (
        "STARTUP_NOT_READY",
        "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
    ):
        return "startup_check"

    if flag.startswith("TRADING_SESSION_"):
        return (
            "trading_session_preview"
            if "PREVIEW" in flag
            else "trading_session_execute"
        )

    if flag.startswith("EXECUTE_BUY_SIGNALS_"):
        return (
            "execute_buy_signals_preview"
            if "PREVIEW" in flag
            else "execute_buy_signals_execute"
        )

    if flag.startswith("EXECUTE_SELL_SIGNALS_"):
        return (
            "execute_sell_signals_preview"
            if "PREVIEW" in flag
            else "execute_sell_signals_execute"
        )

    if flag.startswith("AFTER_CLOSE_"):
        return "after_close_preview" if "PREVIEW" in flag else "after_close_write"

    if flag.startswith("ORDER_MAINTENANCE_"):
        return (
            "order_maintenance_preview"
            if "PREVIEW" in flag
            else "order_maintenance_execute"
        )

    if flag == "MANUAL_RECOVERY_REQUIRED":
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries.get(key)
            if isinstance(row, dict) and row.get("exists"):
                return key
        return None

    if flag == "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS":
        review_row = summaries.get("stale_signal_cleanup_review")
        if isinstance(review_row, dict) and review_row.get("exists"):
            blocked_count = review_row.get("blocked_item_count")
            if isinstance(blocked_count, int) and blocked_count > 0:
                return "stale_signal_cleanup_review"
        for key in ("order_maintenance_execute", "order_maintenance_preview"):
            row = summaries.get(key)
            if not isinstance(row, dict) or not row.get("exists"):
                continue
            blocked_count = row.get("stale_signal_blocked_count")
            if isinstance(blocked_count, int) and blocked_count > 0:
                return key
        return None

    if flag == "EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS":
        return "execution_recovery_review"

    if flag.startswith("EXECUTION_RECOVERY_IMPORT_"):
        return (
            "execution_recovery_import_preview"
            if "PREVIEW" in flag
            else "execution_recovery_import_execute"
        )

    return None


def _rehearsal_matches_flag(flag: str, rehearsal: dict[str, Any]) -> bool:
    outcome = _optional_text(rehearsal.get("overall_outcome")) or "UNKNOWN"
    if flag == "REHEARSAL_FAILED":
        return outcome.endswith("_FAILED")
    if flag == "REHEARSAL_BLOCKED":
        return outcome.endswith("_BLOCKED")
    if flag == "REHEARSAL_TIMING2_30S_NOT_VERIFIED":
        scan_settings = rehearsal.get("scan_settings")
        return (
            isinstance(scan_settings, dict)
            and _scan_settings_request_timing2_validation(scan_settings)
            and outcome == "COMPLETED"
            and rehearsal.get("timing2_30s_verified") is False
        )
    return False


def _resolve_row_status_level(
    *,
    exists: bool,
    highest_severity: str,
) -> str:
    if not exists:
        return "MISSING"
    if highest_severity == SEVERITY_CRITICAL:
        return "CRITICAL"
    if highest_severity == SEVERITY_WARNING:
        return "WARNING"
    return "READY"


def _annotate_summaries_with_status(
    *,
    summaries: dict[str, dict[str, Any]],
    flag_details: list[dict[str, Any]],
) -> None:
    flags_by_summary: dict[str, list[dict[str, Any]]] = {}
    for row in flag_details:
        flag = _optional_text(row.get("flag"))
        if flag is None:
            continue
        summary_key = _resolve_summary_key_for_flag(flag=flag, summaries=summaries)
        if summary_key is None:
            continue
        flags_by_summary.setdefault(summary_key, []).append(row)

    for key, summary in summaries.items():
        row_flags = flags_by_summary.get(key, [])
        highest_severity = _resolve_highest_severity(row_flags)
        summary["attention_flags"] = [
            str(row.get("flag"))
            for row in row_flags
            if _optional_text(row.get("flag")) is not None
        ]
        summary["highest_severity"] = highest_severity
        summary["status_level"] = _resolve_row_status_level(
            exists=bool(summary.get("exists")),
            highest_severity=highest_severity,
        )


def _annotate_rehearsals_with_status(
    *,
    rehearsals: list[dict[str, Any]],
    flag_details: list[dict[str, Any]],
) -> None:
    for rehearsal in rehearsals:
        row_flags = [
            row
            for row in flag_details
            if _rehearsal_matches_flag(
                _optional_text(row.get("flag")) or "",
                rehearsal,
            )
        ]
        highest_severity = _resolve_highest_severity(row_flags)
        rehearsal["attention_flags"] = [
            str(row.get("flag"))
            for row in row_flags
            if _optional_text(row.get("flag")) is not None
        ]
        rehearsal["highest_severity"] = highest_severity
        rehearsal["status_level"] = _resolve_row_status_level(
            exists=True,
            highest_severity=highest_severity,
        )


def _build_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "ready": 0,
        "warning": 0,
        "critical": 0,
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("exists") is False:
            continue
        status_level = _optional_text(row.get("status_level")) or "READY"
        if status_level == "CRITICAL":
            counts["critical"] += 1
        elif status_level == "WARNING":
            counts["warning"] += 1
        else:
            counts["ready"] += 1
    return counts


def _severity_rank(severity: str | None) -> int:
    if severity == SEVERITY_CRITICAL:
        return 0
    if severity == SEVERITY_WARNING:
        return 1
    return 2


def _build_alert_line_from_action_item(row: dict[str, Any]) -> str:
    severity = _optional_text(row.get("severity")) or "INFO"
    summary = _optional_text(row.get("summary")) or "Review required."
    detail = _optional_text(row.get("detail"))
    if detail is None:
        return f"{severity}: {summary}"
    return f"{severity}: {summary} ({detail})"


def _build_alert_payload(
    *,
    trade_date: str,
    artifact_count: int,
    report_outcome: str,
    health_outcome: str,
    highest_severity: str,
    attention_flags: list[str],
    flag_details: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    latest_kill_switch: dict[str, Any] | None,
) -> dict[str, Any]:
    critical_count = sum(
        1
        for row in flag_details
        if (_optional_text(row.get("severity")) or "") == SEVERITY_CRITICAL
    )
    warning_count = sum(
        1
        for row in flag_details
        if (_optional_text(row.get("severity")) or "") == SEVERITY_WARNING
    )
    title = f"[{health_outcome}] Daily ops {trade_date}"
    lines: list[str] = []

    if health_outcome == "NO_ARTIFACTS":
        summary = f"No ops artifacts found for {trade_date}."
        lines.append("No ops artifacts were found for this trade date.")
        lines.append("Run startup or session commands before checking alerts again.")
    elif health_outcome == "READY":
        summary = f"Daily ops looks ready across {artifact_count} artifacts."
        lines.append("No attention flags detected.")
        lines.append(
            f"report_outcome={report_outcome}, highest_severity={highest_severity}, artifacts={artifact_count}"
        )
    else:
        summary = (
            f"{len(attention_flags)} attention flags detected "
            f"({critical_count} critical, {warning_count} warning)."
        )
        lines.append(summary)
        if latest_kill_switch is not None and latest_kill_switch.get("enabled") is True:
            note = _optional_text(latest_kill_switch.get("note"))
            if note is None:
                lines.append("Kill switch is enabled.")
            else:
                lines.append(f"Kill switch is enabled. note={note}")
        sorted_actions = sorted(
            action_items,
            key=lambda row: (
                _severity_rank(_optional_text(row.get("severity"))),
                _optional_text(row.get("action_code")) or "",
            ),
        )
        for row in sorted_actions[:DEFAULT_ALERT_ACTION_LIMIT]:
            lines.append(_build_alert_line_from_action_item(row))

    return {
        "level": health_outcome,
        "title": title,
        "summary": summary,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "action_count": len(action_items),
        "lines": lines,
        "text": "\n".join([title, *lines]),
    }


def _print_artifact_summary(row: dict[str, Any]) -> None:
    _section(str(row.get("label")))
    _ok("exists", str(row.get("exists")))
    if not row.get("exists"):
        return
    for key in (
        "status_level",
        "highest_severity",
        "outcome",
        "reason",
        "reconcile_reason_code",
        "reconcile_reason_message",
        "reconcile_changed_rows",
        "session_outcome",
        "session_reason",
        "stop_reason",
        "manual_recovery_required_count",
        "preview_ready_count",
        "submitted_count",
        "imported_count",
        "blocked_count",
        "enabled",
        "updated_at",
    ):
        if key in row:
            value = row.get(key)
            _ok(key, "" if value is None else str(value))
    attention_flags = row.get("attention_flags")
    if isinstance(attention_flags, list) and attention_flags:
        _warn("attention_flags", ", ".join(str(flag) for flag in attention_flags))


def main() -> int:
    args = _parse_args()

    try:
        ops_dir = _resolve_ops_dir(args)
        output_path = _resolve_path(args.output) if args.output else None
        alert_output_path = (
            _resolve_path(args.alert_output) if args.alert_output else None
        )
    except Exception as exc:
        _fail("path", f"{type(exc).__name__}: {exc}")
        return 5

    _section("Daily Ops Report")
    _ok("trade_date", args.trade_date)
    _ok("ops_dir", str(ops_dir))

    if not ops_dir.exists():
        _warn("ops_dir", "No ops directory exists for this date.")
        return 4

    try:
        summaries = {
            "startup_check": _summarize_startup(
                path=ops_dir / KNOWN_ARTIFACT_FILES["startup_check"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["startup_check"]),
            ),
            "trading_session_preview": _summarize_trading_session(
                label="trading_session_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["trading_session_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["trading_session_preview"]),
            ),
            "trading_session_execute": _summarize_trading_session(
                label="trading_session_execute",
                path=ops_dir / KNOWN_ARTIFACT_FILES["trading_session_execute"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["trading_session_execute"]),
            ),
            "execute_buy_signals_preview": _summarize_execute_buy_signals(
                label="execute_buy_signals_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execute_buy_signals_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execute_buy_signals_preview"]),
            ),
            "execute_buy_signals_execute": _summarize_execute_buy_signals(
                label="execute_buy_signals_execute",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execute_buy_signals_execute"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execute_buy_signals_execute"]),
            ),
            "execute_sell_signals_preview": _summarize_execute_sell_signals(
                label="execute_sell_signals_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execute_sell_signals_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execute_sell_signals_preview"]),
            ),
            "execute_sell_signals_execute": _summarize_execute_sell_signals(
                label="execute_sell_signals_execute",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execute_sell_signals_execute"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execute_sell_signals_execute"]),
            ),
            "after_close_preview": _summarize_after_close(
                label="after_close_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["after_close_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["after_close_preview"]),
            ),
            "after_close_write": _summarize_after_close(
                label="after_close_write",
                path=ops_dir / KNOWN_ARTIFACT_FILES["after_close_write"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["after_close_write"]),
            ),
            "order_maintenance_preview": _summarize_order_maintenance(
                label="order_maintenance_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["order_maintenance_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["order_maintenance_preview"]),
            ),
            "order_maintenance_execute": _summarize_order_maintenance(
                label="order_maintenance_execute",
                path=ops_dir / KNOWN_ARTIFACT_FILES["order_maintenance_execute"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["order_maintenance_execute"]),
            ),
            "stale_signal_cleanup_review": _summarize_stale_signal_cleanup_review(
                path=ops_dir / KNOWN_ARTIFACT_FILES["stale_signal_cleanup_review"],
                payload=_load_optional_json(
                    ops_dir / KNOWN_ARTIFACT_FILES["stale_signal_cleanup_review"]
                ),
            ),
            "execution_recovery_review": _summarize_execution_recovery_review(
                path=ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_review"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_review"]),
            ),
            "execution_recovery_draft": _summarize_execution_recovery_draft(
                path=ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_draft"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_draft"]),
            ),
            "execution_recovery_import_preview": _summarize_manual_import(
                label="execution_recovery_import_preview",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_import_preview"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_import_preview"]),
            ),
            "execution_recovery_import_execute": _summarize_manual_import(
                label="execution_recovery_import_execute",
                path=ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_import_execute"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["execution_recovery_import_execute"]),
            ),
            "kill_switch_status": _summarize_kill_switch(
                label="kill_switch_status",
                path=ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_status"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_status"]),
            ),
            "kill_switch_enable": _summarize_kill_switch(
                label="kill_switch_enable",
                path=ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_enable"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_enable"]),
            ),
            "kill_switch_disable": _summarize_kill_switch(
                label="kill_switch_disable",
                path=ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_disable"],
                payload=_load_optional_json(ops_dir / KNOWN_ARTIFACT_FILES["kill_switch_disable"]),
            ),
        }
        rehearsals = _scan_rehearsals(ops_dir)
    except Exception as exc:
        _fail("report", f"{type(exc).__name__}: {exc}")
        return 5

    artifact_count = sum(1 for row in summaries.values() if row.get("exists")) + len(rehearsals)
    attention_flags = _collect_attention_flags(
        summaries=summaries,
        rehearsals=rehearsals,
    )
    flag_details = _build_flag_details(
        attention_flags=attention_flags,
        summaries=summaries,
        rehearsals=rehearsals,
    )
    _annotate_summaries_with_status(
        summaries=summaries,
        flag_details=flag_details,
    )
    _annotate_rehearsals_with_status(
        rehearsals=rehearsals,
        flag_details=flag_details,
    )
    action_items = _build_action_items(
        trade_date=args.trade_date,
        flag_details=flag_details,
        summaries=summaries,
        rehearsals=rehearsals,
    )
    report_outcome = _resolve_report_outcome(
        artifact_count=artifact_count,
        attention_flags=attention_flags,
    )
    health_outcome = _resolve_health_outcome(
        artifact_count=artifact_count,
        flag_details=flag_details,
    )
    highest_severity = _resolve_highest_severity(flag_details)
    latest_kill_switch = _latest_kill_switch_state(summaries)
    alert = _build_alert_payload(
        trade_date=args.trade_date,
        artifact_count=artifact_count,
        report_outcome=report_outcome,
        health_outcome=health_outcome,
        highest_severity=highest_severity,
        attention_flags=attention_flags,
        flag_details=flag_details,
        action_items=action_items,
        latest_kill_switch=latest_kill_switch,
    )

    payload = {
        "trade_date": args.trade_date,
        "ops_dir": str(ops_dir),
        "artifact_count": artifact_count,
        "artifact_status_counts": _build_status_counts(list(summaries.values())),
        "rehearsal_status_counts": _build_status_counts(rehearsals),
        "report_outcome": report_outcome,
        "health_outcome": health_outcome,
        "highest_severity": highest_severity,
        "attention_flags": attention_flags,
        "flag_details": flag_details,
        "action_items": action_items,
        "alert": alert,
        "latest_kill_switch": latest_kill_switch,
        "artifacts": summaries,
        "rehearsals": rehearsals,
        "strict": args.strict,
    }

    _ok("artifact_count", str(artifact_count))
    _ok("report_outcome", report_outcome)
    _ok("health_outcome", health_outcome)
    _ok("highest_severity", highest_severity)
    _section("Alert")
    _ok("title", str(alert["title"]))
    _ok("summary", str(alert["summary"]))
    for line in alert["lines"]:
        if health_outcome == "READY":
            _ok("line", line)
        else:
            _warn("line", line)
    if attention_flags:
        _warn("attention_flags", ", ".join(attention_flags))
    for row in flag_details:
        message = _optional_text(row.get("message"))
        _warn(
            f"flag:{row['flag']}",
            (
                str(row["severity"])
                if message is None
                else f"{row['severity']} | {message}"
            ),
        )
    for item in action_items:
        detail = _optional_text(item.get("detail"))
        _warn(
            f"action:{item['action_code']}",
            (
                str(item["severity"])
                if detail is None
                else f"{item['severity']} | {item['summary']} | {detail}"
            ),
        )

    for row in summaries.values():
        if row.get("exists"):
            _print_artifact_summary(row)

    if rehearsals:
        _section("rehearsals")
        for row in rehearsals:
            print(
                f"{row['name']} status={row.get('status_level')} "
                f"outcome={row['overall_outcome']} "
                f"include_after_close={row['include_after_close']} "
                f"reason={'' if row['overall_reason'] is None else row['overall_reason']}"
            )
            attention_flags = row.get("attention_flags")
            if isinstance(attention_flags, list) and attention_flags:
                _warn(
                    f"{row['name']}.attention_flags",
                    ", ".join(str(flag) for flag in attention_flags),
                )

    if output_path is not None:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))
    if alert_output_path is not None:
        _save_text(alert_output_path, f"{alert['text']}\n")
        _ok("alert_saved", str(alert_output_path))

    if artifact_count == 0:
        return 4
    if args.strict:
        if health_outcome == "CRITICAL":
            return 5
        if health_outcome == "WARNING":
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
