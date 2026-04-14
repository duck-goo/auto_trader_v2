"""Tests for UniverseFilterService."""

from __future__ import annotations

import pytest

from services import (
    UniverseFilterInput,
    UniverseFilterResult,
    UniverseFilterService,
    UniverseFilterSettings,
    UniverseRejectReason,
)


def _service() -> UniverseFilterService:
    return UniverseFilterService()


def _settings() -> UniverseFilterSettings:
    return UniverseFilterSettings(
        min_price=5_000,
        max_price=200_000,
        min_avg_trade_value_20=100_000_000,
    )


def test_filter_candidates_accepts_valid_items_and_builds_refresh_items():
    service = _service()

    result = service.filter_candidates(
        items=[
            UniverseFilterInput(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
                close_price=70500,
                prev_day_trade_value=950_000_000_000,
                avg_trade_value_20=880_000_000_000,
            ),
            UniverseFilterInput(
                symbol="035420",
                name="NAVER",
                market="KOSPI",
                close_price=180000,
                prev_day_trade_value=410_000_000_000,
                avg_trade_value_20=350_000_000_000,
            ),
        ],
        settings=_settings(),
    )

    assert isinstance(result, UniverseFilterResult)
    assert result.total_count == 2
    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert [item.symbol for item in result.accepted_items] == ["005930", "035420"]
    assert [item.symbol for item in result.refresh_items] == ["005930", "035420"]
    assert result.refresh_items[0].prev_day_trade_value == 950_000_000_000
    assert result.rejected_items == ()


def test_filter_candidates_collects_multiple_reject_reasons():
    service = _service()

    result = service.filter_candidates(
        items=[
            UniverseFilterInput(
                symbol="069500",
                name="KODEX 200",
                market="ETF",
                close_price=36250,
                prev_day_trade_value=120_000_000_000,
                avg_trade_value_20=110_000_000_000,
                is_etf=True,
                is_attention_issue=True,
            )
        ],
        settings=_settings(),
    )

    assert result.accepted_count == 0
    assert result.rejected_count == 1

    rejected = result.rejected_items[0]
    assert rejected.item.symbol == "069500"
    assert set(rejected.reasons) == {
        UniverseRejectReason.ETF,
        UniverseRejectReason.ATTENTION_ISSUE,
    }


def test_filter_candidates_rejects_price_and_liquidity_rules():
    service = _service()

    result = service.filter_candidates(
        items=[
            UniverseFilterInput(
                symbol="111111",
                name="Too Cheap",
                market="KOSDAQ",
                close_price=4000,
                prev_day_trade_value=5_000_000_000,
                avg_trade_value_20=300_000_000,
            ),
            UniverseFilterInput(
                symbol="222222",
                name="Too Expensive",
                market="KOSPI",
                close_price=250000,
                prev_day_trade_value=8_000_000_000,
                avg_trade_value_20=300_000_000,
            ),
            UniverseFilterInput(
                symbol="333333",
                name="Low Liquidity",
                market="KOSDAQ",
                close_price=12000,
                prev_day_trade_value=15_000_000,
                avg_trade_value_20=25_000_000,
            ),
        ],
        settings=_settings(),
    )

    reasons_by_symbol = {
        rejected.item.symbol: set(rejected.reasons)
        for rejected in result.rejected_items
    }

    assert result.accepted_count == 0
    assert result.rejected_count == 3
    assert UniverseRejectReason.PRICE_BELOW_MIN in reasons_by_symbol["111111"]
    assert UniverseRejectReason.PRICE_ABOVE_MAX in reasons_by_symbol["222222"]
    assert (
        UniverseRejectReason.AVG_TRADE_VALUE_20_BELOW_MIN
        in reasons_by_symbol["333333"]
    )


def test_filter_candidates_allows_empty_input():
    service = _service()

    result = service.filter_candidates(
        items=[],
        settings=_settings(),
    )

    assert result.total_count == 0
    assert result.accepted_count == 0
    assert result.rejected_count == 0
    assert result.accepted_items == ()
    assert result.refresh_items == ()
    assert result.rejected_items == ()


def test_filter_candidates_rejects_duplicate_symbols():
    service = _service()

    with pytest.raises(ValueError, match="Duplicate symbol"):
        service.filter_candidates(
            items=[
                UniverseFilterInput(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=950_000_000_000,
                    avg_trade_value_20=880_000_000_000,
                ),
                UniverseFilterInput(
                    symbol="005930",
                    name="Samsung Electronics Duplicate",
                    market="KOSPI",
                    close_price=70500,
                    prev_day_trade_value=950_000_000_000,
                    avg_trade_value_20=880_000_000_000,
                ),
            ],
            settings=_settings(),
        )


def test_filter_candidates_rejects_wrong_item_type():
    service = _service()

    with pytest.raises(ValueError, match="UniverseFilterInput"):
        service.filter_candidates(
            items=[{"symbol": "005930"}],  # type: ignore[list-item]
            settings=_settings(),
        )


def test_filter_candidates_rejects_invalid_settings():
    service = _service()

    with pytest.raises(ValueError, match="max_price must be >="):
        service.filter_candidates(
            items=[],
            settings=UniverseFilterSettings(
                min_price=200_000,
                max_price=5_000,
                min_avg_trade_value_20=100_000_000,
            ),
        )
