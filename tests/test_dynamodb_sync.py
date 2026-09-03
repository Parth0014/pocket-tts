import pytest

from narration_content.dynamodb_sync import (
    GHOST_SYNC_PK,
    GHOST_SYNC_SK,
    DynamoCatalogStore,
    DynamoCatalogStoreError,
    DynamoSyncStateStore,
    DynamoSyncStateStoreError,
)
from narration_content.sync_core import (
    CatalogObservation,
    SyncStateConflictError,
)
from narration_content.sync_models import (
    CatalogAuthor,
    CatalogStatus,
    CatalogTag,
    SyncState,
    SyncStatus,
)

SYNC_ID = "sync_" + ("1" * 32)
START = "2026-09-03T12:00:00Z"
NOW = "2026-09-03T12:01:00Z"


class FakeAwsError(Exception):
    def __init__(
        self,
        code,
        *,
        sensitive_message="",
    ):
        super().__init__(
            sensitive_message
        )

        self.response = {
            "Error": {
                "Code": code,
            }
        }


class RecordingTable:
    def __init__(self):
        self.calls = []
        self.get_responses = []
        self.scan_responses = []
        self.update_responses = []
        self.put_error = None
        self.update_errors = []

    def update_item(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("update_item", kwargs)
        )

        if self.update_errors:
            error = self.update_errors.pop(0)

            if error is not None:
                raise error

        if self.update_responses:
            return self.update_responses.pop(0)

        return {}

    def get_item(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("get_item", kwargs)
        )

        if self.get_responses:
            response = self.get_responses.pop(0)

            if isinstance(
                response,
                Exception,
            ):
                raise response

            return response

        return {}

    def scan(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("scan", kwargs)
        )

        if self.scan_responses:
            response = self.scan_responses.pop(0)

            if isinstance(
                response,
                Exception,
            ):
                raise response

            return response

        return {
            "Items": [],
        }

    def put_item(
        self,
        **kwargs,
    ):
        self.calls.append(
            ("put_item", kwargs)
        )

        if self.put_error is not None:
            raise self.put_error

        return {}


def make_observation():
    return CatalogObservation(
        post_id="ghost-post-1",
        schema_version=1,
        source="GHOST",
        first_seen_at_candidate=START,
        title="A grateful day",
        slug="a-grateful-day",
        url="https://example.test/a-grateful-day/",
        excerpt="A short excerpt",
        feature_image=None,
        published_at="2026-09-01T10:00:00.000Z",
        updated_at="2026-09-02T11:00:00.000Z",
        catalog_content_hash="a" * 64,
        catalog_updated_at=(
            "2026-09-02T11:00:00.000Z"
        ),
        catalog_status=CatalogStatus.PUBLISHED,
        visibility="public",
        access=True,
        authors=(
            CatalogAuthor(
                name="Author",
                id="author-1",
                slug="author",
            ),
        ),
        tags=(
            CatalogTag(
                name="Gratitude",
                id="tag-1",
                slug="gratitude",
            ),
        ),
        last_seen_sync_id=SYNC_ID,
        last_seen_at=NOW,
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
        updated_at=NOW,
        expected_total=expected_total,
        expected_pages=expected_pages,
        completed_at=None,
        last_error_code=None,
    )


def state_item(
    state,
):
    item = {
        "pk": GHOST_SYNC_PK,
        "sk": GHOST_SYNC_SK,
        "sync_id": state.sync_id,
        "status": state.status.value,
        "started_at": state.started_at,
        "updated_at": state.updated_at,
    }

    for field in (
        "next_page",
        "expected_total",
        "expected_pages",
        "completed_at",
        "last_error_code",
    ):
        value = getattr(
            state,
            field,
        )

        if value is not None:
            item[field] = value

    return item


def test_catalog_observation_is_whitelist_only():
    table = RecordingTable()

    store = DynamoCatalogStore(
        table=table
    )

    store.observe_post(
        make_observation()
    )

    assert len(table.calls) == 2

    operation, base = table.calls[0]

    assert operation == "update_item"

    assert base["Key"] == {
        "post_id": "ghost-post-1"
    }

    written_names = set(
        base[
            "ExpressionAttributeNames"
        ].values()
    )

    assert "content_hash" not in written_names
    assert "ghost_status" not in written_names
    assert "last_webhook_at" not in written_names

    assert "catalog_content_hash" in written_names
    assert "catalog_status" in written_names
    assert "last_seen_sync_id" in written_names
    assert "first_seen_at" in written_names

    expression = base[
        "UpdateExpression"
    ]

    assert "if_not_exists" in expression


def test_display_metadata_has_atomic_freshness_guard():
    table = RecordingTable()

    DynamoCatalogStore(
        table=table
    ).observe_post(
        make_observation()
    )

    _, call = table.calls[1]

    assert call["ConditionExpression"] == (
        "attribute_not_exists(#updated_at) "
        "OR #updated_at <= :incoming_updated_at"
    )

    names = set(
        call[
            "ExpressionAttributeNames"
        ].values()
    )

    assert "title" in names
    assert "updated_at" in names

    # None is skipped rather than removing or nulling
    # pre-existing webhook metadata.
    assert "feature_image" not in names


def test_stale_display_metadata_condition_is_expected_noop():
    table = RecordingTable()

    table.update_errors = [
        None,
        FakeAwsError(
            "ConditionalCheckFailedException"
        ),
    ]

    store = DynamoCatalogStore(
        table=table
    )

    store.observe_post(
        make_observation()
    )

    assert len(table.calls) == 2


def test_catalog_unknown_failure_is_sanitized():
    table = RecordingTable()

    table.update_errors = [
        RuntimeError(
            "sensitive SDK details"
        )
    ]

    store = DynamoCatalogStore(
        table=table
    )

    with pytest.raises(
        DynamoCatalogStoreError
    ) as exc_info:
        store.observe_post(
            make_observation()
        )

    assert "sensitive" not in str(
        exc_info.value
    )


def test_verification_record_is_loaded_consistently():
    table = RecordingTable()

    table.get_responses = [
        {
            "Item": {
                "post_id": "ghost-post-1",
                "last_seen_sync_id": SYNC_ID,
                "catalog_content_hash": "a" * 64,
                "catalog_updated_at": (
                    "2026-09-02T11:00:00.000Z"
                ),
                "catalog_status": "PUBLISHED",
                "visibility": "public",
                "access": True,
            }
        }
    ]

    record = DynamoCatalogStore(
        table=table
    ).get_verification_record(
        "ghost-post-1"
    )

    assert record is not None
    assert record.post_id == "ghost-post-1"
    assert record.last_seen_sync_id == SYNC_ID
    assert record.catalog_status is (
        CatalogStatus.PUBLISHED
    )

    _, call = table.calls[0]

    assert call["ConsistentRead"] is True


def test_missing_verification_record_returns_none():
    table = RecordingTable()

    table.get_responses = [{}]

    assert (
        DynamoCatalogStore(
            table=table
        ).get_verification_record(
            "missing"
        )
        is None
    )


def test_reconciliation_marks_only_safe_unseen_ghost_items():
    table = RecordingTable()

    table.scan_responses = [
        {
            "Items": [
                {
                    "post_id": "safe",
                    "source": "GHOST",
                    "last_seen_sync_id": "old-sync",
                    "last_webhook_at": (
                        "2026-09-03T11:00:00Z"
                    ),
                },
                {
                    "post_id": "seen",
                    "source": "GHOST",
                    "last_seen_sync_id": SYNC_ID,
                },
                {
                    "post_id": "race",
                    "source": "GHOST",
                    "last_seen_sync_id": "old-sync",
                    "last_webhook_at": (
                        "2026-09-03T12:00:30Z"
                    ),
                },
                {
                    "post_id": "legacy",
                    "source": "LEGACY",
                    "last_seen_sync_id": "old-sync",
                },
            ]
        }
    ]

    result = DynamoCatalogStore(
        table=table
    ).reconcile_absent(
        current_sync_id=SYNC_ID,
        sync_started_at=START,
        reconciled_at=NOW,
    )

    assert result.marked_post_ids == (
        "safe",
    )

    assert (
        result.webhook_race_skipped_post_ids
        == ("race",)
    )

    update_calls = [
        kwargs
        for name, kwargs in table.calls
        if name == "update_item"
    ]

    assert len(update_calls) == 1

    update_names = set(
        update_calls[0][
            "ExpressionAttributeNames"
        ].values()
    )

    update_expression = (
        update_calls[0][
            "UpdateExpression"
        ]
    )

    # Reading last_webhook_at in the condition is allowed.
    assert "last_webhook_at" in update_names

    # The actual SET list is sync-owned only.
    assert "#catalog_status" in update_expression
    assert "#last_reconciled_sync_id" in update_expression
    assert "#last_reconciled_at" in update_expression


def test_reconciliation_detects_webhook_race_after_scan():
    table = RecordingTable()

    table.scan_responses = [
        {
            "Items": [
                {
                    "post_id": "race",
                    "source": "GHOST",
                    "last_seen_sync_id": "old-sync",
                    "last_webhook_at": (
                        "2026-09-03T11:00:00Z"
                    ),
                },
            ]
        }
    ]

    table.update_errors = [
        FakeAwsError(
            "ConditionalCheckFailedException"
        )
    ]

    table.get_responses = [
        {
            "Item": {
                "post_id": "race",
                "source": "GHOST",
                "last_webhook_at": (
                    "2026-09-03T12:00:30Z"
                ),
            }
        }
    ]

    result = DynamoCatalogStore(
        table=table
    ).reconcile_absent(
        current_sync_id=SYNC_ID,
        sync_started_at=START,
        reconciled_at=NOW,
    )

    assert result.marked_post_ids == ()

    assert (
        result.webhook_race_skipped_post_ids
        == ("race",)
    )


def test_sync_state_start_new_uses_guarded_current_write():
    table = RecordingTable()

    state = DynamoSyncStateStore(
        table=table
    ).start_new(
        now=START
    )

    assert state.status is SyncStatus.RUNNING
    assert state.next_page == 1
    assert state.sync_id.startswith(
        "sync_"
    )

    operation, call = table.calls[0]

    assert operation == "put_item"

    assert call["Item"]["pk"] == GHOST_SYNC_PK
    assert call["Item"]["sk"] == GHOST_SYNC_SK
    assert call["Item"]["status"] == "RUNNING"

    assert (
        "attribute_not_exists"
        in call["ConditionExpression"]
    )

    assert (
        ":complete"
        in call["ExpressionAttributeValues"]
    )


def test_active_current_cannot_be_replaced():
    table = RecordingTable()

    table.put_error = FakeAwsError(
        "ConditionalCheckFailedException"
    )

    with pytest.raises(
        SyncStateConflictError
    ):
        DynamoSyncStateStore(
            table=table
        ).start_new(
            now=START
        )


def test_get_current_uses_consistent_read():
    table = RecordingTable()

    state = running_state()

    table.get_responses = [
        {
            "Item": state_item(
                state
            )
        }
    ]

    result = DynamoSyncStateStore(
        table=table
    ).get_current()

    assert result == state

    _, call = table.calls[0]

    assert call["Key"] == {
        "pk": GHOST_SYNC_PK,
        "sk": GHOST_SYNC_SK,
    }

    assert call["ConsistentRead"] is True


def test_advance_page_has_exact_ownership_and_pagination_guards():
    table = RecordingTable()

    returned = running_state(
        next_page=2,
        expected_total=714,
        expected_pages=8,
    )

    table.update_responses = [
        {
            "Attributes": (
                state_item(returned)
            )
        }
    ]

    result = DynamoSyncStateStore(
        table=table
    ).advance_page(
        expected_sync_id=SYNC_ID,
        expected_status=SyncStatus.RUNNING,
        expected_next_page=1,
        new_next_page=2,
        expected_total=714,
        expected_pages=8,
        updated_at=NOW,
    )

    assert result == returned

    _, call = table.calls[0]

    condition = call[
        "ConditionExpression"
    ]

    assert "#sync_id = :expected_sync_id" in condition
    assert "#status = :expected_status" in condition
    assert "#next_page = :expected_next_page" in condition
    assert "#expected_total = :expected_total" in condition
    assert "#expected_pages = :expected_pages" in condition


def test_fail_resume_complete_are_conditional_transitions():
    failed = SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.FAILED,
        next_page=2,
        started_at=START,
        updated_at=NOW,
        expected_total=714,
        expected_pages=8,
        completed_at=None,
        last_error_code=(
            "CATALOG_CHANGED_DURING_SYNC"
        ),
    )

    running = running_state(
        next_page=2,
        expected_total=714,
        expected_pages=8,
    )

    complete = SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.COMPLETE,
        next_page=None,
        started_at=START,
        updated_at=NOW,
        expected_total=714,
        expected_pages=8,
        completed_at=NOW,
        last_error_code=None,
    )

    table = RecordingTable()

    table.update_responses = [
        {
            "Attributes": state_item(
                failed
            )
        },
        {
            "Attributes": state_item(
                running
            )
        },
        {
            "Attributes": state_item(
                complete
            )
        },
    ]

    store = DynamoSyncStateStore(
        table=table
    )

    assert (
        store.fail(
            expected_sync_id=SYNC_ID,
            expected_status=SyncStatus.RUNNING,
            expected_next_page=2,
            error_code=(
                "CATALOG_CHANGED_DURING_SYNC"
            ),
            updated_at=NOW,
        )
        == failed
    )

    assert (
        store.resume(
            expected_sync_id=SYNC_ID,
            expected_next_page=2,
            updated_at=NOW,
        )
        == running
    )

    assert (
        store.complete(
            expected_sync_id=SYNC_ID,
            expected_next_page=9,
            completed_at=NOW,
        )
        == complete
    )

    for name, call in table.calls:
        assert name == "update_item"
        assert (
            "ConditionExpression"
            in call
        )
        assert (
            call["ReturnValues"]
            == "ALL_NEW"
        )


def test_conditional_state_race_maps_to_core_conflict():
    table = RecordingTable()

    table.update_errors = [
        FakeAwsError(
            "ConditionalCheckFailedException"
        )
    ]

    store = DynamoSyncStateStore(
        table=table
    )

    with pytest.raises(
        SyncStateConflictError,
        match="ownership lost",
    ):
        store.advance_page(
            expected_sync_id=SYNC_ID,
            expected_status=SyncStatus.RUNNING,
            expected_next_page=1,
            new_next_page=2,
            expected_total=714,
            expected_pages=8,
            updated_at=NOW,
        )


def test_nonconditional_state_failure_is_sanitized():
    table = RecordingTable()

    table.update_errors = [
        RuntimeError(
            "sensitive DynamoDB details"
        )
    ]

    store = DynamoSyncStateStore(
        table=table
    )

    with pytest.raises(
        DynamoSyncStateStoreError
    ) as exc_info:
        store.advance_page(
            expected_sync_id=SYNC_ID,
            expected_status=SyncStatus.RUNNING,
            expected_next_page=1,
            new_next_page=2,
            expected_total=714,
            expected_pages=8,
            updated_at=NOW,
        )

    assert "sensitive" not in str(
        exc_info.value
    )

def test_current_state_converts_dynamodb_decimal_numbers():
    from decimal import Decimal

    table = RecordingTable()

    table.get_responses = [
        {
            "Item": {
                "pk": GHOST_SYNC_PK,
                "sk": GHOST_SYNC_SK,
                "sync_id": SYNC_ID,
                "status": "RUNNING",
                "next_page": Decimal("1"),
                "started_at": START,
                "updated_at": NOW,
                "expected_total": Decimal("714"),
                "expected_pages": Decimal("8"),
            }
        }
    ]

    state = DynamoSyncStateStore(
        table=table
    ).get_current()

    assert state is not None

    assert state.next_page == 1
    assert type(state.next_page) is int

    assert state.expected_total == 714
    assert type(state.expected_total) is int

    assert state.expected_pages == 8
    assert type(state.expected_pages) is int