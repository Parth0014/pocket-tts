import hashlib
import json
import subprocess

import numpy as np
import pytest
import soundfile as sf

import reference_audio as reference


def _wave(seconds, *, sr=24_000, amplitude=0.2):
    time = np.arange(round(seconds * sr)) / sr
    # Variable envelope and short pauses are repeatable signal-quality fixtures,
    # not simulations of linguistic expressiveness.
    envelope = (0.55 + 0.45 * np.sin(2 * np.pi * 1.3 * time))
    envelope[np.remainder(time, 2.5) > 2.25] = 0
    return (amplitude * envelope * np.sin(2 * np.pi * 170 * time)).astype(np.float32)


def _save(path, samples, sr=24_000):
    sf.write(path, samples, sr, subtype="FLOAT")
    return path


def test_selects_clean_later_audio_instead_of_first_fifteen_seconds(tmp_path):
    source = _save(tmp_path / "raw.wav", np.concatenate([
        np.zeros(24_000 * 18), _wave(20), np.zeros(24_000 * 8)
    ]))
    before = source.read_bytes()
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    samples, rate = sf.read(report["anchor_path"])

    assert report["selected_start_seconds"] > 17
    assert report["selected_end_seconds"] <= 38.3
    assert 10 < report["duration_seconds"] <= 15
    assert rate == 24_000 and samples.ndim == 1
    assert np.max(np.abs(samples)) <= 10 ** (-3 / 20) + 1 / 32_768
    assert source.read_bytes() == before
    assert len(report["source_sha256"]) == 64
    assert json.loads((tmp_path / "anchor.json").read_text())["anchor_sha256"] == report["anchor_sha256"]
    assert "cannot detect" in " ".join(report["warnings"])


def test_prefers_unclipped_channel_and_preserves_antiphase_signal(tmp_path):
    wave = _wave(16)
    stereo = np.column_stack([wave, -wave])
    source = _save(tmp_path / "raw.wav", stereo)
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    samples, _ = sf.read(report["anchor_path"])
    assert np.sqrt(np.mean(samples ** 2)) > 0.01
    assert report["selected_channel"] == 0
    assert "phase cancellation" in " ".join(report["warnings"])

    clipping = np.sign(wave)
    _save(source, np.column_stack([clipping, wave]))
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    assert report["selected_channel"] == 1


@pytest.mark.parametrize("samples", [np.zeros(72_000), np.full(72_000, 0.5), _wave(1)])
def test_rejects_silence_dc_and_too_short_references(tmp_path, samples):
    source = _save(tmp_path / "raw.wav", samples)
    with pytest.raises(ValueError, match="too short|no usable segment"):
        reference.prepare_reference(source, tmp_path / "anchor.wav")
    assert not (tmp_path / "anchor.wav").exists()


def test_rejects_nonfinite_anywhere_in_source(tmp_path):
    samples = _wave(18)
    samples[-1] = np.nan
    source = _save(tmp_path / "raw.wav", samples)
    with pytest.raises(ValueError, match="non-finite"):
        reference.prepare_reference(source, tmp_path / "anchor.wav")


def test_manual_selection_uses_requested_start_and_eof(tmp_path):
    source = _save(tmp_path / "raw.wav", _wave(21))
    report = reference.prepare_reference(source, tmp_path / "anchor.wav", start_seconds=17.125)
    assert report["selected_start_seconds"] == 17.125
    assert report["selected_end_seconds"] == 21
    assert report["duration_seconds"] == pytest.approx(3.875)
    assert report["selection_mode"] == "manual"
    assert "shorter than 5" in " ".join(report["warnings"])
    with pytest.raises(ValueError, match="less than 2"):
        reference.prepare_reference(source, tmp_path / "anchor.wav", start_seconds=20)


def test_cache_validates_source_settings_and_output_content(tmp_path, monkeypatch):
    source = _save(tmp_path / "raw.wav", _wave(20))
    output = tmp_path / "anchor.wav"
    first = reference.prepare_reference(source, output)
    original_analyze = reference._analyze_frames

    def unexpected_analysis(*args, **kwargs):
        pytest.fail("A verified cache should not decode/analyze the source again")

    monkeypatch.setattr(reference, "_analyze_frames", unexpected_analysis)
    cached = reference.prepare_reference(source, output)
    assert cached["cache_hit"]
    assert cached["anchor_sha256"] == first["anchor_sha256"]
    monkeypatch.setattr(reference, "_analyze_frames", original_analyze)

    output.write_bytes(b"broken wav")
    restored = reference.prepare_reference(source, output)
    assert not restored["cache_hit"]
    assert restored["anchor_sha256"] == first["anchor_sha256"]
    changed_settings = reference.prepare_reference(source, output, target_seconds=10)
    assert not changed_settings["cache_hit"]
    assert changed_settings["duration_seconds"] <= 10
    _save(source, _wave(20, amplitude=0.1))
    changed_source = reference.prepare_reference(source, output, target_seconds=10)
    assert not changed_source["cache_hit"]
    assert changed_source["source_sha256"] != first["source_sha256"]


def test_corrupt_manifest_and_wrong_samplerate_output_regenerate(tmp_path):
    source = _save(tmp_path / "raw.wav", _wave(20))
    output = tmp_path / "anchor.wav"
    reference.prepare_reference(source, output)
    manifest = output.with_suffix(".json")
    manifest.write_text("{broken", encoding="utf-8")
    assert not reference.prepare_reference(source, output)["cache_hit"]
    report = json.loads(manifest.read_text())
    _save(output, _wave(10, sr=16_000), sr=16_000)
    report["anchor_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(report))
    assert not reference.prepare_reference(source, output)["cache_hit"]
    assert sf.info(output).samplerate == 24_000
    report = json.loads(manifest.read_text())
    del report["candidates"]
    manifest.write_text(json.dumps(report))
    assert not reference.prepare_reference(source, output)["cache_hit"]


def test_resamples_and_decodes_compressed_input(tmp_path):
    source = _save(tmp_path / "raw.wav", _wave(8, sr=44_100), sr=44_100)
    compressed = tmp_path / "reference.mp3"
    subprocess.run([reference._ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), str(compressed)], check=True, capture_output=True)
    report = reference.prepare_reference(compressed, tmp_path / "anchor.wav")
    assert sf.info(report["anchor_path"]).samplerate == 24_000
    assert reference.probe_reference(compressed)["duration_seconds"] > 7


def test_distinct_candidate_previews_and_changed_source_guard(tmp_path):
    source = _save(tmp_path / "raw.wav", np.concatenate([_wave(20), np.zeros(24_000 * 3), _wave(20)]))
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    previews = reference.export_candidate_previews(source, report, tmp_path / "previews")
    assert 2 <= len(previews) <= 3
    assert all(sf.info(item["audio_path"]).samplerate == 24_000 for item in previews)
    assert previews[0]["start_seconds"] == report["selected_start_seconds"]
    _save(source, _wave(10))
    with pytest.raises(ValueError, match="source changed"):
        reference.export_candidate_previews(source, report, tmp_path / "previews")


def test_does_not_overwrite_source_and_validates_options(tmp_path):
    source = _save(tmp_path / "raw.wav", _wave(10))
    with pytest.raises(ValueError, match="overwrite"):
        reference.prepare_reference(source, source)
    for seconds in (float("nan"), float("inf"), 0, 16):
        with pytest.raises(ValueError, match="target_seconds"):
            reference.prepare_reference(source, tmp_path / "anchor.wav", target_seconds=seconds)
    for start in (float("nan"), float("inf"), -1):
        with pytest.raises(ValueError, match="start_seconds"):
            reference.prepare_reference(source, tmp_path / "anchor.wav", start_seconds=start)


def test_low_volume_gain_is_bounded_and_cli_prints_report(tmp_path, capsys):
    source = _save(tmp_path / "raw.wav", _wave(8, amplitude=0.003))
    assert reference.main([str(source), "--output", str(tmp_path / "anchor.wav"), "--start", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["gain_db"] <= 6
    assert "very quiet" in " ".join(report["warnings"])


def test_arbitrary_sample_length_never_exceeds_fifteen_seconds(tmp_path):
    count = 17 * 24_000 + 1
    samples = 0.1 * np.sin(2 * np.pi * 123 * np.arange(count) / 24_000)
    source = _save(tmp_path / "raw.wav", samples)
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    assert report["duration_seconds"] <= 15
    assert sf.info(report["anchor_path"]).frames <= 15 * 24_000


def test_loud_distorted_intro_does_not_mask_quieter_later_audio(tmp_path):
    source = _save(tmp_path / "raw.wav", np.concatenate([
        np.sign(_wave(20)), np.zeros(24_000 * 2), _wave(20, amplitude=0.04)
    ]))
    report = reference.prepare_reference(source, tmp_path / "anchor.wav")
    assert report["selected_start_seconds"] >= 20
    assert report["candidates"][0]["clipping_fraction"] == 0


def test_preparation_preserves_manual_segment_waveform_and_timing(tmp_path):
    wave = _wave(10)
    source = _save(tmp_path / "raw.wav", wave)
    report = reference.prepare_reference(source, tmp_path / "anchor.wav", start_seconds=1, target_seconds=7)
    actual, _ = sf.read(report["anchor_path"])
    gain = 10 ** (report["gain_db"] / 20)
    expected = wave[24_000:8 * 24_000] * gain
    assert len(actual) == len(expected)
    assert np.max(np.abs(actual - expected)) < 1.1 / 32_768


def test_atomic_write_failure_retains_previous_anchor(tmp_path, monkeypatch):
    source = _save(tmp_path / "raw.wav", _wave(20))
    output = tmp_path / "anchor.wav"
    reference.prepare_reference(source, output)
    previous = output.read_bytes()
    manifest = output.with_suffix(".json").read_bytes()

    def fail_replace(*args):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(reference.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="injected"):
        reference.prepare_reference(source, output, target_seconds=10)
    assert output.read_bytes() == previous
    assert output.with_suffix(".json").read_bytes() == manifest
    assert not list(tmp_path.glob(".anchor.wav.*"))
