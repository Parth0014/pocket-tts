import numpy as np
import pytest
import soundfile as sf

import narration_mastering as mastering


@pytest.fixture
def speech_like(tmp_path):
    rate = 24000
    t = np.arange(rate * 6) / rate
    # Harmonics and slow phrase dynamics exercise EQ/compression and loudness.
    envelope = 0.2 + 0.8 * np.sin(np.pi * t / 2) ** 2
    signal = envelope * (0.12 * np.sin(2 * np.pi * 180 * t) + 0.035 * np.sin(2 * np.pi * 2800 * t))
    source = tmp_path / "input.wav"
    sf.write(source, signal, rate, subtype="FLOAT")
    return source


def test_off_is_byte_identical_without_ffmpeg(speech_like, tmp_path, monkeypatch):
    monkeypatch.setattr(mastering, "resolve_ffmpeg", lambda: pytest.fail("off needs no FFmpeg"))
    output = tmp_path / "off.wav"
    report = mastering.master_file(speech_like, output, "off")
    assert output.read_bytes() == speech_like.read_bytes()
    assert report["input_sha256"] == report["output_sha256"]


@pytest.mark.parametrize("profile", ["auto", "natural", "warm_story"])
def test_encoded_master_preserves_format_and_meets_targets(speech_like, tmp_path, profile):
    output = tmp_path / "master.wav"
    report = mastering.master_file(speech_like, output, profile)
    assert sf.info(output).frames == sf.info(speech_like).frames
    assert sf.info(output).samplerate == 24000
    assert sf.info(output).subtype == "PCM_16"
    assert report["after"]["input_tp"] <= -1.4
    assert abs(report["after"]["input_i"] + 19) < 1
    second = tmp_path / "second.wav"
    mastering.master_file(speech_like, second, profile)
    assert output.read_bytes() == second.read_bytes()


def test_stereo_preserved(speech_like, tmp_path):
    signal, rate = sf.read(speech_like)
    stereo = tmp_path / "stereo.wav"
    sf.write(stereo, np.column_stack([signal, signal * 0.7]), rate, subtype="FLOAT")
    output = tmp_path / "master.wav"
    report = mastering.master_file(stereo, output)
    assert sf.info(output).channels == 2
    assert report["target_lufs"] == -16
    assert abs(report["after"]["input_i"] + 16) < 1


def test_refuses_overwrite(speech_like):
    original = speech_like.read_bytes()
    with pytest.raises(FileExistsError):
        mastering.master_file(speech_like, speech_like)
    assert speech_like.read_bytes() == original


@pytest.mark.parametrize("kind", ["silent", "short", "nonfinite"])
def test_bad_signal_never_publishes(tmp_path, kind):
    samples = np.zeros(24000 * (1 if kind == "short" else 4))
    if kind == "nonfinite":
        samples[100] = np.nan
    source, output = tmp_path / "input.wav", tmp_path / "out.wav"
    sf.write(source, samples, 24000, subtype="FLOAT")
    with pytest.raises(ValueError):
        mastering.master_file(source, output)
    assert not output.exists()


def test_ffmpeg_failure_never_publishes(speech_like, tmp_path):
    output = tmp_path / "out.wav"
    with pytest.raises(RuntimeError, match="FFmpeg failed"):
        mastering.master_file(speech_like, output, ffmpeg=tmp_path / "missing.exe")
    assert not output.exists()


def test_auto_leaves_clean_dynamics_and_tone_alone():
    tone, decisions = mastering.automatic_plan(
        {"input_i": -19, "input_tp": -5}, {"below_50hz_energy_fraction": 0.001}, -19,
    )
    assert tone == "anull"
    assert not any(item["enabled"] for item in decisions if item["stage"] != "loudness")


def test_auto_corrects_rumble_and_peak_pressure():
    tone, decisions = mastering.automatic_plan(
        {"input_i": -27, "input_tp": -2}, {"below_50hz_energy_fraction": 0.2}, -19,
    )
    assert "highpass" in tone and "acompressor" in tone
    assert "equalizer" not in tone
    compression = next(item for item in decisions if item["stage"] == "compression")
    assert 1 < compression["ratio"] <= 2


def test_spectral_analysis_distinguishes_rumble(speech_like, tmp_path):
    clean, rate = sf.read(speech_like)
    t = np.arange(len(clean)) / rate
    rumble = tmp_path / "rumble.wav"
    sf.write(rumble, clean + 0.1 * np.sin(2 * np.pi * 25 * t), rate, subtype="FLOAT")
    assert mastering.analyze_spectrum(speech_like)["below_50hz_energy_fraction"] < 0.03
    assert mastering.analyze_spectrum(rumble)["below_50hz_energy_fraction"] > 0.03
