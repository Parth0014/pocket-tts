"""
generate_narration.py

Integrated pipeline: Ghost post HTML -> typed blocks -> narration
script (quote lead-ins) -> block-aware chunking -> PocketTTS
generation. Model instances are loaded only for roles present in the
document. Quote chunks retain their own expressiveness setting; in
``two_voice`` mode they also use a distinct reference voice.

This is a LOCAL TEST harness: it reads an .html file from disk and
writes a .wav file to disk, deliberately, so you can iterate and
listen to output before anything touches AWS. The generation core
(caching, silence-aware trim, DSP variant rendering) is carried over
from the original PocketTTS_narration.py almost unchanged.
"""

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata

import numpy as np
import soundfile as sf
import torch
from pocket_tts import TTSModel, export_model_state

from chunking import build_chunks_from_blocks, generation_settings_for
from narration_quality import audio_diagnostics, boundary_kind, boundary_pause_ms, get_profile
from narration_script import build_narration_blocks
from reference_audio import ALGORITHM_VERSION, prepare_reference, probe_reference
from worker_document import extract_worker_blocks

# ============================================================
# 1. PATHS + RUNTIME CONFIGURATION  (LOCAL TEST ONLY)
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
INTERNAL_DIR = os.path.join(OUTPUT_DIR, "_internal")
RAW_CACHE_DIR = os.path.join(INTERNAL_DIR, "raw_cache")
VOICE_STATE_CACHE_DIR = os.path.join(INTERNAL_DIR, "voice_states")
DEBUG_DIR = os.path.join(INTERNAL_DIR, "debug")

DEFAULT_REFERENCE_AUDIO = os.path.join("voice_samples", "solution.wav")
DEFAULT_POST_HTML_FILE = "sample.html"

OUTPUT_NAME_PREFIX = "narration"
OUTPUT_NUMBER_WIDTH = 4


def parse_dotenv_text(text):
    """Parse the small KEY=VALUE subset used by this local harness."""
    values = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env entry on line {line_number}")
        value = value.strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            closing_index = value.find(quote, 1)
            if closing_index < 0:
                raise ValueError(f"Unterminated quoted .env value on line {line_number}")
            trailing = value[closing_index + 1:].strip()
            if trailing and not trailing.startswith("#"):
                raise ValueError(f"Unexpected text after .env value on line {line_number}")
            value = value[1:closing_index]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def load_runtime_environment(base_dir, environ=None):
    """Load BASE_DIR/.env, with the process environment taking precedence."""
    dotenv_path = os.path.join(base_dir, ".env")
    dotenv_values = {}
    if os.path.isfile(dotenv_path):
        with open(dotenv_path, "r", encoding="utf-8-sig") as dotenv_file:
            dotenv_values = parse_dotenv_text(dotenv_file.read())
    merged = dict(dotenv_values)
    merged.update(dict(os.environ if environ is None else environ))
    return merged


def resolve_config_path(value, base_dir):
    """Resolve configured paths consistently, independent of the caller's cwd."""
    value = os.path.expanduser(os.fspath(value).strip())
    if not value:
        return None
    if not os.path.isabs(value):
        value = os.path.join(base_dir, value)
    return os.path.normpath(os.path.abspath(value))


def build_runtime_config(base_dir, environment):
    """Build a side-effect-free runtime configuration from environment values."""
    output_dir = os.path.join(base_dir, "output")
    internal_dir = os.path.join(output_dir, "_internal")
    quote_reference = environment.get("NARRATION_QUOTE_REFERENCE_AUDIO", "")
    return {
        "base_dir": os.path.abspath(base_dir),
        "post_html_file": resolve_config_path(
            environment.get("NARRATION_POST_HTML_FILE", DEFAULT_POST_HTML_FILE), base_dir
        ),
        "narration_reference_audio": resolve_config_path(
            environment.get("NARRATION_REFERENCE_AUDIO", DEFAULT_REFERENCE_AUDIO), base_dir
        ),
        "quote_reference_audio": resolve_config_path(quote_reference, base_dir),
        "quote_mode": environment.get("NARRATION_QUOTE_MODE", "preserve").strip().lower(),
        "profile": environment.get("NARRATION_PROFILE", "balanced").strip().lower(),
        "speed": float(environment.get("NARRATION_SPEED", "1.0")),
        "reference_start_seconds": optional_float(environment.get("NARRATION_REFERENCE_START")),
        "quote_reference_start_seconds": optional_float(environment.get("NARRATION_QUOTE_REFERENCE_START")),
        "output_dir": output_dir,
        "internal_dir": internal_dir,
        "raw_cache_dir": os.path.join(internal_dir, "raw_cache"),
        "voice_state_cache_dir": os.path.join(internal_dir, "voice_states"),
        "debug_dir": os.path.join(internal_dir, "debug"),
    }


def optional_float(value):
    return None if value is None or not str(value).strip() else float(value)


def next_numbered_output_path(directory):
    """Return the next likely path; callers that write must reserve it atomically."""
    pattern = re.compile(rf"^{re.escape(OUTPUT_NAME_PREFIX)}_(\d+)\.wav$", re.IGNORECASE)
    numbers = []
    for name in os.listdir(directory) if os.path.isdir(directory) else ():
        match = pattern.fullmatch(name)
        if match:
            numbers.append(int(match.group(1)))
    next_number = max(numbers, default=0) + 1
    filename = f"{OUTPUT_NAME_PREFIX}_{next_number:0{OUTPUT_NUMBER_WIDTH}d}.wav"
    return os.path.join(directory, filename)


def reserve_numbered_output_path(directory):
    """Atomically reserve a numbered output path, even across concurrent runs."""
    candidate = next_numbered_output_path(directory)
    pattern = re.compile(rf"^{re.escape(OUTPUT_NAME_PREFIX)}_(\d+)\.wav$", re.IGNORECASE)
    match = pattern.fullmatch(os.path.basename(candidate))
    number = int(match.group(1)) if match else 1
    while True:
        candidate = os.path.join(
            directory, f"{OUTPUT_NAME_PREFIX}_{number:0{OUTPUT_NUMBER_WIDTH}d}.wav"
        )
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            number += 1
            continue
        os.close(descriptor)
        return candidate


# ============================================================
# 2. NARRATION SETTINGS
# ============================================================

SEED = 1234
MODEL_LANGUAGE = "english_2026-04"
SEED_OVERRIDES = {}
PRONUNCIATION_OVERRIDES = {}

ANCHOR_SECONDS = 15
OUTPUT_SPEED = 1.0
RENDER_SPEED = OUTPUT_SPEED

NARRATION_TEMP = 0.6
QUOTE_TEMP = 0.85

LSD_DECODE_STEPS = 2
NOISE_CLAMP = None
EOS_THRESHOLD = -4.0
FRAMES_AFTER_EOS = None
QUANTIZE = False
RESUME = True

RAW_CACHE_SCHEMA = 4
VOICE_CACHE_SCHEMA = 2

TRIM_THRESHOLD = 0.008
TRIM_WINDOW_MS = 20
TRIM_KEEP_END_MS = 180
EDGE_FADE_MS = 4
TRIM_KEEP_START_MS = 180

SAFE_TOKEN_BUDGET = 44
PARAGRAPH_PAUSE_MS = 700
SENTENCE_PAUSE_MS = 350
CLAUSE_PAUSE_MS = 150
LEAD_IN_MS = 700
NORMALIZE_PEAK = 0.95


# ============================================================
# ORIGINAL SCRIPT LOGIC — helpers (unchanged from PocketTTS_narration.py)
# ============================================================

def load_audio_mono(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    tensor = torch.from_numpy(data).unsqueeze(0)
    return tensor, sr


def save_audio(path, tensor, sr, subtype=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if tensor.dim() == 2:
        data = tensor.squeeze(0).detach().cpu().numpy()
    else:
        data = tensor.detach().cpu().numpy()
    sf.write(path, data, sr, subtype=subtype)


def _json_ready(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(v) for v in value]
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return _json_ready(value.dict())
    return str(value)


def canonical_json_bytes(payload):
    return json.dumps(
        _json_ready(payload), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def payload_sha256(payload):
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def sha256_file(path, block_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ffmpeg_is_usable(executable):
    """Return whether an ffmpeg candidate can successfully start."""
    if not executable or not os.path.isfile(executable):
        return False
    try:
        result = subprocess.run(
            [executable, "-version"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve_ffmpeg():
    """Prefer bundled ffmpeg, then accept a PATH executable only if runnable."""
    candidates = []
    try:
        import imageio_ffmpeg

        candidates.append(("imageio-ffmpeg", imageio_ffmpeg.get_ffmpeg_exe()))
    except (ImportError, OSError, RuntimeError):
        pass
    candidates.append(("PATH", shutil.which("ffmpeg")))

    checked = []
    for source, executable in candidates:
        if not executable:
            continue
        normalized = os.path.normcase(os.path.abspath(executable))
        if normalized in checked:
            continue
        checked.append(normalized)
        if ffmpeg_is_usable(executable):
            return executable
    detail = ", ".join(checked) if checked else "no candidates found"
    raise RuntimeError(
        "No working ffmpeg executable was found. Install imageio-ffmpeg "
        f"or a functional system ffmpeg. Checked: {detail}"
    )


def validate_runtime_config(config):
    """Fail fast on configuration and local-tool errors, before model loading."""
    quote_mode = config["quote_mode"]
    if quote_mode not in {"preserve", "exclude", "two_voice"}:
        raise ValueError(
            "NARRATION_QUOTE_MODE must be one of preserve, exclude, or two_voice; "
            f"got {quote_mode!r}"
        )

    post_path = config["post_html_file"]
    if not post_path or not os.path.isfile(post_path):
        raise FileNotFoundError(f"Post HTML file not found:\n{post_path}")

    render_speed = config.get("render_speed", RENDER_SPEED)
    if not 0.5 <= float(render_speed) <= 2.0:
        raise ValueError(f"render speed must be in [0.5, 2.0], got {render_speed}")
    ffmpeg_path = None
    if float(render_speed) != 1.0:
        ffmpeg_path = resolve_ffmpeg()
    return ffmpeg_path


def required_roles(narration_blocks):
    """Return generation roles in deterministic model-load order."""
    present = {
        generation_settings_for({"block_type": block["block_type"]})["role"]
        for block in narration_blocks
        if str(block.get("text", "")).strip()
    }
    return tuple(role for role in ("narration", "quote") if role in present)


def reference_paths_for_roles(config, roles):
    """Route roles to references; only two_voice may use the quote reference."""
    paths = {}
    for role in roles:
        if role == "quote" and config["quote_mode"] == "two_voice":
            paths[role] = config["quote_reference_audio"]
        else:
            paths[role] = config["narration_reference_audio"]
    return paths


def validate_reference_config(config, roles):
    """Validate only the references needed by content that will be generated."""
    reference_paths = reference_paths_for_roles(config, roles)
    for role, reference_path in reference_paths.items():
        uses_quote_reference = role == "quote" and config["quote_mode"] == "two_voice"
        setting = (
            "NARRATION_QUOTE_REFERENCE_AUDIO"
            if uses_quote_reference else "NARRATION_REFERENCE_AUDIO"
        )
        if not reference_path:
            raise ValueError(f"{setting} is required for the {role} generation role")
        if not os.path.isfile(reference_path):
            raise FileNotFoundError(f"{role.capitalize()} reference audio not found:\n{reference_path}")
        try:
            info = probe_reference(reference_path)
        except Exception as exc:
            raise ValueError(
                f"Cannot read {role} reference audio {reference_path!r}: {exc}"
            ) from exc
        if info["frames"] <= 0 or info["sample_rate"] <= 0:
            raise ValueError(
                f"{role.capitalize()} reference audio is empty: {reference_path}"
            )

    if config["quote_mode"] == "two_voice" and {
        "narration", "quote"
    }.issubset(reference_paths):
        narration_reference = reference_paths["narration"]
        quote_reference = reference_paths["quote"]
        try:
            same_file = os.path.samefile(narration_reference, quote_reference)
        except OSError:
            same_file = (
                os.path.normcase(narration_reference)
                == os.path.normcase(quote_reference)
            )
        if same_file or sha256_file(narration_reference) == sha256_file(quote_reference):
            raise ValueError(
                "two_voice mode requires quote reference audio that is distinct "
                "from NARRATION_REFERENCE_AUDIO"
            )
    return reference_paths


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="._json_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(_json_ready(payload), f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def validate_audio_tensor(audio, sr, expected_sr):
    if sr != expected_sr:
        raise ValueError(f"sample rate {sr} does not match expected {expected_sr}")
    if audio.dim() != 2 or audio.shape[0] != 1:
        raise ValueError(f"expected mono [1, samples] audio, got {tuple(audio.shape)}")
    if audio.numel() == 0:
        raise ValueError("audio contains no samples")
    if not torch.isfinite(audio).all():
        raise ValueError("audio contains NaN or infinite samples")


def load_valid_audio(path, expected_sr, expected_sha256=None):
    try:
        info = sf.info(path)
        if info.channels != 1:
            return None, f"expected mono cache, found {info.channels} channels"
        audio, sr = load_audio_mono(path)
        validate_audio_tensor(audio, sr, expected_sr)
        if expected_sha256 and sha256_file(path) != expected_sha256:
            return None, "file fingerprint does not match manifest"
        return audio, None
    except Exception as exc:
        return None, str(exc)


def atomic_save_audio(path, tensor, sr, subtype="FLOAT", verify_samples=True):
    validate_audio_tensor(tensor, sr, sr)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="._audio_", suffix=".wav", dir=directory)
    os.close(fd)
    try:
        save_audio(temp_path, tensor, sr, subtype=subtype)
        if verify_samples:
            check, reason = load_valid_audio(temp_path, sr)
            if check is None or check.shape[1] != tensor.shape[1]:
                raise RuntimeError(
                    f"temporary WAV validation failed: {reason or 'frame mismatch'}"
                )
        else:
            info = sf.info(temp_path)
            if (
                info.channels != 1
                or info.samplerate != sr
                or info.frames != tensor.shape[1]
            ):
                raise RuntimeError("temporary WAV metadata validation failed")
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def apply_pronunciation_overrides(text, overrides):
    result = text
    for written, spoken in sorted(overrides.items(), key=lambda item: len(item[0]), reverse=True):
        if not written or not spoken:
            raise ValueError("Pronunciation overrides require non-empty written and spoken forms")
        pattern = re.compile(rf"(?<!\w){re.escape(written)}(?!\w)")
        result = pattern.sub(spoken, result)
    return result


def resolved_seed(text, index):
    key = text_sha256(text)
    return int(SEED_OVERRIDES.get(key, SEED + index))


def package_version(distribution):
    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def model_identity_for(model, role, temp):
    return {
        "pocket_tts_version": package_version("pocket-tts"),
        "torch_version": torch.__version__,
        "language": MODEL_LANGUAGE,
        "sample_rate": int(model.sample_rate),
        "role": role,
        "temp": float(temp),
        "config": _json_ready(getattr(model, "config", None)),
    }


def find_clean_cutpoint(audio_np, sr, target_seconds, search_window_seconds=1.5):
    target_sample = int(target_seconds * sr)
    window = max(1, int(0.02 * sr))
    radius = int(search_window_seconds * sr)
    lo = max(window, target_sample - radius)
    hi = min(len(audio_np) - window, target_sample + radius)
    if hi <= lo:
        return min(target_sample, len(audio_np))
    best_idx, best_energy = target_sample, float("inf")
    step = max(1, window // 4)
    for i in range(lo, hi, step):
        seg = audio_np[i:i + window]
        if len(seg) == 0:
            continue
        energy = float(np.sqrt(np.mean(seg ** 2)))
        if energy < best_energy:
            best_energy = energy
            best_idx = i
    return best_idx


def prepare_voice_anchor(reference_path, anchor_path, start_seconds=None):
    """Select and cache a contiguous reference segment from the whole upload."""
    return prepare_reference(
        reference_path, anchor_path, target_seconds=ANCHOR_SECONDS, start_seconds=start_seconds
    )


def load_or_build_voice_state(model, anchor_path, model_identity, cache_dir):
    payload = {
        "schema": VOICE_CACHE_SCHEMA,
        "anchor_sha256": sha256_file(anchor_path),
        "anchor_seconds_requested": ANCHOR_SECONDS,
        "anchor_cut_algorithm": ALGORITHM_VERSION,
        "model": model_identity,
        "quantize": QUANTIZE,
    }
    key = payload_sha256(payload)
    state_path = os.path.join(cache_dir, f"voice_state_{key[:16]}.safetensors")
    manifest_path = os.path.join(cache_dir, f"voice_state_{key[:16]}.json")

    manifest = read_json(manifest_path)
    if RESUME and os.path.isfile(state_path) and manifest:
        expected_hash = manifest.get("state_sha256")
        if manifest.get("payload") == _json_ready(payload) and expected_hash:
            try:
                if sha256_file(state_path) != expected_hash:
                    raise ValueError("voice-state fingerprint does not match manifest")
                state = model.get_state_for_audio_prompt(state_path)
                print(f"  Loading cached voice state ({model_identity['role']}):", state_path)
                return state, state_path, {
                    "anchor_sha256": payload["anchor_sha256"],
                    "voice_state_payload_sha256": key,
                    "voice_state_sha256": expected_hash,
                }
            except Exception as exc:
                print(f"  Voice-state cache rejected ({exc}); rebuilding it.")

    print(f"  Extracting voice state ({model_identity['role']}) from reference audio...")
    state = model.get_state_for_audio_prompt(anchor_path)
    fd, temp_path = tempfile.mkstemp(
        prefix="._voice_", suffix=".safetensors", dir=cache_dir
    )
    os.close(fd)
    os.remove(temp_path)
    try:
        export_model_state(state, temp_path)
        validated_state = model.get_state_for_audio_prompt(temp_path)
        state_hash = sha256_file(temp_path)
        os.replace(temp_path, state_path)
        atomic_write_json(manifest_path, {
            "payload": payload,
            "state_sha256": state_hash,
            "state_file": os.path.basename(state_path),
            "created_utc": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  Cached voice state ({model_identity['role']}):", state_path)
        return validated_state, state_path, {
            "anchor_sha256": payload["anchor_sha256"],
            "voice_state_payload_sha256": key,
            "voice_state_sha256": state_hash,
        }
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def count_tokens_for(model):
    from pocket_tts.models.tts_model import prepare_text_prompt

    def _count(text):
        # Match PocketTTS's own normalization before budgeting. Guessing token
        # lengths can silently create another split inside generate_audio().
        prepared, _ = prepare_text_prompt(
            text, model.pad_with_spaces_for_short_inputs, model.remove_semicolons
        )
        return model.flow_lm.conditioner.prepare(prepared).tokens.shape[1]
    return _count


def pause_ms_for(record):
    if record["paragraph_end"]:
        return PARAGRAPH_PAUSE_MS
    stripped = record["text"].rstrip()
    if stripped and stripped[-1] in ".!?":
        return SENTENCE_PAUSE_MS
    return CLAUSE_PAUSE_MS


def boundary_kind_for(record):
    return boundary_kind(record)


def detect_activity(audio, sr, threshold=None, window_ms=TRIM_WINDOW_MS):
    validate_audio_tensor(audio, sr, sr)
    values = audio.squeeze(0).detach().cpu().numpy()
    n = len(values)
    window = max(1, int(round(sr * window_ms / 1000)))
    if threshold is None:
        # A relative floor retains quiet delivery; this is energy detection,
        # not a speech recognizer. Never increase the previous absolute floor.
        frame_rms = [float(np.sqrt(np.mean(values[i:i + window].astype(np.float64) ** 2)))
                     for i in range(0, n, window)]
        threshold = min(TRIM_THRESHOLD, max(1e-6, float(np.percentile(frame_rms, 90)) * 0.025))
    active_start = None
    active_end = None
    for start in range(0, n, window):
        segment = values[start:min(start + window, n)]
        rms = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
        if rms > threshold:
            if active_start is None:
                active_start = start
            active_end = min(start + len(segment), n)
    if active_start is None or active_end is None or active_end <= active_start:
        raise RuntimeError("No speech activity detected in generated chunk")
    return {"start": int(active_start), "end": int(active_end)}


def trim_edge_silence(chunk, sr, keep_start_ms, keep_end_ms,
                       threshold=None, window_ms=TRIM_WINDOW_MS):
    raw_activity = detect_activity(chunk, sr, threshold=threshold, window_ms=window_ms)
    n = chunk.shape[1]
    keep_start = int(round(sr * keep_start_ms / 1000))
    keep_end = int(round(sr * keep_end_ms / 1000))
    crop_start = max(0, raw_activity["start"] - keep_start)
    crop_end = min(n, raw_activity["end"] + keep_end)
    if crop_end <= crop_start:
        raise RuntimeError("Silence trim produced an empty chunk")
    trimmed = chunk[:, crop_start:crop_end].clone()
    activity = {
        "start": raw_activity["start"] - crop_start,
        "end": raw_activity["end"] - crop_start,
    }
    return {"audio": trimmed, "activity": activity}


def with_edge_fades(chunk, sr, fade_ms):
    chunk = chunk.clone()
    n = chunk.shape[1]
    fade_samples = min(int(round(sr * fade_ms / 1000)), n // 4)
    if fade_samples > 0 and n > 0:
        ramp = torch.linspace(0.0, 1.0, fade_samples, dtype=chunk.dtype, device=chunk.device)
        chunk[:, :fade_samples] *= ramp
        ramp = torch.linspace(1.0, 0.0, fade_samples, dtype=chunk.dtype, device=chunk.device)
        chunk[:, -fade_samples:] *= ramp
    return chunk


def apply_tempo_to_chunk(chunk, sr, speed, temp_dir, ffmpeg_path=None):
    if float(speed) == 1.0:
        return chunk
    if not 0.5 <= float(speed) <= 2.0:
        raise ValueError(f"atempo speed must be in [0.5, 2.0], got {speed}")
    ffmpeg = ffmpeg_path or resolve_ffmpeg()
    in_fd, in_path = tempfile.mkstemp(prefix="._atempo_in_", suffix=".wav", dir=temp_dir)
    out_fd, out_path = tempfile.mkstemp(prefix="._atempo_out_", suffix=".wav", dir=temp_dir)
    os.close(in_fd)
    os.close(out_fd)
    try:
        save_audio(in_path, chunk, sr, subtype="FLOAT")
        os.remove(out_path)
        result = subprocess.run(
            [ffmpeg, "-y", "-i", in_path, "-filter:a", f"atempo={float(speed):.8f}",
             "-c:a", "pcm_f32le", out_path],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="ignore")[-500:]
            raise RuntimeError(f"ffmpeg atempo failed: {detail}")
        processed, reason = load_valid_audio(out_path, sr)
        if processed is None:
            raise RuntimeError(f"ffmpeg output validation failed: {reason}")
        return processed
    finally:
        for temp_path in (in_path, out_path):
            if os.path.exists(temp_path):
                os.remove(temp_path)


def render_raw_chunk(raw_audio, sr, speed, temp_dir, ffmpeg_path=None):
    """Trim, time-stretch, and fade one raw chunk before retaining it."""
    trimmed = trim_edge_silence(
        raw_audio, sr, TRIM_KEEP_START_MS, TRIM_KEEP_END_MS
    )["audio"]
    tempo_applied = apply_tempo_to_chunk(
        trimmed, sr, speed, temp_dir, ffmpeg_path=ffmpeg_path
    )
    return with_edge_fades(tempo_applied, sr, EDGE_FADE_MS)


# ============================================================
# 4. CHUNK + CACHE HELPERS
# ============================================================

def build_role_chunks(narration_blocks, count_tokens_by_role, budget=SAFE_TOKEN_BUDGET):
    """Chunk each role independently, then restore original document order."""
    indexed_blocks = list(enumerate(narration_blocks))
    role_of_block_index = {
        index: generation_settings_for({"block_type": block["block_type"]})["role"]
        for index, block in indexed_blocks
    }
    all_chunks = []
    for role in ("narration", "quote"):
        original_indices = [
            index for index, _block in indexed_blocks
            if role_of_block_index[index] == role
        ]
        if not original_indices:
            continue
        role_blocks = [narration_blocks[index] for index in original_indices]
        role_chunks = build_chunks_from_blocks(
            count_tokens_by_role[role], role_blocks, budget
        )
        for chunk in role_chunks:
            chunk["role"] = role
            chunk["block_index"] = original_indices[chunk["block_index"]]
        all_chunks.extend(role_chunks)
    all_chunks.sort(key=lambda chunk: chunk["block_index"])
    return all_chunks


def raw_generation_payload(
    chunk_text, chunk_seed, role, temperature, model_identity, voice_identity,
    profile=None,
):
    return {
        "schema": RAW_CACHE_SCHEMA,
        "text": chunk_text,
        "text_sha256": text_sha256(chunk_text),
        "seed": int(chunk_seed),
        "role": role,
        "generation": {
            "temperature": temperature,
            "decode_steps": profile.decode_steps if profile else LSD_DECODE_STEPS,
            "max_tokens": profile.token_budget if profile else SAFE_TOKEN_BUDGET,
            "noise_clamp": NOISE_CLAMP,
            "eos_threshold": EOS_THRESHOLD,
            "frames_after_eos": FRAMES_AFTER_EOS,
            "quantize": QUANTIZE,
            "signal_retry_policy": "two-attempts-seed-plus-1000003-v1",
        },
        "model": model_identity,
        "voice": voice_identity,
    }


def raw_cache_paths(payload, cache_dir):
    key = payload_sha256(payload)
    return (
        key,
        os.path.join(cache_dir, f"raw_{key[:20]}.wav"),
        os.path.join(cache_dir, f"raw_{key[:20]}.json"),
    )


def load_raw_cache(payload, expected_sr, cache_dir):
    key, wav_path, manifest_path = raw_cache_paths(payload, cache_dir)
    manifest = read_json(manifest_path)
    if not (RESUME and os.path.isfile(wav_path) and manifest):
        return None, None, key, wav_path, manifest_path
    if manifest.get("payload") != _json_ready(payload):
        return None, "manifest payload mismatch", key, wav_path, manifest_path
    expected_hash = manifest.get("wav_sha256")
    if not expected_hash:
        return None, "manifest has no WAV fingerprint", key, wav_path, manifest_path
    try:
        if sf.info(wav_path).subtype != "FLOAT":
            return None, "raw cache is not a float WAV", key, wav_path, manifest_path
    except Exception as exc:
        return None, str(exc), key, wav_path, manifest_path
    audio, reason = load_valid_audio(wav_path, expected_sr, expected_sha256=expected_hash)
    if audio is None:
        return None, reason, key, wav_path, manifest_path
    if int(manifest.get("frames", -1)) != audio.shape[1]:
        return None, "frame count does not match manifest", key, wav_path, manifest_path
    return audio, None, key, wav_path, manifest_path


def generate_checked_chunk(model, voice_state, text, seed, token_budget):
    """Retry one broken signal with a deterministic alternate seed.

    Model/tool exceptions propagate immediately. Only invalid returned signals
    are retried; these checks cannot judge the performance or recognize words.
    """
    rejected = []
    for attempt in range(2):
        attempt_seed = seed + attempt * 1_000_003
        torch.manual_seed(attempt_seed)
        audio = model.generate_audio(
            voice_state, text, frames_after_eos=FRAMES_AFTER_EOS,
            copy_state=True, max_tokens=token_budget,
        )
        try:
            if audio is None or audio.numel() == 0:
                raise ValueError("Model returned no audio")
            audio = audio.detach().cpu()
            if audio.dim() == 1:
                audio = audio.unsqueeze(0)
            validate_audio_tensor(audio, model.sample_rate, model.sample_rate)
            quality = audio_diagnostics(audio.numpy(), model.sample_rate, text)
        except ValueError as exc:
            rejected.append({"seed": attempt_seed, "reason": str(exc)})
            print(f"  Signal check rejected attempt {attempt + 1}: {exc}")
            continue
        quality.update({"generation_seed": attempt_seed, "rejected_attempts": rejected})
        return audio, quality
    raise RuntimeError(f"Audio failed signal checks twice: {rejected}. Review the reference and text.")


# ============================================================
# 5. PIPELINE ENTRY POINT
# ============================================================
def run_pipeline(
    post_html_file,
    narration_reference_audio,
    quote_reference_audio=None,
    quote_mode="preserve",
    output_dir=None,
    speed: float = 1.0,
    *,
    profile="balanced",
    reference_start_seconds=None,
    quote_reference_start_seconds=None,
    seed=SEED,
    max_chunks=None,
):
    """Generate local audio and an audit report; shared by CLI and AWS worker.

    All configuration is per-call. Profiles are audition starting points;
    this engine has no instruction-based emotion control.
    """
    # Narration pace multiplier validation
    if isinstance(speed, bool):
        raise ValueError("speed must be a numeric pace multiplier")
    speed = float(speed)
    allowed_speeds = {
        0.65, 0.67, 0.69, 0.71, 0.73, 0.75,
        0.77, 0.79, 0.81, 0.83, 0.85,
    }
    legacy_speeds = {
        0.80, 0.82, 0.84, 0.86, 0.88, 0.90,
        0.92, 0.94, 0.96, 0.98, 1.00,
    }
    if speed not in allowed_speeds and speed not in legacy_speeds:
        raise ValueError(
            "speed must be an active Studio pace from 0.65 to 0.85 "
            "in 0.02 steps, or a supported legacy pace"
        )
    settings = get_profile(profile)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise ValueError("seed must be an integer in [0, 2147483647]")
    if max_chunks is not None and (
        isinstance(max_chunks, bool) or not isinstance(max_chunks, int) or max_chunks < 1
    ):
        raise ValueError("max_chunks must be a positive integer")
    for start in (reference_start_seconds, quote_reference_start_seconds):
        if start is not None and (isinstance(start, bool) or not np.isfinite(start) or start < 0):
            raise ValueError("reference start must be a finite non-negative number")
    render_speed = settings.base_speed * speed
    post_html_file = os.path.abspath(os.fspath(post_html_file))
    narration_reference_audio = os.path.abspath(
        os.fspath(narration_reference_audio)
    )
    quote_reference_audio = (
        os.path.abspath(os.fspath(quote_reference_audio))
        if quote_reference_audio
        else None
    )

    if output_dir is None:
        output_dir = os.path.join(BASE_DIR, "output")
    output_dir = os.path.abspath(os.fspath(output_dir))
    internal_dir = os.path.join(output_dir, "_internal")

    config = {
        "base_dir": BASE_DIR,
        "post_html_file": post_html_file,
        "narration_reference_audio": narration_reference_audio,
        "quote_reference_audio": quote_reference_audio,
        "quote_mode": str(quote_mode).strip().lower(),
        "output_dir": output_dir,
        "internal_dir": internal_dir,
        "raw_cache_dir": os.path.join(internal_dir, "raw_cache"),
        "voice_state_cache_dir": os.path.join(internal_dir, "voice_states"),
        "debug_dir": os.path.join(internal_dir, "debug"),
        "render_speed": render_speed,
    }

    ffmpeg_path = validate_runtime_config(config)

    print("=" * 72)
    print("PocketTTS - Long-Form Cloned-Voice Narration (block-aware)")
    print("=" * 72)

    with open(config["post_html_file"], "r", encoding="utf-8") as post_file:
        post_html = post_file.read().strip()
    if not post_html:
        raise ValueError("Post HTML file is empty.")

    extracted_blocks = extract_worker_blocks(post_html)
    if not extracted_blocks:
        raise ValueError(
            "Extraction produced zero narration blocks. Check that the input is "
            "real Ghost post.html content (paragraphs/blockquotes), not the "
            "rendered public page."
        )

    narration_blocks = build_narration_blocks(
        extracted_blocks, quote_mode=config["quote_mode"]
    )
    for block in narration_blocks:
        block["text"] = apply_pronunciation_overrides(
            block["text"], PRONUNCIATION_OVERRIDES
        )
    if not narration_blocks or not any(
        str(block.get("text", "")).strip() for block in narration_blocks
    ):
        raise ValueError(
            "Narration is empty after applying quote mode. If the post contains "
            "only quotes, do not use NARRATION_QUOTE_MODE=exclude."
        )

    quote_count = sum(
        1 for block in narration_blocks if block["block_type"] == "quote"
    )
    print(
        f"\nExtracted {len(extracted_blocks)} blocks from HTML -> "
        f"{len(narration_blocks)} narration blocks ({quote_count} quotes, "
        f"mode={config['quote_mode']})"
    )

    roles = required_roles(narration_blocks)
    if not roles:
        raise ValueError("Narration produced no non-empty generation roles")
    references = validate_reference_config(config, roles)
    temperatures = {
        "narration": settings.narration_temperature, "quote": settings.quote_temperature
    }
    print(f"Profile: {settings.name}; rendered speed: {render_speed:.0%}; seed: {seed}")

    # Mutate directories only after config, references, routing, and ffmpeg pass.
    for directory in (
        config["output_dir"],
        config["raw_cache_dir"],
        config["voice_state_cache_dir"],
        config["debug_dir"],
    ):
        os.makedirs(directory, exist_ok=True)

    print("\nPreparing role-specific voice anchors...")
    anchor_paths = {}
    anchor_metadata = {}
    for role in roles:
        start_seconds = (
            quote_reference_start_seconds
            if role == "quote" and config["quote_mode"] == "two_voice"
            else reference_start_seconds
        )
        reference_hash = sha256_file(references[role])
        anchor_key = payload_sha256({
            "source_sha256": reference_hash,
            "anchor_seconds": ANCHOR_SECONDS,
            "cut_algorithm": ALGORITHM_VERSION,
            "start_seconds": start_seconds,
        })
        anchor_path = os.path.join(
            config["internal_dir"],
            f"voice_anchor_{role}_{anchor_key[:20]}.wav",
        )
        metadata = prepare_voice_anchor(references[role], anchor_path, start_seconds)
        anchor_paths[role] = anchor_path
        anchor_metadata[role] = metadata
        print(
            f"  {role.capitalize()} reference duration: "
            f"{metadata['duration_seconds']:.2f}s (target {ANCHOR_SECONDS}s)"
        )
        for warning in metadata.get("warnings", []):
            print(f"  Reference note: {warning}")
    if (
        config["quote_mode"] == "two_voice"
        and "quote" in anchor_metadata
        and "narration" in anchor_metadata
        and anchor_metadata["narration"]["anchor_sha256"]
        == anchor_metadata["quote"]["anchor_sha256"]
    ):
        raise ValueError(
            "two_voice references produced identical voice anchors; provide "
            "recordings with distinct selected voice segments"
        )

    print("\nPython:", sys.version.split()[0])
    print("PyTorch:", torch.__version__)

    models = {}
    identities = {}
    count_tokens = {}
    sample_rate = None
    models_by_temperature = {}
    for role in roles:
        temperature = temperatures[role]
        print(f"\nLoading PocketTTS {role} model (temp={temperature})...")
        load_start = time.time()
        # v2.1 calls this lsd_decode_steps; v3 calls it sampler_decode_steps.
        # Inspect the supported API instead of silently upgrading dependencies.
        step_argument = (
            "sampler_decode_steps"
            if "sampler_decode_steps" in inspect.signature(TTSModel.load_model).parameters
            else "lsd_decode_steps"
        )
        if temperature not in models_by_temperature:
            models_by_temperature[temperature] = TTSModel.load_model(
                language=MODEL_LANGUAGE, temp=temperature,
                **{step_argument: settings.decode_steps},
                noise_clamp=NOISE_CLAMP, eos_threshold=EOS_THRESHOLD, quantize=QUANTIZE,
            )
        model = models_by_temperature[temperature]
        print(f"  Loaded in {time.time() - load_start:.1f}s on {model.device}")
        role_sample_rate = int(model.sample_rate)
        if sample_rate is None:
            sample_rate = role_sample_rate
        elif role_sample_rate != sample_rate:
            raise RuntimeError(
                "Narration and quote models report different sample rates"
            )
        models[role] = model
        identities[role] = model_identity_for(model, role, temperature)
        count_tokens[role] = count_tokens_for(model)
    sr = sample_rate

    print("\nBuilding voice states (once per loaded model)...")
    voice_states = {}
    voice_identities = {}
    for role in roles:
        state, _state_path, voice_identity = load_or_build_voice_state(
            models[role],
            anchor_paths[role],
            identities[role],
            config["voice_state_cache_dir"],
        )
        voice_identity["reference_audio_sha256"] = anchor_metadata[role]["source_sha256"]
        voice_states[role] = state
        voice_identities[role] = voice_identity

    all_chunks = build_role_chunks(narration_blocks, count_tokens, settings.token_budget)
    if max_chunks is not None:
        all_chunks = all_chunks[:max_chunks]
    if not all_chunks:
        raise ValueError("Narration produced zero text chunks after tokenization")
    narration_chunk_count = sum(
        1 for chunk in all_chunks if chunk["role"] == "narration"
    )
    quote_chunk_count = sum(
        1 for chunk in all_chunks if chunk["role"] == "quote"
    )

    print(
        f"\nTotal chunks: {len(all_chunks)} "
        f"(narration: {narration_chunk_count}, quote: {quote_chunk_count})"
    )
    print("Chunk structure:")
    for index, record in enumerate(all_chunks, 1):
        token_count = count_tokens[record["role"]](record["text"])
        boundary = boundary_kind_for(record)
        print(
            f"  {index:2d}. [{record['role']:9s}] {token_count:3d} tok, "
            f"pause-after: {boundary:9s} | {record['text'][:70]}"
        )
        if token_count > settings.token_budget:
            print(
                f"      NOTE: exceeds token budget ({settings.token_budget}); "
                "may be internally re-split by PocketTTS."
            )

    print("\n" + "=" * 72)
    print("GENERATE / TRIM / FADE / TEMPO")
    print("=" * 72)
    generation_start = time.time()
    processed_outputs = []
    chunk_reports = []

    for index, record in enumerate(all_chunks, start=1):
        role = record["role"]
        model = models[role]
        chunk_text = record["text"]
        chunk_seed = int(SEED_OVERRIDES.get(text_sha256(chunk_text), seed + index))
        cache_payload = raw_generation_payload(
            chunk_text,
            chunk_seed,
            role,
            temperatures[role],
            identities[role],
            voice_identities[role],
            profile=settings,
        )
        (
            audio_chunk,
            reject_reason,
            _raw_key,
            raw_path,
            raw_manifest_path,
        ) = load_raw_cache(
            cache_payload,
            model.sample_rate,
            config["raw_cache_dir"],
        )

        cache_hit = audio_chunk is not None
        quality = None
        if audio_chunk is not None:
            try:
                quality = audio_diagnostics(audio_chunk.numpy(), model.sample_rate, chunk_text)
                manifest = read_json(raw_manifest_path) or {}
                quality.update(manifest.get("generation_diagnostics", {}))
            except ValueError as exc:
                reject_reason = str(exc)
                audio_chunk = None
                cache_hit = False
        if audio_chunk is not None:
            print(f"[{index}/{len(all_chunks)}] ({role}) Raw cache hit: {raw_path}")
        else:
            if reject_reason:
                print(
                    f"[{index}/{len(all_chunks)}] ({role}) "
                    f"Raw cache rejected: {reject_reason}"
                )
            print(
                f"\nGenerating raw chunk {index}/{len(all_chunks)} [{role}] "
                f" ({len(chunk_text)} chars)"
            )
            try:
                audio_chunk, quality = generate_checked_chunk(
                    model, voice_states[role], chunk_text, chunk_seed, settings.token_budget
                )
                atomic_save_audio(
                    raw_path,
                    audio_chunk,
                    model.sample_rate,
                    subtype="FLOAT",
                )
                atomic_write_json(
                    raw_manifest_path,
                    {
                        "payload": cache_payload,
                        "wav_sha256": sha256_file(raw_path),
                        "frames": int(audio_chunk.shape[1]),
                        "generation_diagnostics": {
                            "generation_seed": quality["generation_seed"],
                            "rejected_attempts": quality["rejected_attempts"],
                        },
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as exc:
                print(f"\nERROR generating raw chunk {index} ({role}): {exc}")
                raise

        raw_duration = audio_chunk.shape[1] / model.sample_rate
        with tempfile.TemporaryDirectory(
            prefix="chunk_render_",
            dir=config["internal_dir"],
        ) as chunk_temp_dir:
            processed = render_raw_chunk(
                audio_chunk,
                sr,
                render_speed,
                chunk_temp_dir,
                ffmpeg_path=ffmpeg_path,
            )
        processed_outputs.append(processed)
        chunk_reports.append({
            "index": index, "role": role, "text": chunk_text, "seed": chunk_seed,
            "block_index": record["block_index"], "boundary": boundary_kind_for(record),
            "token_count": count_tokens[role](chunk_text), "raw_path": raw_path,
            "cache_hit": cache_hit, "quality": quality,
        })
        del audio_chunk
        print(f"  Ready: {raw_duration:.2f}s raw; post-processing complete")

    shared_peak = max(float(audio.abs().max()) for audio in processed_outputs)
    if not np.isfinite(shared_peak) or shared_peak <= 0:
        raise RuntimeError("Cannot normalize an empty or non-finite render")
    shared_gain = NORMALIZE_PEAK / shared_peak
    print(f"Shared normalization gain: {shared_gain:.6f}")

    final_chunks = []
    for audio in processed_outputs:
        audio.mul_(shared_gain)
        final_chunks.append({"audio": audio, "activity": detect_activity(audio, sr)})
    processed_outputs.clear()

    pieces = []
    first_lead = final_chunks[0]["activity"]["start"]
    lead_target = int(round(sr * settings.lead_in_ms / 1000))
    lead_zero = max(lead_target - first_lead, 0)
    if lead_zero:
        pieces.append(
            torch.zeros(
                1,
                lead_zero,
                dtype=final_chunks[0]["audio"].dtype,
            )
        )

    for index, item in enumerate(final_chunks):
        pieces.append(item["audio"])
        if index == len(final_chunks) - 1:
            continue
        record = all_chunks[index]
        next_item = final_chunks[index + 1]
        left_tail = item["audio"].shape[1] - item["activity"]["end"]
        right_lead = next_item["activity"]["start"]
        natural_pad = left_tail + right_lead

        target_ms = boundary_pause_ms(record, all_chunks[index + 1], settings)

        target_samples = int(round(sr * target_ms / 1000))
        inserted_zero = max(target_samples - natural_pad, 0)
        if inserted_zero:
            pieces.append(
                torch.zeros(
                    1,
                    inserted_zero,
                    dtype=item["audio"].dtype,
                )
            )

    joined_audio = torch.cat(pieces, dim=1)

    # Reserve only when the render is ready so concurrent runs cannot collide.
    final_output = reserve_numbered_output_path(config["output_dir"])
    try:
        atomic_save_audio(
            final_output,
            joined_audio,
            sr,
            subtype="PCM_16",
            verify_samples=False,
        )
    except Exception:
        try:
            if os.path.isfile(final_output) and os.path.getsize(final_output) == 0:
                os.remove(final_output)
        except OSError:
            pass
        raise

    generation_time = time.time() - generation_start
    duration = joined_audio.shape[1] / sr
    atomic_write_json(os.path.splitext(final_output)[0] + ".json", {
        "schema_version": 1, "profile": settings.name, "settings": settings.to_dict(),
        "seed": seed, "speed": render_speed, "preview": max_chunks is not None,
        "source_html_sha256": sha256_file(post_html_file),
        "reference_anchors": anchor_metadata, "models": identities,
        "chunks": chunk_reports, "output_sha256": sha256_file(final_output),
        "duration_seconds": duration, "generation_seconds": generation_time,
        "warnings": [warning for entry in chunk_reports for warning in entry["quality"]["warnings"]],
        "assessment": "Signal validation passed; voice similarity, word accuracy and storytelling require audition.",
    })

    print("\n" + "=" * 72)
    print("SUCCESS")
    print("=" * 72)
    print("Output:", final_output)
    print(f"Duration: {duration / 60:.2f} minutes")
    print(f"Generation + render time: {generation_time / 60:.2f} minutes")
    print(
        f"Chunks: {len(all_chunks)}  (narration: {narration_chunk_count}, "
        f"quote: {quote_chunk_count})"
    )
    return final_output


def main(argv=None):
    """
    Local development entry point.

    This keeps the existing CLI behavior while delegating all actual work to
    the same run_pipeline() function that AWS Lambda will use.
    """
    environment = load_runtime_environment(BASE_DIR)
    config = build_runtime_config(BASE_DIR, environment)
    parser = argparse.ArgumentParser(description="Generate narration with a prepared reference and audit report.")
    parser.add_argument("--html", default=config["post_html_file"])
    parser.add_argument("--reference", default=config["narration_reference_audio"])
    parser.add_argument("--quote-reference", default=config["quote_reference_audio"])
    parser.add_argument("--quote-mode", choices=("preserve", "exclude", "two_voice"), default=config["quote_mode"])
    parser.add_argument("--profile", choices=("faithful", "balanced", "expressive", "legacy"), default=config["profile"])
    parser.add_argument("--speed", type=float, default=config["speed"])
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--reference-start", type=float, default=config["reference_start_seconds"])
    parser.add_argument("--quote-reference-start", type=float, default=config["quote_reference_start_seconds"])
    parser.add_argument("--max-chunks", type=int, help="Limit generation for a short preview")
    parser.add_argument("--output-dir", default=config["output_dir"])
    args = parser.parse_args(argv)
    return run_pipeline(
        post_html_file=args.html, narration_reference_audio=args.reference,
        quote_reference_audio=args.quote_reference, quote_mode=args.quote_mode,
        output_dir=args.output_dir, profile=args.profile, speed=args.speed,
        reference_start_seconds=args.reference_start,
        quote_reference_start_seconds=args.quote_reference_start,
        seed=args.seed, max_chunks=args.max_chunks,
    )


if __name__ == "__main__":
    main()
