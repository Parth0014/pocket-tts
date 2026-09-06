import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import narration_preview as preview


def _write_reference(path):
    sample_rate = 16000
    times = np.arange(sample_rate * 18) / sample_rate
    signal = 0.15 * np.sin(2 * np.pi * 180 * times)
    # Deliberate pauses exercise the real reference preparation without a model.
    signal[np.mod(times, 3) > 2.5] = 0
    sf.write(path, signal.astype(np.float32), sample_rate)
    return path


def _fake_reference_tools():
    def prepare(source_path, output_path, *, start_seconds=None):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"reference audio")
        return {
            "anchor_path": str(output_path.resolve()),
            "duration_seconds": 15,
            "selected_start_seconds": start_seconds or 0,
            "selected_channel": 0,
            "warnings": [],
        }

    def candidates(source_path, report, output_dir, *, limit):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "clip with # and 'quotes'.wav"
        path.write_bytes(b"candidate audio")
        return [{"audio_path": str(path.resolve()), "start_seconds": 3, "end_seconds": 18}]

    return prepare, candidates


def test_prepare_only_real_audio_never_imports_narration_runtime(tmp_path):
    reference = _write_reference(tmp_path / "raw.wav")
    code = """
import sys
class BlockModelImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'generate_narration', 'torch', 'pocket_tts'}:
            raise AssertionError('Unexpected model runtime import: ' + fullname)
sys.meta_path.insert(0, BlockModelImports())
from narration_preview import main
raise SystemExit(main(sys.argv[1:]))
"""
    environment = dict(os.environ)
    project_dir = str(Path(preview.__file__).resolve().parent)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [project_dir, environment.get("PYTHONPATH")]))
    result = subprocess.run(
        [sys.executable, "-c", code, "--prepare-only", "--reference", str(reference),
         "--output-dir", str(tmp_path / "sessions")],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    session = next((tmp_path / "sessions").iterdir())
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["prepare_only"] is True
    assert manifest["results"] == []
    assert Path(manifest["reference"]["anchor_path"]).is_file()
    assert sf.info(manifest["reference"]["anchor_path"]).duration <= 15.01
    assert "No narration model was loaded" in (session / "index.html").read_text(encoding="utf-8")


def test_profiles_share_excerpt_and_seed_and_full_command_uses_original_html(tmp_path, monkeypatch):
    reference = tmp_path / "raw.wav"
    reference.write_bytes(b"raw")
    source = tmp_path / "story.html"
    source.write_text(
        "<p>She opened the door. Rain was falling, but her brother had finally come home.</p>"
        "<p>" + "A distant memory returned. " * 40 + "</p>", encoding="utf-8",
    )
    calls = []

    def run_pipeline(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True)
        audio = output / "narration_0001.wav"
        audio.write_bytes(b"preview audio")
        audio.with_suffix(".json").write_text(json.dumps({
            "chunks": [{"text": 'Model words <script>alert("x")</script>'}],
            "warnings": ["Listen carefully & compare"],
        }), encoding="utf-8")
        return str(audio)

    monkeypatch.setattr(preview, "_reference_tools", _fake_reference_tools)
    monkeypatch.setattr(preview, "_generation_tools", lambda: SimpleNamespace(run_pipeline=run_pipeline))
    manifest, session = preview.create_audition_session(
        reference, tmp_path / "sessions", html_path=source,
        profiles=["faithful", "legacy", "expressive"], seed=42, preview_words=35,
    )
    assert len(calls) == 3
    assert len({call["post_html_file"] for call in calls}) == 1
    assert {call["seed"] for call in calls} == {42}
    assert all(call["max_chunks"] is None for call in calls)
    assert len(manifest["excerpt_text"].split()) <= 35
    assert "distant memory" in manifest["excerpt_text"]
    assert all(result["status"] == "complete" for result in manifest["results"])
    assert all(str(source) in result["full_generation_command"] for result in manifest["results"])
    assert all("--max-chunks" not in result["full_generation_command"] for result in manifest["results"])
    page = (session / "index.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in page
    assert '<script>' not in page
    assert 'src="faithful/narration_0001.wav"' in page
    assert "%23" in page
    assert "%27" in page


def test_one_profile_failure_keeps_other_auditions_and_review_page(tmp_path, monkeypatch):
    reference = tmp_path / "raw.wav"
    reference.touch()
    source = tmp_path / "story.html"
    source.write_text("<p>This is the story of a long journey.</p>", encoding="utf-8")

    def run_pipeline(**kwargs):
        if kwargs["profile"] == "faithful":
            raise RuntimeError('Failure <img src=x onerror="alert(1)">')
        output = Path(kwargs["output_dir"])
        output.mkdir()
        audio = output / "narration.wav"
        audio.write_bytes(b"preview")
        return audio

    monkeypatch.setattr(preview, "_reference_tools", _fake_reference_tools)
    monkeypatch.setattr(preview, "_generation_tools", lambda: SimpleNamespace(run_pipeline=run_pipeline))
    manifest, session = preview.create_audition_session(
        reference, tmp_path / "sessions", html_path=source, profiles=["faithful", "balanced"],
    )
    assert [result["status"] for result in manifest["results"]] == ["error", "complete"]
    persisted = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["results"] == manifest["results"]
    page = (session / "index.html").read_text(encoding="utf-8")
    assert "&lt;img" in page
    assert "<img" not in page


def test_session_paths_do_not_collide_and_reference_start_is_preserved(tmp_path, monkeypatch):
    reference = tmp_path / "raw.wav"
    reference.touch()
    monkeypatch.setattr(preview, "_reference_tools", _fake_reference_tools)
    first, first_dir = preview.create_audition_session(
        reference, tmp_path / "sessions", prepare_only=True, reference_start_seconds=7.5,
    )
    second, second_dir = preview.create_audition_session(reference, tmp_path / "sessions", prepare_only=True)
    assert first_dir != second_dir
    assert first["reference"]["selected_start_seconds"] == 7.5
    assert second["reference"]["selected_start_seconds"] == 0
    assert first_dir.joinpath("index.html").is_file()


def test_excerpt_keeps_quote_attribution_without_adding_spoken_directions():
    from worker_document import extract_worker_blocks

    markup, text = preview.build_excerpt(
        '<p>At last, she spoke.</p><blockquote><p>I have missed you.</p><p>— Maria</p></blockquote>',
        word_budget=30,
    )
    blocks = extract_worker_blocks(markup)
    assert blocks[1] == {"type": "quote", "speaker": "Maria", "text": "I have missed you."}
    assert text == "At last, she spoke.\n\nI have missed you."


def test_asset_links_cannot_escape_session(tmp_path):
    with pytest.raises(ValueError):
        preview._asset_url(tmp_path / "outside.wav", tmp_path / "session")


def test_powershell_command_quotes_apostrophes_and_substitutions():
    command = preview._command(["python", "path with ' quote $(danger).wav"], windows=True)
    assert command == "& 'python' 'path with '' quote $(danger).wav'"


@pytest.mark.parametrize("invalid", [0, -1, 601, True])
def test_preview_word_limit_rejects_unbounded_or_invalid_work(tmp_path, invalid):
    reference = tmp_path / "raw.wav"
    reference.touch()
    with pytest.raises(ValueError, match="preview_words"):
        preview.create_audition_session(reference, tmp_path / "sessions", prepare_only=True, preview_words=invalid)
    assert not (tmp_path / "sessions").exists()
