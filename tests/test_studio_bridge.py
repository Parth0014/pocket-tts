from narration_studio.bridge import (
    bridge_current_key,
    bridge_receipt_key,
    narration_document_key,
    prepare_bridge_intake,
)

POST_ID = "60bdb2d2609e29003bef5486"
CONTENT_HASH = "a" * 64
NARRATION_HASH = "b" * 64
NOW = "2026-09-03T12:00:00Z"


def test_bridge_keys_are_post_and_content_idempotent():
    assert bridge_receipt_key(
        POST_ID,
        CONTENT_HASH,
    ) == (
        f"POST#{POST_ID}",
        f"BRIDGE#{CONTENT_HASH}",
    )

    assert bridge_current_key(
        POST_ID
    ) == (
        f"POST#{POST_ID}",
        "BRIDGE#CURRENT",
    )


def test_narration_document_key_matches_canonical_layout():
    assert narration_document_key(
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        processor_version=1,
        narration_hash=NARRATION_HASH,
    ) == (
        f"narration-documents/{POST_ID}/"
        f"{CONTENT_HASH}/p000001/"
        f"{NARRATION_HASH}.json"
    )


def test_bridge_intake_is_manager_state_not_generation_intent():
    intake = prepare_bridge_intake(
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        narration_hash=NARRATION_HASH,
        processor_version=1,
        raw_bucket="pocket-tts-dev-test",
        raw_key=(
            f"ghost/{POST_ID}/"
            f"{CONTENT_HASH}.html"
        ),
        document_bucket=(
            "pocket-tts-dev-test"
        ),
        document_key=(
            f"narration-documents/{POST_ID}/"
            f"{CONTENT_HASH}/p000001/"
            f"{NARRATION_HASH}.json"
        ),
        reason="NEW_POST",
        title="Example",
        slug="example",
        url="https://example.test/example/",
        ghost_updated_at=NOW,
        observed_at=NOW,
    )

    assert (
        intake.receipt[
            "bridge_status"
        ]
        == "INGESTED"
    )
    assert (
        intake.current[
            "content_hash"
        ]
        == CONTENT_HASH
    )

    forbidden = {
        "room_id",
        "voice_id",
        "generation_id",
        "job_id",
        "publication_key",
        "production_audio_bucket",
        "production_audio_key",
    }

    assert forbidden.isdisjoint(
        intake.receipt
    )
    assert forbidden.isdisjoint(
        intake.current
    )
