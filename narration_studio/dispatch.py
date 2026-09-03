"""Durable Studio worker-dispatch adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .core import PreparedArtifact
from .models import StudioContractError
from .worker_contract import (
    DEV_BUCKET,
    FIFO_MESSAGE_GROUP_ID,
    canonical_job_json,
    job_fingerprint,
    validate_worker_job_v1,
)


class StudioDispatchError(RuntimeError):
    """Safe dispatch/storage failure."""


class StudioDispatchConflictError(StudioDispatchError):
    """Pinned generation intent conflicts."""


class WorkerVoiceArtifactConflictError(StudioDispatchError):
    """Canonical worker WAV key has different bytes."""


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", {})
    if not isinstance(response, dict):
        return ""
    error = response.get("Error", {})
    if not isinstance(error, dict):
        return ""
    return str(error.get("Code", ""))


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", {})
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("HTTPStatusCode")
    return value if isinstance(value, int) else None


def _conditional_failure(exc: Exception) -> bool:
    return _error_code(exc) in {
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
    }


def _s3_precondition_failure(exc: Exception) -> bool:
    return (
        _error_code(exc)
        in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
            "412",
        }
        or _http_status(exc) == 412
    )


@dataclass(frozen=True, slots=True)
class PinnedWorkerJob:
    job_id: str
    generation_id: str
    body: str
    fingerprint: str

    def __post_init__(self) -> None:
        try:
            parsed = json.loads(self.body)
        except (TypeError, ValueError) as exc:
            raise StudioContractError(
                "pinned worker body is not JSON"
            ) from exc

        validated = validate_worker_job_v1(parsed)
        if validated["job_id"] != self.job_id:
            raise StudioContractError("pinned job_id mismatch")
        if validated["generation_id"] != self.generation_id:
            raise StudioContractError("pinned generation_id mismatch")
        if canonical_job_json(validated) != self.body:
            raise StudioContractError("pinned worker body is not canonical")

        actual = hashlib.sha256(self.body.encode("utf-8")).hexdigest()
        if actual != self.fingerprint:
            raise StudioContractError("pinned worker fingerprint mismatch")


class WorkerVoiceS3Store:
    def __init__(self, *, client, bucket_name: str) -> None:
        if bucket_name != DEV_BUCKET:
            raise StudioContractError(
                "worker voice store must use the DEV bucket"
            )
        self._client = client
        self._bucket_name = bucket_name

    def put_immutable(self, artifact: PreparedArtifact) -> None:
        if not artifact.key.startswith("voices/"):
            raise StudioContractError(
                "worker voice artifact key must use voices/"
            )
        if not artifact.key.endswith("/reference.wav"):
            raise StudioContractError(
                "worker voice artifact must be reference.wav"
            )

        try:
            self._client.put_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
                Body=artifact.body,
                ContentType="audio/wav",
                Metadata=dict(artifact.metadata),
                IfNoneMatch="*",
            )
            return
        except Exception as exc:
            if not _s3_precondition_failure(exc):
                raise StudioDispatchError(
                    "worker voice S3 write failed"
                ) from exc

        try:
            existing = self._client.get_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
            )["Body"].read()
        except Exception as exc:
            raise StudioDispatchError(
                "worker voice S3 conflict verification failed"
            ) from exc

        if existing != artifact.body:
            raise WorkerVoiceArtifactConflictError(
                "worker voice key already contains different bytes"
            )


class DynamoGenerationDispatchStore:
    def __init__(self, *, client, table_name: str) -> None:
        if not isinstance(table_name, str) or not table_name:
            raise StudioContractError("table_name is required")
        self._client = client
        self._table_name = table_name

    def pin(self, *, room_id: str, job, pinned_at: str) -> PinnedWorkerJob:
        if not isinstance(room_id, str) or not room_id.startswith("room_"):
            raise StudioContractError("room_id is invalid")
        if not isinstance(pinned_at, str) or not pinned_at.endswith("Z"):
            raise StudioContractError("pinned_at must be UTC RFC3339 Z")

        validated = validate_worker_job_v1(job)
        body = canonical_job_json(validated)
        fingerprint = job_fingerprint(validated)
        pinned = PinnedWorkerJob(
            job_id=validated["job_id"],
            generation_id=validated["generation_id"],
            body=body,
            fingerprint=fingerprint,
        )

        names = {
            "#generation_id": "generation_id",
            "#job_id": "job_id",
            "#source_post_id": "source_post_id",
            "#source_content_hash": "source_content_hash",
            "#quote_mode": "quote_mode",
            "#worker_job_body": "worker_job_body",
            "#worker_job_fingerprint": "worker_job_fingerprint",
            "#dispatch_pinned_at": "dispatch_pinned_at",
        }
        values = {
            ":generation_id": {"S": pinned.generation_id},
            ":job_id": {"S": pinned.job_id},
            ":source_post_id": {"S": validated["post_id"]},
            ":source_content_hash": {"S": validated["content_hash"]},
            ":quote_mode": {"S": validated["quote_mode"]},
            ":worker_job_body": {"S": pinned.body},
            ":worker_job_fingerprint": {"S": pinned.fingerprint},
            ":dispatch_pinned_at": {"S": pinned_at},
        }
        condition = " AND ".join(
            (
                "attribute_exists(#generation_id)",
                "#generation_id = :generation_id",
                "(attribute_not_exists(#job_id) OR #job_id = :job_id)",
                (
                    "(attribute_not_exists(#source_post_id) "
                    "OR #source_post_id = :source_post_id)"
                ),
                (
                    "(attribute_not_exists(#source_content_hash) "
                    "OR #source_content_hash = :source_content_hash)"
                ),
                (
                    "(attribute_not_exists(#quote_mode) "
                    "OR #quote_mode = :quote_mode)"
                ),
                (
                    "(attribute_not_exists(#worker_job_body) "
                    "OR #worker_job_body = :worker_job_body)"
                ),
                (
                    "(attribute_not_exists(#worker_job_fingerprint) "
                    "OR #worker_job_fingerprint = :worker_job_fingerprint)"
                ),
            )
        )
        update = (
            "SET "
            "#job_id = if_not_exists(#job_id, :job_id), "
            "#source_post_id = if_not_exists(#source_post_id, :source_post_id), "
            "#source_content_hash = if_not_exists("
            "#source_content_hash, :source_content_hash), "
            "#quote_mode = if_not_exists(#quote_mode, :quote_mode), "
            "#worker_job_body = if_not_exists(#worker_job_body, :worker_job_body), "
            "#worker_job_fingerprint = if_not_exists("
            "#worker_job_fingerprint, :worker_job_fingerprint), "
            "#dispatch_pinned_at = if_not_exists("
            "#dispatch_pinned_at, :dispatch_pinned_at)"
        )

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": f"ROOM#{room_id}"},
                    "sk": {"S": f"GEN#{pinned.generation_id}"},
                },
                ConditionExpression=condition,
                UpdateExpression=update,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except Exception as exc:
            if _conditional_failure(exc):
                raise StudioDispatchConflictError(
                    "generation worker intent conflicts with an existing pin"
                ) from exc
            raise StudioDispatchError(
                "generation dispatch pin failed"
            ) from exc

        return pinned

    def get(
        self,
        *,
        room_id: str,
        generation_id: str,
    ) -> PinnedWorkerJob | None:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": f"ROOM#{room_id}"},
                    "sk": {"S": f"GEN#{generation_id}"},
                },
                ConsistentRead=True,
            )
        except Exception as exc:
            raise StudioDispatchError(
                "generation dispatch read failed"
            ) from exc

        item = response.get("Item")
        if item is None:
            return None

        required = {
            "generation_id",
            "job_id",
            "worker_job_body",
            "worker_job_fingerprint",
        }
        if not required.issubset(item):
            return None

        try:
            return PinnedWorkerJob(
                job_id=item["job_id"]["S"],
                generation_id=item["generation_id"]["S"],
                body=item["worker_job_body"]["S"],
                fingerprint=item["worker_job_fingerprint"]["S"],
            )
        except (KeyError, TypeError, StudioContractError) as exc:
            raise StudioDispatchError(
                "pinned generation dispatch is malformed"
            ) from exc


class SqsStudioJobPublisher:
    def __init__(self, *, client, queue_url: str) -> None:
        if not isinstance(queue_url, str) or not queue_url:
            raise StudioContractError("queue_url is required")
        self._client = client
        self._queue_url = queue_url

    def publish(self, pinned: PinnedWorkerJob) -> str | None:
        response = self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=pinned.body,
            MessageGroupId=FIFO_MESSAGE_GROUP_ID,
            MessageDeduplicationId=pinned.generation_id,
        )
        message_id = response.get("MessageId")
        return None if message_id is None else str(message_id)
