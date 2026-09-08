"""Optional local restoration experiment. Never called automatically by generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from narration_mastering import master_file
from speaker_similarity import regression_gate, score_checkpoints


def restore_candidate(source, destination, run_dir, *, mode="enhance", nfe=32, tau=0.3):
    """Adapter for upstream Resemble Enhance; keep dependency installation outside the TTS environment."""
    if mode not in {"enhance", "denoise_reference"} or nfe not in {32, 64} or not 0 <= tau <= 0.5:
        raise ValueError("Unsupported restoration experiment settings")
    run_dir, destination = Path(run_dir), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    # No surprise checkpoint download during an audio-generation job.
    for relative in ("hparams.yaml", "ds/G/latest", "ds/G/default/mp_rank_00_model_states.pt"):
        if not (run_dir / relative).is_file():
            raise FileNotFoundError(f"Install the Resemble checkpoint first: {run_dir / relative}")
    try:
        import torch
        from resemble_enhance.enhancer.inference import denoise, enhance
    except ImportError as exc:
        raise RuntimeError("Resemble Enhance needs a separate compatible environment; see docs/advanced-audio.md") from exc
    samples, sr = sf.read(source, dtype="float32", always_2d=True)
    if samples.shape[1] != 1 or not np.isfinite(samples).all() or len(samples) / sr < 3:
        raise ValueError("Restoration experiments require at least 3 seconds of finite mono audio")
    tensor = torch.from_numpy(samples[:, 0])
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(1234)
        if mode == "denoise_reference":
            result, rate = denoise(tensor, sr, "cpu", run_dir=run_dir)
        else:
            # Upstream uses lambd, not denoise=False. Zero bypasses its denoiser branch.
            result, rate = enhance(tensor, sr, "cpu", nfe=nfe, solver="midpoint", lambd=0.0,
                                   tau=tau, run_dir=run_dir)
    result = result.detach().cpu().numpy()
    if result.ndim != 1 or not np.isfinite(result).all():
        raise ValueError("Restoration produced invalid audio")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        sf.write(output, result, rate, format="WAV", subtype="FLOAT")
    return {"mode": mode,
            **({"nfe": nfe, "solver": "midpoint", "lambd": 0, "tau": tau} if mode == "enhance" else {}),
            "seed": 1234, "sample_rate": rate,
            "checkpoint_sha256": hashlib.sha256((run_dir / "ds/G/default/mp_rank_00_model_states.pt").read_bytes()).hexdigest()}


def evaluate_candidate(anchor, baseline, candidate, directory):
    """Fall back to the deterministic baseline when similarity or timing checks fail.

    The selected experimental audio is mastered AFTER restoration, so neural processing
    cannot bypass loudness/peak validation. This gate does not authorize shipping.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        similarity = score_checkpoints(anchor, {"spectral": baseline, "neural": candidate})
        gate = regression_gate(similarity["scores"]["spectral"]["windows"],
                               similarity["scores"]["neural"]["windows"])
    except ValueError as exc:
        similarity, gate = None, {"passed": False, "reason": str(exc)}
    selected = candidate if gate["passed"] else baseline
    master = master_file(selected, directory / "selected.wav", "auto")
    report = {"selected": "neural" if gate["passed"] else "spectral",
              "gate": gate, "similarity": similarity, "mastering": master,
              "shipping_approved": False, "next_check": "Audition speaker character and verify spoken words"}
    (directory / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Spectral premaster, or raw reference in denoise mode")
    parser.add_argument("--anchor", type=Path, required=True, help="One speaker's exact reference anchor")
    parser.add_argument("--output-dir", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--candidate", type=Path, help="Score an already restored candidate")
    inputs.add_argument("--run-dir", type=Path, help="Installed Resemble Enhance checkpoint directory")
    parser.add_argument("--mode", choices=("enhance", "denoise_reference"), default="enhance")
    parser.add_argument("--nfe", type=int, choices=(32, 64), default=32)
    parser.add_argument("--tau", type=float, default=0.3)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    candidate = args.candidate
    inference = None
    if candidate is None:
        candidate = args.output_dir / "candidate.wav"
        inference = restore_candidate(args.source, candidate, args.run_dir, mode=args.mode, nfe=args.nfe, tau=args.tau)
    if args.mode == "denoise_reference":
        # Denoised references must be separately auditioned and passed to prepare_reference;
        # never automatically replace an enrolled source or apply delivery mastering to it.
        similarity = score_checkpoints(args.anchor, {"raw_reference": args.source, "denoised_reference": candidate})
        report = {"inference": inference, "similarity": similarity, "automatic_reference_replacement": False}
        (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    else:
        report = evaluate_candidate(args.anchor, args.source, candidate, args.output_dir / "evaluation")
        report["inference"] = inference
        (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(args.output_dir)


if __name__ == "__main__":
    main()
