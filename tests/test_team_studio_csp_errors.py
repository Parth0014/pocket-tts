import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "team_studio_web" / "app.js"
CSS = ROOT / "team_studio_web" / "styles.css"
HANDLER = ROOT / "aws" / "pocket-tts-team-studio" / "lambda_function.py"


def test_team_studio_frontend_uses_no_inline_style_mutation():
    app = APP.read_text(encoding="utf-8")

    assert 'style="' not in app
    assert re.search(r"\\.style\\.", app) is None
    assert "wave-h-${Math.round(h * 100)}" in app
    assert "wave-progress-${Math.round(pct)}" in app


def test_waveform_css_has_finite_csp_safe_classes():
    css = CSS.read_text(encoding="utf-8")

    assert "Team Studio CSP-safe waveform levels" in css
    assert ".wave-h-12" in css
    assert ".wave-h-100" in css
    assert ".wave-progress-0" in css
    assert ".wave-progress-100" in css


def test_team_studio_keeps_strict_style_csp():
    handler = HANDLER.read_text(encoding="utf-8")

    assert "style-src 'self'" in handler
    assert "'unsafe-inline'" not in handler


def test_unexpected_team_studio_errors_are_logged_server_side_only():
    handler = HANDLER.read_text(encoding="utf-8")

    assert "team_studio_internal_error" in handler
    assert "traceback.print_exc()" in handler
    assert '{"error": "internal_error"}' in handler
