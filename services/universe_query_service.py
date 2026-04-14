"""Universe query service."""

from __future__ import annotations

from dataclasses import dataclass

from services.errors import ServiceError
from storage.repositories import UniverseCandidateRepository, UniverseCandidateRow


@dataclass(frozen=True)
class UniverseSnapshotResult:
    trade_date: str
    exists: bool
    candidate_count: int
    refreshed_at: str | None
    rows: tuple[UniverseCandidateRow, ...]


class UniverseQueryService:
    """Read and validate one daily universe snapshot."""

    def __init__(
        self,
        *,
        universe_repo: UniverseCandidateRepository,
    ) -> None:
        self._universe_repo = universe_repo

    def get_snapshot(self, *, trade_date: str) -> UniverseSnapshotResult:
        rows = tuple(self._universe_repo.list_for_date(trade_date))

        if not rows:
            return UniverseSnapshotResult(
                trade_date=trade_date,
                exists=False,
                candidate_count=0,
                refreshed_at=None,
                rows=(),
            )

        refreshed_values = {row.refreshed_at for row in rows}
        if len(refreshed_values) != 1:
            raise ServiceError(
                "Universe snapshot has inconsistent refreshed_at values: "
                f"trade_date={trade_date}, values={sorted(refreshed_values)!r}"
            )

        refreshed_at = next(iter(refreshed_values))
        return UniverseSnapshotResult(
            trade_date=trade_date,
            exists=True,
            candidate_count=len(rows),
            refreshed_at=refreshed_at,
            rows=rows,
        )
