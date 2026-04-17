"""
Prepare market master and first-stage universe in one run.

Flow:
1. Refresh market master snapshot from JSON/CSV, or load the current DB snapshot
2. Build raw universe inputs from KIS daily candles
3. Apply first-stage filter
4. Optionally save the universe snapshot
"""

from __future__ import annotations

import argparse
import enum
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.kis import KisBroker
from config.loader import load_settings
from logger import setup_logging
from market import (
    SUPPORTED_UNIVERSE_MASTER_FORMATS,
    UniverseMasterItem,
    load_universe_master_items,
    resolve_universe_master_format,
)
from services import (
    MarketMasterQueryService,
    MarketMasterValidationResult,
    MarketMasterValidationService,
    MarketMasterRefreshItem,
    PreopenReadinessOutcome,
    PreopenReadinessService,
    Timing1SetupScanResult,
    Timing1SetupScanService,
    Timing2SetupScanResult,
    Timing2SetupScanService,
    UniverseBuildOutcome,
    UniverseFilterSettings,
)
from storage.db import get_connection
from storage.migrations.runner import run_migrations
from storage.repositories import (
    MarketMasterRepository,
    MarketMasterRow,
    OrderRepository,
    PositionRepository,
    SignalRepository,
    UniverseCandidateRepository,
)
from strategy import Timing1SetupSettings, Timing2SetupSettings

KST = pytz.timezone("Asia/Seoul")


class Timing1SetupScanOutcome(str, enum.Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    SKIPPED = "SKIPPED"
    SCANNED = "SCANNED"


@dataclass(frozen=True)
class Timing1SetupScanExecutionResult:
    outcome: Timing1SetupScanOutcome
    reason: str | None
    scan_result: Timing1SetupScanResult | None


class Timing2SetupScanOutcome(str, enum.Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    SKIPPED = "SKIPPED"
    SCANNED = "SCANNED"


@dataclass(frozen=True)
class Timing2SetupScanExecutionResult:
    outcome: Timing2SetupScanOutcome
    reason: str | None
    scan_result: Timing2SetupScanResult | None


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
        description="Refresh market master and prepare pre-open universe."
    )
    parser.add_argument(
        "--master-input",
        help="Path to JSON or CSV market master items.",
    )
    parser.add_argument(
        "--master-format",
        default="auto",
        choices=SUPPORTED_UNIVERSE_MASTER_FORMATS,
        help="Market master input format. Default: auto",
    )
    parser.add_argument(
        "--use-db-master",
        action="store_true",
        help="Use the current market master snapshot already stored in SQLite.",
    )
    parser.add_argument(
        "--require-same-day-master",
        action="store_true",
        help="Block the run if market master refreshed_at date does not match trade_date.",
    )
    parser.add_argument(
        "--min-master-count",
        type=int,
        default=None,
        help="Optional minimum allowed symbol count for market master.",
    )
    parser.add_argument(
        "--required-market",
        action="append",
        default=[],
        help="Required market code. Repeat for multiple values.",
    )
    parser.add_argument(
        "--allow-validation-failures",
        action="store_true",
        help="Continue even if market master validation emits warnings.",
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(KST).strftime("%Y-%m-%d"),
        help="Trade date YYYY-MM-DD. Default: today in KST",
    )
    parser.add_argument(
        "--daily-count",
        type=int,
        default=40,
        help="How many daily candles to request per symbol. Default: 40",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=5_000,
        help="Minimum allowed close price. Default: 5000",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=200_000,
        help="Maximum allowed close price. Default: 200000",
    )
    parser.add_argument(
        "--min-avg-trade-value-20",
        type=int,
        default=100_000_000,
        help="Minimum 20-day average trade value. Default: 100000000",
    )
    parser.add_argument(
        "--write-universe",
        action="store_true",
        help="Actually replace the universe snapshot in SQLite.",
    )
    parser.add_argument(
        "--run-startup-check",
        action="store_true",
        help="Run startup safety gate after universe snapshot is saved.",
    )
    parser.add_argument(
        "--allow-unresolved-orders",
        action="store_true",
        help="Allow startup check to continue even if unresolved orders exist.",
    )
    parser.add_argument(
        "--allow-empty-save",
        action="store_true",
        help="Allow saving an empty snapshot when accepted_count is 0.",
    )
    parser.add_argument(
        "--scan-timing1-setup",
        action="store_true",
        help=(
            "After universe snapshot is saved, scan buy timing1 daily setup "
            "signals."
        ),
    )
    parser.add_argument(
        "--write-timing1-signals",
        action="store_true",
        help=(
            "Persist timing1 setup signals when "
            "--scan-timing1-setup is enabled."
        ),
    )
    parser.add_argument(
        "--timing1-daily-count",
        type=int,
        default=90,
        help=(
            "How many daily candles to request for timing1 setup scan. "
            "Default: 90"
        ),
    )
    parser.add_argument(
        "--scan-timing2-setup",
        action="store_true",
        help=(
            "After universe snapshot is saved, scan buy timing2 daily setup "
            "signals."
        ),
    )
    parser.add_argument(
        "--write-timing2-signals",
        action="store_true",
        help=(
            "Persist timing2 setup signals when "
            "--scan-timing2-setup is enabled."
        ),
    )
    parser.add_argument(
        "--timing2-daily-count",
        type=int,
        default=90,
        help=(
            "How many daily candles to request for timing2 setup scan. "
            "Default: 90"
        ),
    )
    parser.add_argument(
        "--timing2-new-high-lookback-days",
        type=int,
        default=60,
        help=(
            "Lookback window for timing2 prior new-high check. Default: 60"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many accepted/rejected rows to print. Default: 20",
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


def _resolve_master_source(
    *,
    use_db_master: bool,
    has_master_input: bool,
) -> str | None:
    if use_db_master and not has_master_input:
        return "DB"
    if has_master_input and not use_db_master:
        return "FILE"
    return None


def _to_refresh_items(items: list[UniverseMasterItem]) -> list[MarketMasterRefreshItem]:
    return [
        MarketMasterRefreshItem(
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            is_managed=item.is_managed,
            is_investment_warning=item.is_investment_warning,
            is_investment_risk=item.is_investment_risk,
            is_attention_issue=item.is_attention_issue,
            is_disclosure_violation=item.is_disclosure_violation,
            is_liquidation_trade=item.is_liquidation_trade,
            is_trading_halt=item.is_trading_halt,
            is_rights_ex_date=item.is_rights_ex_date,
            is_preferred_stock=item.is_preferred_stock,
            is_etf=item.is_etf,
            is_etn=item.is_etn,
            is_spac=item.is_spac,
        )
        for item in items
    ]


def _rows_to_master_items(rows: tuple[MarketMasterRow, ...]) -> list[UniverseMasterItem]:
    return [
        UniverseMasterItem(
            symbol=row.symbol,
            name=row.name,
            market=row.market,
            is_managed=row.is_managed,
            is_investment_warning=row.is_investment_warning,
            is_investment_risk=row.is_investment_risk,
            is_attention_issue=row.is_attention_issue,
            is_disclosure_violation=row.is_disclosure_violation,
            is_liquidation_trade=row.is_liquidation_trade,
            is_trading_halt=row.is_trading_halt,
            is_rights_ex_date=row.is_rights_ex_date,
            is_preferred_stock=row.is_preferred_stock,
            is_etf=row.is_etf,
            is_etn=row.is_etn,
            is_spac=row.is_spac,
        )
        for row in rows
    ]


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _print_validation_result(
    validation_result: MarketMasterValidationResult,
) -> None:
    _section("Validation Result")
    _ok("validation_total_count", str(validation_result.total_count))
    _ok("validation_is_valid", str(validation_result.is_valid))

    if validation_result.market_counts:
        _section("Market Counts")
        for row in validation_result.market_counts:
            print(f"{row.name} count={row.count}")

    positive_flag_counts = [
        row
        for row in validation_result.flag_counts
        if row.count > 0
    ]
    if positive_flag_counts:
        _section("Flag Counts")
        for row in positive_flag_counts:
            print(f"{row.name} count={row.count}")

    if validation_result.warnings:
        for warning in validation_result.warnings:
            _warn("validation_warning", warning)


def _validation_result_to_payload(
    validation_result: MarketMasterValidationResult,
) -> dict[str, Any]:
    return {
        "total_count": validation_result.total_count,
        "is_valid": validation_result.is_valid,
        "market_counts": [
            {
                "name": row.name,
                "count": row.count,
            }
            for row in validation_result.market_counts
        ],
        "flag_counts": [
            {
                "name": row.name,
                "count": row.count,
            }
            for row in validation_result.flag_counts
        ],
        "warnings": list(validation_result.warnings),
    }


def _timing1_setup_scan_result_to_payload(
    scan_result: Timing1SetupScanResult,
) -> dict[str, Any]:
    return {
        "trade_date": scan_result.trade_date,
        "scanned_at": scan_result.scanned_at,
        "universe_count": scan_result.universe_count,
        "matched_count": scan_result.matched_count,
        "recorded_count": scan_result.recorded_count,
        "skipped_existing_count": scan_result.skipped_existing_count,
        "candidates": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "market": row.market,
                "already_recorded": row.already_recorded,
                "match": {
                    "evaluation_trade_date": row.match.evaluation_trade_date,
                    "latest_daily_date": row.match.latest_daily_date,
                    "latest_close": row.match.latest_close,
                    "ma_short_now": round(row.match.ma_short_now, 6),
                    "ma_short_past": round(row.match.ma_short_past, 6),
                    "ma_long_now": round(row.match.ma_long_now, 6),
                    "ma_long_past": round(row.match.ma_long_past, 6),
                    "strong_day": {
                        "date": row.match.strong_day.date,
                        "open_price": row.match.strong_day.open_price,
                        "close_price": row.match.strong_day.close_price,
                        "prev_close": row.match.strong_day.prev_close,
                        "gain_rate": round(row.match.strong_day.gain_rate, 6),
                        "volume": row.match.strong_day.volume,
                        "avg_volume_before": row.match.strong_day.avg_volume_before,
                        "volume_ratio": round(
                            row.match.strong_day.volume_ratio,
                            6,
                        ),
                    },
                },
            }
            for row in scan_result.candidates
        ],
        "recorded_signal_ids": [
            row.id
            for row in scan_result.recorded_signals
        ],
    }


def _timing2_setup_scan_result_to_payload(
    scan_result: Timing2SetupScanResult,
) -> dict[str, Any]:
    return {
        "trade_date": scan_result.trade_date,
        "scanned_at": scan_result.scanned_at,
        "universe_count": scan_result.universe_count,
        "matched_count": scan_result.matched_count,
        "recorded_count": scan_result.recorded_count,
        "skipped_existing_count": scan_result.skipped_existing_count,
        "candidates": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "market": row.market,
                "already_recorded": row.already_recorded,
                "match": {
                    "evaluation_trade_date": row.match.evaluation_trade_date,
                    "latest_daily_date": row.match.latest_daily_date,
                    "latest_close": row.match.latest_close,
                    "previous_close": row.match.previous_close,
                    "official_upper_limit_price": (
                        row.match.official_upper_limit_price
                    ),
                    "prior_lookback_high": row.match.prior_lookback_high,
                    "lookback_start_date": row.match.lookback_start_date,
                    "lookback_end_date": row.match.lookback_end_date,
                },
            }
            for row in scan_result.candidates
        ],
        "recorded_signal_ids": [
            row.id
            for row in scan_result.recorded_signals
        ],
    }


def _market_master_result_to_payload(
    *,
    market_master_result,
    min_master_count: int | None,
    required_markets: list[str],
) -> dict[str, Any]:
    return {
        "source": market_master_result.source.value,
        "symbol_count": market_master_result.symbol_count,
        "refreshed_at": market_master_result.refreshed_at,
        "refreshed_trade_date": market_master_result.refreshed_trade_date,
        "is_same_trade_date": market_master_result.is_same_trade_date,
        "min_master_count": min_master_count,
        "required_markets": list(required_markets),
        "validation_result": _validation_result_to_payload(
            market_master_result.validation_result
        ),
    }


def _universe_build_result_to_payload(build_result) -> dict[str, Any]:
    filter_result = build_result.filter_result
    return {
        "build_outcome": build_result.outcome.value,
        "build_reason": build_result.reason,
        "filter_result": {
            "total_count": filter_result.total_count,
            "accepted_count": filter_result.accepted_count,
            "rejected_count": filter_result.rejected_count,
        },
        "refresh_result": (
            None
            if build_result.refresh_result is None
            else {
                "candidate_count": build_result.refresh_result.candidate_count,
                "refreshed_at": build_result.refresh_result.refreshed_at,
            }
        ),
    }


def _startup_check_result_to_payload(startup_check_result) -> dict[str, Any] | None:
    if startup_check_result is None:
        return None

    return {
        "outcome": startup_check_result.outcome.value,
        "checked_at": startup_check_result.checked_at,
        "trade_date": startup_check_result.trade_date,
        "reason": startup_check_result.reason,
        "universe_snapshot": {
            "exists": startup_check_result.universe_snapshot.exists,
            "candidate_count": startup_check_result.universe_snapshot.candidate_count,
            "refreshed_at": startup_check_result.universe_snapshot.refreshed_at,
        },
        "reconcile_changed_rows": (
            None
            if startup_check_result.reconcile_result is None
            else startup_check_result.reconcile_result.changed_rows
        ),
        "unresolved_orders": (
            []
            if startup_check_result.reconcile_result is None
            else [
                {
                    "client_order_id": row.client_order_id,
                    "status": row.status.value,
                    "symbol": row.symbol,
                    "side": row.side,
                    "qty": row.qty,
                    "kis_order_no": row.kis_order_no,
                }
                for row in startup_check_result.reconcile_result.unresolved_orders
            ]
        ),
        "live_positions": [
            {
                "symbol": row.symbol,
                "qty": row.qty,
                "avg_price": row.avg_price,
                "updated_at": row.updated_at,
            }
            for row in startup_check_result.live_positions
        ],
    }


def _build_timing1_setup_not_requested() -> Timing1SetupScanExecutionResult:
    return Timing1SetupScanExecutionResult(
        outcome=Timing1SetupScanOutcome.NOT_REQUESTED,
        reason=None,
        scan_result=None,
    )


def _build_timing2_setup_not_requested() -> Timing2SetupScanExecutionResult:
    return Timing2SetupScanExecutionResult(
        outcome=Timing2SetupScanOutcome.NOT_REQUESTED,
        reason=None,
        scan_result=None,
    )


def _print_timing1_setup_scan_result(
    *,
    execution_result: Timing1SetupScanExecutionResult,
    limit: int,
) -> None:
    if execution_result.outcome == Timing1SetupScanOutcome.NOT_REQUESTED:
        return

    _section("Timing1 Setup Scan")
    _ok("timing1_setup_scan_outcome", execution_result.outcome.value)
    if execution_result.reason:
        _warn("timing1_setup_scan_reason", execution_result.reason)

    if execution_result.scan_result is None:
        return

    _ok("scanned_at", execution_result.scan_result.scanned_at)
    _ok("universe_count", str(execution_result.scan_result.universe_count))
    _ok("matched_count", str(execution_result.scan_result.matched_count))
    _ok("recorded_count", str(execution_result.scan_result.recorded_count))
    _ok(
        "skipped_existing_count",
        str(execution_result.scan_result.skipped_existing_count),
    )

    if execution_result.scan_result.candidates:
        _section("Timing1 Matches")
        for row in execution_result.scan_result.candidates[: max(0, limit)]:
            print(
                f"{row.symbol} name={row.name} market={row.market} "
                f"latest_daily_date={row.match.latest_daily_date} "
                f"strong_day={row.match.strong_day.date} "
                f"already_recorded={row.already_recorded}"
            )


def _print_timing2_setup_scan_result(
    *,
    execution_result: Timing2SetupScanExecutionResult,
    limit: int,
) -> None:
    if execution_result.outcome == Timing2SetupScanOutcome.NOT_REQUESTED:
        return

    _section("Timing2 Setup Scan")
    _ok("timing2_setup_scan_outcome", execution_result.outcome.value)
    if execution_result.reason:
        _warn("timing2_setup_scan_reason", execution_result.reason)

    if execution_result.scan_result is None:
        return

    _ok("scanned_at", execution_result.scan_result.scanned_at)
    _ok("universe_count", str(execution_result.scan_result.universe_count))
    _ok("matched_count", str(execution_result.scan_result.matched_count))
    _ok("recorded_count", str(execution_result.scan_result.recorded_count))
    _ok(
        "skipped_existing_count",
        str(execution_result.scan_result.skipped_existing_count),
    )

    if execution_result.scan_result.candidates:
        _section("Timing2 Matches")
        for row in execution_result.scan_result.candidates[: max(0, limit)]:
            print(
                f"{row.symbol} name={row.name} market={row.market} "
                f"latest_daily_date={row.match.latest_daily_date} "
                f"latest_close={row.match.latest_close} "
                f"upper_limit={row.match.official_upper_limit_price} "
                f"already_recorded={row.already_recorded}"
            )


def _build_base_payload(
    *,
    trade_date: str | None,
    pipeline_started: bool,
    pipeline_stage: str,
    pipeline_outcome: str,
    readiness_outcome: str | None,
    readiness_reason: str | None,
    master_source: str | None,
    master_input_path: Path | None,
    master_format: str | None,
    min_master_count: int | None,
    required_markets: list[str],
    input_validation_result: MarketMasterValidationResult | None,
    market_master_result: dict[str, Any] | None,
    source_item_count: int | None,
    universe_build_result: dict[str, Any] | None,
    startup_check_result: dict[str, Any] | None,
    timing1_setup_scan_outcome: str | None,
    timing1_setup_scan_reason: str | None,
    timing1_setup_scan_result: dict[str, Any] | None,
    timing2_setup_scan_outcome: str | None,
    timing2_setup_scan_reason: str | None,
    timing2_setup_scan_result: dict[str, Any] | None,
    error_type: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "pipeline_started": pipeline_started,
        "pipeline_stage": pipeline_stage,
        "pipeline_outcome": pipeline_outcome,
        "readiness_outcome": readiness_outcome,
        "readiness_reason": readiness_reason,
        "master_source": master_source,
        "master_input": (
            None
            if master_input_path is None
            else str(master_input_path)
        ),
        "master_format": master_format,
        "min_master_count": min_master_count,
        "required_markets": list(required_markets),
        "input_validation_result": (
            None
            if input_validation_result is None
            else _validation_result_to_payload(input_validation_result)
        ),
        "market_master_result": market_master_result,
        "source_item_count": source_item_count,
        "universe_build_result": universe_build_result,
        "startup_check_result": startup_check_result,
        "timing1_setup_scan_outcome": timing1_setup_scan_outcome,
        "timing1_setup_scan_reason": timing1_setup_scan_reason,
        "timing1_setup_scan_result": timing1_setup_scan_result,
        "timing2_setup_scan_outcome": timing2_setup_scan_outcome,
        "timing2_setup_scan_reason": timing2_setup_scan_reason,
        "timing2_setup_scan_result": timing2_setup_scan_result,
        "error_type": error_type,
        "error_message": error_message,
    }


def _build_validation_blocked_payload(
    *,
    trade_date: str,
    master_source: str,
    master_input_path: Path | None,
    master_format: str | None,
    min_master_count: int | None,
    required_markets: list[str],
    validation_result: MarketMasterValidationResult,
) -> dict[str, Any]:
    return _build_base_payload(
        trade_date=trade_date,
        pipeline_started=False,
        pipeline_stage="INPUT_VALIDATION",
        pipeline_outcome="VALIDATION_BLOCKED",
        readiness_outcome=None,
        readiness_reason="Preopen pipeline blocked by input validation warnings.",
        master_source=master_source,
        master_input_path=master_input_path,
        master_format=master_format,
        min_master_count=min_master_count,
        required_markets=required_markets,
        input_validation_result=validation_result,
        market_master_result=None,
        source_item_count=None,
        universe_build_result=None,
        startup_check_result=None,
        timing1_setup_scan_outcome=None,
        timing1_setup_scan_reason=None,
        timing1_setup_scan_result=None,
        timing2_setup_scan_outcome=None,
        timing2_setup_scan_reason=None,
        timing2_setup_scan_result=None,
        error_type=None,
        error_message=None,
    )


def _build_failure_payload(
    *,
    trade_date: str | None,
    pipeline_started: bool,
    pipeline_stage: str,
    pipeline_outcome: str,
    readiness_reason: str,
    master_source: str | None,
    master_input_path: Path | None,
    master_format: str | None,
    min_master_count: int | None,
    required_markets: list[str],
    input_validation_result: MarketMasterValidationResult | None = None,
    market_master_result: dict[str, Any] | None = None,
    source_item_count: int | None = None,
    universe_build_result: dict[str, Any] | None = None,
    startup_check_result: dict[str, Any] | None = None,
    timing1_setup_scan_outcome: str | None = None,
    timing1_setup_scan_reason: str | None = None,
    timing1_setup_scan_result: dict[str, Any] | None = None,
    timing2_setup_scan_outcome: str | None = None,
    timing2_setup_scan_reason: str | None = None,
    timing2_setup_scan_result: dict[str, Any] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return _build_base_payload(
        trade_date=trade_date,
        pipeline_started=pipeline_started,
        pipeline_stage=pipeline_stage,
        pipeline_outcome=pipeline_outcome,
        readiness_outcome=None,
        readiness_reason=readiness_reason,
        master_source=master_source,
        master_input_path=master_input_path,
        master_format=master_format,
        min_master_count=min_master_count,
        required_markets=required_markets,
        input_validation_result=input_validation_result,
        market_master_result=market_master_result,
        source_item_count=source_item_count,
        universe_build_result=universe_build_result,
        startup_check_result=startup_check_result,
        timing1_setup_scan_outcome=timing1_setup_scan_outcome,
        timing1_setup_scan_reason=timing1_setup_scan_reason,
        timing1_setup_scan_result=timing1_setup_scan_result,
        timing2_setup_scan_outcome=timing2_setup_scan_outcome,
        timing2_setup_scan_reason=timing2_setup_scan_reason,
        timing2_setup_scan_result=timing2_setup_scan_result,
        error_type=error_type,
        error_message=error_message,
    )


def _build_completed_payload(
    *,
    trade_date: str,
    readiness_outcome: str,
    readiness_reason: str | None,
    master_source: str,
    master_input_path: Path | None,
    master_format: str | None,
    min_master_count: int | None,
    required_markets: list[str],
    input_validation_result: MarketMasterValidationResult,
    market_master_result: dict[str, Any],
    source_item_count: int,
    universe_build_result: dict[str, Any],
    startup_check_result: dict[str, Any] | None,
    timing1_setup_scan_outcome: str,
    timing1_setup_scan_reason: str | None,
    timing1_setup_scan_result: dict[str, Any] | None,
    timing2_setup_scan_outcome: str,
    timing2_setup_scan_reason: str | None,
    timing2_setup_scan_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return _build_base_payload(
        trade_date=trade_date,
        pipeline_started=True,
        pipeline_stage="COMPLETED",
        pipeline_outcome="COMPLETED",
        readiness_outcome=readiness_outcome,
        readiness_reason=readiness_reason,
        master_source=master_source,
        master_input_path=master_input_path,
        master_format=master_format,
        min_master_count=min_master_count,
        required_markets=required_markets,
        input_validation_result=input_validation_result,
        market_master_result=market_master_result,
        source_item_count=source_item_count,
        universe_build_result=universe_build_result,
        startup_check_result=startup_check_result,
        timing1_setup_scan_outcome=timing1_setup_scan_outcome,
        timing1_setup_scan_reason=timing1_setup_scan_reason,
        timing1_setup_scan_result=timing1_setup_scan_result,
        timing2_setup_scan_outcome=timing2_setup_scan_outcome,
        timing2_setup_scan_reason=timing2_setup_scan_reason,
        timing2_setup_scan_result=timing2_setup_scan_result,
        error_type=None,
        error_message=None,
    )


def _save_failure_output(
    *,
    output_path: Path | None,
    payload: dict[str, Any],
) -> int | None:
    if output_path is None:
        return None
    try:
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))
        return None
    except Exception as exc:
        _fail("output", f"{type(exc).__name__}: {exc}")
        return 5


def _run_timing1_setup_scan(
    *,
    scan_requested: bool,
    write_signals: bool,
    broker: KisBroker,
    conn,
    readiness_result,
    trade_date: str,
    daily_count: int,
) -> Timing1SetupScanExecutionResult:
    if not scan_requested:
        return _build_timing1_setup_not_requested()

    build_result = readiness_result.preopen_universe_result.universe_build_result
    if build_result.outcome != UniverseBuildOutcome.SAVED:
        return Timing1SetupScanExecutionResult(
            outcome=Timing1SetupScanOutcome.SKIPPED,
            reason=(
                "Timing1 setup scan skipped because universe snapshot was not "
                f"saved in this run. build_outcome={build_result.outcome.value}"
            ),
            scan_result=None,
        )

    if build_result.refresh_result is None:
        return Timing1SetupScanExecutionResult(
            outcome=Timing1SetupScanOutcome.SKIPPED,
            reason=(
                "Timing1 setup scan skipped because universe refresh result is "
                "missing."
            ),
            scan_result=None,
        )

    if build_result.refresh_result.candidate_count == 0:
        return Timing1SetupScanExecutionResult(
            outcome=Timing1SetupScanOutcome.SKIPPED,
            reason=(
                "Timing1 setup scan skipped because saved universe snapshot is "
                "empty."
            ),
            scan_result=None,
        )

    scan_result = Timing1SetupScanService(
        broker=broker,
        conn=conn,
        universe_repo=UniverseCandidateRepository(conn),
        signal_repo=SignalRepository(conn),
    ).scan(
        trade_date=trade_date,
        settings=Timing1SetupSettings(),
        daily_count=daily_count,
        write_signals=write_signals,
    )
    return Timing1SetupScanExecutionResult(
        outcome=Timing1SetupScanOutcome.SCANNED,
        reason=None,
        scan_result=scan_result,
    )


def _run_timing2_setup_scan(
    *,
    scan_requested: bool,
    write_signals: bool,
    broker: KisBroker,
    conn,
    readiness_result,
    trade_date: str,
    daily_count: int,
    new_high_lookback_days: int,
) -> Timing2SetupScanExecutionResult:
    if not scan_requested:
        return _build_timing2_setup_not_requested()

    build_result = readiness_result.preopen_universe_result.universe_build_result
    if build_result.outcome != UniverseBuildOutcome.SAVED:
        return Timing2SetupScanExecutionResult(
            outcome=Timing2SetupScanOutcome.SKIPPED,
            reason=(
                "Timing2 setup scan skipped because universe snapshot was not "
                f"saved in this run. build_outcome={build_result.outcome.value}"
            ),
            scan_result=None,
        )

    if build_result.refresh_result is None:
        return Timing2SetupScanExecutionResult(
            outcome=Timing2SetupScanOutcome.SKIPPED,
            reason=(
                "Timing2 setup scan skipped because universe refresh result is "
                "missing."
            ),
            scan_result=None,
        )

    if build_result.refresh_result.candidate_count == 0:
        return Timing2SetupScanExecutionResult(
            outcome=Timing2SetupScanOutcome.SKIPPED,
            reason=(
                "Timing2 setup scan skipped because saved universe snapshot is "
                "empty."
            ),
            scan_result=None,
        )

    scan_result = Timing2SetupScanService(
        broker=broker,
        conn=conn,
        universe_repo=UniverseCandidateRepository(conn),
        signal_repo=SignalRepository(conn),
    ).scan(
        trade_date=trade_date,
        settings=Timing2SetupSettings(
            new_high_lookback_days=new_high_lookback_days,
        ),
        daily_count=daily_count,
        write_signals=write_signals,
    )
    return Timing2SetupScanExecutionResult(
        outcome=Timing2SetupScanOutcome.SCANNED,
        reason=None,
        scan_result=scan_result,
    )


def main() -> int:
    args = _parse_args()
    output_path = _resolve_path(args.output) if args.output else None
    master_source = _resolve_master_source(
        use_db_master=args.use_db_master,
        has_master_input=bool(args.master_input),
    )

    try:
        settings = load_settings()
        setup_logging(settings)
    except Exception as exc:
        _fail("startup", f"{type(exc).__name__}: {exc}")
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="STARTUP",
                pipeline_outcome="STARTUP_FAILED",
                readiness_reason="Preopen startup initialization failed.",
                master_source=master_source,
                master_input_path=None,
                master_format=None,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type=type(exc).__name__,
                error_message=str(exc),
            ),
        )
        if save_result is not None:
            return save_result
        return 5

    if bool(args.master_input) == bool(args.use_db_master):
        _fail(
            "master_source",
            "Choose exactly one of --master-input or --use-db-master.",
        )
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="ARGUMENT_VALIDATION",
                pipeline_outcome="INVALID_ARGUMENTS",
                readiness_reason=(
                    "Choose exactly one of --master-input or --use-db-master."
                ),
                master_source=None,
                master_input_path=None,
                master_format=None,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type="ArgumentError",
                error_message=(
                    "Choose exactly one of --master-input or --use-db-master."
                ),
            ),
        )
        if save_result is not None:
            return save_result
        return 2

    if args.write_timing1_signals and not args.scan_timing1_setup:
        _fail(
            "timing1_setup",
            "Use --scan-timing1-setup together with --write-timing1-signals.",
        )
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="ARGUMENT_VALIDATION",
                pipeline_outcome="INVALID_ARGUMENTS",
                readiness_reason=(
                    "Use --scan-timing1-setup together with "
                    "--write-timing1-signals."
                ),
                master_source=master_source,
                master_input_path=None,
                master_format=None,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type="ArgumentError",
                error_message=(
                    "Use --scan-timing1-setup together with "
                    "--write-timing1-signals."
                ),
            ),
        )
        if save_result is not None:
            return save_result
        return 2

    if args.write_timing2_signals and not args.scan_timing2_setup:
        _fail(
            "timing2_setup",
            "Use --scan-timing2-setup together with --write-timing2-signals.",
        )
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="ARGUMENT_VALIDATION",
                pipeline_outcome="INVALID_ARGUMENTS",
                readiness_reason=(
                    "Use --scan-timing2-setup together with "
                    "--write-timing2-signals."
                ),
                master_source=master_source,
                master_input_path=None,
                master_format=None,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type="ArgumentError",
                error_message=(
                    "Use --scan-timing2-setup together with "
                    "--write-timing2-signals."
                ),
            ),
        )
        if save_result is not None:
            return save_result
        return 2

    master_input_path = None
    if args.master_input:
        master_input_path = _resolve_path(args.master_input)
        if not master_input_path.exists():
            _fail("master_input", f"File not found: {master_input_path}")
            save_result = _save_failure_output(
                output_path=output_path,
                payload=_build_failure_payload(
                    trade_date=args.trade_date,
                    pipeline_started=False,
                    pipeline_stage="MASTER_INPUT",
                    pipeline_outcome="MASTER_INPUT_NOT_FOUND",
                    readiness_reason="Market master input file was not found.",
                    master_source="FILE",
                    master_input_path=master_input_path,
                    master_format=args.master_format,
                    min_master_count=args.min_master_count,
                    required_markets=list(args.required_market),
                    error_type="FileNotFoundError",
                    error_message=f"File not found: {master_input_path}",
                ),
            )
            if save_result is not None:
                return save_result
            return 2

    try:
        resolved_master_format = (
            None
            if master_input_path is None
            else resolve_universe_master_format(
                master_input_path,
                source_format=args.master_format,
            )
        )
    except Exception as exc:
        _fail("master_format", f"{type(exc).__name__}: {exc}")
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="MASTER_FORMAT",
                pipeline_outcome="MASTER_FORMAT_FAILED",
                readiness_reason="Market master input format resolution failed.",
                master_source=master_source,
                master_input_path=master_input_path,
                master_format=args.master_format,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type=type(exc).__name__,
                error_message=str(exc),
            ),
        )
        if save_result is not None:
            return save_result
        return 2

    db_path = args.db_path or settings.db_path

    filter_settings = UniverseFilterSettings(
        min_price=args.min_price,
        max_price=args.max_price,
        min_avg_trade_value_20=args.min_avg_trade_value_20,
    )

    _section("Prepare Preopen Universe")
    _ok("mode", settings.mode)

    _ok("master_source", str(master_source))
    if master_input_path is not None:
        _ok("master_input", str(master_input_path))
        _ok("master_format", resolved_master_format)
    _ok("required_markets", str(args.required_market or []))
    _ok("allow_validation_failures", str(args.allow_validation_failures))
    _ok("trade_date", args.trade_date)
    _ok("daily_count", str(args.daily_count))
    _ok("write_universe", str(args.write_universe))
    _ok("run_startup_check", str(args.run_startup_check))
    _ok("require_same_day_master", str(args.require_same_day_master))
    _ok("min_master_count", str(args.min_master_count))
    _ok("allow_unresolved_orders", str(args.allow_unresolved_orders))
    _ok("allow_empty_save", str(args.allow_empty_save))
    _ok("scan_timing1_setup", str(args.scan_timing1_setup))
    _ok("write_timing1_signals", str(args.write_timing1_signals))
    _ok("timing1_daily_count", str(args.timing1_daily_count))
    _ok("scan_timing2_setup", str(args.scan_timing2_setup))
    _ok("write_timing2_signals", str(args.write_timing2_signals))
    _ok("timing2_daily_count", str(args.timing2_daily_count))
    _ok(
        "timing2_new_high_lookback_days",
        str(args.timing2_new_high_lookback_days),
    )
    _ok("db_path", str(db_path))

    conn = None
    master_items = None
    validation_result = None
    try:
        run_migrations(db_path)
        conn = get_connection(
            db_path,
            busy_timeout_ms=settings.db_busy_timeout_ms,
        )
        if master_input_path is not None:
            master_items = load_universe_master_items(
                master_input_path,
                source_format=args.master_format,
            )
            validation_items = master_items
        else:
            snapshot = MarketMasterQueryService(
                market_master_repo=MarketMasterRepository(conn),
            ).get_snapshot()
            if not snapshot.exists:
                _fail(
                    "validation",
                    "No market master snapshot found in SQLite.",
                )
                conn.close()
                return 5
            validation_items = _rows_to_master_items(snapshot.rows)

        validation_result = MarketMasterValidationService().validate_items(
            items=validation_items,
            min_symbol_count=args.min_master_count,
            required_markets=args.required_market,
        )
    except Exception as exc:
        if conn is not None:
            conn.close()
        _fail("prepare", f"{type(exc).__name__}: {exc}")
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=False,
                pipeline_stage="PREPARE",
                pipeline_outcome="PREPARE_FAILED",
                readiness_reason="Preopen preparation failed before pipeline start.",
                master_source=master_source,
                master_input_path=master_input_path,
                master_format=resolved_master_format,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                error_type=type(exc).__name__,
                error_message=str(exc),
            ),
        )
        if save_result is not None:
            return save_result
        return 5

    _print_validation_result(validation_result)
    if validation_result.warnings and not args.allow_validation_failures:
        if output_path is not None:
            try:
                payload = _build_validation_blocked_payload(
                    trade_date=args.trade_date,
                    master_source=str(master_source),
                    master_input_path=master_input_path,
                    master_format=resolved_master_format,
                    min_master_count=args.min_master_count,
                    required_markets=list(args.required_market),
                    validation_result=validation_result,
                )
                _save_json(output_path, payload)
                _ok("json_saved", str(output_path))
            except Exception as exc:
                conn.close()
                _fail("output", f"{type(exc).__name__}: {exc}")
                return 5
        conn.close()
        return 4

    try:
        service = PreopenReadinessService(
            conn=conn,
            market_master_repo=MarketMasterRepository(conn),
            universe_repo=UniverseCandidateRepository(conn),
            order_repo=OrderRepository(conn),
            position_repo=PositionRepository(conn),
        )
        timing1_setup_scan_execution = _build_timing1_setup_not_requested()
        timing2_setup_scan_execution = _build_timing2_setup_not_requested()
        with KisBroker(settings) as broker:
            result = service.prepare_and_check(
                broker=broker,
                trade_date=args.trade_date,
                master_items=(
                    None
                    if master_items is None
                    else _to_refresh_items(master_items)
                ),
                use_existing_market_master=args.use_db_master,
                require_same_day_market_master=args.require_same_day_master,
                min_market_master_count=args.min_master_count,
                required_markets=args.required_market,
                filter_settings=filter_settings,
                daily_count=args.daily_count,
                write_universe=args.write_universe,
                run_startup_check=args.run_startup_check,
                allow_unresolved_orders=args.allow_unresolved_orders,
                allow_empty_save=args.allow_empty_save,
            )
            timing1_setup_scan_execution = _run_timing1_setup_scan(
                scan_requested=args.scan_timing1_setup,
                write_signals=args.write_timing1_signals,
                broker=broker,
                conn=conn,
                readiness_result=result,
                trade_date=args.trade_date,
                daily_count=args.timing1_daily_count,
            )
            timing2_setup_scan_execution = _run_timing2_setup_scan(
                scan_requested=args.scan_timing2_setup,
                write_signals=args.write_timing2_signals,
                broker=broker,
                conn=conn,
                readiness_result=result,
                trade_date=args.trade_date,
                daily_count=args.timing2_daily_count,
                new_high_lookback_days=args.timing2_new_high_lookback_days,
            )
    except Exception as exc:
        _fail("pipeline", f"{type(exc).__name__}: {exc}")
        save_result = _save_failure_output(
            output_path=output_path,
            payload=_build_failure_payload(
                trade_date=args.trade_date,
                pipeline_started=True,
                pipeline_stage="PIPELINE",
                pipeline_outcome="PIPELINE_FAILED",
                readiness_reason="Preopen pipeline execution failed.",
                master_source=master_source,
                master_input_path=master_input_path,
                master_format=resolved_master_format,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
                input_validation_result=validation_result,
                market_master_result=(
                    None
                    if "result" not in locals()
                    else _market_master_result_to_payload(
                        market_master_result=result.preopen_universe_result.market_master_result,
                        min_master_count=args.min_master_count,
                        required_markets=list(args.required_market),
                    )
                ),
                source_item_count=(
                    None
                    if "result" not in locals()
                    else result.preopen_universe_result.source_item_count
                ),
                universe_build_result=(
                    None
                    if "result" not in locals()
                    else _universe_build_result_to_payload(
                        result.preopen_universe_result.universe_build_result
                    )
                ),
                startup_check_result=(
                    None
                    if "result" not in locals() or result.startup_check_result is None
                    else _startup_check_result_to_payload(
                        result.startup_check_result
                    )
                ),
                timing1_setup_scan_outcome=(
                    None
                    if "timing1_setup_scan_execution" not in locals()
                    else timing1_setup_scan_execution.outcome.value
                ),
                timing1_setup_scan_reason=(
                    None
                    if "timing1_setup_scan_execution" not in locals()
                    else timing1_setup_scan_execution.reason
                ),
                timing1_setup_scan_result=(
                    None
                    if (
                        "timing1_setup_scan_execution" not in locals()
                        or timing1_setup_scan_execution.scan_result is None
                    )
                    else _timing1_setup_scan_result_to_payload(
                        timing1_setup_scan_execution.scan_result
                    )
                ),
                timing2_setup_scan_outcome=(
                    None
                    if "timing2_setup_scan_execution" not in locals()
                    else timing2_setup_scan_execution.outcome.value
                ),
                timing2_setup_scan_reason=(
                    None
                    if "timing2_setup_scan_execution" not in locals()
                    else timing2_setup_scan_execution.reason
                ),
                timing2_setup_scan_result=(
                    None
                    if (
                        "timing2_setup_scan_execution" not in locals()
                        or timing2_setup_scan_execution.scan_result is None
                    )
                    else _timing2_setup_scan_result_to_payload(
                        timing2_setup_scan_execution.scan_result
                    )
                ),
                error_type=type(exc).__name__,
                error_message=str(exc),
            ),
        )
        if save_result is not None:
            return save_result
        return 5
    finally:
        conn.close()

    build_result = result.preopen_universe_result.universe_build_result
    filter_result = build_result.filter_result
    market_master_result = result.preopen_universe_result.market_master_result

    _section("Market Master Result")
    _ok(
        "master_source",
        market_master_result.source.value,
    )
    _ok(
        "symbol_count",
        str(market_master_result.symbol_count),
    )
    _ok(
        "master_refreshed_at",
        market_master_result.refreshed_at,
    )
    _ok(
        "master_refreshed_trade_date",
        market_master_result.refreshed_trade_date,
    )
    _ok(
        "master_is_same_trade_date",
        str(market_master_result.is_same_trade_date),
    )

    _section("Universe Result")
    _ok("source_item_count", str(result.preopen_universe_result.source_item_count))
    _ok("total_count", str(filter_result.total_count))
    _ok("accepted_count", str(filter_result.accepted_count))
    _ok("rejected_count", str(filter_result.rejected_count))
    _ok("build_outcome", build_result.outcome.value)

    if filter_result.accepted_items:
        _section("Accepted")
        for item in filter_result.accepted_items[: max(0, args.limit)]:
            print(
                f"{item.symbol} name={item.name} market={item.market} "
                f"close_price={item.close_price} "
                f"prev_day_trade_value={item.prev_day_trade_value} "
                f"avg_trade_value_20={item.avg_trade_value_20}"
            )

    if filter_result.rejected_items:
        _section("Rejected")
        for rejected in filter_result.rejected_items[: max(0, args.limit)]:
            reason_text = ",".join(reason.value for reason in rejected.reasons)
            item = rejected.item
            print(f"{item.symbol} name={item.name} reasons={reason_text}")

    if build_result.reason:
        _warn("build_reason", build_result.reason)
    if result.reason:
        _warn("readiness_reason", result.reason)

    if result.startup_check_result is not None:
        startup_result = result.startup_check_result
        _section("Startup Result")
        _ok("startup_outcome", startup_result.outcome.value)
        _ok("checked_at", startup_result.checked_at)
        _ok("universe_exists", str(startup_result.universe_snapshot.exists))
        _ok(
            "universe_candidate_count",
            str(startup_result.universe_snapshot.candidate_count),
        )
        _ok(
            "universe_refreshed_at",
            str(startup_result.universe_snapshot.refreshed_at),
        )
        _ok(
            "reconcile_changed_rows",
            (
                "not_run"
                if startup_result.reconcile_result is None
                else str(startup_result.reconcile_result.changed_rows)
            ),
        )
        _ok(
            "unresolved_orders",
            (
                "0"
                if startup_result.reconcile_result is None
                else str(len(startup_result.reconcile_result.unresolved_orders))
            ),
        )
        _ok("live_positions", str(len(startup_result.live_positions)))

    _print_timing1_setup_scan_result(
        execution_result=timing1_setup_scan_execution,
        limit=args.limit,
    )
    _print_timing2_setup_scan_result(
        execution_result=timing2_setup_scan_execution,
        limit=args.limit,
    )

    exit_code = 0
    if build_result.outcome == UniverseBuildOutcome.SAVED:
        _section("Refresh Result")
        _ok("candidate_count", str(build_result.refresh_result.candidate_count))
        _ok("refreshed_at", build_result.refresh_result.refreshed_at)
    elif build_result.outcome == UniverseBuildOutcome.SKIPPED_EMPTY:
        exit_code = 4

    if result.outcome in (
        PreopenReadinessOutcome.STARTUP_SKIPPED,
        PreopenReadinessOutcome.BLOCKED,
    ):
        exit_code = 4

    if timing1_setup_scan_execution.outcome == Timing1SetupScanOutcome.SKIPPED:
        exit_code = 4
    if timing2_setup_scan_execution.outcome == Timing2SetupScanOutcome.SKIPPED:
        exit_code = 4

    if output_path is not None:
        payload = _build_completed_payload(
            trade_date=result.trade_date,
            readiness_outcome=result.outcome.value,
            readiness_reason=result.reason,
            master_source=str(master_source),
            master_input_path=master_input_path,
            master_format=resolved_master_format,
            min_master_count=args.min_master_count,
            required_markets=list(args.required_market),
            input_validation_result=validation_result,
            market_master_result=_market_master_result_to_payload(
                market_master_result=market_master_result,
                min_master_count=args.min_master_count,
                required_markets=list(args.required_market),
            ),
            source_item_count=result.preopen_universe_result.source_item_count,
            universe_build_result=_universe_build_result_to_payload(build_result),
            startup_check_result=_startup_check_result_to_payload(
                result.startup_check_result
            ),
            timing1_setup_scan_outcome=timing1_setup_scan_execution.outcome.value,
            timing1_setup_scan_reason=timing1_setup_scan_execution.reason,
            timing1_setup_scan_result=(
                None
                if timing1_setup_scan_execution.scan_result is None
                else _timing1_setup_scan_result_to_payload(
                    timing1_setup_scan_execution.scan_result
                )
            ),
            timing2_setup_scan_outcome=timing2_setup_scan_execution.outcome.value,
            timing2_setup_scan_reason=timing2_setup_scan_execution.reason,
            timing2_setup_scan_result=(
                None
                if timing2_setup_scan_execution.scan_result is None
                else _timing2_setup_scan_result_to_payload(
                    timing2_setup_scan_execution.scan_result
                )
            ),
        )
        _save_json(output_path, payload)
        _ok("json_saved", str(output_path))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
