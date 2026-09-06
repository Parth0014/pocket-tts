"""Conservative, inspectable reference selection for Pocket TTS.

The ranking measures signal level, clipping, pauses and cut boundaries. It does
not identify speech, background music, speakers, emotion or voice similarity.
Listen to the selected anchor before accepting a new voice reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import soundfile as sf

ALGORITHM_VERSION = "reference-v1"
SAMPLE_RATE = 24_000
FRAME_SAMPLES = 480
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
MIN_SECONDS = 2.0
MAX_SOURCE_SECONDS = 7_200
MAX_CHANNELS = 8
REVIEW_WARNING = (
    "Signal-quality ranking cannot detect music, multiple speakers, speech accuracy, "
    "accent or emotion. Audition this segment and choose a clean single-speaker "
    "recording performed in the storytelling style you want."
)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ffmpeg():
    try:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, OSError, RuntimeError):
        executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("Reference decoding requires imageio-ffmpeg or ffmpeg on PATH.")
    return executable


def _check_info(info):
    if info.channels < 1 or info.channels > MAX_CHANNELS:
        raise ValueError(f"Reference must have between 1 and {MAX_CHANNELS} audio channels.")
    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("Reference audio is empty or has an invalid sample rate.")
    if info.duration > MAX_SOURCE_SECONDS:
        raise ValueError("Reference exceeds the two-hour processing limit; select a shorter recording.")


@contextmanager
def _decoded_source(source):
    """Yield a 24 kHz file while preserving channels; never downmix by averaging."""
    try:
        info = sf.info(source)
    except (RuntimeError, OSError):
        info = None
    if info is not None:
        _check_info(info)
        if info.samplerate == SAMPLE_RATE:
            yield source, info
            return
    with tempfile.TemporaryDirectory(prefix="narration-reference-") as directory:
        decoded = Path(directory) / "decoded.wav"
        command = [
            _ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:a:0", "-vn", "-ar", str(SAMPLE_RATE),
            "-t", str(MAX_SOURCE_SECONDS + 1), "-c:a", "pcm_f32le", str(decoded),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not decode reference audio: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-1500:]
            raise ValueError(f"Could not decode reference audio: {detail}")
        info = sf.info(decoded)
        _check_info(info)
        yield decoded, info


def probe_reference(source_path):
    """Validate the container and return duration/channels; prepare checks samples."""
    source = Path(source_path).expanduser().resolve(strict=True)
    try:
        info = sf.info(source)
        _check_info(info)
    except (RuntimeError, OSError):
        with _decoded_source(source) as (_, info):
            return {"sample_rate": info.samplerate, "channels": info.channels,
                    "duration_seconds": info.duration, "frames": info.frames}
    return {"sample_rate": info.samplerate, "channels": info.channels,
            "duration_seconds": info.duration, "frames": info.frames}


def _analyze_frames(path, channels):
    """Scan the entire recording in bounded memory, including every channel."""
    metrics = {key: [] for key in ("rms", "peak", "clipped", "mean")}
    with sf.SoundFile(path) as stream:
        while True:
            block = stream.read(SAMPLE_RATE * 10, dtype="float32", always_2d=True)
            if not len(block):
                break
            if not np.isfinite(block).all():
                raise ValueError("Reference contains non-finite audio samples (NaN or infinity).")
            remainder = len(block) % FRAME_SAMPLES
            if remainder:
                block = np.pad(block, ((0, FRAME_SAMPLES - remainder), (0, 0)))
            frames = block.reshape(-1, FRAME_SAMPLES, channels).astype(np.float64)
            mean = frames.mean(axis=1)
            metrics["mean"].append(mean)
            metrics["rms"].append(np.sqrt(np.mean((frames - mean[:, None, :]) ** 2, axis=1)))
            metrics["peak"].append(np.max(np.abs(frames), axis=1))
            metrics["clipped"].append(np.mean(np.abs(frames) >= 0.999, axis=1))
    if not metrics["rms"]:
        raise ValueError("Reference audio is empty.")
    return {key: np.concatenate(values) for key, values in metrics.items()}


def _longest_false_run(active):
    indexes = np.flatnonzero(np.r_[True, active, True])
    return int(np.max(np.diff(indexes) - 1))


def _candidate(metrics, channel, start, end, threshold, target, total_seconds, *, trim):
    first = max(0, int(round(start / FRAME_SECONDS)))
    last = min(len(metrics["rms"]), int(math.ceil(end / FRAME_SECONDS)))
    active = metrics["rms"][first:last, channel] >= threshold
    positions = np.flatnonzero(active)
    if not len(positions):
        return None
    if trim:
        # Retain 200 ms room at the outside, and preserve all internal pauses.
        original_first = first
        first = max(first, original_first + int(positions[0]) - 10)
        last = min(last, original_first + int(positions[-1]) + 11)
        start = first * FRAME_SECONDS
        end = min(last * FRAME_SECONDS, total_seconds, start + target)
        active = metrics["rms"][first:last, channel] >= threshold
    duration = end - start
    if duration < MIN_SECONDS or np.count_nonzero(active) * FRAME_SECONDS < 1.0:
        return None
    rms = metrics["rms"][first:last, channel]
    clipping = float(np.mean(metrics["clipped"][first:last, channel]))
    active_fraction = float(np.mean(active))
    # Quiet cut boundaries reduce the chance of beginning or ending mid-phrase.
    boundary = float((np.mean(active[:5]) + np.mean(active[-5:])) / 2)
    silence_run = _longest_false_run(active) * FRAME_SECONDS
    level = float(np.sqrt(np.mean(rms ** 2)))
    active_levels = rms[active]
    variation_db = float(20 * np.log10(max(np.percentile(active_levels, 90), 1e-9)
                                    / max(np.percentile(active_levels, 10), 1e-9)))
    score = (
        4 * active_fraction + min(duration / target, 1.0)
        - 15 * clipping - 1.5 * silence_run / duration - 0.4 * boundary
        + 0.25 * min(variation_db / 12.0, 1.0)
        - max(0.0, (-40 - 20 * math.log10(max(level, 1e-9))) / 20)
    )
    return {
        "start_seconds": round(start, 6), "end_seconds": round(end, 6),
        "duration_seconds": round(duration, 6), "channel": int(channel),
        "score": round(score, 6), "active_fraction": round(active_fraction, 6),
        "clipping_fraction": round(clipping, 8),
        "peak": round(float(np.max(metrics["peak"][first:last, channel])), 7),
        "rms": round(level, 7), "boundary_activity": round(boundary, 6),
        "level_variation_db": round(variation_db, 3),
        "longest_quiet_seconds": round(silence_run, 3),
        "mean_offset": round(float(np.mean(metrics["mean"][first:last, channel])), 7),
    }


def _rank_candidates(metrics, total_seconds, target, manual_start):
    frame_count, channels = metrics["rms"].shape
    candidates = []
    for channel in range(channels):
        levels = metrics["rms"][:, channel]
        if float(np.max(levels)) < 0.0001:
            continue
        # Energy activity is deliberately not called speech or a VAD estimate.
        nonquiet = levels[levels >= 0.0001]
        # A loud/clipped stretch must not hide the quieter useful section.
        threshold = max(0.0001, min(0.005, float(np.percentile(nonquiet, 75)) * 0.10))
        if manual_start is not None:
            candidate = _candidate(metrics, channel, manual_start,
                                   min(total_seconds, manual_start + target),
                                   threshold, target, total_seconds, trim=False)
            if candidate:
                candidates.append(candidate)
            continue
        latest = max(0.0, total_seconds - target)
        starts = {0.0, latest}
        starts.update(float(value) for value in np.arange(0, latest, 0.5))
        # Add local low-energy starts as options without joining discontinuous audio.
        for frame in range(0, int(latest / FRAME_SECONDS), 25):
            right = min(frame_count, frame + 25)
            quiet_start = (frame + int(np.argmin(levels[frame:right]))) * FRAME_SECONDS
            if quiet_start <= latest:
                starts.add(quiet_start)
        for start in sorted(starts):
            candidate = _candidate(metrics, channel, start, min(start + target, total_seconds),
                                   threshold, target, total_seconds, trim=True)
            if candidate:
                candidates.append(candidate)
    if not candidates:
        raise ValueError(
            "Reference has no usable segment: use at least 2 seconds of audible audio "
            "with at least 1 second of activity; silence, DC-only and near-silent clips cannot condition a voice."
        )
    candidates.sort(key=lambda item: (-item["score"], item["start_seconds"], item["channel"]))
    selected = []
    for candidate in candidates:
        if all(
            max(0, min(candidate["end_seconds"], other["end_seconds"])
                - max(candidate["start_seconds"], other["start_seconds"]))
            < 0.5 * min(candidate["duration_seconds"], other["duration_seconds"])
            for other in selected
        ):
            selected.append(candidate)
        if len(selected) == 3:
            break
    return selected


def _read_segment(decoded, candidate):
    start = int(round(candidate["start_seconds"] * SAMPLE_RATE))
    end = int(round(candidate["end_seconds"] * SAMPLE_RATE))
    with sf.SoundFile(decoded) as stream:
        stream.seek(start)
        samples = stream.read(end - start, dtype="float32", always_2d=True)[:, candidate["channel"]]
    if len(samples) < MIN_SECONDS * SAMPLE_RATE or not np.isfinite(samples).all():
        raise ValueError("Selected reference is too short or contains non-finite samples.")
    peak = float(np.max(np.abs(samples)))
    # Only a small level increase is allowed. Peak attenuation prevents output clipping.
    gain = min(10 ** (6 / 20), (10 ** (-3 / 20)) / max(peak, 1e-9))
    samples = samples * gain
    return samples, 20 * math.log10(gain)


def _atomic_wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".wav", dir=path.parent)
    os.close(descriptor)
    try:
        sf.write(temporary, samples, SAMPLE_RATE, subtype="PCM_16", format="WAV")
        content_hash = _sha256(temporary)
        os.replace(temporary, path)
        return content_hash
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_cache(output, manifest, settings, source_hash):
    try:
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if (report["algorithm_version"] != ALGORITHM_VERSION or report["settings"] != settings
                or report["source_sha256"] != source_hash or report["anchor_sha256"] != _sha256(output)):
            return None
        info = sf.info(output)
        if (info.samplerate != SAMPLE_RATE or info.channels != 1 or info.format != "WAV"
                or abs(info.duration - report["duration_seconds"]) > 1 / SAMPLE_RATE
                or not MIN_SECONDS <= info.duration <= settings["target_seconds"] + 1 / SAMPLE_RATE):
            return None
        if (not isinstance(report["warnings"], list)
                or not all(isinstance(warning, str) for warning in report["warnings"])
                or not isinstance(report["candidates"], list) or not report["candidates"]
                or abs(report["selected_end_seconds"] - report["selected_start_seconds"]
                       - info.duration) > 1 / SAMPLE_RATE):
            return None
        first = report["candidates"][0]
        if (first["start_seconds"] != report["selected_start_seconds"]
                or first["end_seconds"] != report["selected_end_seconds"]
                or first["channel"] != report["selected_channel"]):
            return None
        samples, _ = sf.read(output, dtype="float32")
        if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 0.0001:
            return None
        report.update(anchor_path=str(output), manifest_path=str(manifest), cache_hit=True)
        return report
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def prepare_reference(source_path, output_path, *, target_seconds=15.0, start_seconds=None):
    """Persist a mono 24 kHz anchor and .json report; reuse verified content cache.

    Automatic mode ranks contiguous windows across the entire source. Manual
    mode honors the supplied start without trimming; the end is capped by EOF.
    Processing never changes pitch, tempo or internal pauses and never denoises.
    """
    source = Path(source_path).expanduser().resolve(strict=True)
    output = Path(output_path).expanduser().resolve()
    manifest = output.with_suffix(".json")
    if source in (output, manifest) or (output.exists() and os.path.samefile(source, output)):
        raise ValueError("The processed anchor and manifest must not overwrite the raw source.")
    if output.suffix.lower() != ".wav":
        raise ValueError("Processed reference output must have a .wav extension.")
    target = float(target_seconds)
    start = None if start_seconds is None else float(start_seconds)
    if not math.isfinite(target) or not MIN_SECONDS <= target <= 15.0:
        raise ValueError("target_seconds must be finite and between 2 and 15 seconds.")
    if start is not None and (not math.isfinite(start) or start < 0):
        raise ValueError("start_seconds must be a finite, non-negative number.")
    settings = {"target_seconds": target, "start_seconds": start, "sample_rate": SAMPLE_RATE,
                "max_gain_db": 6.0, "peak_target_dbfs": -3.0}
    source_hash = _sha256(source)
    cached = _load_cache(output, manifest, settings, source_hash)
    if cached is not None:
        cached["source_path"] = str(source)
        return cached
    with _decoded_source(source) as (decoded, info):
        if info.duration < MIN_SECONDS:
            raise ValueError("Reference is too short: provide at least 2 seconds of usable audio.")
        if start is not None and info.duration - start < MIN_SECONDS:
            raise ValueError("Manual segment start leaves less than 2 seconds of reference audio.")
        metrics = _analyze_frames(decoded, info.channels)
        candidates = _rank_candidates(metrics, info.duration, target, start)
        selected = candidates[0]
        samples, gain_db = _read_segment(decoded, selected)
        warnings = [REVIEW_WARNING]
        if len(samples) / SAMPLE_RATE < 5:
            warnings.append("This anchor is shorter than 5 seconds; a longer clean performance may condition the voice better.")
        if selected["active_fraction"] < 0.60:
            warnings.append("The anchor contains substantial low-energy audio or pauses; audition another candidate.")
        if selected["clipping_fraction"] > 0.001:
            warnings.append("The source segment contains clipped samples; lowering its level cannot undo this distortion.")
        if selected["boundary_activity"] > 0.75:
            warnings.append("The segment has activity at both cut boundaries; check for cut-off words and use --start if needed.")
        if selected["level_variation_db"] < 3:
            warnings.append("The segment has little level variation; verify that it contains natural speech rather than steady noise or tone.")
        if abs(selected["mean_offset"]) > 0.01:
            warnings.append("The selected segment has a noticeable DC offset; check the source recording.")
        if info.channels > 1:
            warnings.append(f"Selected channel {selected['channel'] + 1} of {info.channels}; channels were not mixed, avoiding phase cancellation. Verify the intended speaker.")
        if selected["rms"] < 0.005:
            warnings.append("The reference is very quiet. Gain is limited to +6 dB to avoid amplifying background noise excessively.")
        report = {
            "algorithm_version": ALGORITHM_VERSION, "settings": settings,
            "source_path": str(source), "source_sha256": source_hash,
            "anchor_path": str(output), "manifest_path": str(manifest),
            "source_duration_seconds": info.duration, "source_channels": info.channels,
            "duration_seconds": len(samples) / SAMPLE_RATE, "sample_rate": SAMPLE_RATE,
            "selected_start_seconds": selected["start_seconds"],
            "selected_end_seconds": selected["end_seconds"],
            "selected_channel": selected["channel"],
            "selection_mode": "manual" if start is not None else "automatic",
            "gain_db": round(gain_db, 5), "warnings": warnings, "candidates": candidates,
            "cache_hit": False,
        }
        # Detect a replaced/uploaded source while analysis was running.
        if _sha256(source) != source_hash:
            raise RuntimeError("The source changed during reference preparation; retry with a stable file.")
        report["anchor_sha256"] = _atomic_wav(output, samples)
    _atomic_json(manifest, report)
    return report


def export_candidate_previews(source_path, report, output_dir, *, limit=3):
    """Export alternatives for human listening, using the same conservative gain."""
    source = Path(source_path).expanduser().resolve(strict=True)
    directory = Path(output_dir).expanduser().resolve()
    if _sha256(source) != report["source_sha256"]:
        raise ValueError("Reference source changed after selection; prepare it again before exporting previews.")
    previews = []
    with _decoded_source(source) as (decoded, _):
        for index, candidate in enumerate(report["candidates"][:max(0, int(limit))], start=1):
            output = directory / f"reference-candidate-{index}.wav"
            if output == source or (output.exists() and os.path.samefile(source, output)):
                raise ValueError("Candidate preview must not overwrite the raw source.")
            samples, gain_db = _read_segment(decoded, candidate)
            _atomic_wav(output, samples)
            previews.append({**candidate, "audio_path": str(output), "gain_db": round(gain_db, 5)})
    return previews


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Raw single-speaker reference recording")
    parser.add_argument("--output", type=Path, required=True, help="Prepared .wav path (raw source is retained)")
    parser.add_argument("--seconds", type=float, default=15, help="Maximum contiguous duration, 2 to 15 seconds")
    parser.add_argument("--start", type=float, help="Override automatic selection with an exact start in seconds")
    parser.add_argument("--previews-dir", type=Path, help="Also export up to three alternatives for listening")
    args = parser.parse_args(argv)
    try:
        report = prepare_reference(args.source, args.output, target_seconds=args.seconds, start_seconds=args.start)
        if args.previews_dir:
            report["previews"] = export_candidate_previews(args.source, report, args.previews_dir)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Reference preparation failed: {exc}\n")
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
