"""Market master query service."""

from __future__ import annotations

from dataclasses import dataclass

from services.errors import ServiceError
from storage.repositories import MarketMasterRepository, MarketMasterRow


@dataclass(frozen=True)
class MarketMasterSnapshotResult:
    exists: bool
    symbol_count: int
    refreshed_at: str | None
    rows: tuple[MarketMasterRow, ...]


class MarketMasterQueryService:
    """Read and validate the current market master snapshot."""

    def __init__(
        self,
        *,
        market_master_repo: MarketMasterRepository,
    ) -> None:
        self._market_master_repo = market_master_repo

    def get_snapshot(self) -> MarketMasterSnapshotResult:
        rows = tuple(self._market_master_repo.list_all())
        if not rows:
            return MarketMasterSnapshotResult(
                exists=False,
                symbol_count=0,
                refreshed_at=None,
                rows=(),
            )

        refreshed_values = {row.refreshed_at for row in rows}
        if len(refreshed_values) != 1:
            raise ServiceError(
                "Market master snapshot has inconsistent refreshed_at values: "
                f"values={sorted(refreshed_values)!r}"
            )

        return MarketMasterSnapshotResult(
            exists=True,
            symbol_count=len(rows),
            refreshed_at=next(iter(refreshed_values)),
            rows=rows,
        )
