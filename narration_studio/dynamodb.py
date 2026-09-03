"""Concrete DynamoDB persistence adapters for Studio V1."""

from __future__ import annotations

from .models import (
    ArtifactRef,
    GenerationRecord,
    RoomRecord,
    VoiceRecord,
    VoiceStatus,
)


class StudioRepositoryError(RuntimeError):
    """Base Studio DynamoDB adapter error."""


class StudioConflictError(StudioRepositoryError):
    """Raised when a conditional Studio write loses ownership."""


def _error_code(
    exc: Exception,
) -> str | None:
    response = getattr(
        exc,
        "response",
        None,
    )

    if not isinstance(response, dict):
        return None

    error = response.get("Error")

    if not isinstance(error, dict):
        return None

    code = error.get("Code")

    return code if isinstance(code, str) else None


def _s(
    value: str,
) -> dict[str, str]:
    return {"S": value}


def _n(
    value: int,
) -> dict[str, str]:
    return {"N": str(value)}


class DynamoStudioRepository:
    """ROOM/DOC/GEN repository using the shared app table."""

    def __init__(
        self,
        *,
        client,
        table_name: str,
    ) -> None:
        self._client = client
        self._table_name = table_name

    def create_room(
        self,
        room: RoomRecord,
    ) -> None:
        room_item = {
            "pk": _s(
                f"ROOM#{room.room_id}"
            ),
            "sk": _s("META"),
            "entity_type": _s("room"),
            "schema_version": _n(1),
            "room_id": _s(room.room_id),
            "owner_id": _s(room.owner_id),
            "title": _s(room.title),
            "status": _s(room.status.value),
            "version": _n(room.version),
            "created_at": _s(room.created_at),
            "updated_at": _s(room.updated_at),
        }

        owner_item = {
            "pk": _s(
                f"OWNER#{room.owner_id}"
            ),
            "sk": _s(
                f"ROOM#{room.room_id}"
            ),
            "entity_type": _s("owner_room"),
            "schema_version": _n(1),
            "room_id": _s(room.room_id),
            "owner_id": _s(room.owner_id),
            "title": _s(room.title),
            "status": _s(room.status.value),
            "created_at": _s(room.created_at),
            "updated_at": _s(room.updated_at),
        }

        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": room_item,
                            "ConditionExpression": (
                                "attribute_not_exists(pk)"
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": owner_item,
                            "ConditionExpression": (
                                "attribute_not_exists(pk)"
                            ),
                        }
                    },
                ]
            )
        except Exception as exc:
            if _error_code(exc) in {
                "TransactionCanceledException",
                "ConditionalCheckFailedException",
            }:
                raise StudioConflictError(
                    "room already exists"
                ) from None

            raise StudioRepositoryError(
                "room creation failed"
            ) from None

    def record_document_revision(
        self,
        revision,
    ) -> None:
        expected_previous = (
            revision.revision - 1
        )

        names = {
            "#entity_type": "entity_type",
            "#schema_version": "schema_version",
            "#room_id": "room_id",
            "#doc_id": "doc_id",
            "#r": "current_revision",
            "#bucket": "current_document_bucket",
            "#key": "current_document_key",
            "#sha": "current_document_sha256",
            "#source_post": "source_post_id",
            "#source_content": "source_content_hash",
            "#source_narration": "source_narration_hash",
            "#source_processor": "source_processor_version",
            "#created_at": "created_at",
            "#updated_at": "updated_at",
        }

        values = {
            ":entity_type": _s(
                "studio_document"
            ),
            ":schema_version": _n(1),
            ":room_id": _s(revision.room_id),
            ":doc_id": _s(revision.doc_id),
            ":revision": _n(revision.revision),
            ":bucket": _s(
                revision.document.bucket
            ),
            ":key": _s(
                revision.document.key
            ),
            ":sha": _s(
                revision.document.sha256
            ),
            ":source_post": _s(
                revision.source_post_id
            ),
            ":source_content": _s(
                revision.source_content_hash
            ),
            ":source_narration": _s(
                revision.source_narration_hash
            ),
            ":source_processor": _n(
                revision.source_processor_version
            ),
            ":created_at": _s(
                revision.created_at
            ),
            ":updated_at": _s(
                revision.created_at
            ),
        }

        if expected_previous == 0:
            condition = (
                "attribute_not_exists(#r)"
            )
        else:
            condition = "#r = :expected"
            values[":expected"] = _n(
                expected_previous
            )

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": _s(
                        f"ROOM#{revision.room_id}"
                    ),
                    "sk": _s(
                        f"DOC#{revision.doc_id}"
                    ),
                },
                ConditionExpression=condition,
                UpdateExpression=(
                    "SET "
                    "#entity_type = :entity_type, "
                    "#schema_version = :schema_version, "
                    "#room_id = :room_id, "
                    "#doc_id = :doc_id, "
                    "#r = :revision, "
                    "#bucket = :bucket, "
                    "#key = :key, "
                    "#sha = :sha, "
                    "#source_post = :source_post, "
                    "#source_content = :source_content, "
                    "#source_narration = :source_narration, "
                    "#source_processor = :source_processor, "
                    "#created_at = if_not_exists("
                    "#created_at, :created_at), "
                    "#updated_at = :updated_at"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except Exception as exc:
            if _error_code(exc) == (
                "ConditionalCheckFailedException"
            ):
                raise StudioConflictError(
                    "document revision conflict"
                ) from None

            raise StudioRepositoryError(
                "document revision write failed"
            ) from None

    def create_generation(
        self,
        generation: GenerationRecord,
    ) -> None:
        item = {
            "pk": _s(
                f"ROOM#{generation.room_id}"
            ),
            "sk": _s(
                f"GEN#{generation.generation_id}"
            ),
            "entity_type": _s(
                "generation"
            ),
            "schema_version": _n(1),
            "room_id": _s(
                generation.room_id
            ),
            "generation_id": _s(
                generation.generation_id
            ),
            "doc_id": _s(
                generation.doc_id
            ),
            "document_revision": _n(
                generation.document_revision
            ),
            "document_bucket": _s(
                generation.document.bucket
            ),
            "document_key": _s(
                generation.document.key
            ),
            "document_sha256": _s(
                generation.document.sha256
            ),
            "source_post_id": _s(
                generation.source_post_id
            ),
            "source_content_hash": _s(
                generation.source_content_hash
            ),
            "source_narration_hash": _s(
                generation.source_narration_hash
            ),
            "voice_id": _s(
                generation.voice_id
            ),
            "voice_version": _n(
                generation.voice_version
            ),
            "voice_reference_bucket": _s(
                generation.voice_reference_audio.bucket
            ),
            "voice_reference_key": _s(
                generation.voice_reference_audio.key
            ),
            "voice_reference_sha256": _s(
                generation.voice_reference_audio.sha256
            ),
            "quote_mode": _s(
                generation.quote_mode
            ),
            "generation_input_bucket": _s(
                generation.generation_input.bucket
            ),
            "generation_input_key": _s(
                generation.generation_input.key
            ),
            "generation_input_sha256": _s(
                generation.generation_input.sha256
            ),
            "review_status": _s(
                generation.review_status.value
            ),
            "version": _n(
                generation.version
            ),
            "created_at": _s(
                generation.created_at
            ),
            "updated_at": _s(
                generation.updated_at
            ),
        }

        if generation.generation_status is not None:
            item[
                "generation_status"
            ] = _s(
                generation.generation_status.value
            )

        if generation.quote_voice_id is not None:
            assert (
                generation.quote_voice_version
                is not None
            )
            assert (
                generation.quote_voice_reference_audio
                is not None
            )

            item["quote_voice_id"] = _s(
                generation.quote_voice_id
            )
            item["quote_voice_version"] = _n(
                generation.quote_voice_version
            )
            item["quote_voice_reference_bucket"] = _s(
                generation.quote_voice_reference_audio.bucket
            )
            item["quote_voice_reference_key"] = _s(
                generation.quote_voice_reference_audio.key
            )
            item["quote_voice_reference_sha256"] = _s(
                generation.quote_voice_reference_audio.sha256
            )

        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(pk)"
                ),
            )
        except Exception as exc:
            if _error_code(exc) == (
                "ConditionalCheckFailedException"
            ):
                raise StudioConflictError(
                    "generation already exists"
                ) from None

            raise StudioRepositoryError(
                "generation creation failed"
            ) from None


class DynamoVoiceRepository:
    """Voice registry adapter for NarrationVoices."""

    def __init__(
        self,
        *,
        client,
        table_name: str,
    ) -> None:
        self._client = client
        self._table_name = table_name

    def create_voice(
        self,
        voice: VoiceRecord,
    ) -> None:
        item = {
            "voice_id": _s(
                voice.voice_id
            ),
            "schema_version": _n(1),
            "display_name": _s(
                voice.display_name
            ),
            "status": _s(
                voice.status.value
            ),
            "version": _n(
                voice.version
            ),
            "reference_bucket": _s(
                voice.reference_audio.bucket
            ),
            "reference_key": _s(
                voice.reference_audio.key
            ),
            "reference_sha256": _s(
                voice.reference_audio.sha256
            ),
            "created_at": _s(
                voice.created_at
            ),
            "updated_at": _s(
                voice.updated_at
            ),
        }

        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(voice_id)"
                ),
            )
        except Exception as exc:
            if _error_code(exc) == (
                "ConditionalCheckFailedException"
            ):
                raise StudioConflictError(
                    "voice already exists"
                ) from None

            raise StudioRepositoryError(
                "voice creation failed"
            ) from None

    def get_voice(
        self,
        voice_id: str,
    ) -> VoiceRecord | None:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={
                    "voice_id": _s(
                        voice_id
                    )
                },
                ConsistentRead=True,
            )
        except Exception:
            raise StudioRepositoryError(
                "voice read failed"
            ) from None

        item = response.get("Item")

        if item is None:
            return None

        try:
            return VoiceRecord(
                voice_id=item[
                    "voice_id"
                ]["S"],
                display_name=item[
                    "display_name"
                ]["S"],
                status=VoiceStatus(
                    item["status"]["S"]
                ),
                version=int(
                    item["version"]["N"]
                ),
                reference_audio=ArtifactRef(
                    bucket=item[
                        "reference_bucket"
                    ]["S"],
                    key=item[
                        "reference_key"
                    ]["S"],
                    sha256=item[
                        "reference_sha256"
                    ]["S"],
                ),
                created_at=item[
                    "created_at"
                ]["S"],
                updated_at=item[
                    "updated_at"
                ]["S"],
            )
        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            raise StudioRepositoryError(
                "voice item is malformed"
            ) from None
