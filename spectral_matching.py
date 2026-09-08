"""Conservative reference-derived match EQ; no learned model or new dependencies."""

from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

VERSION = "spectral-match-v1"


def spectrum(samples, sr):
    """Welch-style mean power from active Hann frames; channels contribute power, not phase."""
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] not in (1, 2) or sr < 16000:
        raise ValueError("Expected mono/stereo samples at 16 kHz or higher")
    if not data.size or not np.isfinite(data).all():
        raise ValueError("Empty or non-finite spectral input")
    size = 2 ** math.ceil(math.log2(sr * 0.085))
    hop = size // 2
    window = np.hanning(size)[:, None]
    starts = range(0, max(1, len(data) - size + 1), hop)
    energies = []
    for start in starts:
        energies.append(float(np.mean(data[start:start + size] ** 2)))
    threshold = max(max(energies) * 1e-4, 1e-12)
    power = np.zeros(size // 2 + 1)
    active = 0
    for start, energy in zip(starts, energies):
        if energy < threshold:
            continue
        frame = np.zeros((size, data.shape[1]))
        segment = data[start:start + size]
        frame[:len(segment)] = segment
        power += np.mean(np.abs(np.fft.rfft(frame * window, axis=0)) ** 2, axis=1)
        active += 1
    return np.fft.rfftfreq(size, 1 / sr), power / max(active, 1), active * hop / sr


def _shape(freqs, power, centers, fraction):
    # Average energy over fractional-octave bands, then remove overall gain.
    values = []
    for center in centers:
        mask = (freqs >= center * 2 ** (-fraction / 2)) & (freqs <= center * 2 ** (fraction / 2))
        values.append(float(np.mean(power[mask])) if mask.any() else float(np.interp(center, freqs, power)))
    values = np.asarray(values)
    db = 10 * np.log10(np.maximum(values, max(values.max() * 1e-8, 1e-30)))
    return db - db.mean(), values > max(values.max() * 1e-5, 1e-20)


def _centroid(freqs, power, upper):
    mask = (freqs >= 120) & (freqs <= upper)
    amplitude = np.sqrt(power[mask])
    return float(np.sum(freqs[mask] * amplitude) / max(amplitude.sum(), 1e-30))


def apply_curve(samples, sr, centers, gains):
    """Windowed overlap-add with bounded FFT gains; same length/channels and no circular edges."""
    original = np.asarray(samples)
    data = original[:, None] if original.ndim == 1 else original
    size = 2 ** math.ceil(math.log2(sr * 0.085))
    hop = size // 4
    padded = np.pad(data.astype(np.float64), ((size, size), (0, 0)), mode="constant")
    output = np.zeros_like(padded)
    weights = np.zeros(len(padded))
    window = np.sqrt(np.hanning(size))
    freqs = np.fft.rfftfreq(size, 1 / sr)
    knots = np.r_[0, 80, centers, min(sr / 2, centers[-1] * 1.3), sr / 2]
    db = np.interp(freqs, knots, np.r_[0, 0, gains, 0, 0])
    response = 10 ** (db / 20)
    for start in range(0, len(padded) - size + 1, hop):
        frame = padded[start:start + size] * window[:, None]
        filtered = np.fft.irfft(np.fft.rfft(frame, axis=0) * response[:, None], n=size, axis=0)
        output[start:start + size] += filtered * window[:, None]
        weights[start:start + size] += window ** 2
    result = output[size:size + len(data)] / weights[size:size + len(data), None]
    return (result[:, 0] if original.ndim == 1 else result).astype(np.float32)


def _deess(samples, sr, ffmpeg=None):
    # FFmpeg i/f/m are normalized controls, not Hz or dB.
    from narration_mastering import _run, resolve_ffmpeg

    settings = "deesser=i=0.1:m=0.2:f=0.5"
    with tempfile.TemporaryDirectory(prefix="match_deess_") as directory:
        source, output = Path(directory) / "in.wav", Path(directory) / "out.wav"
        sf.write(source, samples, sr, subtype="FLOAT")
        _run(ffmpeg or resolve_ffmpeg(), ["-y", "-i", source, "-af", settings, "-c:a", "pcm_f32le", output])
        result, output_rate = sf.read(output, dtype="float32", always_2d=np.asarray(samples).ndim == 2)
        if output_rate != sr or result.shape != np.asarray(samples).shape:
            raise RuntimeError("De-essing changed the audio format or duration")
    return result, settings


def spectral_match(anchor_path, premaster_samples, sr, *, amount=0.5, max_db=6.0,
                   octave_fraction=1 / 3, ffmpeg=None, segments=None):
    """Derive one broad curve per speaker; render segments independently to avoid cross-join leakage.

    The defaults bound applied gain to +/-3 dB (6 dB difference times 0.5).
    Accept only a measured spectral-distance improvement without centroid overshoot.
    These gates are technical proxies, not speaker identity or content validation.
    """
    if not (math.isfinite(amount) and 0 <= amount <= 0.6 and math.isfinite(max_db) and 0 <= max_db <= 6
            and math.isfinite(octave_fraction) and 1 / 3 <= octave_fraction <= 1):
        raise ValueError("Match EQ limits: amount 0..0.6, max_db 0..6, octave_fraction 1/3..1")
    original = np.asarray(premaster_samples, dtype=np.float32)
    freqs, power, active = spectrum(original, sr)
    reference, reference_rate = sf.read(anchor_path, dtype="float32", always_2d=True)
    rf, rp, reference_active = spectrum(reference, reference_rate)
    upper = min(8000, sr * 0.45, reference_rate * 0.45)
    centers = 125 * 2 ** (np.arange(0, math.floor(math.log2(upper / 125) / octave_fraction) + 1) * octave_fraction)
    # Explicit log grid avoids treating high sample rate references as extra target bandwidth.
    before_shape, supported = _shape(freqs, power, centers, octave_fraction)
    reference_shape, reference_supported = _shape(rf, rp, centers, octave_fraction)
    supported &= reference_supported
    difference = reference_shape - before_shape
    difference -= np.median(difference[supported]) if supported.any() else 0
    # Additional broad smoothing suppresses phoneme-specific narrow corrections.
    difference = np.convolve(np.pad(difference, (1, 1), mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    difference[~supported] = 0  # Do not invent missing bandwidth or amplify spectral nulls.
    before_centroid = _centroid(freqs, power, upper)
    reference_centroid = _centroid(rf, rp, upper)
    gap = float(np.sqrt(np.mean((reference_shape[supported] - before_shape[supported]) ** 2))) if supported.any() else 0
    report = {"version": VERSION, "enabled": False, "amount": amount, "max_difference_db": max_db,
              "octave_fraction": octave_fraction, "anchor_sha256": hashlib.sha256(Path(anchor_path).read_bytes()).hexdigest(),
              "common_band_hz": [120, upper], "centers_hz": centers.tolist(), "supported_bands": int(supported.sum()),
              "before_centroid_hz": before_centroid, "reference_centroid_hz": reference_centroid,
              "before_distance_db": gap, "active_seconds": active, "reference_active_seconds": reference_active}
    if active < 1 or reference_active < 1 or supported.sum() < 8 or gap < 1 or not amount or not max_db:
        report["reason"] = "Insufficient active broadband evidence, negligible tonal gap, or matching disabled"
        return original.copy(), report
    ranges = segments or [(0, len(original))]
    if (ranges[0][0] != 0 or ranges[-1][1] != len(original)
            or any(start < 0 or end <= start for start, end in ranges)
            or any(left[1] != right[0] for left, right in zip(ranges, ranges[1:]))):
        raise ValueError("Segments must partition the samples in order")
    for scale in (1.0, 0.5, 0.25):
        gains = np.clip(difference, -max_db, max_db) * amount * scale
        candidate = np.concatenate([apply_curve(original[start:end], sr, centers, gains) for start, end in ranges])
        deesser = None
        if np.any(gains[centers >= 4000] > 2):
            candidate, deesser = _deess(candidate, sr, ffmpeg)
        af, ap, _ = spectrum(candidate, sr)
        after_shape, _ = _shape(af, ap, centers, octave_fraction)
        distance = float(np.sqrt(np.mean((reference_shape[supported] - after_shape[supported]) ** 2)))
        centroid = _centroid(af, ap, upper)
        # Permit tiny numerical centroid changes, but no material movement away or past the target.
        delta = reference_centroid - before_centroid
        movement = centroid - before_centroid
        centroid_ok = (movement * delta >= -abs(delta) * 1.0 and abs(movement) <= abs(delta) + 1.0)
        if np.isfinite(candidate).all() and distance < gap - 0.05 and centroid_ok:
            report.update(enabled=True, reason="Broad spectral distance improved without centroid overshoot",
                          applied_amount=amount * scale, gains_db=gains.tolist(), after_distance_db=distance,
                          after_centroid_hz=centroid, deesser_filter=deesser)
            return candidate, report
    report["reason"] = "Candidate correction did not pass spectral-distance and centroid regression checks"
    return original.copy(), report


def match_roles(samples, sr, spans, anchors, *, ffmpeg=None):
    """Match each role to its own anchor; retain pause samples and exact boundaries."""
    output = np.array(samples, dtype=np.float32, copy=True)
    reports = {}
    for role, anchor in anchors.items():
        ranges = [(start, end) for start, end, item_role in spans if item_role == role]
        if not ranges:
            continue
        role_samples = np.concatenate([samples[start:end] for start, end in ranges])
        offset, segments = 0, []
        for start, end in ranges:
            segments.append((offset, offset + end - start))
            offset += end - start
        matched, report = spectral_match(anchor, role_samples, sr, segments=segments, ffmpeg=ffmpeg)
        reports[role] = report
        for (start, end), (local_start, local_end) in zip(ranges, segments):
            output[start:end] = matched[local_start:local_end]
    return output, reports
