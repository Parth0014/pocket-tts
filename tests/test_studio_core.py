import json

import pytest

import narration_studio.core as module
from narration_studio.core import (
    prepare_generation_input,
    prepare_imported_revision,
    prepare_voice_reference,
)
from narration_studio.models import (
    GenerationReviewStatus,
    StudioContractError,
    VoiceRecord,
    VoiceStatus,
)

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


def active_voice():
    reference = prepare_voice_reference(
        voice_id=VOICE_ID,
        version=9,
        wav_bytes=wav_bytes(),
        bucket="pocket-tts-dev-test",
    ).reference

    return VoiceRecord(
        voice_id=VOICE_ID,
        display_name="Narrator",
        status=VoiceStatus.ACTIVE,
        version=9,
        reference_audio=reference,
        created_at=NOW,
        updated_at=NOW,
    )


def test_imported_revision_preserves_exact_canonical_document(
    monkeypatch,
):
    validated = []

    def fake_validate(
        document,
        *,
        verify_hash,
    ):
        validated.append(
            (
                document,
                verify_hash,
            )
        )

    monkeypatch.setattr(
        module,
        "validate_document",
        fake_validate,
    )

    canonical = canonical_document()

    prepared = prepare_imported_revision(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=1,
        narration_document=canonical,
        bucket="pocket-tts-dev-test",
        created_at=NOW,
    )

    payload = json.loads(
        prepared.artifact.body
    )

    assert validated == [
        (
            canonical,
            True,
        )
    ]

    assert (
        payload["narration_document"]
        == canonical
    )

    assert prepared.revision.revision == 1

    assert prepared.artifact.key == (
        f"studio-documents/{ROOM_ID}/"
        f"{DOC_ID}/v000001.json"
    )


def test_voice_reference_is_immutable_hash_pinned():
    wav = wav_bytes()

    prepared = prepare_voice_reference(
        voice_id=VOICE_ID,
        version=2,
        wav_bytes=wav,
        bucket="pocket-tts-dev-test",
    )

    assert prepared.artifact.body == wav
    assert (
        prepared.reference.sha256
        == prepared.artifact.metadata[
            "sha256"
        ]
    )


def test_reference_audio_must_be_wave():
    with pytest.raises(
        StudioContractError
    ):
        prepare_voice_reference(
            voice_id=VOICE_ID,
            version=1,
            wav_bytes=b"not a wav",
            bucket="pocket-tts-dev-test",
        )


def test_generation_snapshot_pins_source_quote_mode_and_separate_state(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "validate_document",
        lambda document, verify_hash: None,
    )

    revision = prepare_imported_revision(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=4,
        narration_document=canonical_document(),
        bucket="pocket-tts-dev-test",
        created_at=NOW,
    ).revision

    prepared = prepare_generation_input(
        room_id=ROOM_ID,
        generation_id=GEN_ID,
        revision=revision,
        voice=active_voice(),
        quote_mode="preserve",
        quote_voice=None,
        bucket="pocket-tts-dev-test",
        created_at=NOW,
    )

    payload = json.loads(
        prepared.artifact.body
    )

    assert payload[
        "document"
    ]["revision"] == 4

    assert payload[
        "source"
    ]["post_id"] == "ghost-post-1"

    assert payload[
        "quote_mode"
    ] == "preserve"

    assert prepared.generation.generation_status is None

    assert (
        prepared.generation.review_status
        is GenerationReviewStatus.UNREVIEWED
    )


def test_generation_rejects_disabled_voice(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "validate_document",
        lambda document, verify_hash: None,
    )

    revision = prepare_imported_revision(
        room_id=ROOM_ID,
        doc_id=DOC_ID,
        revision=1,
        narration_document=canonical_document(),
        bucket="pocket-tts-dev-test",
        created_at=NOW,
    ).revision

    reference = prepare_voice_reference(
        voice_id=VOICE_ID,
        version=1,
        wav_bytes=wav_bytes(),
        bucket="pocket-tts-dev-test",
    ).reference

    voice = VoiceRecord(
        voice_id=VOICE_ID,
        display_name="Disabled",
        status=VoiceStatus.DISABLED,
        version=1,
        reference_audio=reference,
        created_at=NOW,
        updated_at=NOW,
    )

    with pytest.raises(
        StudioContractError
    ):
        prepare_generation_input(
            room_id=ROOM_ID,
            generation_id=GEN_ID,
            revision=revision,
            voice=voice,
            quote_mode="preserve",
            quote_voice=None,
            bucket="pocket-tts-dev-test",
            created_at=NOW,
        )
