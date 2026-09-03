"""DynamoDB persistence adapters for Content Sync V1.

Both adapters consume injected boto3-compatible DynamoDB Table objects.
This module does not construct AWS clients and does not read environment
variables.

NarrationPosts writes are deliberately whitelist-based. Catalog observation
writes are separated from display-metadata writes so that Content Sync never
uses a generic dataclass-to-DynamoDB serializer.

The CURRENT sync-state item uses conditional ownership for every transition.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from uuid import uuid4

from .sync_core import (
    CatalogObservation,
    CatalogVerificationRecord,
    ReconciliationResult,
    SyncStateConflictError,
)
from .sync_models import (
    CatalogStatus,
    SyncState,
    SyncStatus,
)

GHOST_SYNC_PK = "SYSTEM#GHOST_SYNC"
GHOST_SYNC_SK = "CURRENT"

GHOST_SYNC_ENTITY_TYPE = "ghost_sync_state"
SCHEMA_VERSION = 1


class DynamoCatalogStoreError(RuntimeError):
    """Raised when NarrationPosts persistence fails."""


class DynamoSyncStateStoreError(RuntimeError):
    """Raised for non-conditional sync-state persistence failures."""


def _aws_error_code(
    exc: Exception,
) -> str | None:
    response = getattr(
        exc,
        "response",
        None,
    )

    if not isinstance(response, Mapping):
        return None

    error = response.get("Error")

    if not isinstance(error, Mapping):
        return None

    code = error.get("Code")

    if not isinstance(code, str):
        return None

    return code


def _is_conditional_failure(
    exc: Exception,
) -> bool:
    return (
        _aws_error_code(exc)
        == "ConditionalCheckFailedException"
    )


def _dynamo_optional_int(
    value: Any,
    *,
    field: str,
) -> int | None:
    """Convert an exact DynamoDB integer into a native Python int."""

    if value is None:
        return None

    if isinstance(value, bool):
        raise DynamoSyncStateStoreError(
            f"Ghost sync CURRENT {field} is invalid"
        )

    if isinstance(value, int):
        return value

    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise DynamoSyncStateStoreError(
                f"Ghost sync CURRENT {field} is invalid"
            )

        return int(value)

    raise DynamoSyncStateStoreError(
        f"Ghost sync CURRENT {field} is invalid"
    )

def _author_item(author) -> dict[str, str]:
    result = {
        "name": author.name,
    }

    if author.id is not None:
        result["id"] = author.id

    if author.slug is not None:
        result["slug"] = author.slug

    return result


def _tag_item(tag) -> dict[str, str]:
    result = {
        "name": tag.name,
    }

    if tag.id is not None:
        result["id"] = tag.id

    if tag.slug is not None:
        result["slug"] = tag.slug

    return result


class DynamoCatalogStore:
    """NarrationPosts implementation of the CatalogStore protocol."""

    def __init__(
        self,
        *,
        table,
    ) -> None:
        self._table = table

    def observe_post(
        self,
        observation: CatalogObservation,
    ) -> None:
        """Persist one whitelist-only Content Sync observation."""

        self._write_catalog_observation(
            observation
        )

        self._write_display_metadata_if_fresh(
            observation
        )

    def _write_catalog_observation(
        self,
        observation: CatalogObservation,
    ) -> None:
        names = {
            "#schema_version": "schema_version",
            "#source": "source",
            "#first_seen_at": "first_seen_at",
            "#catalog_content_hash": "catalog_content_hash",
            "#catalog_updated_at": "catalog_updated_at",
            "#catalog_status": "catalog_status",
            "#visibility": "visibility",
            "#access": "access",
            "#authors": "authors",
            "#tags": "tags",
            "#last_seen_sync_id": "last_seen_sync_id",
            "#last_seen_at": "last_seen_at",
        }

        values = {
            ":schema_version": observation.schema_version,
            ":source": observation.source,
            ":first_seen_at": (
                observation.first_seen_at_candidate
            ),
            ":catalog_content_hash": (
                observation.catalog_content_hash
            ),
            ":catalog_updated_at": (
                observation.catalog_updated_at
            ),
            ":catalog_status": (
                observation.catalog_status.value
            ),
            ":visibility": observation.visibility,
            ":access": observation.access,
            ":authors": [
                _author_item(author)
                for author in observation.authors
            ],
            ":tags": [
                _tag_item(tag)
                for tag in observation.tags
            ],
            ":last_seen_sync_id": (
                observation.last_seen_sync_id
            ),
            ":last_seen_at": (
                observation.last_seen_at
            ),
        }

        update_expression = (
            "SET "
            "#schema_version = :schema_version, "
            "#source = :source, "
            "#first_seen_at = "
            "if_not_exists(#first_seen_at, :first_seen_at), "
            "#catalog_content_hash = :catalog_content_hash, "
            "#catalog_updated_at = :catalog_updated_at, "
            "#catalog_status = :catalog_status, "
            "#visibility = :visibility, "
            "#access = :access, "
            "#authors = :authors, "
            "#tags = :tags, "
            "#last_seen_sync_id = :last_seen_sync_id, "
            "#last_seen_at = :last_seen_at"
        )

        try:
            self._table.update_item(
                Key={
                    "post_id": observation.post_id,
                },
                UpdateExpression=update_expression,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except Exception:
            raise DynamoCatalogStoreError(
                "NarrationPosts catalog observation write failed"
            ) from None

    def _write_display_metadata_if_fresh(
        self,
        observation: CatalogObservation,
    ) -> None:
        display_fields = {
            "title": observation.title,
            "slug": observation.slug,
            "url": observation.url,
            "excerpt": observation.excerpt,
            "feature_image": observation.feature_image,
            "published_at": observation.published_at,
            "updated_at": observation.updated_at,
        }

        names: dict[str, str] = {
            "#updated_at": "updated_at",
        }

        values: dict[str, Any] = {
            ":incoming_updated_at": (
                observation.updated_at
            ),
        }

        assignments: list[str] = []

        for field, value in display_fields.items():
            if value is None:
                continue

            name_token = f"#{field}"
            value_token = f":{field}"

            names[name_token] = field
            values[value_token] = value

            assignments.append(
                f"{name_token} = {value_token}"
            )

        if not assignments:
            return

        try:
            self._table.update_item(
                Key={
                    "post_id": observation.post_id,
                },
                UpdateExpression=(
                    "SET " + ", ".join(assignments)
                ),
                ConditionExpression=(
                    "attribute_not_exists(#updated_at) "
                    "OR #updated_at <= :incoming_updated_at"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except Exception as exc:
            if _is_conditional_failure(exc):
                # A newer webhook/catalog observation already won.
                return

            raise DynamoCatalogStoreError(
                "NarrationPosts display metadata write failed"
            ) from None

    def get_verification_record(
        self,
        post_id: str,
    ) -> CatalogVerificationRecord | None:
        try:
            response = self._table.get_item(
                Key={
                    "post_id": post_id,
                },
                ConsistentRead=True,
            )
        except Exception:
            raise DynamoCatalogStoreError(
                "NarrationPosts verification read failed"
            ) from None

        if not isinstance(response, Mapping):
            raise DynamoCatalogStoreError(
                "NarrationPosts verification response is invalid"
            )

        item = response.get("Item")

        if item is None:
            return None

        if not isinstance(item, Mapping):
            raise DynamoCatalogStoreError(
                "NarrationPosts verification item is invalid"
            )

        try:
            return CatalogVerificationRecord(
                post_id=str(item["post_id"]),
                last_seen_sync_id=str(
                    item["last_seen_sync_id"]
                ),
                catalog_content_hash=str(
                    item["catalog_content_hash"]
                ),
                catalog_updated_at=str(
                    item["catalog_updated_at"]
                ),
                catalog_status=CatalogStatus(
                    item["catalog_status"]
                ),
                visibility=str(
                    item["visibility"]
                ),
                access=item["access"],
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            raise DynamoCatalogStoreError(
                "NarrationPosts verification item is malformed"
            ) from None

    def reconcile_absent(
        self,
        *,
        current_sync_id: str,
        sync_started_at: str,
        reconciled_at: str,
    ) -> ReconciliationResult:
        marked: list[str] = []
        webhook_race_skipped: list[str] = []

        last_key = None

        while True:
            scan_args: dict[str, Any] = {
                "ProjectionExpression": (
                    "#post_id, #source, "
                    "#last_seen_sync_id, #last_webhook_at"
                ),
                "ExpressionAttributeNames": {
                    "#post_id": "post_id",
                    "#source": "source",
                    "#last_seen_sync_id": (
                        "last_seen_sync_id"
                    ),
                    "#last_webhook_at": (
                        "last_webhook_at"
                    ),
                },
            }

            if last_key is not None:
                scan_args["ExclusiveStartKey"] = (
                    last_key
                )

            try:
                response = self._table.scan(
                    **scan_args
                )
            except Exception:
                raise DynamoCatalogStoreError(
                    "NarrationPosts reconciliation scan failed"
                ) from None

            if not isinstance(response, Mapping):
                raise DynamoCatalogStoreError(
                    "NarrationPosts reconciliation response is invalid"
                )

            items = response.get(
                "Items",
                [],
            )

            if not isinstance(items, list):
                raise DynamoCatalogStoreError(
                    "NarrationPosts reconciliation items are invalid"
                )

            for item in items:
                if not isinstance(item, Mapping):
                    raise DynamoCatalogStoreError(
                        "NarrationPosts reconciliation item is invalid"
                    )

                post_id = item.get("post_id")

                if not isinstance(post_id, str):
                    raise DynamoCatalogStoreError(
                        "NarrationPosts reconciliation post_id is invalid"
                    )

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
                    isinstance(last_webhook_at, str)
                    and last_webhook_at
                    >= sync_started_at
                ):
                    webhook_race_skipped.append(
                        post_id
                    )
                    continue

                outcome = self._mark_absent_if_safe(
                    post_id=post_id,
                    current_sync_id=current_sync_id,
                    sync_started_at=sync_started_at,
                    reconciled_at=reconciled_at,
                )

                if outcome == "MARKED":
                    marked.append(post_id)

                elif outcome == "WEBHOOK_RACE":
                    webhook_race_skipped.append(
                        post_id
                    )

            last_key = response.get(
                "LastEvaluatedKey"
            )

            if not last_key:
                break

        return ReconciliationResult(
            marked_post_ids=tuple(
                sorted(set(marked))
            ),
            webhook_race_skipped_post_ids=tuple(
                sorted(
                    set(webhook_race_skipped)
                )
            ),
        )

    def _mark_absent_if_safe(
        self,
        *,
        post_id: str,
        current_sync_id: str,
        sync_started_at: str,
        reconciled_at: str,
    ) -> str:
        names = {
            "#source": "source",
            "#last_seen_sync_id": (
                "last_seen_sync_id"
            ),
            "#last_webhook_at": (
                "last_webhook_at"
            ),
            "#catalog_status": (
                "catalog_status"
            ),
            "#last_reconciled_sync_id": (
                "last_reconciled_sync_id"
            ),
            "#last_reconciled_at": (
                "last_reconciled_at"
            ),
        }

        values = {
            ":ghost": "GHOST",
            ":current_sync_id": (
                current_sync_id
            ),
            ":sync_started_at": (
                sync_started_at
            ),
            ":absent_status": (
                CatalogStatus.NOT_IN_PUBLISHED_CATALOG.value
            ),
            ":reconciled_at": reconciled_at,
        }

        try:
            self._table.update_item(
                Key={
                    "post_id": post_id,
                },
                UpdateExpression=(
                    "SET "
                    "#catalog_status = :absent_status, "
                    "#last_reconciled_sync_id = "
                    ":current_sync_id, "
                    "#last_reconciled_at = :reconciled_at"
                ),
                ConditionExpression=(
                    "#source = :ghost "
                    "AND "
                    "(attribute_not_exists(#last_seen_sync_id) "
                    "OR #last_seen_sync_id <> :current_sync_id) "
                    "AND "
                    "(attribute_not_exists(#last_webhook_at) "
                    "OR #last_webhook_at < :sync_started_at)"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )

            return "MARKED"

        except Exception as exc:
            if not _is_conditional_failure(exc):
                raise DynamoCatalogStoreError(
                    "NarrationPosts reconciliation update failed"
                ) from None

        # Something changed between scan and conditional update.
        try:
            response = self._table.get_item(
                Key={
                    "post_id": post_id,
                },
                ConsistentRead=True,
            )
        except Exception:
            raise DynamoCatalogStoreError(
                "NarrationPosts reconciliation race read failed"
            ) from None

        item = response.get("Item")

        if not isinstance(item, Mapping):
            return "SKIPPED"

        latest_webhook_at = item.get(
            "last_webhook_at"
        )

        if (
            isinstance(latest_webhook_at, str)
            and latest_webhook_at
            >= sync_started_at
        ):
            return "WEBHOOK_RACE"

        return "SKIPPED"


class DynamoSyncStateStore:
    """pocket-tts-app CURRENT state implementation."""

    def __init__(
        self,
        *,
        table,
    ) -> None:
        self._table = table

    @property
    def key(self) -> dict[str, str]:
        return {
            "pk": GHOST_SYNC_PK,
            "sk": GHOST_SYNC_SK,
        }

    def get_current(
        self,
    ) -> SyncState | None:
        try:
            response = self._table.get_item(
                Key=self.key,
                ConsistentRead=True,
            )
        except Exception:
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT read failed"
            ) from None

        if not isinstance(response, Mapping):
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT response is invalid"
            )

        item = response.get("Item")

        if item is None:
            return None

        if not isinstance(item, Mapping):
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT item is invalid"
            )

        return self._state_from_item(
            item
        )

    def start_new(
        self,
        *,
        now: str,
    ) -> SyncState:
        state = SyncState(
            sync_id=(
                "sync_" + uuid4().hex
            ),
            status=SyncStatus.RUNNING,
            next_page=1,
            started_at=now,
            updated_at=now,
            expected_total=None,
            expected_pages=None,
            completed_at=None,
            last_error_code=None,
        )

        item = {
            **self.key,
            "entity_type": (
                GHOST_SYNC_ENTITY_TYPE
            ),
            "schema_version": (
                SCHEMA_VERSION
            ),
            **self._state_attributes(
                state
            ),
        }

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(#pk) "
                    "OR #status = :complete"
                ),
                ExpressionAttributeNames={
                    "#pk": "pk",
                    "#status": "status",
                },
                ExpressionAttributeValues={
                    ":complete": (
                        SyncStatus.COMPLETE.value
                    ),
                },
            )
        except Exception as exc:
            if _is_conditional_failure(exc):
                raise SyncStateConflictError(
                    "cannot replace active Ghost sync CURRENT"
                ) from None

            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT creation failed"
            ) from None

        return state

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
        return self._conditional_update(
            UpdateExpression=(
                "SET "
                "#next_page = :new_next_page, "
                "#expected_total = "
                "if_not_exists("
                "#expected_total, :expected_total"
                "), "
                "#expected_pages = "
                "if_not_exists("
                "#expected_pages, :expected_pages"
                "), "
                "#updated_at = :updated_at"
            ),
            ConditionExpression=(
                "#sync_id = :expected_sync_id "
                "AND #status = :expected_status "
                "AND #next_page = :expected_next_page "
                "AND "
                "(attribute_not_exists(#expected_total) "
                "OR #expected_total = :expected_total) "
                "AND "
                "(attribute_not_exists(#expected_pages) "
                "OR #expected_pages = :expected_pages)"
            ),
            ExpressionAttributeNames={
                "#sync_id": "sync_id",
                "#status": "status",
                "#next_page": "next_page",
                "#expected_total": "expected_total",
                "#expected_pages": "expected_pages",
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":expected_sync_id": (
                    expected_sync_id
                ),
                ":expected_status": (
                    expected_status.value
                ),
                ":expected_next_page": (
                    expected_next_page
                ),
                ":new_next_page": (
                    new_next_page
                ),
                ":expected_total": (
                    expected_total
                ),
                ":expected_pages": (
                    expected_pages
                ),
                ":updated_at": updated_at,
            },
        )

    def fail(
        self,
        *,
        expected_sync_id: str,
        expected_status: SyncStatus,
        expected_next_page: int,
        error_code: str,
        updated_at: str,
    ) -> SyncState:
        return self._conditional_update(
            UpdateExpression=(
                "SET "
                "#status = :failed, "
                "#last_error_code = :error_code, "
                "#updated_at = :updated_at"
            ),
            ConditionExpression=(
                "#sync_id = :expected_sync_id "
                "AND #status = :expected_status "
                "AND #next_page = :expected_next_page"
            ),
            ExpressionAttributeNames={
                "#sync_id": "sync_id",
                "#status": "status",
                "#next_page": "next_page",
                "#last_error_code": (
                    "last_error_code"
                ),
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":expected_sync_id": (
                    expected_sync_id
                ),
                ":expected_status": (
                    expected_status.value
                ),
                ":expected_next_page": (
                    expected_next_page
                ),
                ":failed": (
                    SyncStatus.FAILED.value
                ),
                ":error_code": error_code,
                ":updated_at": updated_at,
            },
        )

    def resume(
        self,
        *,
        expected_sync_id: str,
        expected_next_page: int,
        updated_at: str,
    ) -> SyncState:
        return self._conditional_update(
            UpdateExpression=(
                "SET "
                "#status = :running, "
                "#updated_at = :updated_at "
                "REMOVE #last_error_code"
            ),
            ConditionExpression=(
                "#sync_id = :expected_sync_id "
                "AND #status = :failed "
                "AND #next_page = :expected_next_page"
            ),
            ExpressionAttributeNames={
                "#sync_id": "sync_id",
                "#status": "status",
                "#next_page": "next_page",
                "#updated_at": "updated_at",
                "#last_error_code": (
                    "last_error_code"
                ),
            },
            ExpressionAttributeValues={
                ":expected_sync_id": (
                    expected_sync_id
                ),
                ":expected_next_page": (
                    expected_next_page
                ),
                ":failed": (
                    SyncStatus.FAILED.value
                ),
                ":running": (
                    SyncStatus.RUNNING.value
                ),
                ":updated_at": updated_at,
            },
        )

    def complete(
        self,
        *,
        expected_sync_id: str,
        expected_next_page: int,
        completed_at: str,
    ) -> SyncState:
        return self._conditional_update(
            UpdateExpression=(
                "SET "
                "#status = :complete, "
                "#completed_at = :completed_at, "
                "#updated_at = :completed_at "
                "REMOVE #last_error_code, #next_page"
            ),
            ConditionExpression=(
                "#sync_id = :expected_sync_id "
                "AND #status = :running "
                "AND #next_page = :expected_next_page"
            ),
            ExpressionAttributeNames={
                "#sync_id": "sync_id",
                "#status": "status",
                "#next_page": "next_page",
                "#completed_at": "completed_at",
                "#updated_at": "updated_at",
                "#last_error_code": (
                    "last_error_code"
                ),
            },
            ExpressionAttributeValues={
                ":expected_sync_id": (
                    expected_sync_id
                ),
                ":expected_next_page": (
                    expected_next_page
                ),
                ":running": (
                    SyncStatus.RUNNING.value
                ),
                ":complete": (
                    SyncStatus.COMPLETE.value
                ),
                ":completed_at": (
                    completed_at
                ),
            },
        )

    def _conditional_update(
        self,
        **kwargs,
    ) -> SyncState:
        try:
            response = self._table.update_item(
                Key=self.key,
                ReturnValues="ALL_NEW",
                **kwargs,
            )
        except Exception as exc:
            if _is_conditional_failure(exc):
                raise SyncStateConflictError(
                    "conditional CURRENT ownership lost"
                ) from None

            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT update failed"
            ) from None

        if not isinstance(response, Mapping):
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT update response is invalid"
            )

        attributes = response.get(
            "Attributes"
        )

        if not isinstance(
            attributes,
            Mapping,
        ):
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT update returned no state"
            )

        return self._state_from_item(
            attributes
        )

    @staticmethod
    def _state_attributes(
        state: SyncState,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sync_id": state.sync_id,
            "status": state.status.value,
            "started_at": state.started_at,
            "updated_at": state.updated_at,
        }

        optional_values = {
            "next_page": state.next_page,
            "expected_total": (
                state.expected_total
            ),
            "expected_pages": (
                state.expected_pages
            ),
            "completed_at": (
                state.completed_at
            ),
            "last_error_code": (
                state.last_error_code
            ),
        }

        for key, value in optional_values.items():
            if value is not None:
                result[key] = value

        return result

    @staticmethod
    def _state_from_item(
        item: Mapping[str, Any],
    ) -> SyncState:
        try:
            return SyncState(
                sync_id=str(
                    item["sync_id"]
                ),
                status=SyncStatus(
                    item["status"]
                ),
                next_page=_dynamo_optional_int(
                    item.get("next_page"),
                    field="next_page",
                ),
                started_at=str(
                    item["started_at"]
                ),
                updated_at=str(
                    item["updated_at"]
                ),
                expected_total=_dynamo_optional_int(
                    item.get("expected_total"),
                    field="expected_total",
                ),
                expected_pages=_dynamo_optional_int(
                    item.get("expected_pages"),
                    field="expected_pages",
                ),
                completed_at=item.get(
                    "completed_at"
                ),
                last_error_code=item.get(
                    "last_error_code"
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            raise DynamoSyncStateStoreError(
                "Ghost sync CURRENT item is malformed"
            ) from None