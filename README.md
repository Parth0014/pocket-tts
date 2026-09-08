# PocketTTS narration

This project converts Ghost post HTML into block-aware, long-form narration with PocketTTS. It keeps
paragraphs, headings, lists, and quotations separate so quotation pacing and voice treatment do not leak
into surrounding narration.

## Requirements

- Conda or Miniconda
- A consented voice-reference WAV
- Enough memory for PocketTTS model inference

The Python dependencies are declared in `environment.yml` and `pyproject.toml`. A self-contained FFmpeg
binary is supplied by `imageio-ffmpeg`, so a separate system installation is not required.

## Set up

Create the environment from scratch:

```powershell
conda env create -f environment.yml
conda activate pockettts
```

To update an existing environment:

```powershell
conda env update -n pockettts -f environment.yml
conda activate pockettts
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env`, then place the primary reference recording at
`voice_samples/solution.wav`. Paths in `.env` may be absolute or relative to the project directory.
The checked-in defaults read `sample.html` and use one voice with a higher quote temperature.

The first PocketTTS run may download model data. Do not use a person's voice without their permission.

## Run

```powershell
pocket-tts-narrate
```

or:

```powershell
python generate_narration.py
```

Final files are written as `output/narration_NNNN.wav`. Cache, debug, and role-specific voice-state files
live under `output/_internal/`. Raw cache keys include the reference-voice identity, so changing the voice
cannot reuse audio generated for an earlier speaker.

### Quote modes

- `preserve`: keep quotations, using the primary reference voice with a higher generation temperature.
- `exclude`: omit quotations.
- `two_voice`: use a second speaker for quotations. Set
  `NARRATION_QUOTE_REFERENCE_AUDIO=voice_samples/quote_voice.wav`; it must differ from the primary voice.

## Validate

### Local audio mastering experiment

Mastering defaults to `auto` for generation on this experimental branch, including
callers of `run_pipeline`. No deployment has been performed. Use `--mastering off`
(or `mastering="off"` in Python) for the original behavior.
It runs locally using bundled FFmpeg, with no model download or Adobe service.
Activate the `pockettts` environment, then compare an existing narration:

```powershell
python narration_mastering.py output/narration_0005.wav --compare-dir output/my-mastering-comparison
```

Open `output/my-mastering-comparison/index.html` to switch between Original (`off`),
Automatic, Natural, and Warm Story at the same playback position. The player uses gain-only,
loudness-matched audition copies; separate delivery WAVs retain their mastered levels.
The comparison directory must be new. Source audio is never overwritten.

To automatically master one file, or generate with automatic mastering:

```powershell
python narration_mastering.py output/narration_0005.wav --output output/story-master.wav
python generate_narration.py
```

`auto` measures the signal and chooses processing without manual preset selection.
It applies a 55 Hz high-pass only when over 3% of spectral energy is below 50 Hz,
and gentle compression only when projected peaks at target loudness exceed the
ceiling by over 1 dB. Otherwise it preserves tone and dynamics and applies measured
loudness normalization. These are conservative engineering heuristics, not a trained
quality or emotion model. Every decision and its reason is saved in the report.

`natural` applies a 55 Hz high-pass, gentle compression, and measured two-pass loudness
normalization. `warm_story` adds subtle warmth/presence EQ and slightly more compression.
Targets are -19 LUFS mono / -16 LUFS stereo and a -1.5 dBTP ceiling, with the encoded
PCM16 WAV remeasured. Enabled processing requires at least three seconds of finite,
non-silent mono/stereo audio at 16 kHz or higher. Unsupported input or processing failure
stops the render before a final audio file is published. Automatic generation previews
under three seconds retain the existing render and report `skipped_short_audio`;
the standalone mastering command still requires three seconds. Off mode preserves the old pipeline.

Reports record exact filters, processor version, hashes, timing, loudness, true peak, and
warnings. Dynamic loudness fallback and target deviation are reported. Generation caches
are independent of mastering settings. No denoising, neural restoration, de-essing,
reverb, or artificial emotion is applied in this first experiment. Compare by listening;
measurements cannot establish better storytelling. See [research and design](docs/audio-enhancement-research.md).

## Tests

The unit tests do not load a TTS model or generate audio:

```powershell
pytest
ruff check .
```

## Main files

- `extractor.py`: safely converts Ghost HTML into typed narration blocks.
- `narration_script.py`: applies quote modes and spoken lead-ins.
- `chunking.py`: packs blocks into tokenizer-safe chunks.
- `generate_narration.py`: loads configuration and models, caches raw speech, renders, and joins audio.

Local `.env` files, voice recordings, generated WAVs, model states, and debug artifacts are ignored by Git.
