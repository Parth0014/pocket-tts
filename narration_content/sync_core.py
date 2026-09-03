"""Dependency-injected Content Sync V1 orchestration.

This module contains no AWS, HTTP, environment, Lambda, or queue code.

Runtime adapters added later will implement the protocols in this file.
The pure core owns canonical artifact preparation, first-pass ordering,
checkpoint semantics, verification, and reconciliation orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Sequence

from .document import build_document
from .ghost_client import GHOST_PAGE_SIZE, GhostCatalogPage
from .hashing import canonical_json_bytes
from .normalizer import normalize_ghost_html
from .sync_models import (
    CatalogAuthor,
    CatalogStatus,
    CatalogTag,
    GhostCatalogPost,
    SyncErrorCode,
    SyncState,
    SyncStatus,
    UnsupportedGhostAccessError,
)
from .validation import validate_document


class ContentSyncCoreError(RuntimeError):
    """Base error for pure Content Sync orchestration."""


class SyncStateConflictError(ContentSyncCoreError):
    """Raised when conditional CURRENT-state ownership is lost."""


class CatalogChangedDuringSyncError(ContentSyncCoreError):
    """Raised when verification cannot prove one stable catalog."""

    error_code = SyncErrorCode.CATALOG_CHANGED_DURING_SYNC.value


class ImmutableArtifactConflictError(ContentSyncCoreError):
    """Raised when one immutable key already contains different bytes."""


@dataclass(frozen=True)
class ImmutableArtifact:
    """One content-addressed artifact to create or verify."""

    key: str
    body: bytes
    metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ContentSyncCoreError(
                "artifact key must be non-empty"
            )

        if not isinstance(self.body, bytes):
            raise ContentSyncCoreError(
                "artifact body must be bytes"
            )

        if not isinstance(self.metadata, Mapping):
            raise ContentSyncCoreError(
                "artifact metadata must be a mapping"
            )


@dataclass(frozen=True)
class PreparedCanonicalPost:
    """Exact immutable source and Narration Document for one post."""

    post_id: str
    catalog_content_hash: str
    narration_hash: str
    processor_version: int
    raw_html: ImmutableArtifact
    narration_document: ImmutableArtifact
    document: Mapping[str, Any]


@dataclass(frozen=True)
class CatalogObservation:
    """Only fields Content Sync is permitted to offer to NarrationPosts.

    Deliberately absent:

    - content_hash
    - ghost_status
    - last_webhook_at

    A future DynamoDB adapter must therefore build its UpdateExpression
    from this safe observation rather than from arbitrary post dictionaries.
    """

    post_id: str
    schema_version: int
    source: str

    first_seen_at_candidate: str

    title: str
    slug: str
    url: str
    excerpt: Optional[str]
    feature_image: Optional[str]
    published_at: str
    updated_at: str

    catalog_content_hash: str
    catalog_updated_at: str
    catalog_status: CatalogStatus
    visibility: str
    access: bool
    authors: tuple[CatalogAuthor, ...]
    tags: tuple[CatalogTag, ...]

    last_seen_sync_id: str
    last_seen_at: str


@dataclass(frozen=True)
class CatalogVerificationRecord:
    """Sync-owned fields required to verify the first-pass observation."""

    post_id: str
    last_seen_sync_id: str
    catalog_content_hash: str
    catalog_updated_at: str
    catalog_status: CatalogStatus
    visibility: str
    access: bool


@dataclass(frozen=True)
class ReconciliationResult:
    """Summary returned by the catalog adapter after safe reconciliation."""

    marked_post_ids: tuple[str, ...] = ()
    webhook_race_skipped_post_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    """Successful verification/reconciliation/completion result."""

    completed_state: SyncState
    reconciliation: ReconciliationResult


class ArtifactStore(Protocol):
    """Immutable artifact persistence boundary."""

    def put_immutable(
        self,
        artifact: ImmutableArtifact,
    ) -> None:
        """Create artifact or verify an identical existing artifact."""


class CatalogStore(Protocol):
    """NarrationPosts persistence boundary."""

    def observe_post(
        self,
        observation: CatalogObservation,
    ) -> None:
        """Persist one sync-safe catalog observation.

        The adapter must preserve existing first_seen_at with write-once
        semantics and must freshness-guard shared display metadata using
        updated_at.

        It must never write webhook-owned content_hash, ghost_status, or
        last_webhook_at.
        """

    def get_verification_record(
        self,
        post_id: str,
    ) -> Optional[CatalogVerificationRecord]:
        """Return sync-owned fields needed for verification."""

    def reconcile_absent(
        self,
        *,
        current_sync_id: str,
        sync_started_at: str,
        reconciled_at: str,
    ) -> ReconciliationResult:
        """Reconcile source=GHOST records absent from this verified sync.

        The adapter must skip a candidate whose last_webhook_at is greater
        than or equal to sync_started_at.
        """


class SyncStateStore(Protocol):
    """Conditional persistence boundary for SYSTEM#GHOST_SYNC / CURRENT."""

    def advance_page(
        self,
        *,
        expected_sync_id: str,
        expected_status: SyncStatus,
        expected_next_page: int,
        new_next_page: int,
        expected_total: int,
        expected_pages: int,
        updated_at: str,
    ) -> SyncState:
        """Conditionally advance one fully durable first-pass page."""

    def fail(
        self,
        *,
        expected_sync_id: str,
        expected_status: SyncStatus,
        expected_next_page: int,
        error_code: str,
        updated_at: str,
    ) -> SyncState:
        """Conditionally transition the owned run to FAILED."""

    def resume(
        self,
        *,
        expected_sync_id: str,
        expected_next_page: int,
        updated_at: str,
    ) -> SyncState:
        """Conditionally resume the same FAILED logical run."""

    def complete(
        self,
        *,
        expected_sync_id: str,
        expected_next_page: int,
        completed_at: str,
    ) -> SyncState:
        """Conditionally complete a verified and reconciled run."""


def prepare_canonical_post(
    post: GhostCatalogPost,
) -> PreparedCanonicalPost:
    """Build exact immutable source artifacts for one supported Ghost post."""

    post.require_v1_access()

    content_hash = post.catalog_content_hash

    blocks = normalize_ghost_html(post.html)

    document = build_document(
        post_id=post.post_id,
        content_hash=content_hash,
        blocks=blocks,
    )

    validate_document(document)

    narration_hash = document["narration_hash"]
    processor_version = document["processor_version"]

    raw_artifact = ImmutableArtifact(
        key=post.html_s3_key,
        body=post.html.encode("utf-8"),
        metadata={
            "artifact-kind": "ghost-html",
            "post-id": post.post_id,
            "content-hash": content_hash,
        },
    )

    document_artifact = ImmutableArtifact(
        key=post.document_s3_key(
            narration_hash,
            processor_version=processor_version,
        ),
        body=canonical_json_bytes(document),
        metadata={
            "artifact-kind": "narration-document-v1",
            "post-id": post.post_id,
            "content-hash": content_hash,
            "narration-hash": narration_hash,
            "processor-version": str(processor_version),
        },
    )

    return PreparedCanonicalPost(
        post_id=post.post_id,
        catalog_content_hash=content_hash,
        narration_hash=narration_hash,
        processor_version=processor_version,
        raw_html=raw_artifact,
        narration_document=document_artifact,
        document=document,
    )


def metadata_is_not_older(
    stored_updated_at: Optional[str],
    incoming_updated_at: str,
) -> bool:
    """Return whether incoming Ghost metadata may refresh stored metadata."""

    incoming = _parse_utc_z(
        "incoming_updated_at",
        incoming_updated_at,
    )

    if stored_updated_at is None:
        return True

    stored = _parse_utc_z(
        "stored_updated_at",
        stored_updated_at,
    )

    return incoming >= stored


class ContentSyncCore:
    """Pure Content Sync V1 orchestration."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        catalog: CatalogStore,
        state_store: SyncStateStore,
    ) -> None:
        self._artifacts = artifacts
        self._catalog = catalog
        self._state_store = state_store

    def process_first_pass_page(
        self,
        *,
        state: SyncState,
        page: GhostCatalogPage,
        seen_at: str,
    ) -> SyncState:
        """Durably process exactly the CURRENT first-pass page."""

        _parse_utc_z("seen_at", seen_at)

        if state.status is not SyncStatus.RUNNING:
            raise ContentSyncCoreError(
                "first-pass processing requires RUNNING state"
            )

        if state.next_page is None:
            raise ContentSyncCoreError(
                "RUNNING state must contain next_page"
            )

        if page.pagination.page != state.next_page:
            raise ContentSyncCoreError(
                "page does not match CURRENT next_page"
            )

        if page.pagination.limit != GHOST_PAGE_SIZE:
            raise ContentSyncCoreError(
                "page limit does not match Content Sync V1"
            )

        self._assert_unique_page_ids(
            state=state,
            page=page,
            now=seen_at,
        )

        if state.expected_total is None:
            if state.next_page != 1:
                raise ContentSyncCoreError(
                    "uninitialized pagination totals require page 1"
                )

            expected_total = page.pagination.total
            expected_pages = page.pagination.pages
        else:
            expected_total = state.expected_total
            expected_pages = state.expected_pages

            if (
                page.pagination.total != expected_total
                or page.pagination.pages != expected_pages
            ):
                self._fail_catalog_changed(
                    state=state,
                    now=seen_at,
                )

        assert expected_pages is not None

        for post in page.posts:
            try:
                post.require_v1_access()
            except UnsupportedGhostAccessError:
                self._state_store.fail(
                    expected_sync_id=state.sync_id,
                    expected_status=SyncStatus.RUNNING,
                    expected_next_page=state.next_page,
                    error_code=(
                        SyncErrorCode
                        .UNSUPPORTED_GHOST_ACCESS
                        .value
                    ),
                    updated_at=seen_at,
                )
                raise

            prepared = prepare_canonical_post(post)

            # Required durable order:
            # raw HTML -> narration document -> catalog observation.
            self._artifacts.put_immutable(
                prepared.raw_html
            )

            self._artifacts.put_immutable(
                prepared.narration_document
            )

            observation = CatalogObservation(
                post_id=post.post_id,
                schema_version=1,
                source="GHOST",
                first_seen_at_candidate=seen_at,
                title=post.title,
                slug=post.slug,
                url=post.url,
                excerpt=post.excerpt,
                feature_image=post.feature_image,
                published_at=post.published_at,
                updated_at=post.updated_at,
                catalog_content_hash=(
                    prepared.catalog_content_hash
                ),
                catalog_updated_at=post.updated_at,
                catalog_status=CatalogStatus.PUBLISHED,
                visibility=post.visibility,
                access=post.access,
                authors=post.authors,
                tags=post.tags,
                last_seen_sync_id=state.sync_id,
                last_seen_at=seen_at,
            )

            self._catalog.observe_post(
                observation
            )

        new_next_page = state.next_page + 1

        if new_next_page > expected_pages + 1:
            raise ContentSyncCoreError(
                "checkpoint would exceed verification sentinel"
            )

        return self._state_store.advance_page(
            expected_sync_id=state.sync_id,
            expected_status=SyncStatus.RUNNING,
            expected_next_page=state.next_page,
            new_next_page=new_next_page,
            expected_total=expected_total,
            expected_pages=expected_pages,
            updated_at=seen_at,
        )

    def resume_failed(
        self,
        *,
        state: SyncState,
        now: str,
    ) -> SyncState:
        """Resume the same FAILED run without changing its checkpoint."""

        _parse_utc_z("now", now)

        if state.status is not SyncStatus.FAILED:
            raise ContentSyncCoreError(
                "resume requires FAILED state"
            )

        if state.next_page is None:
            raise ContentSyncCoreError(
                "FAILED state must contain next_page"
            )

        return self._state_store.resume(
            expected_sync_id=state.sync_id,
            expected_next_page=state.next_page,
            updated_at=now,
        )

    def run_verification(
        self,
        *,
        state: SyncState,
        pages: Sequence[GhostCatalogPage],
        completed_at: str,
    ) -> VerificationResult:
        """Verify one stable catalog, reconcile safely, then complete."""

        _parse_utc_z(
            "completed_at",
            completed_at,
        )

        if state.status is not SyncStatus.RUNNING:
            raise ContentSyncCoreError(
                "verification requires RUNNING state"
            )

        if not state.verification_pending:
            raise ContentSyncCoreError(
                "verification requires expected_pages + 1 checkpoint"
            )

        if (
            state.expected_total is None
            or state.expected_pages is None
            or state.next_page is None
        ):
            raise ContentSyncCoreError(
                "verification state is incomplete"
            )

        if len(pages) != state.expected_pages:
            self._fail_catalog_changed(
                state=state,
                now=completed_at,
            )

        seen_ids: set[str] = set()

        for expected_page_number, page in enumerate(
            pages,
            start=1,
        ):
            pagination = page.pagination

            if (
                pagination.page != expected_page_number
                or pagination.limit != GHOST_PAGE_SIZE
                or pagination.pages != state.expected_pages
                or pagination.total != state.expected_total
            ):
                self._fail_catalog_changed(
                    state=state,
                    now=completed_at,
                )

            for post in page.posts:
                if post.post_id in seen_ids:
                    self._fail_catalog_changed(
                        state=state,
                        now=completed_at,
                    )

                seen_ids.add(post.post_id)

                # An access-class change between passes means the catalog
                # changed during the logical synchronization.
                if (
                    post.visibility != "public"
                    or post.access is not True
                ):
                    self._fail_catalog_changed(
                        state=state,
                        now=completed_at,
                    )

                record = (
                    self._catalog
                    .get_verification_record(
                        post.post_id
                    )
                )

                if record is None:
                    self._fail_catalog_changed(
                        state=state,
                        now=completed_at,
                    )

                assert record is not None

                if (
                    record.last_seen_sync_id
                    != state.sync_id
                    or record.catalog_content_hash
                    != post.catalog_content_hash
                    or record.catalog_updated_at
                    != post.updated_at
                    or record.catalog_status
                    is not CatalogStatus.PUBLISHED
                    or record.visibility
                    != post.visibility
                    or record.access
                    != post.access
                ):
                    self._fail_catalog_changed(
                        state=state,
                        now=completed_at,
                    )

        if len(seen_ids) != state.expected_total:
            self._fail_catalog_changed(
                state=state,
                now=completed_at,
            )

        # Reconciliation happens only after the entire verification pass
        # has succeeded.
        reconciliation = (
            self._catalog.reconcile_absent(
                current_sync_id=state.sync_id,
                sync_started_at=state.started_at,
                reconciled_at=completed_at,
            )
        )

        completed_state = (
            self._state_store.complete(
                expected_sync_id=state.sync_id,
                expected_next_page=state.next_page,
                completed_at=completed_at,
            )
        )

        return VerificationResult(
            completed_state=completed_state,
            reconciliation=reconciliation,
        )

    def _assert_unique_page_ids(
        self,
        *,
        state: SyncState,
        page: GhostCatalogPage,
        now: str,
    ) -> None:
        ids = [
            post.post_id
            for post in page.posts
        ]

        if len(ids) != len(set(ids)):
            self._fail_catalog_changed(
                state=state,
                now=now,
            )

    def _fail_catalog_changed(
        self,
        *,
        state: SyncState,
        now: str,
    ) -> None:
        assert state.next_page is not None

        self._state_store.fail(
            expected_sync_id=state.sync_id,
            expected_status=SyncStatus.RUNNING,
            expected_next_page=state.next_page,
            error_code=(
                SyncErrorCode
                .CATALOG_CHANGED_DURING_SYNC
                .value
            ),
            updated_at=now,
        )

        raise CatalogChangedDuringSyncError(
            "catalog changed during synchronization"
        )


def _parse_utc_z(
    name: str,
    value: str,
) -> datetime:
    if (
        not isinstance(value, str)
        or not value
        or not value.endswith("Z")
    ):
        raise ContentSyncCoreError(
            f"{name} must be a UTC RFC 3339 timestamp ending in Z"
        )

    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00"
        )
    except ValueError as exc:
        raise ContentSyncCoreError(
            f"{name} must be valid RFC 3339"
        ) from exc

    if (
        parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise ContentSyncCoreError(
            f"{name} must represent UTC"
        )

    return parsed