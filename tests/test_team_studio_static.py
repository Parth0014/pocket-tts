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


def test_team_studio_reads_ghost_and_uses_canonical_v4():
    source = HANDLER.read_text(encoding="utf-8")

    assert "normalize_ghost_html(" in source
    assert "build_document(" in source
    assert 'document.get("processor_version") != 4' in source
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
    assert "NARRATION TEXT" in html
    assert "AUDIO HISTORY" in html
    assert "Import Narration Document" not in html
    assert "Create room" not in html


def test_generation_button_is_runtime_gated_for_live_studio():
    frontend = JS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")

    assert 'id="generate-button"' in html
    assert "Generate audio" in html
    assert "execution_enabled" in frontend
    assert "Processing paused" not in frontend


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

def test_team_ui_hides_rollout_infrastructure_details():
    html = HTML.read_text(encoding="utf-8")
    frontend = JS.read_text(encoding="utf-8")
    combined = html + frontend

    for marker in (
        "6 × 8 GB",
        "Worker profile",
        "DEV Studio · no publishing",
        "six-worker",
        "Processor V3 path",
    ):
        assert marker not in combined


def test_team_generations_are_origin_scoped():
    source = HANDLER.read_text(encoding="utf-8")

    assert 'item.get("studio_origin") != "TEAM_STUDIO"' in source
    assert '"SET studio_origin = :origin"' in source
    assert '":origin": {"S": "TEAM_STUDIO"}' in source


def test_voice_cards_show_only_active_registry_entries():
    frontend = JS.read_text(encoding="utf-8")

    assert 'const active = state.voices.filter((voice) => voice.status === "ACTIVE")' in frontend
    assert "active.map(voiceCard)" in frontend
