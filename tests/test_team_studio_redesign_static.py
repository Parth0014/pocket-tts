from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "team_studio_web" / "index.html"
JS = ROOT / "team_studio_web" / "app.js"
CSS = ROOT / "team_studio_web" / "styles.css"


def test_redesigned_studio_keeps_live_team_workflow():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    assert "Pick a story." in html
    assert "Give it a voice." in html
    assert 'id="generate-button"' in html
    assert "Generate audio" in html
    assert "/studio-api/posts" in js
    assert "/studio-api/voices" in js
    assert "/studio-api/runtime" in js
    assert "/auth/login" in js
    assert 'credentials: "same-origin"' in js


def test_redesign_has_voice_management_and_audio_review():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    assert "+ Add voice" in html
    assert "data-voice-archive" in js
    assert "/archive" in js
    assert "data-voice-play" in js
    assert 'id="player-bar"' in html
    assert 'id="player-audio"' in html
    assert "playGeneration" in js
    assert "toggleCompare" in js


def test_redesign_hides_infrastructure_rollout_details():
    combined = (
        HTML.read_text(encoding="utf-8")
        + JS.read_text(encoding="utf-8")
    )

    for marker in (
        "6 × 8 GB",
        "Worker profile",
        "MaximumConcurrency",
        "pocket-tts-dev",
        ">DEV<",
    ):
        assert marker not in combined


def test_redesign_assets_are_nonempty():
    assert HTML.stat().st_size > 5000
    assert JS.stat().st_size > 10000
    assert CSS.stat().st_size > 10000
