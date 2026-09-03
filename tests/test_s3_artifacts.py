import pytest

from narration_content.s3_artifacts import (
    ContentSyncS3ArtifactStore,
    S3ArtifactStoreError,
)
from narration_content.sync_core import (
    ImmutableArtifact,
    ImmutableArtifactConflictError,
    prepare_canonical_post,
)
from narration_content.sync_models import (
    GhostCatalogPost,
)

BUCKET = "pocket-tts-dev-test"


class FakeAwsError(Exception):
    def __init__(
        self,
        code,
        status,
        *,
        sensitive_message="",
    ):
        super().__init__(
            sensitive_message
        )

        self.response = {
            "Error": {
                "Code": code,
            },
            "ResponseMetadata": {
                "HTTPStatusCode": status,
            },
        }


class FakeBody:
    def __init__(
        self,
        data,
        *,
        fail_read=False,
    ):
        self.data = data
        self.fail_read = fail_read
        self.closed = False
        self.read_sizes = []

    def read(
        self,
        size=-1,
    ):
        self.read_sizes.append(size)

        if self.fail_read:
            raise RuntimeError(
                "sensitive stream failure"
            )

        if size < 0:
            return self.data

        return self.data[:size]

    def close(self):
        self.closed = True


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.calls = []

        self.put_error = None
        self.head_error = None
        self.get_error = None

        self.existing_conflict_code = (
            "PreconditionFailed"
        )
        self.existing_conflict_status = 412

        self.last_body_stream = None

    def put_object(self, **kwargs):
        self.calls.append(
            ("put_object", kwargs)
        )

        if self.put_error is not None:
            raise self.put_error

        identity = (
            kwargs["Bucket"],
            kwargs["Key"],
        )

        if identity in self.objects:
            raise FakeAwsError(
                self.existing_conflict_code,
                self.existing_conflict_status,
            )

        self.objects[identity] = {
            "Body": bytes(
                kwargs["Body"]
            ),
            "Metadata": dict(
                kwargs["Metadata"]
            ),
        }

        return {
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
            }
        }

    def head_object(self, **kwargs):
        self.calls.append(
            ("head_object", kwargs)
        )

        if self.head_error is not None:
            raise self.head_error

        identity = (
            kwargs["Bucket"],
            kwargs["Key"],
        )

        if identity not in self.objects:
            raise FakeAwsError(
                "NoSuchKey",
                404,
            )

        obj = self.objects[identity]

        return {
            "Metadata": dict(
                obj["Metadata"]
            ),
            "ContentLength": len(
                obj["Body"]
            ),
        }

    def get_object(self, **kwargs):
        self.calls.append(
            ("get_object", kwargs)
        )

        if self.get_error is not None:
            raise self.get_error

        identity = (
            kwargs["Bucket"],
            kwargs["Key"],
        )

        if identity not in self.objects:
            raise FakeAwsError(
                "NoSuchKey",
                404,
            )

        stream = FakeBody(
            self.objects[
                identity
            ]["Body"]
        )

        self.last_body_stream = stream

        return {
            "Body": stream,
        }


def make_artifact(
    *,
    key=(
        "ghost/post-1/"
        + ("a" * 64)
        + ".html"
    ),
    body=b"<p>Hello</p>",
    metadata=None,
):
    if metadata is None:
        metadata = {
            "artifact-kind": "ghost-html",
            "post-id": "post-1",
            "content-hash": "a" * 64,
        }

    return ImmutableArtifact(
        key=key,
        body=body,
        metadata=metadata,
    )


def make_post():
    return GhostCatalogPost(
        post_id="ghost-post-1",
        title="A grateful day",
        slug="a-grateful-day",
        url="https://example.test/a-grateful-day/",
        published_at="2026-09-01T10:00:00.000Z",
        updated_at="2026-09-02T11:00:00.000Z",
        html=(
            "<h2>Gratitude</h2>"
            "<p>Today was beautiful.</p>"
        ),
        visibility="public",
        access=True,
    )


def test_bucket_name_is_required():
    client = FakeS3Client()

    with pytest.raises(
        S3ArtifactStoreError,
        match="bucket_name",
    ):
        ContentSyncS3ArtifactStore(
            client=client,
            bucket_name="",
        )


def test_new_artifact_uses_conditional_put():
    client = FakeS3Client()

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    artifact = make_artifact()

    store.put_immutable(
        artifact
    )

    assert len(client.calls) == 1

    operation, kwargs = (
        client.calls[0]
    )

    assert operation == "put_object"
    assert kwargs == {
        "Bucket": BUCKET,
        "Key": artifact.key,
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
        "IfNoneMatch": "*",
    }


def test_identical_retry_verifies_metadata_length_and_body():
    client = FakeS3Client()

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    artifact = make_artifact()

    store.put_immutable(
        artifact
    )

    store.put_immutable(
        artifact
    )

    operations = [
        operation
        for operation, _ in client.calls
    ]

    assert operations == [
        "put_object",
        "put_object",
        "head_object",
        "get_object",
    ]

    assert (
        client.last_body_stream
        is not None
    )

    assert (
        client.last_body_stream.read_sizes
        == [len(artifact.body) + 1]
    )

    assert (
        client.last_body_stream.closed
        is True
    )


def test_existing_different_body_is_conflict():
    client = FakeS3Client()

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    first = make_artifact()

    store.put_immutable(first)

    conflicting = make_artifact(
        body=b"<p>Different</p>"
    )

    with pytest.raises(
        ImmutableArtifactConflictError
    ):
        store.put_immutable(
            conflicting
        )


def test_existing_different_metadata_is_conflict():
    client = FakeS3Client()

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    first = make_artifact()

    store.put_immutable(first)

    conflicting = make_artifact(
        metadata={
            "artifact-kind": "ghost-html",
            "post-id": "post-1",
            "content-hash": "b" * 64,
        }
    )

    with pytest.raises(
        ImmutableArtifactConflictError
    ):
        store.put_immutable(
            conflicting
        )


def test_length_mismatch_is_conflict_without_get():
    client = FakeS3Client()

    artifact = make_artifact()

    identity = (
        BUCKET,
        artifact.key,
    )

    client.objects[identity] = {
        "Body": b"short",
        "Metadata": dict(
            artifact.metadata
        ),
    }

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        ImmutableArtifactConflictError
    ):
        store.put_immutable(
            artifact
        )

    operations = [
        operation
        for operation, _ in client.calls
    ]

    assert operations == [
        "put_object",
        "head_object",
    ]


def test_conditional_request_conflict_409_can_verify_retry():
    client = FakeS3Client()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
    }

    client.existing_conflict_code = (
        "ConditionalRequestConflict"
    )
    client.existing_conflict_status = 409

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    store.put_immutable(
        artifact
    )


def test_conditional_conflict_without_existing_object_fails_safely():
    client = FakeS3Client()

    client.put_error = FakeAwsError(
        "ConditionalRequestConflict",
        409,
        sensitive_message=(
            "secret internal request details"
        ),
    )

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError,
        match="could not find",
    ) as exc_info:
        store.put_immutable(
            make_artifact()
        )

    assert (
        "secret internal request details"
        not in str(exc_info.value)
    )


def test_access_denied_create_error_does_not_leak_sdk_message():
    client = FakeS3Client()

    client.put_error = FakeAwsError(
        "AccessDenied",
        403,
        sensitive_message=(
            "request contained sensitive details"
        ),
    )

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError
    ) as exc_info:
        store.put_immutable(
            make_artifact()
        )

    assert str(exc_info.value) == (
        "S3 immutable artifact create failed"
    )

    assert (
        "sensitive"
        not in str(exc_info.value)
    )


def test_head_failure_does_not_leak_sdk_message():
    client = FakeS3Client()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
    }

    client.head_error = FakeAwsError(
        "AccessDenied",
        403,
        sensitive_message="secret head details",
    )

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError
    ) as exc_info:
        store.put_immutable(
            artifact
        )

    assert "secret" not in str(
        exc_info.value
    )


def test_get_failure_does_not_leak_sdk_message():
    client = FakeS3Client()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
    }

    client.get_error = FakeAwsError(
        "AccessDenied",
        403,
        sensitive_message="secret get details",
    )

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError
    ) as exc_info:
        store.put_immutable(
            artifact
        )

    assert "secret" not in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    "key",
    [
        "generations/gen-1/output.wav",
        "voices/voice-1/reference.wav",
        "test-results/job-1.wav",
        "ghost/",
        "narration-documents/",
    ],
)
def test_adapter_rejects_non_content_sync_or_empty_prefix_keys(
    key,
):
    store = ContentSyncS3ArtifactStore(
        client=FakeS3Client(),
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError,
        match="outside Content Sync",
    ):
        store.put_immutable(
            make_artifact(
                key=key
            )
        )


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (
            {
                "Post-ID": "post-1",
            },
            "lowercase",
        ),
        (
            {
                "x-amz-meta-post-id": "post-1",
            },
            "omit x-amz-meta",
        ),
        (
            {
                "post-id": 123,
            },
            "values must be strings",
        ),
    ],
)
def test_expected_metadata_is_s3_safe(
    metadata,
    message,
):
    store = ContentSyncS3ArtifactStore(
        client=FakeS3Client(),
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError,
        match=message,
    ):
        store.put_immutable(
            make_artifact(
                metadata=metadata
            )
        )


def test_prepare_canonical_post_artifacts_are_supported_and_retry_safe():
    client = FakeS3Client()

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    prepared = prepare_canonical_post(
        make_post()
    )

    store.put_immutable(
        prepared.raw_html
    )
    store.put_immutable(
        prepared.narration_document
    )

    store.put_immutable(
        prepared.raw_html
    )
    store.put_immutable(
        prepared.narration_document
    )

    assert len(
        client.objects
    ) == 2

    assert (
        prepared.raw_html.key.startswith(
            "ghost/"
        )
    )

    assert (
        prepared.narration_document.key.startswith(
            "narration-documents/"
        )
    )


def test_existing_metadata_key_case_is_normalized_like_s3():
    client = FakeS3Client()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": {
            "ARTIFACT-KIND": "ghost-html",
            "POST-ID": "post-1",
            "CONTENT-HASH": "a" * 64,
        },
    }

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    store.put_immutable(
        artifact
    )


def test_malformed_existing_content_length_fails_safely():
    class BadHeadClient(FakeS3Client):
        def head_object(
            self,
            **kwargs,
        ):
            result = super().head_object(
                **kwargs
            )

            result["ContentLength"] = "12"

            return result

    client = BadHeadClient()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
    }

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError,
        match="ContentLength",
    ):
        store.put_immutable(
            artifact
        )


def test_body_read_failure_is_sanitized_and_stream_closed():
    class ReadFailureClient(FakeS3Client):
        def get_object(
            self,
            **kwargs,
        ):
            self.calls.append(
                ("get_object", kwargs)
            )

            stream = FakeBody(
                b"",
                fail_read=True,
            )

            self.last_body_stream = stream

            return {
                "Body": stream,
            }

    client = ReadFailureClient()

    artifact = make_artifact()

    client.objects[
        (BUCKET, artifact.key)
    ] = {
        "Body": artifact.body,
        "Metadata": dict(
            artifact.metadata
        ),
    }

    store = ContentSyncS3ArtifactStore(
        client=client,
        bucket_name=BUCKET,
    )

    with pytest.raises(
        S3ArtifactStoreError,
        match="body verification failed",
    ):
        store.put_immutable(
            artifact
        )

    assert (
        client.last_body_stream.closed
        is True
    )