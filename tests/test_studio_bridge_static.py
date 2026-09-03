from pathlib import Path

BRIDGE = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-studio-bridge"
    / "lambda_function.py"
)


def text():
    return BRIDGE.read_text(
        encoding="utf-8",
    )


def test_bridge_re_fetches_ghost_and_hash_compares():
    source = text()

    assert (
        "/ghost/api/content/"
        in source
    )
    assert (
        "current_hash != event.content_hash"
        in source
    )
    assert (
        '"status": "STALE"'
        in source
    )


def test_bridge_has_no_tts_or_publication_authority_in_source():
    source = text()

    assert "send_message(" not in source
    assert (
        "gratefulness-narration-audio"
        not in source
    )
    assert "NarrationPublications" not in source
    assert "NarrationJobs" not in source
    assert "generation_id" not in source
    assert "job_id" not in source
    assert "voice_id" not in source
