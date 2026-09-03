import pytest

import narration_studio.core as studio_core
from narration_studio.models import (
    RoomRecord,
    RoomStatus,
    StudioContractError,
    VoiceStatus,
)
from narration_studio.service import StudioService

NOW = "2026-09-03T10:00:00Z"

ROOM_ID = "room_" + ("1" * 32)
DOC_ID = "doc_" + ("2" * 32)
GEN_ID = "gen_" + ("3" * 32)
VOICE_ID = "voice_" + ("4" * 32)


def canonical_document():
    return {
        "schema_version": 1,
        "post_id": "ghost-post-1",
        "content_hash": "a" * 64,
        "narration_hash": "b" * 64,
        "processor_version": 1,
        "blocks": [
            {
                "block_id": "block-1",
                "type": "paragraph",
                "text": "Gratitude.",
            }
        ],
    }


def wav_bytes():
    return (
        b"RIFF"
        + (40).to_bytes(
            4,
            "little",
        )
        + b"WAVE"
        + (b"\x00" * 40)
    )


class FakeArtifacts:
    def __init__(
        self,
        events,
    ):
        self.events = events

    def put_immutable(
        self,
        artifact,
    ):
        self.events.append(
            (
                "artifact",
                artifact.key,
            )
        )


class FakeStudioRepository:
    def __init__(
        self,
        events,
    ):
        self.events = events

    def create_room(
        self,
        room,
    ):
        self.events.append(
            (
                "room",
                room.room_id,
            )
        )

    def record_document_revision(
        self,
        revision,
    ):
        self.events.append(
            (
                "document",
                revision.doc_id,
            )
        )

    def create_generation(
        self,
        generation,
    ):
        self.events.append(
            (
                "generation",
                generation.generation_id,
            )
        )


class FakeVoiceRepository:
    def __init__(
        self,
        events,
    ):
        self.events = events

    def create_voice(
        self,
        voice,
    ):
        self.events.append(
            (
                "voice",
                voice.voice_id,
            )
        )


def make_service(
    events,
):
    return StudioService(
        artifacts=FakeArtifacts(
            events
        ),
        studio_repository=FakeStudioRepository(
            events
        ),
        voice_repository=FakeVoiceRepository(
            events
        ),
        bucket_name="pocket-tts-dev-test",
    )


def test_room_delegates_to_repository():
    events = []

    service = make_service(
        events
    )

    room = RoomRecord(
        room_id=ROOM_ID,
        owner_id="owner",
        title="Room",
        status=RoomStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    assert (
        service.create_room(room)
        == room
    )

    assert events == [
        (
            "room",
            ROOM_ID,
        )
    ]


def test_document_artifact_precedes_pointer(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        studio_core,
        "validate_document",
        lambda document, verify_hash: None,
    )

    service = make_service(
        events
    )

    prepared = service.import_narration_document(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=1,
        narration_document=canonical_document(),
        created_at=NOW,
    )

    assert events == [
        (
            "artifact",
            prepared.artifact.key,
        ),
        (
            "document",
            DOC_ID,
        ),
    ]


def test_reference_artifact_precedes_voice_registry():
    events = []

    service = make_service(
        events
    )

    voice = service.register_voice(
        voice_id=VOICE_ID,
        display_name="Warm",
        version=1,
        wav_bytes=wav_bytes(),
        status=VoiceStatus.ACTIVE,
        created_at=NOW,
    )

    assert events == [
        (
            "artifact",
            voice.reference_audio.key,
        ),
        (
            "voice",
            VOICE_ID,
        ),
    ]


def test_generation_artifact_precedes_ready_record(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        studio_core,
        "validate_document",
        lambda document, verify_hash: None,
    )

    service = make_service(
        events
    )

    revision = service.import_narration_document(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=1,
        narration_document=canonical_document(),
        created_at=NOW,
    ).revision

    events.clear()

    voice = service.register_voice(
        voice_id=VOICE_ID,
        display_name="Warm",
        version=1,
        wav_bytes=wav_bytes(),
        status=VoiceStatus.ACTIVE,
        created_at=NOW,
    )

    events.clear()

    prepared = service.create_generation(
        room_id=ROOM_ID,
        generation_id=GEN_ID,
        revision=revision,
        voice=voice,
        created_at=NOW,
    )

    assert events == [
        (
            "artifact",
            prepared.artifact.key,
        ),
        (
            "generation",
            GEN_ID,
        ),
    ]


def test_disabled_voice_fails_before_generation_storage(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        studio_core,
        "validate_document",
        lambda document, verify_hash: None,
    )

    service = make_service(
        events
    )

    revision = service.import_narration_document(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=1,
        narration_document=canonical_document(),
        created_at=NOW,
    ).revision

    events.clear()

    voice = service.register_voice(
        voice_id=VOICE_ID,
        display_name="Disabled",
        version=1,
        wav_bytes=wav_bytes(),
        status=VoiceStatus.DISABLED,
        created_at=NOW,
    )

    events.clear()

    with pytest.raises(
        StudioContractError
    ):
        service.create_generation(
            room_id=ROOM_ID,
            generation_id=GEN_ID,
            revision=revision,
            voice=voice,
            created_at=NOW,
        )

    assert events == []