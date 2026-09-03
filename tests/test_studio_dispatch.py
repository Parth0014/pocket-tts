import io

import pytest

from narration_studio.artifacts import PreparedArtifact
from narration_studio.dispatch import (
    DynamoGenerationDispatchStore,
    PinnedWorkerJob,
    SqsStudioJobPublisher,
    StudioDispatchConflictError,
    WorkerVoiceArtifactConflictError,
    WorkerVoiceS3Store,
)
from narration_studio.worker_contract import (
    build_worker_job_v1,
    canonical_job_json,
    job_fingerprint,
)

ROOM_ID = "room_" + ("0" * 32)
OTHER_ROOM_ID = "room_" + ("9" * 32)
JOB_ID = "job_" + ("1" * 32)
GENERATION_ID = "gen_" + ("2" * 32)
VOICE_ID = "voice_" + ("3" * 32)
POST_ID = "ghostpost123"
CONTENT_HASH = "a" * 64
NOW = "2026-09-03T10:30:00Z"


def make_job():
    return build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
    )


class ConditionalFailure(RuntimeError):
    response = {
        "Error": {
            "Code": (
                "ConditionalCheckFailedException"
            )
        }
    }


class PreconditionFailure(RuntimeError):
    response = {
        "Error": {
            "Code": "PreconditionFailed"
        },
        "ResponseMetadata": {
            "HTTPStatusCode": 412
        },
    }


class FakeDynamo:
    def __init__(
        self,
        *,
        item=None,
        update_conflict=False,
        put_conflict=False,
        route_item=None,
    ):
        self.item = item
        self.update_conflict = (
            update_conflict
        )
        self.put_conflict = put_conflict
        self.route_item = route_item
        self.update_calls = []
        self.get_calls = []
        self.put_calls = []

    def update_item(
        self,
        **kwargs,
    ):
        self.update_calls.append(
            kwargs
        )

        if self.update_conflict:
            raise ConditionalFailure(
                "synthetic"
            )

        return {}

    def put_item(
        self,
        **kwargs,
    ):
        self.put_calls.append(
            kwargs
        )

        if self.put_conflict:
            raise ConditionalFailure(
                "synthetic"
            )

        return {}

    def get_item(
        self,
        **kwargs,
    ):
        self.get_calls.append(
            kwargs
        )

        if (
            kwargs[
                "Key"
            ][
                "sk"
            ][
                "S"
            ]
            == "ROUTE"
        ):
            return (
                {}
                if self.route_item is None
                else {
                    "Item": self.route_item
                }
            )

        return (
            {}
            if self.item is None
            else {
                "Item": self.item
            }
        )


class FakeSqs:
    def __init__(self):
        self.calls = []

    def send_message(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return {
            "MessageId": "message-123"
        }


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(
        self,
        **kwargs,
    ):
        key = kwargs[
            "Key"
        ]

        if key in self.objects:
            raise PreconditionFailure(
                "synthetic"
            )

        self.objects[
            key
        ] = kwargs[
            "Body"
        ]

        return {}

    def get_object(
        self,
        **kwargs,
    ):
        return {
            "Body": io.BytesIO(
                self.objects[
                    kwargs[
                        "Key"
                    ]
                ]
            )
        }


def test_generation_route_is_created_conditionally():
    client = FakeDynamo()

    store = DynamoGenerationDispatchStore(
        client=client,
        table_name="pocket-tts-app",
    )

    store.ensure_route(
        room_id=ROOM_ID,
        generation_id=GENERATION_ID,
        created_at=NOW,
    )

    call = client.put_calls[0]

    assert call[
        "Item"
    ][
        "pk"
    ][
        "S"
    ] == (
        f"GEN#{GENERATION_ID}"
    )

    assert call[
        "Item"
    ][
        "sk"
    ][
        "S"
    ] == "ROUTE"

    assert call[
        "Item"
    ][
        "room_id"
    ][
        "S"
    ] == ROOM_ID

    assert (
        call[
            "ConditionExpression"
        ]
        == "attribute_not_exists(pk)"
    )


def test_identical_generation_route_retry_is_accepted():
    client = FakeDynamo(
        put_conflict=True,
        route_item={
            "generation_id": {
                "S": GENERATION_ID
            },
            "room_id": {
                "S": ROOM_ID
            },
        },
    )

    store = DynamoGenerationDispatchStore(
        client=client,
        table_name="pocket-tts-app",
    )

    store.ensure_route(
        room_id=ROOM_ID,
        generation_id=GENERATION_ID,
        created_at=NOW,
    )

    assert (
        client.get_calls[0][
            "ConsistentRead"
        ]
        is True
    )


def test_generation_route_conflict_fails_closed():
    client = FakeDynamo(
        put_conflict=True,
        route_item={
            "generation_id": {
                "S": GENERATION_ID
            },
            "room_id": {
                "S": OTHER_ROOM_ID
            },
        },
    )

    store = DynamoGenerationDispatchStore(
        client=client,
        table_name="pocket-tts-app",
    )

    with pytest.raises(
        StudioDispatchConflictError
    ):
        store.ensure_route(
            room_id=ROOM_ID,
            generation_id=GENERATION_ID,
            created_at=NOW,
        )


def test_dispatch_pin_is_conditional_and_idempotent_shape():
    client = FakeDynamo()

    store = DynamoGenerationDispatchStore(
        client=client,
        table_name="pocket-tts-app",
    )

    job = make_job()

    pinned = store.pin(
        room_id=ROOM_ID,
        job=job,
        pinned_at=NOW,
    )

    assert (
        pinned.body
        == canonical_job_json(
            job
        )
    )

    assert (
        pinned.fingerprint
        == job_fingerprint(
            job
        )
    )

    call = client.update_calls[0]

    assert call[
        "Key"
    ] == {
        "pk": {
            "S": f"ROOM#{ROOM_ID}"
        },
        "sk": {
            "S": f"GEN#{GENERATION_ID}"
        },
    }

    assert (
        "attribute_exists(#generation_id)"
        in call[
            "ConditionExpression"
        ]
    )

    assert (
        "worker_job_body"
        in call[
            "ExpressionAttributeNames"
        ].values()
    )

    assert (
        call[
            "ExpressionAttributeValues"
        ][
            ":source_post_id"
        ][
            "S"
        ]
        == POST_ID
    )

    assert (
        call[
            "ExpressionAttributeValues"
        ][
            ":source_content_hash"
        ][
            "S"
        ]
        == CONTENT_HASH
    )


def test_conflicting_generation_pin_fails_closed():
    store = DynamoGenerationDispatchStore(
        client=FakeDynamo(
            update_conflict=True
        ),
        table_name="pocket-tts-app",
    )

    with pytest.raises(
        StudioDispatchConflictError
    ):
        store.pin(
            room_id=ROOM_ID,
            job=make_job(),
            pinned_at=NOW,
        )


def test_pinned_job_round_trip_is_verified():
    job = make_job()
    body = canonical_job_json(
        job
    )
    fingerprint = job_fingerprint(
        job
    )

    client = FakeDynamo(
        item={
            "generation_id": {
                "S": GENERATION_ID
            },
            "job_id": {
                "S": JOB_ID
            },
            "worker_job_body": {
                "S": body
            },
            "worker_job_fingerprint": {
                "S": fingerprint
            },
        }
    )

    store = DynamoGenerationDispatchStore(
        client=client,
        table_name="pocket-tts-app",
    )

    pinned = store.get(
        room_id=ROOM_ID,
        generation_id=GENERATION_ID,
    )

    assert pinned == PinnedWorkerJob(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        body=body,
        fingerprint=fingerprint,
    )

    assert (
        client.get_calls[0][
            "ConsistentRead"
        ]
        is True
    )


def test_fifo_publisher_uses_frozen_transport_values():
    job = make_job()

    pinned = PinnedWorkerJob(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        body=canonical_job_json(
            job
        ),
        fingerprint=job_fingerprint(
            job
        ),
    )

    client = FakeSqs()

    publisher = SqsStudioJobPublisher(
        client=client,
        queue_url=(
            "https://example.invalid/"
            "studio.fifo"
        ),
    )

    assert (
        publisher.publish(
            pinned
        )
        == "message-123"
    )

    assert client.calls == [
        {
            "QueueUrl": (
                "https://example.invalid/"
                "studio.fifo"
            ),
            "MessageBody": pinned.body,
            "MessageGroupId": "tts",
            "MessageDeduplicationId": (
                GENERATION_ID
            ),
        }
    ]


def test_worker_voice_store_is_immutable_and_retry_safe():
    client = FakeS3()

    store = WorkerVoiceS3Store(
        client=client,
        bucket_name="pocket-tts-dev-test",
    )

    artifact = PreparedArtifact(
        key=(
            f"voices/{VOICE_ID}/"
            "reference.wav"
        ),
        body=b"same-reference",
        metadata={
            "sha256": "b" * 64
        },
    )

    store.put_immutable(
        artifact
    )

    store.put_immutable(
        artifact
    )

    with pytest.raises(
        WorkerVoiceArtifactConflictError
    ):
        store.put_immutable(
            PreparedArtifact(
                key=artifact.key,
                body=b"different-reference",
                metadata=artifact.metadata,
            )
        )
