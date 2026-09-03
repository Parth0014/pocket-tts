from io import BytesIO

from narration_studio.artifacts import PreparedArtifact
from narration_studio.dynamodb import (
    DynamoStudioRepository,
    DynamoVoiceRepository,
)
from narration_studio.models import (
    ArtifactRef,
    GenerationRecord,
    GenerationReviewStatus,
    RoomRecord,
    RoomStatus,
    StudioDocumentRevision,
    VoiceRecord,
    VoiceStatus,
)
from narration_studio.s3_store import (
    StudioS3ArtifactStore,
)

NOW = "2026-09-03T10:00:00Z"

ROOM_ID = "room_" + ("1" * 32)
DOC_ID = "doc_" + ("2" * 32)
GEN_ID = "gen_" + ("3" * 32)
VOICE_ID = "voice_" + ("4" * 32)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


class FakeDynamoClient:
    def __init__(self):
        self.transactions = []
        self.updates = []
        self.puts = []
        self.get_response = {}

    def transact_write_items(
        self,
        **kwargs,
    ):
        self.transactions.append(
            kwargs
        )

    def update_item(
        self,
        **kwargs,
    ):
        self.updates.append(
            kwargs
        )

    def put_item(
        self,
        **kwargs,
    ):
        self.puts.append(
            kwargs
        )

    def get_item(
        self,
        **kwargs,
    ):
        return self.get_response


class FakeS3Client:
    def __init__(self):
        self.puts = []

    def put_object(
        self,
        **kwargs,
    ):
        self.puts.append(
            kwargs
        )


def artifact(
    sha=SHA_A,
    key="studio-documents/example.json",
):
    return ArtifactRef(
        bucket="pocket-tts-dev-test",
        key=key,
        sha256=sha,
    )


def test_room_creation_is_atomic_with_owner_index():
    client = FakeDynamoClient()

    repository = DynamoStudioRepository(
        client=client,
        table_name="pocket-tts-app",
    )

    room = RoomRecord(
        room_id=ROOM_ID,
        owner_id="owner-1",
        title="Studio room",
        status=RoomStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    repository.create_room(
        room
    )

    transaction = client.transactions[0][
        "TransactItems"
    ]

    assert len(transaction) == 2

    assert transaction[0]["Put"][
        "Item"
    ]["pk"]["S"] == (
        f"ROOM#{ROOM_ID}"
    )

    assert transaction[1]["Put"][
        "Item"
    ]["pk"]["S"] == (
        "OWNER#owner-1"
    )


def test_document_revision_uses_compare_and_swap():
    client = FakeDynamoClient()

    repository = DynamoStudioRepository(
        client=client,
        table_name="pocket-tts-app",
    )

    revision = StudioDocumentRevision(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=2,
        source_post_id="ghost-post-1",
        source_content_hash=SHA_A,
        source_narration_hash=SHA_B,
        source_processor_version=1,
        document=artifact(
            SHA_C,
            key=(
                f"studio-documents/{ROOM_ID}/"
                f"{DOC_ID}/v000002.json"
            ),
        ),
        created_at=NOW,
    )

    repository.record_document_revision(
        revision
    )

    call = client.updates[0]

    assert call[
        "ConditionExpression"
    ] == "#r = :expected"

    assert call[
        "ExpressionAttributeValues"
    ][":expected"]["N"] == "1"


def test_generation_creation_persists_source_and_separate_states():
    client = FakeDynamoClient()

    repository = DynamoStudioRepository(
        client=client,
        table_name="pocket-tts-app",
    )

    generation = GenerationRecord(
        room_id=ROOM_ID,
        generation_id=GEN_ID,
        doc_id=DOC_ID,
        document_revision=4,
        document=artifact(
            SHA_A
        ),
        source_post_id="ghostpost123",
        source_content_hash=SHA_A,
        source_narration_hash=SHA_B,
        voice_id=VOICE_ID,
        voice_version=7,
        voice_reference_audio=artifact(
            SHA_B,
            key=(
                f"studio-voices/{VOICE_ID}/"
                "v000007/reference.wav"
            ),
        ),
        quote_mode="preserve",
        quote_voice_id=None,
        quote_voice_version=None,
        quote_voice_reference_audio=None,
        generation_input=artifact(
            SHA_C,
            key=(
                f"studio-generation-inputs/"
                f"{ROOM_ID}/{GEN_ID}.json"
            ),
        ),
        generation_status=None,
        review_status=GenerationReviewStatus.UNREVIEWED,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    repository.create_generation(
        generation
    )

    item = client.puts[0][
        "Item"
    ]

    assert item[
        "source_post_id"
    ]["S"] == "ghostpost123"

    assert item[
        "source_content_hash"
    ]["S"] == SHA_A

    assert item[
        "review_status"
    ]["S"] == "UNREVIEWED"

    assert (
        "generation_status"
        not in item
    )

    assert "status" not in item


def test_voice_repository_round_trip_shape():
    client = FakeDynamoClient()

    repository = DynamoVoiceRepository(
        client=client,
        table_name="NarrationVoices",
    )

    voice = VoiceRecord(
        voice_id=VOICE_ID,
        display_name="Warm",
        status=VoiceStatus.ACTIVE,
        version=3,
        reference_audio=artifact(
            SHA_B,
            key=(
                f"studio-voices/{VOICE_ID}/"
                "v000003/reference.wav"
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )

    repository.create_voice(
        voice
    )

    stored = client.puts[0][
        "Item"
    ]

    client.get_response = {
        "Item": stored
    }

    loaded = repository.get_voice(
        VOICE_ID
    )

    assert loaded == voice


def test_s3_store_uses_conditional_create():
    client = FakeS3Client()

    store = StudioS3ArtifactStore(
        client=client,
        bucket_name="pocket-tts-dev-test",
    )

    prepared = PreparedArtifact(
        key=(
            f"studio-generation-inputs/"
            f"{ROOM_ID}/{GEN_ID}.json"
        ),
        body=b'{"ok":true}',
        metadata={
            "artifact-kind": (
                "studio-generation-input-v1"
            )
        },
    )

    store.put_immutable(
        prepared
    )

    call = client.puts[0]

    assert call[
        "IfNoneMatch"
    ] == "*"


class ConditionalError(Exception):
    def __init__(self):
        self.response = {
            "Error": {
                "Code": "PreconditionFailed",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 412,
            },
        }


class ExistingS3Client:
    def __init__(
        self,
        artifact,
    ):
        self.artifact = artifact

    def put_object(
        self,
        **kwargs,
    ):
        raise ConditionalError()

    def head_object(
        self,
        **kwargs,
    ):
        return {
            "ContentLength": len(
                self.artifact.body
            ),
            "Metadata": dict(
                self.artifact.metadata
            ),
        }

    def get_object(
        self,
        **kwargs,
    ):
        return {
            "Body": BytesIO(
                self.artifact.body
            )
        }


def test_s3_identical_retry_is_idempotent():
    prepared = PreparedArtifact(
        key=(
            f"studio-documents/{ROOM_ID}/"
            f"{DOC_ID}/v000001.json"
        ),
        body=b"same",
        metadata={
            "artifact-kind": (
                "studio-document-v1"
            ),
        },
    )

    store = StudioS3ArtifactStore(
        client=ExistingS3Client(
            prepared
        ),
        bucket_name="pocket-tts-dev-test",
    )

    store.put_immutable(
        prepared
    )
