"""Immutable DEV S3 artifact persistence for Content Sync.

The adapter consumes an injected boto3-compatible S3 client. It does not
construct AWS clients or read environment variables.

Immutable write algorithm:

1. PutObject with If-None-Match: *.
2. If the object already exists, HEAD it.
3. Verify exact user metadata and byte length.
4. GET and compare exact bytes.
5. Treat an identical object as idempotent success.
6. Raise ImmutableArtifactConflictError for any immutable mismatch.

Only Content Sync DEV archive prefixes are accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .sync_core import (
    ImmutableArtifact,
    ImmutableArtifactConflictError,
)

CONTENT_SYNC_PREFIXES = (
    "ghost/",
    "narration-documents/",
)

_CONDITIONAL_CODES = {
    "PreconditionFailed",
    "ConditionalRequestConflict",
}

_NOT_FOUND_CODES = {
    "404",
    "NoSuchKey",
    "NotFound",
}


class S3ArtifactStoreError(RuntimeError):
    """Raised for non-conflict S3 artifact persistence failures."""


class ContentSyncS3ArtifactStore:
    """S3 implementation of the Content Sync ArtifactStore protocol."""

    def __init__(
        self,
        *,
        client,
        bucket_name: str,
    ) -> None:
        if (
            not isinstance(bucket_name, str)
            or not bucket_name.strip()
        ):
            raise S3ArtifactStoreError(
                "bucket_name must be a non-empty string"
            )

        self._client = client
        self._bucket_name = bucket_name

    def put_immutable(
        self,
        artifact: ImmutableArtifact,
    ) -> None:
        """Create an immutable object or verify an identical retry."""

        self._validate_key(artifact.key)

        metadata = self._validate_expected_metadata(
            artifact.metadata
        )

        try:
            self._client.put_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
                Body=artifact.body,
                Metadata=metadata,
                IfNoneMatch="*",
            )
            return

        except Exception as exc:
            code, status = _aws_error_details(exc)

            if (
                code in _CONDITIONAL_CODES
                or status in {409, 412}
            ):
                self._verify_existing(
                    artifact=artifact,
                    expected_metadata=metadata,
                )
                return

            raise S3ArtifactStoreError(
                "S3 immutable artifact create failed"
            ) from None

    def _verify_existing(
        self,
        *,
        artifact: ImmutableArtifact,
        expected_metadata: Mapping[str, str],
    ) -> None:
        try:
            head = self._client.head_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
            )
        except Exception as exc:
            code, status = _aws_error_details(exc)

            if (
                code in _NOT_FOUND_CODES
                or status == 404
            ):
                raise S3ArtifactStoreError(
                    "S3 immutable artifact verification "
                    "could not find the object"
                ) from None

            raise S3ArtifactStoreError(
                "S3 immutable artifact metadata verification failed"
            ) from None

        if not isinstance(head, Mapping):
            raise S3ArtifactStoreError(
                "S3 immutable artifact HEAD response is invalid"
            )

        existing_metadata = head.get(
            "Metadata"
        )

        if not isinstance(
            existing_metadata,
            Mapping,
        ):
            self._raise_conflict()

        normalized_existing_metadata = (
            self._normalize_existing_metadata(
                existing_metadata
            )
        )

        if (
            normalized_existing_metadata
            != dict(expected_metadata)
        ):
            self._raise_conflict()

        content_length = head.get(
            "ContentLength"
        )

        if (
            isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length < 0
        ):
            raise S3ArtifactStoreError(
                "S3 immutable artifact ContentLength is invalid"
            )

        if content_length != len(artifact.body):
            self._raise_conflict()

        try:
            response = self._client.get_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
            )
        except Exception:
            raise S3ArtifactStoreError(
                "S3 immutable artifact body verification failed"
            ) from None

        if not isinstance(response, Mapping):
            raise S3ArtifactStoreError(
                "S3 immutable artifact GET response is invalid"
            )

        body_stream = response.get(
            "Body"
        )

        if (
            body_stream is None
            or not callable(
                getattr(body_stream, "read", None)
            )
        ):
            raise S3ArtifactStoreError(
                "S3 immutable artifact body stream is invalid"
            )

        try:
            existing_body = body_stream.read(
                len(artifact.body) + 1
            )
        except Exception:
            raise S3ArtifactStoreError(
                "S3 immutable artifact body verification failed"
            ) from None
        finally:
            close = getattr(
                body_stream,
                "close",
                None,
            )

            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        if not isinstance(
            existing_body,
            (bytes, bytearray),
        ):
            raise S3ArtifactStoreError(
                "S3 immutable artifact body is not bytes"
            )

        if bytes(existing_body) != artifact.body:
            self._raise_conflict()

    @staticmethod
    def _validate_key(
        key: str,
    ) -> None:
        if (
            not isinstance(key, str)
            or not key
        ):
            raise S3ArtifactStoreError(
                "artifact key must be non-empty"
            )

        if not any(
            key.startswith(prefix)
            and len(key) > len(prefix)
            for prefix in CONTENT_SYNC_PREFIXES
        ):
            raise S3ArtifactStoreError(
                "artifact key is outside Content Sync DEV prefixes"
            )

    @staticmethod
    def _validate_expected_metadata(
        metadata: Mapping[str, str],
    ) -> dict[str, str]:
        if not isinstance(metadata, Mapping):
            raise S3ArtifactStoreError(
                "artifact metadata must be a mapping"
            )

        normalized: dict[str, str] = {}

        for key, value in metadata.items():
            if (
                not isinstance(key, str)
                or not key
            ):
                raise S3ArtifactStoreError(
                    "artifact metadata keys must be non-empty strings"
                )

            if key != key.lower():
                raise S3ArtifactStoreError(
                    "artifact metadata keys must be lowercase"
                )

            if key.startswith(
                "x-amz-meta-"
            ):
                raise S3ArtifactStoreError(
                    "artifact metadata keys must omit x-amz-meta-"
                )

            if not isinstance(value, str):
                raise S3ArtifactStoreError(
                    "artifact metadata values must be strings"
                )

            normalized[key] = value

        return normalized

    @staticmethod
    def _normalize_existing_metadata(
        metadata: Mapping[str, Any],
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}

        for key, value in metadata.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
            ):
                raise ImmutableArtifactConflictError(
                    "immutable S3 object differs from expected artifact"
                )

            normalized[key.lower()] = value

        return normalized

    @staticmethod
    def _raise_conflict() -> None:
        raise ImmutableArtifactConflictError(
            "immutable S3 object differs from expected artifact"
        )


def _aws_error_details(
    exc: Exception,
) -> tuple[str | None, int | None]:
    """Extract safe AWS error code/status without stringifying exc."""

    response = getattr(
        exc,
        "response",
        None,
    )

    if not isinstance(response, Mapping):
        return None, None

    code: str | None = None
    status: int | None = None

    error = response.get(
        "Error"
    )

    if isinstance(error, Mapping):
        raw_code = error.get(
            "Code"
        )

        if isinstance(raw_code, str):
            code = raw_code

    response_metadata = response.get(
        "ResponseMetadata"
    )

    if isinstance(
        response_metadata,
        Mapping,
    ):
        raw_status = response_metadata.get(
            "HTTPStatusCode"
        )

        if (
            not isinstance(raw_status, bool)
            and isinstance(raw_status, int)
        ):
            status = raw_status

    return code, status