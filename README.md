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
