from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/"aws"/"pocket-tts-team-studio"/"lambda_function.py"
HTML=ROOT/"team_studio_web"/"index.html"
CSS=ROOT/"team_studio_web"/"styles.css"
JS=ROOT/"team_studio_web"/"reference-library.js"

def test_reference_library_contract():
    h=H.read_text(encoding="utf-8"); html=HTML.read_text(encoding="utf-8"); js=JS.read_text(encoding="utf-8")
    for m in ("/studio-api/reference-folders","_create_reference_folder","_add_reference_voices","_update_reference_metadata","_remove_reference_voice","_archive_reference_folder",'"REFLIB#TEAM"'): assert m in h
    for m in ('id="reference-library-shell"','id="reference-create-folder"','id="reference-folder-list"','id="reference-add-voices"','id="reference-audio-grid"','id="reference-alias"','id="reference-description"'): assert m in html
    for m in ("/studio-api/reference-folders","voice_ids","short_description","favorite","Add selected voices"): assert m in js

def test_alias_description_are_optional():
    html=HTML.read_text(encoding="utf-8")
    assert 'id="reference-folder-name" maxlength="80" required' in html
    assert 'id="reference-alias" maxlength="80"' in html
    assert 'id="reference-description" maxlength="280"' in html
    assert 'id="reference-alias" maxlength="80" required' not in html
    assert 'id="reference-description" maxlength="280" required' not in html

def test_reference_library_does_not_copy_or_delete_audio():
    h=H.read_text(encoding="utf-8"); section=h.split("# Shared team Reference Audio Library",1)[1].split("def _create_generation(",1)[0]
    for m in ("s3.put_object","s3.copy_object","s3.delete_object","delete_item"): assert m not in section

def test_reference_asset_is_served():
    h=H.read_text(encoding="utf-8"); html=HTML.read_text(encoding="utf-8")
    assert "/studio/reference-library.js" in h
    assert '/studio/reference-library.js' in html
    assert JS.stat().st_size>5000

def test_reference_styles_exist():
    css=CSS.read_text(encoding="utf-8")
    for m in (".reference-library-shell",".reference-folder-row",".reference-audio-card",".reference-add-list"): assert m in css
