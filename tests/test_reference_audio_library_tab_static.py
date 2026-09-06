from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "team_studio_web" / "index.html"
APP = ROOT / "team_studio_web" / "app.js"
REF = ROOT / "team_studio_web" / "reference-library.js"


def test_reference_folders_have_their_own_sidebar_view():
    html = HTML.read_text(encoding="utf-8")

    assert 'data-view="references"' in html
    assert 'id="references-view"' in html
    assert 'id="reference-library-shell"' in html
    assert "Voice Folders" in html

    voices_start = html.index('id="voices-view"')
    references_start = html.index('id="references-view"')

    assert voices_start < references_start

    voices_slice = html[voices_start:references_start]
    assert 'id="voice-grid"' in voices_slice
    assert 'id="reference-library-shell"' not in voices_slice

    references_slice = html[references_start:]
    assert 'id="reference-library-shell"' in references_slice
    assert "All reference voices" not in references_slice


def test_main_app_routes_reference_tab_like_a_real_view():
    app = APP.read_text(encoding="utf-8")

    assert 'name === "references"' in app
    assert '$("#page-title").textContent = "Voice folders"' in app


def test_reference_library_bootstraps_only_when_reference_tab_is_open():
    js = REF.read_text(encoding="utf-8")

    assert 'const v=$("#references-view")' in js
    assert '$$(\'[data-view="references"]\')' in js
    assert 'const v=$("#voices-view")' not in js
    assert '$$(\'[data-view="voices"]\')' not in js
