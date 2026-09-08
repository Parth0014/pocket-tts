"""Optional offline speaker-embedding regression checks; never a proof of identity or word accuracy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import numpy as np
import soundfile as sf


def cosine(left, right):
    left, right = np.asarray(left), np.asarray(right)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if not denominator or not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Invalid speaker embedding")
    return float(np.clip(np.dot(left, right) / denominator, -1, 1))


def regression_gate(baseline, candidate):
    """Require no aggregate regression; reject large individual-window losses too."""
    before, after = np.asarray(baseline), np.asarray(candidate)
    if before.ndim != 1 or not before.size or before.shape != after.shape:
        raise ValueError("Similarity checkpoints must use the same non-empty windows")
    if not np.isfinite(before).all() or not np.isfinite(after).all():
        raise ValueError("Non-finite similarity measurements")
    delta = after - before
    return {"passed": bool(delta.mean() >= 0 and delta.min() >= -0.02),
            "mean_delta": float(delta.mean()), "worst_window_delta": float(delta.min()),
            "minimum_mean_delta": 0, "minimum_window_delta": -0.02,
            "assessment": "Embedding regression gate only; listening and word checks still required"}


def score_checkpoints(anchor, checkpoints, *, encoder=None, preprocess=None):
    """Score common time windows of mono, single-speaker checkpoints against the exact anchor."""
    # Conda-based venvs need this directory for librosa's lzma dependency on Windows.
    base = Path(sys.base_prefix)
    dll_paths = [base / "Library" / "bin"]
    if base.parent.name == "envs":
        dll_paths.append(base.parent.parent / "Library" / "bin")
    dll_handles = [os.add_dll_directory(str(path)) for path in dll_paths if path.is_dir()] if os.name == "nt" else []
    if encoder is None or preprocess is None:
        try:
            from resemblyzer import VoiceEncoder, preprocess_wav
        except ImportError as exc:
            raise RuntimeError("Speaker scoring needs the optional Resemblyzer environment; see docs/advanced-audio.md") from exc
        encoder, preprocess = VoiceEncoder(device="cpu"), preprocess_wav
    if not checkpoints:
        raise ValueError("Provide at least one checkpoint")
    files = {"anchor": Path(anchor), **{key: Path(value) for key, value in checkpoints.items()}}
    audio = {}
    for label, path in files.items():
        samples, sr = sf.read(path, dtype="float32", always_2d=True)
        if samples.shape[1] != 1 or not samples.size or not np.isfinite(samples).all():
            raise ValueError("Similarity scoring requires finite mono single-speaker audio")
        if np.max(np.abs(samples)) >= 1:
            # Common constant attenuation prevents preprocessing int16 overflow.
            samples = samples / (np.max(np.abs(samples)) + 1e-6) * 0.95
        audio[label] = (samples[:, 0], sr)
    ref, ref_rate = audio.pop("anchor")
    ref = preprocess(ref, source_sr=ref_rate)
    if len(ref) < 16000:
        raise ValueError("Reference has less than one second of detected speech")
    reference_embedding = encoder.embed_utterance(ref)
    durations = [len(samples) / sr for samples, sr in audio.values()]
    if max(durations) - min(durations) > 0.05:
        raise ValueError("Checkpoint durations differ; align before speaker regression scoring")
    duration = min(durations)
    width = min(8.0, duration)
    starts = np.linspace(0, max(0, duration - width), max(1, min(8, int(np.ceil(duration / width)))))
    scores = {label: [] for label in checkpoints}
    accepted, skipped = [], []
    for start in starts:
        prepared = {}
        for label, (samples, sr) in audio.items():
            segment = samples[int(start * sr):int((start + width) * sr)]
            prepared[label] = preprocess(segment, source_sr=sr)
        if any(len(segment) < 16000 for segment in prepared.values()):
            skipped.append(float(start))
            continue
        accepted.append(float(start))
        for label, segment in prepared.items():
            scores[label].append(cosine(reference_embedding, encoder.embed_utterance(segment)))
    if not accepted:
        raise ValueError("No common windows contain enough detected speech")
    report = {
        "metric": "speaker_encoder_cosine_similarity", "encoder": "Resemblyzer/VoiceEncoder CPU",
        "window_seconds": width, "window_starts": accepted, "skipped_window_starts": skipped,
        "files": {label: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for label, path in files.items()},
        "scores": {label: {"mean": float(np.mean(values)), "windows": values} for label, values in scores.items()},
        "assessment": "A content/channel-sensitive proxy; not proof of identity, emotion, or word preservation",
    }
    try:
        report["encoder_package_version"] = version("resemblyzer")
        weights = Path(distribution("resemblyzer").locate_file("resemblyzer/pretrained.pt"))
        if weights.is_file():
            report["encoder_checkpoint_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
    except PackageNotFoundError:
        report["encoder_package_version"] = "injected-test-encoder"
    for handle in dll_handles:
        handle.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--spectral", required=True, type=Path)
    parser.add_argument("--neural", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    checkpoints = {"raw": args.raw, "spectral": args.spectral}
    if args.neural:
        checkpoints["neural"] = args.neural
    report = score_checkpoints(args.anchor, checkpoints)
    report["spectral_gate"] = regression_gate(report["scores"]["raw"]["windows"],
                                               report["scores"]["spectral"]["windows"])
    if args.neural:
        report["neural_gate"] = regression_gate(report["scores"]["spectral"]["windows"],
                                                 report["scores"]["neural"]["windows"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
