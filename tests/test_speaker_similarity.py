import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import neural_restoration
from speaker_similarity import score_checkpoints


def test_same_audio_same_score_and_common_windows(tmp_path):
    source = tmp_path / "voice.wav"
    samples = np.sin(np.arange(16000 * 4) * 0.08).astype("float32") * 0.1
    sf.write(source, samples, 16000)
    encoder = SimpleNamespace(embed_utterance=lambda wave: np.array([1.0, float(np.std(wave))]))
    report = score_checkpoints(source, {"raw": source, "spectral": source}, encoder=encoder,
                               preprocess=lambda wave, source_sr: wave)
    assert report["scores"]["raw"]["windows"] == report["scores"]["spectral"]["windows"]
    assert report["window_starts"] == [0]


def test_duration_mismatch_rejected(tmp_path):
    source, shortened = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(source, np.ones(16000 * 4) * 0.1, 16000)
    sf.write(shortened, np.ones(16000 * 3) * 0.1, 16000)
    with pytest.raises(ValueError, match="durations differ"):
        score_checkpoints(source, {"raw": source, "spectral": shortened},
                          encoder=SimpleNamespace(embed_utterance=lambda wave: np.ones(2)),
                          preprocess=lambda wave, source_sr: wave)


def test_neural_regression_selects_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(neural_restoration, "score_checkpoints", lambda *args: {
        "scores": {"spectral": {"windows": [0.8, 0.81]}, "neural": {"windows": [0.7, 0.75]}},
    })
    selected = []
    monkeypatch.setattr(neural_restoration, "master_file", lambda source, *args: selected.append(source) or {})
    report = neural_restoration.evaluate_candidate("anchor", "baseline", "candidate", tmp_path / "evaluation")
    assert selected == ["baseline"]
    assert report["selected"] == "spectral" and not report["shipping_approved"]


def test_missing_neural_checkpoint_fails_without_importing_model(tmp_path):
    with pytest.raises(FileNotFoundError, match="checkpoint"):
        neural_restoration.restore_candidate("input.wav", tmp_path / "output.wav", tmp_path / "missing")


def test_neural_adapter_disables_denoiser_and_uses_documented_api(tmp_path, monkeypatch):
    source = tmp_path / "input.wav"
    sf.write(source, np.sin(np.arange(72000) * 0.1) * 0.1, 24000)
    checkpoint = tmp_path / "checkpoint"
    for relative in ("hparams.yaml", "ds/G/latest", "ds/G/default/mp_rank_00_model_states.pt"):
        path = checkpoint / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test fixture, not real model weights")
    calls = []

    def enhance(wave, sr, device, **kwargs):
        calls.append(kwargs)
        return wave, sr

    module = SimpleNamespace(enhance=enhance, denoise=lambda *args, **kwargs: pytest.fail("Denoiser called"))
    monkeypatch.setitem(sys.modules, "resemble_enhance.enhancer.inference", module)
    output = tmp_path / "candidate.wav"
    neural_restoration.restore_candidate(source, output, checkpoint)
    assert calls[0]["lambd"] == 0
    assert calls[0]["solver"] == "midpoint" and calls[0]["nfe"] == 32
    assert sf.info(output).frames == sf.info(source).frames
