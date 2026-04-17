"""Refresh the current market master snapshot from a JSON or CSV file."""

from __future__ import annotations

from market_master_refresh_cli import run_market_master_refresh_cli


def main() -> int:
    return run_market_master_refresh_cli(
        title="Refresh Market Master",
        description="Refresh market master snapshot from a JSON or CSV file.",
        input_help="Path to JSON or CSV market master items.",
        include_input_format=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
