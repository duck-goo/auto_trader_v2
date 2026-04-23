"""Tests for buy strategy selection helpers."""

from __future__ import annotations

import pytest

from strategy.buy_strategy_selection import (
    resolve_buy_strategy_selection,
    selection_to_buy_strategy,
)


def test_resolve_defaults_to_both_for_legacy_empty_flags():
    assert resolve_buy_strategy_selection(
        buy_strategy=None,
        scan_timing1=False,
        scan_timing2=False,
    ) == (True, True)


def test_resolve_explicit_timing2_for_ui_selection():
    assert resolve_buy_strategy_selection(
        buy_strategy="timing2",
        scan_timing1=False,
        scan_timing2=False,
    ) == (False, True)


def test_resolve_rejects_conflicting_legacy_flags():
    with pytest.raises(ValueError, match="conflicts"):
        resolve_buy_strategy_selection(
            buy_strategy="timing2",
            scan_timing1=True,
            scan_timing2=False,
        )


def test_selection_to_buy_strategy_round_trip_name():
    assert selection_to_buy_strategy(
        run_timing1=True,
        run_timing2=False,
    ) == "timing1"
