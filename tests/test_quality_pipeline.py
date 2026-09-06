import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import torch

import generate_narration as narration
from narration_quality import audio_diagnostics, boundary_pause_ms, get_profile


def test_quote_continuation_does_not_receive_two_role_pauses():
    quote = {"text": 'She was finally home."', "block_type": "quote", "paragraph_end": False}
    assert boundary_pause_ms(quote, quote, get_profile()) == 220
    end = {**quote, "paragraph_end": True}
    paragraph = {**quote, "block_type": "paragraph"}
    assert boundary_pause_ms(end, paragraph, get_profile()) == 600


def test_quiet_delivery_survives_activity_detection():
    speech = 0.002 * np.sin(np.arange(24_000) * 2 * np.pi * 200 / 24_000)
    audio = torch.tensor(np.r_[np.zeros(4800), speech, np.zeros(4800)]).unsqueeze(0)
    activity = narration.detect_activity(audio, 24_000)
    assert activity == {"start": 4800, "end": 28800}
    trimmed = narration.trim_edge_silence(audio, 24_000, 180, 180)["audio"]
    assert trimmed.shape[-1] >= len(speech)


@pytest.mark.parametrize("values", [np.zeros(24000), np.full(24000, np.nan), np.ones(100)])
def test_broken_signals_are_rejected(values):
    with pytest.raises(ValueError):
        audio_diagnostics(values, 24_000, "This should be spoken.")


def test_unusual_rate_is_a_review_note_not_an_accuracy_score():
    report = audio_diagnostics(np.full(24_000, 0.1), 24_000, "word " * 20)
    assert report["warnings"]
    assert "similarity" not in report and "emotion" not in report


def test_signal_retry_uses_bounded_deterministic_seeds():
    seeds = []

    def generate(*args, **kwargs):
        seeds.append(torch.initial_seed())
        return torch.zeros(24000) if len(seeds) == 1 else torch.full((24000,), 0.1)

    model = SimpleNamespace(generate_audio=generate, sample_rate=24_000)
    _, report = narration.generate_checked_chunk(model, {}, "Hello there.", 1234, 80)
    assert seeds == [1234, 1_001_237]
    assert report["generation_seed"] == seeds[-1]
    assert len(report["rejected_attempts"]) == 1
    model.generate_audio = lambda *args, **kwargs: torch.zeros(24000)
    with pytest.raises(RuntimeError, match="twice"):
        narration.generate_checked_chunk(model, {}, "Hello there.", 1234, 80)


@pytest.fixture
def pipeline_fixture(tmp_path, monkeypatch):
    source = tmp_path / "source.html"
    source.write_text('<p>She opened the door. The room was quiet.</p><blockquote>At last, she was home.</blockquote>')
    reference = tmp_path / "voice.wav"
    wave = (0.1 * np.sin(np.arange(72000) * 2 * np.pi * 200 / 24000)).astype("float32")
    sf.write(reference, wave, 24000)
    calls = {"load": [], "generate": []}

    def generate(state, text, **kwargs):
        calls["generate"].append({"text": text, **kwargs})
        return torch.tensor(wave[:24000])

    model = SimpleNamespace(
        generate_audio=generate, sample_rate=24000, device="cpu", config={"test": True},
        pad_with_spaces_for_short_inputs=False, remove_semicolons=False,
        flow_lm=SimpleNamespace(conditioner=SimpleNamespace(
            prepare=lambda text: SimpleNamespace(tokens=torch.zeros(1, len(text.split())))
        )),
    )

    def load_model(**kwargs):
        calls["load"].append(kwargs)
        return model

    monkeypatch.setattr(narration, "TTSModel", SimpleNamespace(load_model=load_model))
    monkeypatch.setattr(narration, "load_or_build_voice_state", lambda model, anchor, identity, cache: (
        {}, None, {"anchor_sha256": narration.sha256_file(anchor)}
    ))
    return source, reference, calls, tmp_path / "output"


def test_pipeline_native_speed_role_reuse_report_and_cache(pipeline_fixture):
    source, reference, calls, output = pipeline_fixture
    first = narration.run_pipeline(source, reference, output_dir=output)
    report = json.loads(Path(first).with_suffix(".json").read_text())
    assert report["speed"] == 1.0
    assert len(calls["load"]) == 1  # Same temperature reuses one model for both roles.
    assert calls["load"][0]["language"] == "english_2026-04"
    assert calls["load"][0]["lsd_decode_steps"] == 5
    assert all(item["max_tokens"] == 80 and item["copy_state"] for item in calls["generate"])
    assert len(report["chunks"]) == 2
    assert report["reference_anchors"]["narration"]["duration_seconds"] <= 15
    first_count = len(calls["generate"])
    second = narration.run_pipeline(source, reference, output_dir=output, speed=0.90)
    second_report = json.loads(Path(second).with_suffix(".json").read_text())
    assert second_report["speed"] == 0.9
    assert all(item["cache_hit"] for item in second_report["chunks"])
    assert len(calls["generate"]) == first_count  # Tempo is render-only.
    assert second_report["duration_seconds"] > report["duration_seconds"]


def test_profile_changes_invalidate_raw_cache(pipeline_fixture):
    source, reference, calls, output = pipeline_fixture
    narration.run_pipeline(source, reference, output_dir=output, profile="faithful")
    first_count = len(calls["generate"])
    result = narration.run_pipeline(source, reference, output_dir=output, profile="expressive")
    report = json.loads(Path(result).with_suffix(".json").read_text())
    assert len(calls["generate"]) > first_count
    assert not any(item["cache_hit"] for item in report["chunks"])


def test_token_counter_uses_model_normalization_and_does_not_guess():
    seen = []

    def prepare(text):
        seen.append(text)
        return SimpleNamespace(tokens=torch.zeros(1, 5))

    model = SimpleNamespace(pad_with_spaces_for_short_inputs=False, remove_semicolons=True,
                            flow_lm=SimpleNamespace(conditioner=SimpleNamespace(prepare=prepare)))
    assert narration.count_tokens_for(model)("hello; friend") == 5
    assert seen == ["Hello, friend."]
    model.flow_lm.conditioner.prepare = lambda text: (_ for _ in ()).throw(RuntimeError("broken tokenizer"))
    with pytest.raises(RuntimeError, match="broken tokenizer"):
        narration.count_tokens_for(model)("hello")


@pytest.mark.parametrize("kwargs", [{"speed": True}, {"profile": "magic"}, {"seed": True},
                                   {"max_chunks": 0}, {"reference_start_seconds": float("nan")}])
def test_invalid_options_fail_before_touching_files(tmp_path, kwargs):
    with pytest.raises(ValueError):
        narration.run_pipeline("missing.html", "missing.wav", output_dir=tmp_path / "output", **kwargs)
    assert not (tmp_path / "output").exists()
