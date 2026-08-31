import os
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
import soundfile as sf

import generate_narration as narration


def _write_wav(path, value):
    samples = np.full(800, value, dtype=np.float32)
    sf.write(path, samples, 8_000, subtype="FLOAT")


def test_dotenv_precedence_and_base_relative_paths(tmp_path):
    (tmp_path / ".env").write_text(
        "NARRATION_POST_HTML_FILE=from-dotenv.html\n"
        'NARRATION_REFERENCE_AUDIO="voices/narrator.wav" # durable voice\n'
        "NARRATION_QUOTE_REFERENCE_AUDIO=voices/quote.wav\n",
        encoding="utf-8",
    )

    environment = narration.load_runtime_environment(
        tmp_path,
        {"NARRATION_POST_HTML_FILE": "from-process.html"},
    )
    config = narration.build_runtime_config(tmp_path, environment)

    assert config["post_html_file"] == str(tmp_path / "from-process.html")
    assert config["narration_reference_audio"] == str(
        tmp_path / "voices" / "narrator.wav"
    )
    assert config["quote_reference_audio"] == str(
        tmp_path / "voices" / "quote.wav"
    )


def test_default_paths_are_durable_and_base_relative(tmp_path):
    config = narration.build_runtime_config(tmp_path, {})

    assert config["post_html_file"] == str(tmp_path / "sample.html")
    assert config["narration_reference_audio"] == str(
        tmp_path / "voice_samples" / "solution.wav"
    )


def test_needed_roles_ignore_empty_content_and_preserve_load_order():
    assert narration.required_roles([
        {"block_type": "quote", "text": "Quoted"},
        {"block_type": "paragraph", "text": "Narrated"},
        {"block_type": "quote", "text": ""},
    ]) == ("narration", "quote")
    assert narration.required_roles([
        {"block_type": "paragraph", "text": "Narrated"},
    ]) == ("narration",)
    assert narration.required_roles([
        {"block_type": "quote", "text": "Quoted"},
    ]) == ("quote",)


def test_reference_routing_depends_on_two_voice_mode():
    base = {
        "narration_reference_audio": "narrator.wav",
        "quote_reference_audio": "quote.wav",
    }

    preserve = narration.reference_paths_for_roles(
        {**base, "quote_mode": "preserve"}, ("narration", "quote")
    )
    two_voice = narration.reference_paths_for_roles(
        {**base, "quote_mode": "two_voice"}, ("narration", "quote")
    )

    assert preserve == {"narration": "narrator.wav", "quote": "narrator.wav"}
    assert two_voice == {"narration": "narrator.wav", "quote": "quote.wav"}


def test_two_voice_rejects_identical_references(tmp_path):
    narrator_path = tmp_path / "narrator.wav"
    quote_path = tmp_path / "quote.wav"
    _write_wav(narrator_path, 0.1)
    _write_wav(quote_path, 0.1)
    config = {
        "quote_mode": "two_voice",
        "narration_reference_audio": str(narrator_path),
        "quote_reference_audio": str(quote_path),
    }

    with pytest.raises(ValueError, match="distinct"):
        narration.validate_reference_config(config, ("narration", "quote"))


def test_two_voice_reference_validation_is_role_aware(tmp_path):
    narrator_path = tmp_path / "narrator.wav"
    quote_path = tmp_path / "quote.wav"
    _write_wav(narrator_path, 0.1)
    _write_wav(quote_path, 0.2)

    narration_only = {
        "quote_mode": "two_voice",
        "narration_reference_audio": str(narrator_path),
        "quote_reference_audio": None,
    }
    quote_only = {
        "quote_mode": "two_voice",
        "narration_reference_audio": None,
        "quote_reference_audio": str(quote_path),
    }

    assert narration.validate_reference_config(
        narration_only, ("narration",)
    ) == {"narration": str(narrator_path)}
    assert narration.validate_reference_config(quote_only, ("quote",)) == {
        "quote": str(quote_path)
    }


def test_raw_cache_payload_key_changes_with_voice_identity():
    common = ("Hello", 7, "narration", 0.6, {"model": "identity"})
    first = narration.raw_generation_payload(
        *common, {"anchor_sha256": "anchor-a", "voice_state_sha256": "state-a"}
    )
    second = narration.raw_generation_payload(
        *common, {"anchor_sha256": "anchor-b", "voice_state_sha256": "state-b"}
    )

    assert narration.payload_sha256(first) != narration.payload_sha256(second)


def test_bundled_ffmpeg_candidate_is_usable_and_preferred():
    bundled = imageio_ffmpeg.get_ffmpeg_exe()

    assert narration.ffmpeg_is_usable(bundled)
    assert os.path.samefile(narration.resolve_ffmpeg(), bundled)


def test_numbered_output_reservation_is_atomic(tmp_path):
    (tmp_path / "narration_0003.wav").touch()

    first = narration.reserve_numbered_output_path(tmp_path)
    second = narration.reserve_numbered_output_path(tmp_path)

    assert Path(first).name == "narration_0004.wav"
    assert Path(second).name == "narration_0005.wav"
    assert Path(first).is_file()
    assert Path(second).is_file()


def test_import_does_not_validate_config_or_run_generation(tmp_path):
    project_dir = Path(narration.__file__).resolve().parent
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(project_dir), environment.get("PYTHONPATH")))
    )
    environment["NARRATION_POST_HTML_FILE"] = "definitely-missing.html"
    environment["NARRATION_REFERENCE_AUDIO"] = "definitely-missing.wav"

    result = subprocess.run(
        [sys.executable, "-c", "import generate_narration; print('imported')"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"
    assert not list(tmp_path.iterdir())
