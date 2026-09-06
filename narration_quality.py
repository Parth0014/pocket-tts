"""Explicit, auditable narration settings and conservative signal diagnostics.

These measurements cannot establish speaker identity, word accuracy or emotion.
Profiles are listening-test starting points, not quality guarantees.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class NarrationProfile:
    name: str
    narration_temperature: float
    quote_temperature: float
    decode_steps: int = 5
    token_budget: int = 80
    base_speed: float = 1.0
    paragraph_pause_ms: int = 480
    sentence_pause_ms: int = 220
    clause_pause_ms: int = 90
    lead_in_ms: int = 300

    def to_dict(self):
        return asdict(self)


PROFILES = {
    "faithful": NarrationProfile("faithful", 0.3, 0.3),
    "balanced": NarrationProfile("balanced", 0.5, 0.5),
    "expressive": NarrationProfile("expressive", 0.7, 0.7),
    "legacy": NarrationProfile("legacy", 0.6, 0.85, 2, 44, 0.86, 700, 350, 150, 700),
}


def get_profile(name="balanced"):
    try:
        return PROFILES[name]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"profile must be one of: {', '.join(PROFILES)}") from exc


def boundary_kind(record):
    if record["paragraph_end"]:
        return "paragraph"
    ending = record["text"].rstrip().rstrip('\"\u201d\u2019\')]}')
    return "sentence" if ending.endswith((".", "!", "?")) else "clause"


def boundary_pause_ms(record, next_record, profile):
    """Pause at semantic boundaries; do not reintroduce quote pauses mid-quote."""
    kind = boundary_kind(record)
    pause = getattr(profile, f"{kind}_pause_ms")
    if profile.name == "legacy":
        return pause + 250 * (record["block_type"] == "quote") + 250 * (
            next_record["block_type"] == "quote"
        )
    if record["paragraph_end"] and record["block_type"] == "heading":
        pause = 600
    elif record["paragraph_end"] and record["block_type"] == "list":
        pause = 320
    if record["block_type"] != next_record["block_type"] and "quote" in {
        record["block_type"], next_record["block_type"]
    }:
        pause += 120
    return pause


def audio_diagnostics(samples, sample_rate, text=""):
    """Reject broken signals; flag suspicious pace without claiming ASR/VAD."""
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    if sample_rate <= 0 or not values.size:
        raise ValueError("Generated audio is empty or has an invalid sample rate")
    if not np.isfinite(values).all():
        raise ValueError("Generated audio contains non-finite samples")
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values ** 2)))
    if peak < 1e-5 or rms < 1e-6:
        raise ValueError("Generated audio is silent; retry this chunk")
    duration = values.size / sample_rate
    if duration < 0.15:
        raise ValueError("Generated audio is too short to contain the requested speech")
    word_count = len(text.split())
    words_per_minute = word_count * 60 / duration if word_count else None
    clipping_fraction = float(np.mean(np.abs(values) >= 0.999))
    warnings = []
    if clipping_fraction > 0.005:
        warnings.append("Possible clipping: listen for distortion.")
    if word_count >= 8 and not 55 <= words_per_minute <= 330:
        warnings.append("Unusual duration for this text: check for missing words or long gaps.")
    return {
        "duration_seconds": duration, "peak": peak, "rms": rms,
        "clipping_fraction": clipping_fraction, "words_per_minute": words_per_minute,
        "warnings": warnings,
        "assessment": "signal checks only; word accuracy, likeness and delivery require listening",
    }
