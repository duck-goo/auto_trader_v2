"""Tests for KRX price-limit helpers."""

from __future__ import annotations

import pytest

from market import (
    calculate_krx_price_limit_amount,
    calculate_krx_upper_price_limit,
    get_krx_tick_size,
    round_down_to_krx_tick,
)


def test_kosdaq_upper_limit_matches_official_example_shape():
    assert get_krx_tick_size(market="KOSDAQ", price=9_940) == 10
    assert calculate_krx_price_limit_amount(
        market="KOSDAQ",
        base_price=9_940,
    ) == 2_980
    assert calculate_krx_upper_price_limit(
        market="KOSDAQ",
        base_price=9_940,
    ) == 12_920


def test_kospi_and_kosdaq_share_same_stock_tick_rules_in_current_table():
    assert get_krx_tick_size(market="KOSPI", price=109_500) == 100
    assert get_krx_tick_size(market="KOSDAQ", price=109_500) == 100
    assert calculate_krx_upper_price_limit(
        market="KOSPI",
        base_price=109_500,
    ) == 142_300
    assert calculate_krx_upper_price_limit(
        market="KOSDAQ",
        base_price=109_500,
    ) == 142_300


def test_round_down_to_krx_tick_uses_current_price_band():
    assert round_down_to_krx_tick(market="KOSPI", price=250_800) == 250_500
    assert round_down_to_krx_tick(market="KOSDAQ", price=9_947) == 9_940


def test_price_limit_helpers_reject_unknown_market():
    with pytest.raises(ValueError, match="market must be one of"):
        calculate_krx_upper_price_limit(
            market="ETF",
            base_price=10_000,
        )
