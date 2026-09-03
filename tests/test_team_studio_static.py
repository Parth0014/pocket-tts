from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "aws" / "pocket-tts-team-studio" / "lambda_function.py"
HTML = ROOT / "team_studio_web" / "index.html"
JS = ROOT / "team_studio_web" / "app.js"


def test_team_studio_has_no_direct_sqs_or_production_authority():
    source = HANDLER.read_text(encoding="utf-8")

    assert "send_message(" not in source
    assert 'boto3.client("sqs"' not in source
    assert "gratefulness-narration-audio" not in source
    assert "NarrationPublications" not in source
    assert "NarrationJobs" not in source


def test_team_studio_reads_ghost_and_uses_canonical_v3():
    source = HANDLER.read_text(encoding="utf-8")

    assert "normalize_ghost_html(" in source
    assert "build_document(" in source
    assert 'document.get("processor_version") != 3' in source
    assert "ghost/api/content" in source


def test_generation_enqueue_remains_owned_by_existing_app_api():
    source = HANDLER.read_text(encoding="utf-8")
    frontend = JS.read_text(encoding="utf-8")

    assert '"enqueue_path"' in source
    assert 'f"/rooms/{revision.room_id}/generations/"' in source
    assert "await api(prepared.enqueue_path" in frontend
    assert "EXECUTION_ENABLED" in source


def test_frontend_is_team_post_workflow_not_manual_room_workflow():
    html = HTML.read_text(encoding="utf-8")
    frontend = JS.read_text(encoding="utf-8")

    assert "Ghost Posts" in html
    assert "Pick a story." in html
    assert "Give it a voice." in html
    assert 'id="generate-button"' in html
    assert '"Generate audio"' in frontend
    assert "CANONICAL NARRATION" in html
    assert "AUDIO HISTORY" in html
    assert "Import Narration Document" not in html
    assert "Create room" not in html


def test_processing_is_paused_in_initial_deployment():
    frontend = JS.read_text(encoding="utf-8")

    assert "Processing paused" in frontend
    assert "execution_enabled" in frontend


def test_existing_dashboard_auth_is_used():
    source = HANDLER.read_text(encoding="utf-8")
    frontend = JS.read_text(encoding="utf-8")

    assert "verify_session(" in source
    assert "/auth/login" in frontend
    assert "/auth/session" in frontend
    assert "/auth/logout" in frontend

def test_voice_library_supports_add_and_non_destructive_archive():
    source = HANDLER.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    frontend = JS.read_text(encoding="utf-8")

    assert 'id="add-voice-button"' in html
    assert "register_voice(" in source
    assert "_archive_voice(" in source
    assert "VoiceStatus.DISABLED.value" in source
    assert "delete_item(" not in source
    assert "data-voice-archive" in frontend
    assert "/archive" in frontend
