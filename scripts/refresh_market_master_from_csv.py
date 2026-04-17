"""Refresh the current market master snapshot from a CSV file."""

from __future__ import annotations

from market_master_refresh_cli import run_market_master_refresh_cli


def main() -> int:
    return run_market_master_refresh_cli(
        title="Refresh Market Master From CSV",
        description="Refresh market master snapshot from a CSV file.",
        input_help="Path to CSV of market master items.",
        forced_input_format="csv",
    )


if __name__ == "__main__":
    raise SystemExit(main())
