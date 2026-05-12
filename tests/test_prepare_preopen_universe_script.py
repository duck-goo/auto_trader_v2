"""Tests for prepare_preopen_universe script helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from services import (
    MarketMasterValidationCount,
    MarketMasterValidationResult,
    Timing1SetupScanCandidate,
    Timing1SetupScanResult,
    Timing2SetupScanCandidate,
    Timing2SetupScanResult,
    UniverseBuildOutcome,
)
from strategy import (
    Timing1SetupMatch,
    Timing1StrongDay,
    Timing2SetupMatch,
)


def _load_script_module():
    path = Path("scripts/prepare_preopen_universe.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "prepare_preopen_universe_script",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_validation_blocked_payload_has_expected_shape():
    module = _load_script_module()

    validation_result = MarketMasterValidationResult(
        total_count=4,
        is_valid=False,
        market_counts=(
            MarketMasterValidationCount(name="ETF", count=1),
            MarketMasterValidationCount(name="KOSPI", count=3),
        ),
        flag_counts=(
            MarketMasterValidationCount(name="is_attention_issue", count=1),
            MarketMasterValidationCount(name="is_etf", count=1),
        ),
        warnings=("required markets are missing: KONEX",),
    )

    payload = module._build_validation_blocked_payload(
        trade_date="2026-04-15",
        master_source="DB",
        master_input_path=None,
        master_format=None,
        min_master_count=4,
        required_markets=["KONEX"],
        validation_result=validation_result,
    )

    assert payload["trade_date"] == "2026-04-15"
    assert payload["pipeline_started"] is False
    assert payload["pipeline_stage"] == "INPUT_VALIDATION"
    assert payload["pipeline_outcome"] == "VALIDATION_BLOCKED"
    assert payload["readiness_outcome"] is None
    assert payload["readiness_reason"] == (
        "Preopen pipeline blocked by input validation warnings."
    )
    assert payload["master_source"] == "DB"
    assert payload["master_input"] is None
    assert payload["master_format"] is None
    assert payload["min_master_count"] == 4
    assert payload["required_markets"] == ["KONEX"]
    assert payload["market_master_result"] is None
    assert payload["source_item_count"] is None
    assert payload["universe_build_result"] is None
    assert payload["startup_check_result"] is None
    assert payload["timing1_setup_scan_outcome"] is None
    assert payload["timing1_setup_scan_reason"] is None
    assert payload["timing1_setup_scan_result"] is None
    assert payload["timing2_setup_scan_outcome"] is None
    assert payload["timing2_setup_scan_reason"] is None
    assert payload["timing2_setup_scan_result"] is None
    assert payload["error_type"] is None
    assert payload["error_message"] is None
    assert payload["input_validation_result"] == {
        "total_count": 4,
        "is_valid": False,
        "market_counts": [
            {"name": "ETF", "count": 1},
            {"name": "KOSPI", "count": 3},
        ],
        "flag_counts": [
            {"name": "is_attention_issue", "count": 1},
            {"name": "is_etf", "count": 1},
        ],
        "warnings": ["required markets are missing: KONEX"],
    }


def test_build_failure_payload_has_expected_shape():
    module = _load_script_module()

    payload = module._build_failure_payload(
        trade_date="2026-04-15",
        pipeline_started=False,
        pipeline_stage="MASTER_INPUT",
        pipeline_outcome="MASTER_INPUT_NOT_FOUND",
        readiness_reason="Market master input file was not found.",
        master_source="FILE",
        master_input_path=Path("data/debug/missing.json"),
        master_format="json",
        min_master_count=4,
        required_markets=["KOSPI"],
        input_validation_result=None,
        error_type="FileNotFoundError",
        error_message="File not found: data/debug/missing.json",
    )

    assert payload == {
        "trade_date": "2026-04-15",
        "pipeline_started": False,
        "pipeline_stage": "MASTER_INPUT",
        "pipeline_outcome": "MASTER_INPUT_NOT_FOUND",
        "readiness_outcome": None,
        "readiness_reason": "Market master input file was not found.",
        "master_source": "FILE",
        "master_input": str(Path("data/debug/missing.json")),
        "master_format": "json",
        "min_master_count": 4,
        "required_markets": ["KOSPI"],
        "input_validation_result": None,
        "market_master_result": None,
        "source_item_count": None,
        "universe_build_result": None,
        "startup_check_result": None,
        "timing1_setup_scan_outcome": None,
        "timing1_setup_scan_reason": None,
        "timing1_setup_scan_result": None,
        "timing2_setup_scan_outcome": None,
        "timing2_setup_scan_reason": None,
        "timing2_setup_scan_result": None,
        "error_type": "FileNotFoundError",
        "error_message": "File not found: data/debug/missing.json",
    }


def test_build_completed_payload_has_expected_shape():
    module = _load_script_module()

    validation_result = MarketMasterValidationResult(
        total_count=4,
        is_valid=True,
        market_counts=(
            MarketMasterValidationCount(name="ETF", count=1),
            MarketMasterValidationCount(name="KOSPI", count=3),
        ),
        flag_counts=(
            MarketMasterValidationCount(name="is_attention_issue", count=1),
            MarketMasterValidationCount(name="is_etf", count=1),
        ),
        warnings=(),
    )

    payload = module._build_completed_payload(
        trade_date="2026-04-15",
        readiness_outcome="PREPARED_ONLY",
        readiness_reason=None,
        master_source="DB",
        master_input_path=None,
        master_format=None,
        min_master_count=4,
        required_markets=["KOSPI"],
        input_validation_result=validation_result,
        market_master_result={
            "source": "EXISTING_DB",
            "symbol_count": 4,
        },
        source_item_count=4,
        universe_build_result={
            "build_outcome": "DRY_RUN",
        },
        startup_check_result=None,
        timing1_setup_scan_outcome="NOT_REQUESTED",
        timing1_setup_scan_reason=None,
        timing1_setup_scan_result=None,
        timing2_setup_scan_outcome="NOT_REQUESTED",
        timing2_setup_scan_reason=None,
        timing2_setup_scan_result=None,
    )

    assert payload == {
        "trade_date": "2026-04-15",
        "pipeline_started": True,
        "pipeline_stage": "COMPLETED",
        "pipeline_outcome": "COMPLETED",
        "readiness_outcome": "PREPARED_ONLY",
        "readiness_reason": None,
        "master_source": "DB",
        "master_input": None,
        "master_format": None,
        "min_master_count": 4,
        "required_markets": ["KOSPI"],
        "input_validation_result": {
            "total_count": 4,
            "is_valid": True,
            "market_counts": [
                {"name": "ETF", "count": 1},
                {"name": "KOSPI", "count": 3},
            ],
            "flag_counts": [
                {"name": "is_attention_issue", "count": 1},
                {"name": "is_etf", "count": 1},
            ],
            "warnings": [],
        },
        "market_master_result": {
            "source": "EXISTING_DB",
            "symbol_count": 4,
        },
        "source_item_count": 4,
        "universe_build_result": {
            "build_outcome": "DRY_RUN",
        },
        "startup_check_result": None,
        "timing1_setup_scan_outcome": "NOT_REQUESTED",
        "timing1_setup_scan_reason": None,
        "timing1_setup_scan_result": None,
        "timing2_setup_scan_outcome": "NOT_REQUESTED",
        "timing2_setup_scan_reason": None,
        "timing2_setup_scan_result": None,
        "error_type": None,
        "error_message": None,
    }


def test_timing1_setup_scan_result_to_payload_has_expected_shape():
    module = _load_script_module()

    scan_result = Timing1SetupScanResult(
        trade_date="2026-04-15",
        scanned_at="2026-04-15T08:45:00+09:00",
        universe_count=2,
        matched_count=1,
        recorded_count=1,
        skipped_existing_count=0,
        candidates=(
            Timing1SetupScanCandidate(
                symbol="035420",
                name="NAVER",
                market="KOSPI",
                already_recorded=False,
                match=Timing1SetupMatch(
                    symbol="035420",
                    evaluation_trade_date="2026-04-15",
                    latest_daily_date="2026-04-14",
                    latest_close=180000,
                    ma_short_now=171234.123456,
                    ma_short_past=162345.234567,
                    ma_long_now=150111.111111,
                    ma_long_past=148222.222222,
                    strong_day=Timing1StrongDay(
                        date="2026-04-11",
                        open_price=150000,
                        close_price=173000,
                        prev_close=149000,
                        gain_rate=0.161073,
                        volume=5_000_000,
                        avg_volume_before=2_100_000,
                        volume_ratio=2.380952,
                    ),
                ),
            ),
        ),
        recorded_signals=(),
    )

    payload = module._timing1_setup_scan_result_to_payload(scan_result)

    assert payload["trade_date"] == "2026-04-15"
    assert payload["matched_count"] == 1
    assert payload["recorded_count"] == 1
    assert payload["recorded_signal_ids"] == []
    assert payload["candidates"][0]["symbol"] == "035420"
    assert payload["candidates"][0]["match"]["latest_daily_date"] == "2026-04-14"
    assert payload["candidates"][0]["match"]["strong_day"]["date"] == "2026-04-11"


def test_timing2_setup_scan_result_to_payload_has_expected_shape():
    module = _load_script_module()

    scan_result = Timing2SetupScanResult(
        trade_date="2026-04-15",
        scanned_at="2026-04-15T08:50:00+09:00",
        universe_count=2,
        matched_count=1,
        recorded_count=0,
        skipped_existing_count=1,
        candidates=(
            Timing2SetupScanCandidate(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
                already_recorded=True,
                match=Timing2SetupMatch(
                    symbol="005930",
                    market="KOSPI",
                    evaluation_trade_date="2026-04-15",
                    latest_daily_date="2026-04-14",
                    latest_close=150000,
                    previous_close=129500,
                    latest_volume=500000,
                    previous_volume=100000,
                    close_gain_rate=0.158301,
                    volume_ratio=5.0,
                    lookback_highest_close=150000,
                    lookback_start_date="2026-01-14",
                    lookback_end_date="2026-04-14",
                ),
            ),
        ),
        recorded_signals=(),
    )

    payload = module._timing2_setup_scan_result_to_payload(scan_result)

    assert payload["trade_date"] == "2026-04-15"
    assert payload["matched_count"] == 1
    assert payload["recorded_count"] == 0
    assert payload["recorded_signal_ids"] == []
    assert payload["candidates"][0]["symbol"] == "005930"
    assert payload["candidates"][0]["match"]["lookback_highest_close"] == 150000
    assert payload["candidates"][0]["match"]["volume_ratio"] == 5.0


def test_run_timing2_setup_scan_returns_scanned_result_with_expected_settings(
    monkeypatch,
):
    module = _load_script_module()
    captured: dict[str, object] = {}

    fake_scan_result = Timing2SetupScanResult(
        trade_date="2026-04-15",
        scanned_at="2026-04-15T08:50:00+09:00",
        universe_count=2,
        matched_count=1,
        recorded_count=1,
        skipped_existing_count=0,
        candidates=(
            Timing2SetupScanCandidate(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
                already_recorded=False,
                match=Timing2SetupMatch(
                    symbol="005930",
                    market="KOSPI",
                    evaluation_trade_date="2026-04-15",
                    latest_daily_date="2026-04-14",
                    latest_close=150000,
                    previous_close=129500,
                    latest_volume=500000,
                    previous_volume=100000,
                    close_gain_rate=0.158301,
                    volume_ratio=5.0,
                    lookback_highest_close=150000,
                    lookback_start_date="2026-01-14",
                    lookback_end_date="2026-04-14",
                ),
            ),
        ),
        recorded_signals=(),
    )

    class FakeTiming2SetupScanService:
        def __init__(self, *, broker, conn, universe_repo, signal_repo) -> None:
            captured["broker"] = broker
            captured["conn"] = conn
            captured["universe_repo_type"] = type(universe_repo).__name__
            captured["signal_repo_type"] = type(signal_repo).__name__

        def scan(self, *, trade_date, settings, daily_count, write_signals):
            captured["trade_date"] = trade_date
            captured["settings"] = settings
            captured["daily_count"] = daily_count
            captured["write_signals"] = write_signals
            return fake_scan_result

    monkeypatch.setattr(
        module,
        "Timing2SetupScanService",
        FakeTiming2SetupScanService,
    )

    readiness_result = SimpleNamespace(
        preopen_universe_result=SimpleNamespace(
            universe_build_result=SimpleNamespace(
                outcome=UniverseBuildOutcome.SAVED,
                refresh_result=SimpleNamespace(candidate_count=2),
            )
        )
    )

    result = module._run_timing2_setup_scan(
        scan_requested=True,
        write_signals=True,
        broker=object(),
        conn=object(),
        readiness_result=readiness_result,
        trade_date="2026-04-15",
        daily_count=90,
        close_high_lookback_days=60,
        close_gain_rate_threshold=0.15,
        volume_multiplier_threshold=5.0,
    )

    assert result.outcome == module.Timing2SetupScanOutcome.SCANNED
    assert result.reason is None
    assert result.scan_result is fake_scan_result
    assert captured["trade_date"] == "2026-04-15"
    assert captured["daily_count"] == 90
    assert captured["write_signals"] is True
    assert captured["universe_repo_type"] == "UniverseCandidateRepository"
    assert captured["signal_repo_type"] == "SignalRepository"

    settings = captured["settings"]
    assert settings.close_high_lookback_days == 60
    assert settings.close_gain_rate_threshold == 0.15
    assert settings.volume_multiplier_threshold == 5.0
