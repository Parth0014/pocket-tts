from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "team_studio_web" / "index.html"
JS = ROOT / "team_studio_web" / "app.js"
CSS = ROOT / "team_studio_web" / "styles.css"


def test_team_studio_redesign_keeps_stable_workflow_contract():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    for marker in (
        'id="post-grid"',
        'id="document-preview"',
        'id="narrator-select"',
        'id="quote-mode"',
        'id="generate-button"',
        'id="generation-list"',
        'id="player-bar"',
        'id="voice-grid"',
        'id="voice-dialog"',
    ):
        assert marker in html

    for marker in (
        "/studio-api/posts",
        "/studio-api/voices",
        "/studio-api/runtime",
        "/auth/login",
        '"Generate audio"',
        "data-voice-archive",
        "data-audio",
    ):
        assert marker in js


def test_team_studio_redesign_uses_new_light_brand_surface():
    html = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert '<meta name="color-scheme" content="light">' in html
    assert "--ember: #fe678b;" in css
    assert "--ink: #fbf8f3;" in css
    assert "Narration text" in html
    assert "Audio history" in html


def test_team_studio_redesign_hides_runtime_rollout_copy():
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
