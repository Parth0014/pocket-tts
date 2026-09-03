from pathlib import Path

MANAGER = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-production-manager"
    / "lambda_function.py"
)


def source():
    return MANAGER.read_text(
        encoding="utf-8"
    )


def test_manager_has_no_tts_dispatch():
    text = source()

    assert "send_message(" not in text
    assert "STUDIO_QUEUE_URL" not in text
    assert "SqsStudioJobPublisher" not in text


def test_manager_has_no_production_writer():
    text = source()

    assert "put_object(" not in text
    assert "NarrationPublications" not in text
    assert (
        "production_write_performed"
        in text
    )


def test_manager_keeps_review_and_execution_separate():
    text = source()

    assert "generation_status" in text
    assert "review_status" in text
    assert "SELECTED" in text
    assert "READY" in text
    assert "OUTDATED" in text

def test_manager_uses_shared_session_contract_by_keyword():
    text = source()

    assert "token=token" in text
    assert "signing_secret=_secret()" in text
    assert 'claims.get("sub")' in text
    assert 'getattr(claims, "subject"' in text
