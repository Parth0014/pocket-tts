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
    assert '$("#player-progress").value' in app
    assert "seekPlayerToRatio(Number(event.target.value) / 100)" in app


def test_player_range_has_csp_safe_styles():
    css = CSS.read_text(encoding="utf-8")
    html = (ROOT / "team_studio_web" / "index.html").read_text(encoding="utf-8")
    assert '.player-progress' in css
    assert 'id="player-progress"' in html
    assert 'type="range"' in html
    assert 'style="' not in html
    assert '"img-src \'self\' data:; "' in HANDLER.read_text(encoding="utf-8")


def test_team_studio_keeps_strict_style_csp():
    handler = HANDLER.read_text(encoding="utf-8")

    assert "style-src 'self'" in handler
    assert "'unsafe-inline'" not in handler


def test_unexpected_team_studio_errors_are_logged_server_side_only():
    handler = HANDLER.read_text(encoding="utf-8")

    assert "team_studio_internal_error" in handler
    assert "traceback.print_exc()" in handler
    assert '{"error": "internal_error"}' in handler
