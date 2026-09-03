import re

import pytest

from narration_content.sync_models import (
    CatalogAuthor,
    CatalogStatus,
    CatalogTag,
    ContentSyncContractError,
    GhostCatalogPost,
    SyncErrorCode,
    SyncState,
    SyncStatus,
    UnsupportedGhostAccessError,
    ghost_html_key,
    narration_document_key,
    new_running_sync_state,
    new_sync_id,
)

NOW = "2026-09-03T12:00:00Z"
LATER = "2026-09-03T12:01:00Z"

CONTENT_HASH = "a" * 64
NARRATION_HASH = "b" * 64


def make_post(**overrides):
    values = {
        "post_id": "ghost-post-123",
        "title": "A grateful day",
        "slug": "a-grateful-day",
        "url": "https://example.test/a-grateful-day/",
        "published_at": "2026-09-01T10:00:00.000Z",
        "updated_at": "2026-09-02T11:00:00.000Z",
        "html": "<p>Gratitude — café 🌱</p>",
        "visibility": "public",
        "access": True,
        "authors": (
            CatalogAuthor(
                id="author-1",
                name="Author",
                slug="author",
            ),
        ),
        "tags": (
            CatalogTag(
                id="tag-1",
                name="Gratitude",
                slug="gratitude",
            ),
        ),
    }
    values.update(overrides)
    return GhostCatalogPost(**values)


def test_sync_status_values_are_frozen():
    assert [value.value for value in SyncStatus] == [
        "RUNNING",
        "FAILED",
        "COMPLETE",
    ]


def test_catalog_status_values_are_frozen():
    assert [value.value for value in CatalogStatus] == [
        "PUBLISHED",
        "NOT_IN_PUBLISHED_CATALOG",
    ]


def test_documented_sync_error_codes_are_frozen():
    assert [value.value for value in SyncErrorCode] == [
        "UNSUPPORTED_GHOST_ACCESS",
        "CATALOG_CHANGED_DURING_SYNC",
    ]


def test_new_sync_id_uses_required_format():
    sync_id = new_sync_id()

    assert re.fullmatch(
        r"sync_[0-9a-f]{32}",
        sync_id,
    )


def test_new_sync_ids_are_distinct():
    assert new_sync_id() != new_sync_id()


def test_ghost_html_key_matches_frozen_layout():
    assert ghost_html_key(
        "post-123",
        CONTENT_HASH,
    ) == (
        "ghost/post-123/"
        f"{CONTENT_HASH}.html"
    )


def test_narration_document_key_matches_frozen_layout():
    assert narration_document_key(
        "post-123",
        CONTENT_HASH,
        1,
        NARRATION_HASH,
    ) == (
        "narration-documents/post-123/"
        f"{CONTENT_HASH}/"
        "p000001/"
        f"{NARRATION_HASH}.json"
    )


@pytest.mark.parametrize(
    "bad_hash",
    [
        "",
        "a" * 63,
        "A" * 64,
        "g" * 64,
    ],
)
def test_s3_key_builders_require_lowercase_sha256(bad_hash):
    with pytest.raises(ContentSyncContractError):
        ghost_html_key(
            "post-123",
            bad_hash,
        )


def test_post_computes_exact_existing_content_hash_contract():
    post = make_post()

    assert post.catalog_content_hash == (
        "43f46fe7d5ab23134de547c0e7a27e7a"
        "20ca511e923704607bd32cad4ca2eb50"
    )


def test_post_html_key_uses_computed_catalog_hash():
    post = make_post()

    assert post.html_s3_key == (
        "ghost/ghost-post-123/"
        f"{post.catalog_content_hash}.html"
    )


def test_post_document_key_uses_both_hashes():
    post = make_post()

    assert post.document_s3_key(
        NARRATION_HASH,
        processor_version=1,
    ) == (
        "narration-documents/ghost-post-123/"
        f"{post.catalog_content_hash}/"
        "p000001/"
        f"{NARRATION_HASH}.json"
    )


def test_public_accessible_post_passes_v1_access_gate():
    make_post().require_v1_access()


def test_nonpublic_post_fails_closed():
    post = make_post(visibility="members")

    with pytest.raises(
        UnsupportedGhostAccessError
    ) as exc_info:
        post.require_v1_access()

    assert (
        exc_info.value.error_code
        == "UNSUPPORTED_GHOST_ACCESS"
    )


def test_access_false_post_fails_closed():
    post = make_post(access=False)

    with pytest.raises(UnsupportedGhostAccessError):
        post.require_v1_access()


def test_post_requires_nonempty_html():
    with pytest.raises(
        ContentSyncContractError,
        match="html",
    ):
        make_post(html="")


def test_post_requires_boolean_access():
    with pytest.raises(
        ContentSyncContractError,
        match="access",
    ):
        make_post(access="true")


def test_post_keeps_exact_opaque_ghost_identity():
    post = make_post(
        post_id="opaque:ghost/id:value"
    )

    assert post.post_id == "opaque:ghost/id:value"
    assert post.html_s3_key.startswith(
        "ghost/opaque:ghost/id:value/"
    )


def test_author_and_tag_allow_optional_ids_and_slugs():
    author = CatalogAuthor(name="A")
    tag = CatalogTag(name="T")

    assert author.id is None
    assert author.slug is None
    assert tag.id is None
    assert tag.slug is None


def test_new_running_state_starts_at_page_one():
    state = new_running_sync_state(
        NOW,
        sync_id="sync_" + ("1" * 32),
    )

    assert state.sync_id == "sync_" + ("1" * 32)
    assert state.status is SyncStatus.RUNNING
    assert state.next_page == 1
    assert state.started_at == NOW
    assert state.updated_at == NOW
    assert state.expected_total is None
    assert state.expected_pages is None
    assert state.completed_at is None
    assert state.last_error_code is None
    assert state.verification_pending is False


def test_failed_state_keeps_checkpoint_and_requires_error_code():
    state = SyncState(
        sync_id="sync_" + ("2" * 32),
        status=SyncStatus.FAILED,
        next_page=4,
        started_at=NOW,
        updated_at=LATER,
        expected_total=714,
        expected_pages=8,
        last_error_code="CATALOG_CHANGED_DURING_SYNC",
    )

    assert state.next_page == 4
    assert (
        state.last_error_code
        == "CATALOG_CHANGED_DURING_SYNC"
    )


def test_failed_state_without_error_code_is_invalid():
    with pytest.raises(
        ContentSyncContractError,
        match="last_error_code",
    ):
        SyncState(
            sync_id="sync_" + ("3" * 32),
            status=SyncStatus.FAILED,
            next_page=3,
            started_at=NOW,
            updated_at=LATER,
        )


def test_complete_state_has_no_next_page_and_requires_completion_time():
    state = SyncState(
        sync_id="sync_" + ("4" * 32),
        status=SyncStatus.COMPLETE,
        next_page=None,
        started_at=NOW,
        updated_at=LATER,
        expected_total=714,
        expected_pages=8,
        completed_at=LATER,
    )

    assert state.next_page is None
    assert state.completed_at == LATER
    assert state.verification_pending is False


def test_complete_state_with_next_page_is_invalid():
    with pytest.raises(
        ContentSyncContractError,
        match="must not contain next_page",
    ):
        SyncState(
            sync_id="sync_" + ("5" * 32),
            status=SyncStatus.COMPLETE,
            next_page=9,
            started_at=NOW,
            updated_at=LATER,
            expected_total=714,
            expected_pages=8,
            completed_at=LATER,
        )


def test_expected_total_and_pages_must_be_set_together():
    with pytest.raises(
        ContentSyncContractError,
        match="must be set together",
    ):
        SyncState(
            sync_id="sync_" + ("6" * 32),
            status=SyncStatus.RUNNING,
            next_page=1,
            started_at=NOW,
            updated_at=LATER,
            expected_total=714,
        )


def test_checkpoint_cannot_advance_beyond_verification_sentinel():
    with pytest.raises(
        ContentSyncContractError,
        match=r"expected_pages \+ 1",
    ):
        SyncState(
            sync_id="sync_" + ("7" * 32),
            status=SyncStatus.RUNNING,
            next_page=10,
            started_at=NOW,
            updated_at=LATER,
            expected_total=714,
            expected_pages=8,
        )


def test_expected_pages_plus_one_means_verification_pending():
    state = SyncState(
        sync_id="sync_" + ("8" * 32),
        status=SyncStatus.RUNNING,
        next_page=9,
        started_at=NOW,
        updated_at=LATER,
        expected_total=714,
        expected_pages=8,
    )

    assert state.verification_pending is True


def test_timestamps_must_be_utc_z():
    with pytest.raises(
        ContentSyncContractError,
        match="ending in Z",
    ):
        new_running_sync_state(
            "2026-09-03T12:00:00+00:00"
        )


def test_sync_id_must_use_frozen_prefix_and_uuid_hex_shape():
    with pytest.raises(
        ContentSyncContractError,
        match="sync_<uuid4hex>",
    ):
        SyncState(
            sync_id="job_" + ("9" * 32),
            status=SyncStatus.RUNNING,
            next_page=1,
            started_at=NOW,
            updated_at=NOW,
        )


def test_running_state_must_not_carry_failure_code():
    with pytest.raises(
        ContentSyncContractError,
        match="only FAILED",
    ):
        SyncState(
            sync_id="sync_" + ("a" * 32),
            status=SyncStatus.RUNNING,
            next_page=1,
            started_at=NOW,
            updated_at=NOW,
            last_error_code="SOME_ERROR",
        )

def test_document_key_namespaces_processor_versions():
    version_one = narration_document_key(
        "post-123",
        CONTENT_HASH,
        1,
        NARRATION_HASH,
    )

    version_two = narration_document_key(
        "post-123",
        CONTENT_HASH,
        2,
        NARRATION_HASH,
    )

    assert version_one != version_two
    assert "/p000001/" in version_one
    assert "/p000002/" in version_two


@pytest.mark.parametrize(
    "processor_version",
    [0, -1, True, 1.5, "1", 1000000],
)
def test_document_key_requires_positive_integer_processor_version(
    processor_version,
):
    with pytest.raises(
        ContentSyncContractError,
        match="processor_version",
    ):
        narration_document_key(
            "post-123",
            CONTENT_HASH,
            processor_version,
            NARRATION_HASH,
        )