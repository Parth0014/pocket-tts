import pytest

from narration_studio.models import (
    ArtifactRef,
    GenerationRecord,
    GenerationStatus,
    RoomRecord,
    RoomStatus,
    StudioContractError,
    VoiceRecord,
    VoiceStatus,
)

NOW = "2026-09-03T10:00:00Z"

ROOM_ID = "room_" + ("1" * 32)
DOC_ID = "doc_" + ("2" * 32)
GEN_ID = "gen_" + ("3" * 32)
VOICE_ID = "voice_" + ("4" * 32)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def ref(
    sha=SHA_A,
):
    return ArtifactRef(
        bucket="pocket-tts-dev-test",
        key="studio-documents/example.json",
        sha256=sha,
    )


def test_room_contract_accepts_v1_room():
    room = RoomRecord(
        room_id=ROOM_ID,
        owner_id="internal-user",
        title="Gratitude narration",
        status=RoomStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    assert room.status is RoomStatus.ACTIVE


@pytest.mark.parametrize(
    "room_id",
    [
        "room_bad",
        "ROOM_" + ("1" * 32),
        "doc_" + ("1" * 32),
    ],
)
def test_room_id_is_strict(
    room_id,
):
    with pytest.raises(
        StudioContractError
    ):
        RoomRecord(
            room_id=room_id,
            owner_id="owner",
            title="Room",
            status=RoomStatus.ACTIVE,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )


def test_artifact_ref_requires_lowercase_sha256():
    with pytest.raises(
        StudioContractError
    ):
        ArtifactRef(
            bucket="bucket",
            key="key",
            sha256="A" * 64,
        )


def test_voice_contract_pins_reference():
    voice = VoiceRecord(
        voice_id=VOICE_ID,
        display_name="Warm narrator",
        status=VoiceStatus.ACTIVE,
        version=7,
        reference_audio=ref(
            SHA_B
        ),
        created_at=NOW,
        updated_at=NOW,
    )

    assert voice.version == 7
    assert (
        voice.reference_audio.sha256
        == SHA_B
    )


def test_generation_contract_pins_document_and_voice():
    generation = GenerationRecord(
        room_id=ROOM_ID,
        generation_id=GEN_ID,
        doc_id=DOC_ID,
        document_revision=3,
        document=ref(SHA_A),
        voice_id=VOICE_ID,
        voice_version=7,
        voice_reference_audio=ref(
            SHA_B
        ),
        generation_input=ref(
            SHA_C
        ),
        status=GenerationStatus.READY,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )

    assert generation.document_revision == 3
    assert generation.voice_version == 7
    assert (
        generation.status
        is GenerationStatus.READY
    )


def test_timestamps_must_be_utc_z():
    with pytest.raises(
        StudioContractError
    ):
        RoomRecord(
            room_id=ROOM_ID,
            owner_id="owner",
            title="Room",
            status=RoomStatus.ACTIVE,
            version=1,
            created_at=(
                "2026-09-03T22:00:00+12:00"
            ),
            updated_at=NOW,
        )