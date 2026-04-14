"""Universe build orchestration service."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from services.errors import ServiceError
from services.universe_filter_service import (
    UniverseFilterInput,
    UniverseFilterResult,
    UniverseFilterService,
    UniverseFilterSettings,
)
from services.universe_refresh_service import (
    UniverseRefreshResult,
    UniverseRefreshService,
)


class UniverseBuildOutcome(str, enum.Enum):
    DRY_RUN = "DRY_RUN"
    SAVED = "SAVED"
    SKIPPED_EMPTY = "SKIPPED_EMPTY"


@dataclass(frozen=True)
class UniverseBuildResult:
    outcome: UniverseBuildOutcome
    trade_date: str
    filter_result: UniverseFilterResult
    refresh_result: UniverseRefreshResult | None
    reason: str | None


class UniverseBuildService:
    """Orchestrate filter + optional snapshot persistence."""

    def __init__(
        self,
        *,
        filter_service: UniverseFilterService,
        refresh_service: UniverseRefreshService | None = None,
    ) -> None:
        self._filter_service = filter_service
        self._refresh_service = refresh_service

    def build_snapshot(
        self,
        *,
        trade_date: str,
        items: Sequence[UniverseFilterInput],
        settings: UniverseFilterSettings,
        write: bool = False,
        allow_empty_save: bool = False,
    ) -> UniverseBuildResult:
        if not isinstance(write, bool):
            raise ValueError(f"write must be a bool: {write!r}")
        if not isinstance(allow_empty_save, bool):
            raise ValueError(
                f"allow_empty_save must be a bool: {allow_empty_save!r}"
            )

        filter_result = self._filter_service.filter_candidates(
            items=items,
            settings=settings,
        )

        if not write:
            return UniverseBuildResult(
                outcome=UniverseBuildOutcome.DRY_RUN,
                trade_date=trade_date,
                filter_result=filter_result,
                refresh_result=None,
                reason="Write skipped by request.",
            )

        if filter_result.accepted_count == 0 and not allow_empty_save:
            return UniverseBuildResult(
                outcome=UniverseBuildOutcome.SKIPPED_EMPTY,
                trade_date=trade_date,
                filter_result=filter_result,
                refresh_result=None,
                reason=(
                    "accepted_count is 0. "
                    "Re-run with allow_empty_save=True to clear this snapshot."
                ),
            )

        if self._refresh_service is None:
            raise ServiceError(
                "refresh_service is required when write=True."
            )

        refresh_result = self._refresh_service.refresh_snapshot(
            trade_date=trade_date,
            candidates=filter_result.refresh_items,
        )

        return UniverseBuildResult(
            outcome=UniverseBuildOutcome.SAVED,
            trade_date=trade_date,
            filter_result=filter_result,
            refresh_result=refresh_result,
            reason=None,
        )
