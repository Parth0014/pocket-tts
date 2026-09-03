"""Immutable DEV S3 artifact store for Studio V1."""

from __future__ import annotations

from .core import PreparedArtifact


class StudioArtifactStoreError(RuntimeError):
    """Base Studio immutable S3 error."""


class StudioArtifactConflictError(
    StudioArtifactStoreError
):
    """Existing immutable key contains different bytes."""


_ALLOWED_PREFIXES = (
    "studio-documents/",
    "studio-generation-inputs/",
    "studio-voices/",
)


def _error_details(
    exc: Exception,
) -> tuple[str | None, int | None]:
    response = getattr(
        exc,
        "response",
        None,
    )

    if not isinstance(response, dict):
        return None, None

    error = response.get(
        "Error",
        {},
    )

    metadata = response.get(
        "ResponseMetadata",
        {},
    )

    code = (
        error.get("Code")
        if isinstance(error, dict)
        else None
    )

    status = (
        metadata.get("HTTPStatusCode")
        if isinstance(metadata, dict)
        else None
    )

    return (
        code if isinstance(code, str) else None,
        status if isinstance(status, int) else None,
    )


class StudioS3ArtifactStore:
    def __init__(
        self,
        *,
        client,
        bucket_name: str,
    ) -> None:
        self._client = client
        self._bucket_name = bucket_name

    def put_immutable(
        self,
        artifact: PreparedArtifact,
    ) -> None:
        if not artifact.key.startswith(
            _ALLOWED_PREFIXES
        ):
            raise StudioArtifactStoreError(
                "Studio artifact key uses a forbidden prefix"
            )

        metadata = dict(
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
            code, status = _error_details(
                exc
            )

            if (
                code not in {
                    "PreconditionFailed",
                    "ConditionalRequestConflict",
                }
                and status != 412
            ):
                raise StudioArtifactStoreError(
                    "Studio artifact write failed"
                ) from None

        try:
            head = self._client.head_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
            )

            if (
                head.get("ContentLength")
                != len(artifact.body)
            ):
                raise StudioArtifactConflictError(
                    "existing immutable object length differs"
                )

            if (
                dict(head.get("Metadata", {}))
                != metadata
            ):
                raise StudioArtifactConflictError(
                    "existing immutable object metadata differs"
                )

            response = self._client.get_object(
                Bucket=self._bucket_name,
                Key=artifact.key,
            )

            body = response["Body"].read()

        except StudioArtifactConflictError:
            raise

        except Exception:
            raise StudioArtifactStoreError(
                "existing Studio artifact verification failed"
            ) from None

        if body != artifact.body:
            raise StudioArtifactConflictError(
                "existing immutable object bytes differ"
            )