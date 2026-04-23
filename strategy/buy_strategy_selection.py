"""Buy strategy selection helpers.

The UI can map one dropdown value to these choices instead of toggling
individual timing flags directly.
"""

from __future__ import annotations

BUY_STRATEGY_TIMING1 = "timing1"
BUY_STRATEGY_TIMING2 = "timing2"
BUY_STRATEGY_BOTH = "both"
BUY_STRATEGY_CHOICES = (
    BUY_STRATEGY_TIMING1,
    BUY_STRATEGY_TIMING2,
    BUY_STRATEGY_BOTH,
)


def resolve_buy_strategy_selection(
    *,
    buy_strategy: str | None,
    scan_timing1: bool,
    scan_timing2: bool,
) -> tuple[bool, bool]:
    """Resolve one explicit strategy choice plus legacy scan flags.

    Legacy behavior is preserved: if no choice and no flags are provided, both
    timing strategies run. If the new choice is provided with conflicting legacy
    flags, fail early so an operator cannot accidentally run the wrong strategy.
    """

    if buy_strategy is None:
        if not scan_timing1 and not scan_timing2:
            return True, True
        return scan_timing1, scan_timing2

    if buy_strategy not in BUY_STRATEGY_CHOICES:
        raise ValueError(
            "buy_strategy must be one of "
            f"{', '.join(BUY_STRATEGY_CHOICES)}: {buy_strategy!r}"
        )

    selected = _selection_for_strategy(buy_strategy)
    if scan_timing1 or scan_timing2:
        legacy_selected = (scan_timing1, scan_timing2)
        if legacy_selected != selected:
            raise ValueError(
                "--buy-strategy conflicts with --scan-timing1/--scan-timing2. "
                "Use one selection style, or make the legacy flags match."
            )
    return selected


def selection_to_buy_strategy(
    *,
    run_timing1: bool,
    run_timing2: bool,
) -> str:
    if run_timing1 and run_timing2:
        return BUY_STRATEGY_BOTH
    if run_timing1:
        return BUY_STRATEGY_TIMING1
    if run_timing2:
        return BUY_STRATEGY_TIMING2
    raise ValueError("At least one buy strategy must be selected.")


def _selection_for_strategy(buy_strategy: str) -> tuple[bool, bool]:
    if buy_strategy == BUY_STRATEGY_TIMING1:
        return True, False
    if buy_strategy == BUY_STRATEGY_TIMING2:
        return False, True
    if buy_strategy == BUY_STRATEGY_BOTH:
        return True, True
    raise ValueError(f"Unknown buy strategy: {buy_strategy!r}")
