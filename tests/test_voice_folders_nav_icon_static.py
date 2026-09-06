from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "team_studio_web" / "index.html"


def _button(html: str, view: str) -> str:
    pattern = re.compile(
        rf'<button\b(?=[^>]*\bdata-view="{re.escape(view)}")[^>]*>.*?</button>',
        re.S,
    )
    matches = pattern.findall(html)
    assert len(matches) == 1
    return matches[0]


def test_voice_folders_uses_a_real_folder_icon():
    html = HTML.read_text(encoding="utf-8")

    assert 'id="icon-folder"' in html

    button = _button(html, "references")

    assert 'aria-label="Voice Folders"' in button
    assert 'class="nav-icon"' in button
    assert 'href="#icon-folder"' in button
    assert 'href="#icon-voices"' not in button
    assert "<span>Voice Folders</span>" in button


def test_sidebar_items_share_the_same_icon_alignment_wrapper():
    html = HTML.read_text(encoding="utf-8")

    for view in ("posts", "voices", "references"):
        button = _button(html, view)
        assert button.count('class="nav-icon"') == 1
        assert button.count('<svg class="icon"') == 1
