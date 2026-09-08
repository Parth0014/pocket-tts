"""Local automatic narration mastering. No Torch, model downloads, or network access."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

VERSION = "mastering-v2"
PROFILES = ("off", "auto", "natural", "warm_story")
TRUE_PEAK = -1.5


def resolve_ffmpeg():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(executable, arguments):
    try:
        result = subprocess.run(
            [os.fspath(executable), "-hide_banner", "-nostdin", *map(os.fspath, arguments)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Mastering FFmpeg failed: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Mastering FFmpeg failed: {result.stderr[-2000:]}")
    return result


def _stats(stderr):
    for block in reversed(re.findall(r"\{[^{}]*\}", stderr)):
        data = json.loads(block)
        if "input_i" in data:
            numbers = {key: float(data[key]) for key in (
                "input_i", "input_tp", "input_lra", "input_thresh", "target_offset"
            )}
            if not all(math.isfinite(value) for value in numbers.values()):
                raise ValueError("Audio is too quiet or too short for reliable loudness measurement")
            return {**numbers, "normalization_type": data.get("normalization_type")}
    raise RuntimeError("FFmpeg did not return loudness measurements")


def _loudnorm(target):
    # A high LRA target permits linear gain without imposing a narrow dynamic range.
    # Mono is measured as one channel (dual_mono=false), with its own target.
    return f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA=50:dual_mono=false"


def measure(path, ffmpeg=None, target=-19):
    result = _run(ffmpeg or resolve_ffmpeg(), [
        "-i", path, "-map", "0:a:0", "-af", _loudnorm(target) + ":print_format=json",
        "-f", "null", "-",
    ])
    measurements = _stats(result.stderr)
    # The analysis pass's hypothetical render mode is not the actual master mode.
    measurements.pop("normalization_type", None)
    return measurements


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate(path):
    info = sf.info(path)
    if info.channels not in (1, 2) or info.samplerate < 16000:
        raise ValueError("Mastering requires mono/stereo audio sampled at 16 kHz or higher")
    if info.duration < 3:
        raise ValueError("Mastering requires at least 3 seconds for loudness measurement")
    peak, total, count = 0.0, 0.0, 0
    for block in sf.blocks(path, blocksize=65536, dtype="float64", always_2d=True):
        if not np.isfinite(block).all():
            raise ValueError("Audio contains non-finite samples")
        peak = max(peak, float(np.max(np.abs(block))))
        total += float(np.sum(block * block))
        count += block.size
    if peak < 1e-5 or total / max(count, 1) < 1e-12:
        raise ValueError("Audio is silent or too quiet")
    return info, {"sample_peak": peak, "rms": math.sqrt(total / count)}


def _tone(profile, input_lufs):
    # Stabilize the compressor operating level across different input gains.
    pre_gain = min(12.0, max(-24.0, -23.0 - input_lufs))
    filters = [f"volume={pre_gain:.6f}dB", "highpass=f=55:poles=2"]
    if profile == "warm_story":
        filters += ["equalizer=f=180:t=q:w=0.7:g=1.2", "equalizer=f=3000:t=q:w=0.8:g=0.6"]
    ratio = 1.5 if profile == "natural" else 1.7
    filters += [f"acompressor=threshold=0.125:ratio={ratio}:attack=25:release=160:makeup=1:knee=2.828"]
    return ",".join(filters)


def analyze_spectrum(path):
    """Bounded-memory channel-aware low-frequency energy measurement, not an emotion score."""
    rate = sf.info(path).samplerate
    size = 8192
    window = np.hanning(size)[:, None]
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    low, total = 0.0, 0.0
    for block in sf.blocks(path, blocksize=size, dtype="float64", always_2d=True):
        padded = np.zeros((size, block.shape[1]))
        padded[:len(block)] = block
        power = np.abs(np.fft.rfft(padded * window, axis=0)) ** 2
        low += float(power[frequencies < 50].sum())
        total += float(power.sum())
    return {"below_50hz_energy_fraction": low / max(total, 1e-30)}


def automatic_plan(before, spectrum, target):
    """Conservative, versioned heuristics: only correct measured technical issues."""
    filters, decisions = [], []
    low_fraction = spectrum["below_50hz_energy_fraction"]
    highpass = low_fraction > 0.03
    decisions.append({"stage": "highpass", "enabled": highpass,
                      "reason": "More than 3% of energy below 50 Hz" if highpass
                      else "Low-frequency energy is within the conservative threshold"})
    if highpass:
        filters.append("highpass=f=55:poles=2")
    gain_to_target = target - before["input_i"]
    predicted_peak = before["input_tp"] + gain_to_target
    peak_excess = predicted_peak - TRUE_PEAK
    # Avoid compressing healthy dynamics simply because the reading has dramatic pauses.
    compress = peak_excess > 1
    ratio = min(2.0, 1.2 + max(0, peak_excess) / 10) if compress else 1.0
    decisions.append({"stage": "compression", "enabled": compress, "ratio": ratio,
                      "predicted_peak_at_target_dbtp": predicted_peak,
                      "reason": "Peaks constrain the requested listening level" if compress
                      else "Enough headroom; preserve existing dynamics"})
    if compress:
        pre_gain = min(12.0, max(-24.0, -23.0 - before["input_i"]))
        filters.extend([f"volume={pre_gain:.6f}dB",
                        f"acompressor=threshold=0.125:ratio={ratio:.6f}:attack=25:release=160:makeup=1:knee=2.828"])
    decisions.append({"stage": "tone_boost", "enabled": False,
                      "reason": "Warmth and presence are aesthetic choices, not reliably inferred defects"})
    decisions.append({"stage": "loudness", "enabled": True, "target_lufs": target,
                      "reason": "Measure and normalize the complete narration, preserving dynamic range where possible"})
    return ",".join(filters) or "anull", decisions


def master_file(source, destination, profile="auto", *, ffmpeg=None):
    """Write a verified WAV without overwriting files; return a JSON-safe audit report.

    Off mode copies bytes exactly and requires neither FFmpeg nor loudness analysis.
    Enabled modes stage output beside the destination and publish only after validation.
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown mastering profile: {profile}")
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise FileExistsError("Use a new output path; mastering never overwrites audio")
    if destination.suffix.lower() != ".wav":
        raise ValueError("Mastering output must be a .wav file")
    started = time.perf_counter()
    report = {"version": VERSION, "profile": profile, "input_sha256": _hash(source)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="master_", dir=destination.parent) as temporary:
        staged = Path(temporary) / "master.wav"
        if profile == "off":
            shutil.copyfile(source, staged)
        else:
            info, signal = _validate(source)
            executable = ffmpeg or resolve_ffmpeg()
            target = -19 if info.channels == 1 else -16
            before = measure(source, executable, target)
            if profile == "auto":
                spectrum = analyze_spectrum(source)
                tone, decisions = automatic_plan(before, spectrum, target)
                report.update(analysis=spectrum, decisions=decisions)
            else:
                tone = _tone(profile, before["input_i"])
            conditioned = Path(temporary) / "conditioned.wav"
            _run(executable, ["-y", "-i", source, "-map", "0:a:0", "-af", tone,
                              "-ar", str(info.samplerate), "-c:a", "pcm_f32le", conditioned])
            first = measure(conditioned, executable, target)
            normalization = _loudnorm(target) + (
                f":measured_I={first['input_i']}:measured_TP={first['input_tp']}"
                f":measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}"
                f":offset={first['target_offset']}:linear=true:print_format=json"
            )
            # Explicit output rate prevents loudnorm's internal 192 kHz from leaking out.
            encoding = f"aresample={info.samplerate}:osf=s16:dither_method=triangular"
            rendered = _run(executable, [
                "-y", "-i", conditioned, "-map", "0:a:0", "-af", normalization + "," + encoding,
                "-ar", str(info.samplerate), "-c:a", "pcm_s16le", staged,
            ])
            mode = _stats(rendered.stderr)["normalization_type"]
            after_info, after_signal = _validate(staged)
            if (after_info.frames, after_info.channels, after_info.samplerate) != (
                info.frames, info.channels, info.samplerate
            ):
                raise RuntimeError("Mastering changed frame count, channels, or sample rate")
            after = measure(staged, executable, target)
            if after["input_tp"] > TRUE_PEAK + 0.1:
                raise RuntimeError("Encoded master exceeds the true-peak ceiling")
            warnings = []
            if abs(after["input_i"] - target) > 1:
                warnings.append("Loudness differs from target by over 1 LU; audition before use.")
            if mode == "dynamic":
                warnings.append("Peak constraints required dynamic loudness processing.")
            report.update({
                "sample_rate": info.samplerate, "channels": info.channels,
                "frames": info.frames, "target_lufs": target, "true_peak_ceiling_dbtp": TRUE_PEAK,
                "before": {**before, **signal}, "after": {**after, **after_signal},
                "normalization_mode": mode, "tone_filter": tone, "normalization_filter": normalization,
                "encoding_filter": encoding, "ffmpeg_version": _run(executable, ["-version"]).stdout.splitlines()[0],
                "warnings": warnings,
                "assessment": "Signal measurements only; delivery and voice fidelity require listening.",
            })
        # Exclusive creation avoids a concurrent run replacing an existing artifact.
        with destination.open("xb") as output, staged.open("rb") as input_stream:
            try:
                shutil.copyfileobj(input_stream, output)
            except BaseException:
                output.close()
                destination.unlink(missing_ok=True)
                raise
    report.update(output_sha256=_hash(destination), seconds=time.perf_counter() - started)
    return report


def master_audio(samples, sample_rate, profile="auto", *, ffmpeg=None, temp_dir=None):
    """Accept samples in frames or frames x channels layout; return PCM16-grid float samples."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown mastering profile: {profile}")
    if profile == "off":
        return np.array(samples, copy=True), {"version": VERSION, "profile": "off"}
    with tempfile.TemporaryDirectory(prefix="master_audio_", dir=temp_dir) as directory:
        source, destination = Path(directory) / "input.wav", Path(directory) / "output.wav"
        sf.write(source, samples, sample_rate, subtype="FLOAT")
        report = master_file(source, destination, profile, ffmpeg=ffmpeg)
        result, _ = sf.read(destination, dtype="float32", always_2d=np.asarray(samples).ndim == 2)
    return result, report


def compare(source, directory):
    """Create delivery masters and gain-only, loudness-matched audition copies."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    executable = resolve_ffmpeg()
    reports = {}
    for profile in PROFILES:
        output = directory / f"{profile}.wav"
        reports[profile] = master_file(source, output, profile, ffmpeg=executable)
    measurements = {profile: measure(directory / f"{profile}.wav", executable) for profile in PROFILES}
    common = min(value["input_i"] for value in measurements.values())
    cards = []
    for profile, value in measurements.items():
        audition = f"{profile}_matched.wav"
        gain = common - value["input_i"]
        _run(executable, ["-y", "-i", directory / f"{profile}.wav", "-af", f"volume={gain:.6f}dB",
                          "-c:a", "pcm_s24le", directory / audition])
        reports[profile]["audition_gain_db"] = gain
        cards.append(f'<button onclick="choose(\'{audition}\')">{html.escape(profile)}</button>')
    (directory / "report.json").write_text(json.dumps(reports, indent=2, allow_nan=False), encoding="utf-8")
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Narration mastering comparison</title>
<style>body{max-width:800px;margin:60px auto;font:18px system-ui;padding:20px;background:#101820;color:#eef}
button{padding:14px;margin:8px;cursor:pointer}audio{width:100%;margin:24px 0}a{color:#9df}</style>
<h1>Narration mastering comparison</h1><p>Switch versions at the same playback position.
Audition copies are matched using constant gain; delivery masters retain their target loudness.</p>
BUTTONS<p id="label">off</p><audio id="player" controls src="off_matched.wav"></audio>
<p>Listen for naturalness, warmth, clear consonants, and fatigue. Processing does not add acting or emotion.</p>
<p><a href="report.json">Measurements</a> · Delivery WAVs: <a href="off.wav">Original</a> ·
<a href="auto.wav">Automatic</a> · <a href="natural.wav">Natural</a> · <a href="warm_story.wav">Warm story</a></p>
<script>const p=document.getElementById('player');function choose(file){const t=p.currentTime,play=!p.paused;
p.pause();p.src=file;document.getElementById('label').textContent=file.replace('_matched.wav','');
p.onloadedmetadata=()=>{p.currentTime=Math.min(t,p.duration);if(play)p.play().catch(()=>{});};p.load();}</script></html>'''
    (directory / "index.html").write_text(page.replace("BUTTONS", "".join(cards)), encoding="utf-8")
    return directory / "index.html"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile", choices=PROFILES, default="auto")
    outputs = parser.add_mutually_exclusive_group(required=True)
    outputs.add_argument("--output", type=Path)
    outputs.add_argument("--compare-dir", type=Path, help="New folder for all presets and an A/B player")
    args = parser.parse_args(argv)
    if args.compare_dir:
        print(compare(args.source, args.compare_dir))
    else:
        report_path = args.output.with_suffix(".mastering.json")
        if report_path.exists():
            raise FileExistsError(report_path)
        report = master_file(args.source, args.output, args.profile)
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    main()
