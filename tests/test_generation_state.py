import pytest

from narration_studio.models import (
    ArtifactRef,
    GenerationExecutionStatus,
    GenerationRecord,
    GenerationReviewStatus,
    StudioContractError,
)

NOW = "2026-09-03T12:00:00Z"
ROOM_ID = "room_" + ("1" * 32)
DOC_ID = "doc_" + ("2" * 32)
GEN_ID = "gen_" + ("3" * 32)
VOICE_ID = "voice_" + ("4" * 32)
QUOTE_VOICE_ID = "voice_" + ("5" * 32)


def ref(
    sha="a" * 64,
):
    return ArtifactRef(
        bucket="pocket-tts-dev-test",
        key="example",
        sha256=sha,
    )


def generation(
    **overrides,
):
    values = {
        "room_id": ROOM_ID,
        "generation_id": GEN_ID,
        "doc_id": DOC_ID,
        "document_revision": 1,
        "document": ref("a" * 64),
        "source_post_id": "ghostpost123",
        "source_content_hash": "b" * 64,
        "source_narration_hash": "c" * 64,
        "voice_id": VOICE_ID,
        "voice_version": 1,
        "voice_reference_audio": ref("d" * 64),
        "quote_mode": "preserve",
        "quote_voice_id": None,
        "quote_voice_version": None,
        "quote_voice_reference_audio": None,
        "generation_input": ref("e" * 64),
        "generation_status": None,
        "review_status": GenerationReviewStatus.UNREVIEWED,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }

    values.update(
        overrides
    )

    return GenerationRecord(
        **values
    )


def test_new_generation_has_separate_state_machines():
    item = generation()

    assert item.generation_status is None
    assert (
        item.review_status
        is GenerationReviewStatus.UNREVIEWED
    )


def test_execution_and_review_statuses_do_not_overlap():
    assert {
        item.value
        for item in GenerationExecutionStatus
    }.isdisjoint(
        {
            item.value
            for item in GenerationReviewStatus
        }
    )


def test_two_voice_requires_quote_voice_pin():
    with pytest.raises(
        StudioContractError
    ):
        generation(
            quote_mode="two_voice",
        )


def test_two_voice_accepts_complete_quote_voice_pin():
    item = generation(
        quote_mode="two_voice",
        quote_voice_id=QUOTE_VOICE_ID,
        quote_voice_version=2,
        quote_voice_reference_audio=ref(
            "f" * 64
        ),
    )

    assert item.quote_voice_id == (
        QUOTE_VOICE_ID
    )
