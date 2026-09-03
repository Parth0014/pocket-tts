from copy import deepcopy

import pytest

from narration_content.ghost_client import (
    GhostCatalogPage,
    GhostPagination,
)
from narration_content.sync_core import (
    CatalogChangedDuringSyncError,
    CatalogObservation,
    CatalogVerificationRecord,
    ContentSyncCore,
    ImmutableArtifactConflictError,
    ReconciliationResult,
    SyncStateConflictError,
    metadata_is_not_older,
    prepare_canonical_post,
)
from narration_content.sync_models import (
    CatalogStatus,
    GhostCatalogPost,
    SyncState,
    SyncStatus,
    UnsupportedGhostAccessError,
)
from narration_content.validation import (
    validate_document,
)

START = "2026-09-03T12:00:00Z"
PAGE_TIME = "2026-09-03T12:01:00Z"
VERIFY_TIME = "2026-09-03T12:05:00Z"
BEFORE_START = "2026-09-03T11:59:00Z"
AFTER_START = "2026-09-03T12:00:30Z"

SYNC_ID = "sync_" + ("1" * 32)


def make_post(**overrides):
    values = {
        "post_id": "ghost-post-1",
        "title": "A grateful day",
        "slug": "a-grateful-day",
        "url": "https://example.test/a-grateful-day/",
        "published_at": "2026-09-01T10:00:00.000Z",
        "updated_at": "2026-09-02T11:00:00.000Z",
        "html": "<h2>Gratitude</h2><p>Today was beautiful.</p>",
        "visibility": "public",
        "access": True,
    }
    values.update(overrides)
    return GhostCatalogPost(**values)


def make_page(
    posts,
    *,
    page=1,
    pages=1,
    total=None,
):
    posts = tuple(posts)

    if total is None:
        total = len(posts)

    return GhostCatalogPage(
        posts=posts,
        pagination=GhostPagination(
            page=page,
            limit=100,
            pages=pages,
            total=total,
            next=(
                page + 1
                if page < pages
                else None
            ),
            prev=(
                page - 1
                if page > 1
                else None
            ),
        ),
    )


def running_state(
    *,
    next_page=1,
    expected_total=None,
    expected_pages=None,
):
    return SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.RUNNING,
        next_page=next_page,
        started_at=START,
        updated_at=START,
        expected_total=expected_total,
        expected_pages=expected_pages,
    )


class FakeArtifactStore:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def put_immutable(self, artifact):
        self.calls.append(artifact.key)

        existing = self.objects.get(
            artifact.key
        )

        if existing is None:
            self.objects[artifact.key] = (
                artifact.body,
                dict(artifact.metadata),
            )
            return

        existing_body, existing_metadata = existing

        if (
            existing_body != artifact.body
            or existing_metadata
            != dict(artifact.metadata)
        ):
            raise ImmutableArtifactConflictError(
                "immutable object differs"
            )


class FakeCatalogStore:
    def __init__(self, items=None):
        self.items = deepcopy(items or {})
        self.observations = []
        self.reconcile_calls = 0

    def observe_post(
        self,
        observation: CatalogObservation,
    ):
        self.observations.append(
            observation
        )

        item = self.items.setdefault(
            observation.post_id,
            {
                "post_id": observation.post_id,
            },
        )

        # Write-once shared field.
        item.setdefault(
            "first_seen_at",
            observation.first_seen_at_candidate,
        )

        # Shared constants.
        item["schema_version"] = (
            observation.schema_version
        )
        item["source"] = observation.source

        # Sync-owned fields always belong to this observation.
        item["catalog_content_hash"] = (
            observation.catalog_content_hash
        )
        item["catalog_updated_at"] = (
            observation.catalog_updated_at
        )
        item["catalog_status"] = (
            observation.catalog_status.value
        )
        item["visibility"] = observation.visibility
        item["access"] = observation.access
        item["authors"] = observation.authors
        item["tags"] = observation.tags
        item["last_seen_sync_id"] = (
            observation.last_seen_sync_id
        )
        item["last_seen_at"] = (
            observation.last_seen_at
        )

        # Display metadata is freshness guarded.
        if metadata_is_not_older(
            item.get("updated_at"),
            observation.updated_at,
        ):
            item["title"] = observation.title
            item["slug"] = observation.slug
            item["url"] = observation.url
            item["published_at"] = (
                observation.published_at
            )
            item["updated_at"] = (
                observation.updated_at
            )

            if observation.excerpt is not None:
                item["excerpt"] = (
                    observation.excerpt
                )

            if observation.feature_image is not None:
                item["feature_image"] = (
                    observation.feature_image
                )

    def get_verification_record(
        self,
        post_id,
    ):
        item = self.items.get(post_id)

        if item is None:
            return None

        required = (
            "last_seen_sync_id",
            "catalog_content_hash",
            "catalog_updated_at",
            "catalog_status",
            "visibility",
            "access",
        )

        if any(
            name not in item
            for name in required
        ):
            return None

        return CatalogVerificationRecord(
            post_id=post_id,
            last_seen_sync_id=(
                item["last_seen_sync_id"]
            ),
            catalog_content_hash=(
                item["catalog_content_hash"]
            ),
            catalog_updated_at=(
                item["catalog_updated_at"]
            ),
            catalog_status=CatalogStatus(
                item["catalog_status"]
            ),
            visibility=item["visibility"],
            access=item["access"],
        )

    def reconcile_absent(
        self,
        *,
        current_sync_id,
        sync_started_at,
        reconciled_at,
    ):
        self.reconcile_calls += 1

        marked = []
        skipped = []

        for post_id, item in self.items.items():
            if item.get("source") != "GHOST":
                continue

            if (
                item.get("last_seen_sync_id")
                == current_sync_id
            ):
                continue

            last_webhook_at = item.get(
                "last_webhook_at"
            )

            if (
                last_webhook_at is not None
                and metadata_is_not_older(
                    sync_started_at,
                    last_webhook_at,
                )
            ):
                skipped.append(post_id)
                continue

            item["catalog_status"] = (
                CatalogStatus
                .NOT_IN_PUBLISHED_CATALOG
                .value
            )
            item["last_reconciled_sync_id"] = (
                current_sync_id
            )
            item["last_reconciled_at"] = (
                reconciled_at
            )

            marked.append(post_id)

        return ReconciliationResult(
            marked_post_ids=tuple(marked),
            webhook_race_skipped_post_ids=tuple(
                skipped
            ),
        )


class FakeStateStore:
    def __init__(self, state):
        self.current = state
        self.advance_calls = 0
        self.fail_calls = 0
        self.resume_calls = 0
        self.complete_calls = 0

    def _assert_owner(
        self,
        *,
        sync_id,
        status,
        next_page,
    ):
        if (
            self.current.sync_id != sync_id
            or self.current.status is not status
            or self.current.next_page != next_page
        ):
            raise SyncStateConflictError(
                "conditional CURRENT ownership lost"
            )

    def advance_page(
        self,
        *,
        expected_sync_id,
        expected_status,
        expected_next_page,
        new_next_page,
        expected_total,
        expected_pages,
        updated_at,
    ):
        self._assert_owner(
            sync_id=expected_sync_id,
            status=expected_status,
            next_page=expected_next_page,
        )

        if self.current.expected_total is not None:
            if (
                self.current.expected_total
                != expected_total
                or self.current.expected_pages
                != expected_pages
            ):
                raise SyncStateConflictError(
                    "pagination totals changed"
                )

        self.advance_calls += 1

        self.current = SyncState(
            sync_id=self.current.sync_id,
            status=SyncStatus.RUNNING,
            next_page=new_next_page,
            started_at=self.current.started_at,
            updated_at=updated_at,
            expected_total=expected_total,
            expected_pages=expected_pages,
        )

        return self.current

    def fail(
        self,
        *,
        expected_sync_id,
        expected_status,
        expected_next_page,
        error_code,
        updated_at,
    ):
        self._assert_owner(
            sync_id=expected_sync_id,
            status=expected_status,
            next_page=expected_next_page,
        )

        self.fail_calls += 1

        self.current = SyncState(
            sync_id=self.current.sync_id,
            status=SyncStatus.FAILED,
            next_page=self.current.next_page,
            started_at=self.current.started_at,
            updated_at=updated_at,
            expected_total=self.current.expected_total,
            expected_pages=self.current.expected_pages,
            last_error_code=error_code,
        )

        return self.current

    def resume(
        self,
        *,
        expected_sync_id,
        expected_next_page,
        updated_at,
    ):
        self._assert_owner(
            sync_id=expected_sync_id,
            status=SyncStatus.FAILED,
            next_page=expected_next_page,
        )

        self.resume_calls += 1

        self.current = SyncState(
            sync_id=self.current.sync_id,
            status=SyncStatus.RUNNING,
            next_page=self.current.next_page,
            started_at=self.current.started_at,
            updated_at=updated_at,
            expected_total=self.current.expected_total,
            expected_pages=self.current.expected_pages,
        )

        return self.current

    def complete(
        self,
        *,
        expected_sync_id,
        expected_next_page,
        completed_at,
    ):
        self._assert_owner(
            sync_id=expected_sync_id,
            status=SyncStatus.RUNNING,
            next_page=expected_next_page,
        )

        self.complete_calls += 1

        self.current = SyncState(
            sync_id=self.current.sync_id,
            status=SyncStatus.COMPLETE,
            next_page=None,
            started_at=self.current.started_at,
            updated_at=completed_at,
            expected_total=self.current.expected_total,
            expected_pages=self.current.expected_pages,
            completed_at=completed_at,
        )

        return self.current


def make_core(
    *,
    state=None,
    items=None,
):
    if state is None:
        state = running_state()

    artifacts = FakeArtifactStore()
    catalog = FakeCatalogStore(items)
    state_store = FakeStateStore(state)

    core = ContentSyncCore(
        artifacts=artifacts,
        catalog=catalog,
        state_store=state_store,
    )

    return (
        core,
        artifacts,
        catalog,
        state_store,
    )


def process_one_post_to_verification(
    *,
    post=None,
    items=None,
):
    if post is None:
        post = make_post()

    core, artifacts, catalog, state_store = (
        make_core(items=items)
    )

    state = core.process_first_pass_page(
        state=state_store.current,
        page=make_page([post]),
        seen_at=PAGE_TIME,
    )

    assert state.verification_pending is True

    return (
        core,
        artifacts,
        catalog,
        state_store,
        state,
        post,
    )


def test_prepare_canonical_post_uses_exact_shared_pipeline():
    post = make_post()

    prepared = prepare_canonical_post(post)

    assert (
        prepared.catalog_content_hash
        == post.catalog_content_hash
    )

    assert prepared.raw_html.body == (
        post.html.encode("utf-8")
    )

    assert (
        prepared.raw_html.key
        == post.html_s3_key
    )

    assert (
        prepared.narration_document.key
        == post.document_s3_key(
            prepared.narration_hash,
            processor_version=prepared.processor_version,
        )
    )

    assert prepared.processor_version == 1
    assert "/p000001/" in prepared.narration_document.key
    assert (
        prepared.narration_document.metadata[
            "processor-version"
        ]
        == "1"
    )

    validated = validate_document(
        dict(prepared.document)
    )

    assert (
        validated["narration_hash"]
        == prepared.narration_hash
    )


def test_prepared_artifacts_are_deterministic_and_retry_safe():
    post = make_post()

    first = prepare_canonical_post(post)
    second = prepare_canonical_post(post)

    assert first == second

    store = FakeArtifactStore()

    store.put_immutable(first.raw_html)
    store.put_immutable(first.raw_html)

    store.put_immutable(
        first.narration_document
    )
    store.put_immutable(
        first.narration_document
    )

    assert len(store.objects) == 2


def test_immutable_store_rejects_different_bytes_at_same_key():
    post = make_post()

    prepared = prepare_canonical_post(post)

    store = FakeArtifactStore()
    store.put_immutable(
        prepared.raw_html
    )

    conflicting = type(
        prepared.raw_html
    )(
        key=prepared.raw_html.key,
        body=b"different",
        metadata=prepared.raw_html.metadata,
    )

    with pytest.raises(
        ImmutableArtifactConflictError
    ):
        store.put_immutable(conflicting)


def test_first_pass_persists_artifacts_then_observation_and_checkpoint():
    post = make_post()

    core, artifacts, catalog, state_store = (
        make_core()
    )

    result = core.process_first_pass_page(
        state=state_store.current,
        page=make_page([post]),
        seen_at=PAGE_TIME,
    )

    assert len(artifacts.objects) == 2
    assert len(catalog.observations) == 1
    assert state_store.advance_calls == 1

    assert result.expected_total == 1
    assert result.expected_pages == 1
    assert result.next_page == 2
    assert result.verification_pending is True


def test_sync_observation_cannot_touch_webhook_owned_fields():
    items = {
        "ghost-post-1": {
            "post_id": "ghost-post-1",
            "content_hash": "webhook-hash",
            "ghost_status": "PUBLISHED",
            "last_webhook_at": BEFORE_START,
            "first_seen_at": BEFORE_START,
            "updated_at": "2026-09-01T00:00:00Z",
        }
    }

    core, _, catalog, state_store = (
        make_core(items=items)
    )

    core.process_first_pass_page(
        state=state_store.current,
        page=make_page([make_post()]),
        seen_at=PAGE_TIME,
    )

    item = catalog.items["ghost-post-1"]

    assert item["content_hash"] == "webhook-hash"
    assert item["ghost_status"] == "PUBLISHED"
    assert item["last_webhook_at"] == BEFORE_START


def test_first_seen_at_is_preserved():
    items = {
        "ghost-post-1": {
            "post_id": "ghost-post-1",
            "first_seen_at": BEFORE_START,
        }
    }

    core, _, catalog, state_store = (
        make_core(items=items)
    )

    core.process_first_pass_page(
        state=state_store.current,
        page=make_page([make_post()]),
        seen_at=PAGE_TIME,
    )

    assert (
        catalog.items["ghost-post-1"]["first_seen_at"]
        == BEFORE_START
    )


def test_stale_sync_metadata_does_not_replace_newer_display_metadata():
    items = {
        "ghost-post-1": {
            "post_id": "ghost-post-1",
            "title": "Newer webhook title",
            "slug": "newer",
            "url": "https://example.test/newer/",
            "published_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-03T15:00:00Z",
        }
    }

    incoming = make_post(
        title="Older sync title",
        updated_at="2026-09-02T11:00:00Z",
    )

    core, _, catalog, state_store = (
        make_core(items=items)
    )

    core.process_first_pass_page(
        state=state_store.current,
        page=make_page([incoming]),
        seen_at=PAGE_TIME,
    )

    item = catalog.items["ghost-post-1"]

    assert item["title"] == "Newer webhook title"
    assert item["updated_at"] == "2026-09-03T15:00:00Z"

    # Sync-owned observation still reflects the Content API source.
    assert (
        item["catalog_content_hash"]
        == incoming.catalog_content_hash
    )


def test_equal_timestamp_allows_idempotent_metadata_refresh():
    assert metadata_is_not_older(
        "2026-09-03T12:00:00.000Z",
        "2026-09-03T12:00:00Z",
    )


def test_unsupported_access_fails_run_without_checkpoint_advance():
    core, artifacts, catalog, state_store = (
        make_core()
    )

    post = make_post(
        visibility="members",
        access=False,
    )

    with pytest.raises(
        UnsupportedGhostAccessError
    ):
        core.process_first_pass_page(
            state=state_store.current,
            page=make_page([post]),
            seen_at=PAGE_TIME,
        )

    assert state_store.current.status is SyncStatus.FAILED
    assert (
        state_store.current.last_error_code
        == "UNSUPPORTED_GHOST_ACCESS"
    )
    assert state_store.current.next_page == 1
    assert state_store.advance_calls == 0
    assert len(artifacts.objects) == 0
    assert len(catalog.observations) == 0


def test_later_page_pagination_change_fails_run_before_processing():
    initial = running_state(
        next_page=2,
        expected_total=200,
        expected_pages=2,
    )

    core, artifacts, catalog, state_store = (
        make_core(state=initial)
    )

    changed_page = make_page(
        [make_post()],
        page=2,
        pages=3,
        total=201,
    )

    with pytest.raises(
        CatalogChangedDuringSyncError
    ):
        core.process_first_pass_page(
            state=initial,
            page=changed_page,
            seen_at=PAGE_TIME,
        )

    assert state_store.current.status is SyncStatus.FAILED
    assert (
        state_store.current.last_error_code
        == "CATALOG_CHANGED_DURING_SYNC"
    )
    assert len(artifacts.objects) == 0
    assert len(catalog.observations) == 0


def test_checkpoint_advance_is_conditionally_owned():
    stale_state = running_state()

    core, _, _, state_store = make_core(
        state=stale_state
    )

    # Simulate another invocation winning CURRENT before this invocation
    # reaches the conditional checkpoint update.
    state_store.current = SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.RUNNING,
        next_page=2,
        started_at=START,
        updated_at=PAGE_TIME,
        expected_total=1,
        expected_pages=1,
    )

    with pytest.raises(
        SyncStateConflictError
    ):
        core.process_first_pass_page(
            state=stale_state,
            page=make_page([make_post()]),
            seen_at=PAGE_TIME,
        )

    assert state_store.current.next_page == 2


def test_resume_preserves_same_sync_id_and_checkpoint():
    failed = SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.FAILED,
        next_page=4,
        started_at=START,
        updated_at=PAGE_TIME,
        expected_total=714,
        expected_pages=8,
        last_error_code="CATALOG_CHANGED_DURING_SYNC",
    )

    core, _, _, state_store = make_core(
        state=failed
    )

    resumed = core.resume_failed(
        state=failed,
        now=VERIFY_TIME,
    )

    assert resumed.status is SyncStatus.RUNNING
    assert resumed.sync_id == SYNC_ID
    assert resumed.next_page == 4
    assert resumed.expected_total == 714
    assert resumed.expected_pages == 8
    assert resumed.last_error_code is None


def test_successful_verification_reconciles_then_completes():
    (
        core,
        _,
        catalog,
        state_store,
        verification_state,
        post,
    ) = process_one_post_to_verification(
        items={
            "old-post": {
                "post_id": "old-post",
                "source": "GHOST",
                "last_seen_sync_id": "sync_" + ("0" * 32),
                "last_webhook_at": BEFORE_START,
                "content_hash": "webhook-owned",
                "ghost_status": "PUBLISHED",
            }
        }
    )

    result = core.run_verification(
        state=verification_state,
        pages=[make_page([post])],
        completed_at=VERIFY_TIME,
    )

    assert (
        result.completed_state.status
        is SyncStatus.COMPLETE
    )

    assert (
        result.reconciliation.marked_post_ids
        == ("old-post",)
    )

    old_item = catalog.items["old-post"]

    assert (
        old_item["catalog_status"]
        == "NOT_IN_PUBLISHED_CATALOG"
    )

    # Reconciliation cannot touch webhook-owned state.
    assert old_item["content_hash"] == "webhook-owned"
    assert old_item["ghost_status"] == "PUBLISHED"

    assert state_store.complete_calls == 1


def test_verification_detects_content_change_and_does_not_reconcile():
    (
        core,
        _,
        catalog,
        state_store,
        verification_state,
        post,
    ) = process_one_post_to_verification()

    changed = make_post(
        html="<p>Changed after first pass.</p>",
    )

    with pytest.raises(
        CatalogChangedDuringSyncError
    ):
        core.run_verification(
            state=verification_state,
            pages=[make_page([changed])],
            completed_at=VERIFY_TIME,
        )

    assert state_store.current.status is SyncStatus.FAILED
    assert (
        state_store.current.last_error_code
        == "CATALOG_CHANGED_DURING_SYNC"
    )
    assert catalog.reconcile_calls == 0
    assert state_store.complete_calls == 0


def test_verification_detects_catalog_set_change():
    (
        core,
        _,
        catalog,
        state_store,
        verification_state,
        post,
    ) = process_one_post_to_verification()

    extra = make_post(
        post_id="ghost-post-2",
        slug="second",
        url="https://example.test/second/",
    )

    with pytest.raises(
        CatalogChangedDuringSyncError
    ):
        core.run_verification(
            state=verification_state,
            pages=[
                make_page(
                    [post, extra],
                    total=2,
                )
            ],
            completed_at=VERIFY_TIME,
        )

    assert state_store.current.status is SyncStatus.FAILED
    assert catalog.reconcile_calls == 0


def test_reconciliation_skips_realtime_webhook_race():
    (
        core,
        _,
        catalog,
        _,
        verification_state,
        post,
    ) = process_one_post_to_verification(
        items={
            "published-during-sync": {
                "post_id": "published-during-sync",
                "source": "GHOST",
                "last_seen_sync_id": "sync_" + ("0" * 32),
                "last_webhook_at": AFTER_START,
                "content_hash": "realtime-hash",
                "ghost_status": "PUBLISHED",
            }
        }
    )

    result = core.run_verification(
        state=verification_state,
        pages=[make_page([post])],
        completed_at=VERIFY_TIME,
    )

    assert (
        result.reconciliation
        .webhook_race_skipped_post_ids
        == ("published-during-sync",)
    )

    raced = catalog.items[
        "published-during-sync"
    ]

    assert "last_reconciled_sync_id" not in raced
    assert raced["content_hash"] == "realtime-hash"


def test_reconciliation_ignores_non_ghost_legacy_item():
    (
        core,
        _,
        catalog,
        _,
        verification_state,
        post,
    ) = process_one_post_to_verification(
        items={
            "legacy": {
                "post_id": "legacy",
                "slug": "legacy",
                "content_hash": "legacy-hash",
            }
        }
    )

    result = core.run_verification(
        state=verification_state,
        pages=[make_page([post])],
        completed_at=VERIFY_TIME,
    )

    assert result.reconciliation.marked_post_ids == ()
    assert (
        result.reconciliation
        .webhook_race_skipped_post_ids
        == ()
    )

    assert catalog.items["legacy"] == {
        "post_id": "legacy",
        "slug": "legacy",
        "content_hash": "legacy-hash",
    }


def test_verification_requires_first_pass_sentinel():
    state = running_state(
        next_page=1,
        expected_total=1,
        expected_pages=1,
    )

    core, _, _, _ = make_core(
        state=state
    )

    with pytest.raises(
        Exception,
        match="verification requires",
    ):
        core.run_verification(
            state=state,
            pages=[make_page([make_post()])],
            completed_at=VERIFY_TIME,
        )