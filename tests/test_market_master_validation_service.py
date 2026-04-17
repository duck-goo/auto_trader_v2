"""Tests for MarketMasterValidationService."""

from __future__ import annotations

import pytest

from market import UniverseMasterItem
from services import (
    MarketMasterValidationResult,
    MarketMasterValidationService,
)


def _items() -> list[UniverseMasterItem]:
    return [
        UniverseMasterItem(
            symbol="005930",
            name="Samsung Electronics",
            market="KOSPI",
        ),
        UniverseMasterItem(
            symbol="035420",
            name="NAVER",
            market="KOSPI",
        ),
        UniverseMasterItem(
            symbol="069500",
            name="KODEX 200",
            market="ETF",
            is_etf=True,
        ),
        UniverseMasterItem(
            symbol="035720",
            name="Kakao",
            market="KOSDAQ",
            is_attention_issue=True,
        ),
    ]


def test_validate_items_returns_summary_counts():
    service = MarketMasterValidationService()

    result = service.validate_items(
        items=_items(),
        min_symbol_count=4,
        required_markets=["KOSPI", "KOSDAQ"],
    )

    assert isinstance(result, MarketMasterValidationResult)
    assert result.total_count == 4
    assert result.is_valid is True
    assert result.warnings == ()
    assert [(row.name, row.count) for row in result.market_counts] == [
        ("ETF", 1),
        ("KOSDAQ", 1),
        ("KOSPI", 2),
    ]
    positive_flags = {
        row.name: row.count
        for row in result.flag_counts
        if row.count > 0
    }
    assert positive_flags == {
        "is_attention_issue": 1,
        "is_etf": 1,
    }


def test_validate_items_marks_result_invalid_when_count_below_minimum():
    service = MarketMasterValidationService()

    result = service.validate_items(
        items=_items(),
        min_symbol_count=5,
    )

    assert result.is_valid is False
    assert result.warnings == (
        "market master item count is below minimum: actual=4, minimum=5",
    )


def test_validate_items_marks_result_invalid_when_required_market_missing():
    service = MarketMasterValidationService()

    result = service.validate_items(
        items=_items(),
        required_markets=["KOSPI", "KOSDAQ", "KONEX"],
    )

    assert result.is_valid is False
    assert result.warnings == (
        "required markets are missing: KONEX",
    )


def test_validate_items_rejects_duplicate_symbols():
    service = MarketMasterValidationService()

    with pytest.raises(ValueError, match="Duplicate symbol"):
        service.validate_items(
            items=[
                UniverseMasterItem(
                    symbol="005930",
                    name="Samsung Electronics",
                    market="KOSPI",
                ),
                UniverseMasterItem(
                    symbol="005930",
                    name="Duplicate",
                    market="KOSPI",
                ),
            ],
        )
