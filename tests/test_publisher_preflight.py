import pytest

from narration_publisher.preflight import (
    PublisherPreflightError,
    next_publication_key,
    production_audio_key,
    validate_ready_generation,
)

POST = "ghost123"
VOICE = "voice_" + ("1" * 32)
GEN = "gen_" + ("2" * 32)
HASH = "a" * 64
SHA = "b" * 64


def generation():
    return {
        "generation_id": GEN,
        "generation_status": "COMPLETED",
        "review_status": "READY",
        "source_content_hash": HASH,
        "voice_id": VOICE,
        "output_bucket": "pocket-tts-dev-test",
        "output_key": (
            f"generations/{GEN}/output.wav"
        ),
        "output_sha256": SHA,
    }


def test_next_publication_key_is_monotonic():
    assert next_publication_key([]) == "v000001"
    assert next_publication_key(
        ["v000001", "v000007", "v000003"]
    ) == "v000008"


def test_production_audio_key_contract():
    assert production_audio_key(
        post_id=POST,
        voice_id=VOICE,
        publication_key="v000009",
    ) == (
        f"narrations/{POST}/{VOICE}/v000009.wav"
    )


def test_ready_generation_passes_preflight():
    validate_ready_generation(
        generation=generation(),
        current_content_hash=HASH,
        voice_status="ACTIVE",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation_status", "RUNNING"),
        ("review_status", "SELECTED"),
        ("source_content_hash", "c" * 64),
        ("output_bucket", "wrong"),
        ("output_sha256", "bad"),
    ],
)
def test_non_publishable_generation_rejected(
    field,
    value,
):
    item = generation()
    item[field] = value

    with pytest.raises(
        PublisherPreflightError
    ):
        validate_ready_generation(
            generation=item,
            current_content_hash=HASH,
            voice_status="ACTIVE",
        )


def test_disabled_voice_is_rejected():
    with pytest.raises(
        PublisherPreflightError
    ):
        validate_ready_generation(
            generation=generation(),
            current_content_hash=HASH,
            voice_status="DISABLED",
        )
