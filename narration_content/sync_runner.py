"""Bounded orchestration for Content Sync V1.

This layer performs no AWS client construction, environment reads, or HTTP
requests itself. Concrete stores and the page-fetch callable are injected.

A normal first-pass invocation processes one Ghost page. Verification performs
the complete second crawl because the frozen Content Sync contract requires
an exact catalog-set comparison before reconciliation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .ghost_client import GhostCatalogPage
from .sync_core import (
    ContentSyncCore,
    ReconciliationResult,
)
from .sync_models import (
    SyncState,
    SyncStatus,
)


class RuntimeSyncStateStore(Protocol):
    def get_current(
        self,
    ) -> SyncState | None: ...

    def start_new(
        self,
        *,
        now: str,
    ) -> SyncState: ...


@dataclass(frozen=True)
class SyncRunResult:
    action: str
    state: SyncState
    reconciliation: (
        ReconciliationResult | None
    ) = None


class ContentSyncRunner:
    """One bounded Content Sync execution."""

    def __init__(
        self,
        *,
        core: ContentSyncCore,
        state_store: RuntimeSyncStateStore,
        fetch_page: Callable[
            [int],
            GhostCatalogPage,
        ],
        now: Callable[[], str],
    ) -> None:
        self._core = core
        self._state_store = state_store
        self._fetch_page = fetch_page
        self._now = now

    def run_once(
        self,
        *,
        start_new: bool = False,
        resume_failed: bool = False,
    ) -> SyncRunResult:
        state = self._state_store.get_current()

        if state is None:
            state = self._state_store.start_new(
                now=self._now()
            )

        elif start_new:
            state = self._state_store.start_new(
                now=self._now()
            )

        if state.status is SyncStatus.COMPLETE:
            return SyncRunResult(
                action="COMPLETE",
                state=state,
            )

        if state.status is SyncStatus.FAILED:
            if not resume_failed:
                return SyncRunResult(
                    action="FAILED",
                    state=state,
                )

            state = self._core.resume_failed(
                state=state,
                now=self._now(),
            )

        if state.verification_pending:
            if state.expected_pages is None:
                raise RuntimeError(
                    "verification requires expected_pages"
                )

            pages = tuple(
                self._fetch_page(page_number)
                for page_number in range(
                    1,
                    state.expected_pages + 1,
                )
            )

            result = self._core.run_verification(
                state=state,
                pages=pages,
                completed_at=self._now(),
            )

            return SyncRunResult(
                action="VERIFIED_COMPLETE",
                state=result.completed_state,
                reconciliation=result.reconciliation,
            )

        if state.next_page is None:
            raise RuntimeError(
                "RUNNING sync requires next_page"
            )

        page = self._fetch_page(
            state.next_page
        )

        updated = (
            self._core.process_first_pass_page(
                state=state,
                page=page,
                seen_at=self._now(),
            )
        )

        return SyncRunResult(
            action="FIRST_PASS_PAGE",
            state=updated,
        )