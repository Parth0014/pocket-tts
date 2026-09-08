# Reference-based narration enhancement

Implemented locally on `experiment/audio-enhancement`, 2026-09-08. No deployment.

## Signal order and defaults

Prepared per-speaker anchor -> faithful generation -> raw cache -> existing chunk timing ->
bounded reference match EQ per speaker -> conditional de-essing -> existing automatic
mastering -> verified delivery WAV.

Generation now defaults to `faithful` (temperature 0.3), while explicit profile arguments
and `NARRATION_PROFILE` still override it. This is a conservative experiment, not a
guarantee of identity preservation. The earlier profile choices remain available.
Reference selection still supports `--reference-start` and `--quote-reference-start`.
No reference upload is automatically overwritten or denoised. Cleaning a source can
also change its timbre, and signal analysis alone cannot identify all noisy references.

Automatic matching uses the exact `anchor_paths[role]` selected for generation. It
aggregates that role's speech for analysis, then filters its segments independently.
The other speaker and inserted pauses are not used as its spectral target.
Existing activity detection occurs before matching so pause placement remains stable.
`--no-spectral-match` disables matching; `--mastering off` bypasses the entire new chain.

## Match EQ implementation

`spectral_matching.py` implements overlapping Hann-window mean power analysis, rejecting
very quiet frames. It compares broad fractional-octave log-power bands, removes overall
gain differences, and smooths the correction. Both sources use the same comparison band:
120 Hz through the smaller of 8 kHz or 90% of their Nyquist frequencies. It does not
compare a wide-band reference centroid against an unrestricted narrow-band output centroid.

Defaults: one-third-octave bands, difference limited to +/-6 dB, applied at 50% strength:
the actual FFT response is limited to +/-3 dB. Windowed overlap-add retains sample count
and channels. Unsupported spectral bands are left alone; gain cannot reconstruct absent
harmonics. Sparse/narrow-band inputs, under one second of active evidence, and negligible
gaps bypass matching. The activity threshold is a signal heuristic, not linguistic VAD.

Candidates must improve broad spectral distance and avoid material centroid movement away
from, or beyond, the reference. If the first candidate fails, two lower strengths are
tried before bypassing. Centroid and spectral distance are proxies affected by phonemes,
microphones, and recordings; neither proves speaker identity. The reference is a tonal
guide, not ground-truth aligned speech.

When applied boost above 4 kHz exceeds 2 dB, a conservative FFmpeg de-esser is applied and
the spectral gate evaluates the result including de-essing. Its `i`, `m`, and `f` controls
are normalized 0..1 controls; `f` is NOT a frequency in Hz and `m` is NOT dB reduction.
Current trial settings are `deesser=i=0.1:m=0.2:f=0.5`.
[FFmpeg source documentation](https://ffmpeg.org/ffmpeg-filters.html#deesser)

Loudness/peak decisions are measured AFTER spectral correction. Reports record anchor hash,
frequency bands, supported bandwidth, gains, chosen strength, centroid/distance before and
after, de-esser use, and bypass reasons. Raw generation cache keys are not affected by EQ.

## Local usage

Generate with the conservative profile and automatic enhancement:

```powershell
python generate_narration.py --profile faithful
```

Reprocess existing audio without re-running TTS, using the exact prepared anchor:

```powershell
python narration_mastering.py output/narration_0005.wav --anchor output/_internal/voice_anchor_narration_d19e33b21421778cb29e.wav --compare-dir output/new-comparison
```

In this comparison, `auto` includes reference matching; fixed Natural/Warm Story presets
remain controls without matching. A generation run applies per-role matching before any
enabled mastering preset. Use a new comparison/output path each time.

## Speaker regression scoring

`speaker_similarity.py` optionally uses Resemblyzer's CPU encoder. It scores up to eight
common 8-second windows across raw and processed single-speaker recordings, after the
encoder's own 16 kHz preprocessing. It rejects duration mismatches and skips windows
without enough detected speech in every checkpoint. Use separate files for separate
speakers; a mixed-speaker average would hide regressions.

The gate requires no mean similarity loss and no individual-window loss worse than 0.02.
This threshold is an engineering choice, not a validated universal identity boundary.
It does not verify words, acting, accent, or human preference. Scores do not block normal
generation or add a speaker model to Lambda. [Resemblyzer](https://github.com/resemble-ai/Resemblyzer)

An isolated `.venv-quality` was installed for local testing. On Windows, upstream's
`webrtcvad` source dependency needs a C++ compiler, so use the API-compatible wheel package:

```powershell
python -m venv --system-site-packages .venv-quality
.venv-quality/Scripts/python.exe -m pip install --no-deps resemblyzer==0.1.4
.venv-quality/Scripts/python.exe -m pip install librosa==0.11.0 webrtcvad-wheels==2.0.14 typing
.venv-quality/Scripts/python.exe speaker_similarity.py --anchor ANCHOR.wav --raw RAW.wav --spectral SPECTRAL.wav --output output/speaker-check.json
```

Run the environment creation from the `pockettts` Conda environment, which provides Torch,
NumPy, SciPy, and SoundFile. Pip may flag upstream's named `webrtcvad` dependency as missing
despite the functional `webrtcvad-wheels` replacement. No core dependencies were upgraded.

## Optional neural experiment

`neural_restoration.py` is a separate prototype, not enabled in generation. It can either
evaluate a candidate from a separate neural worker or invoke an installed Resemble Enhance
checkpoint. The adapter uses the published `enhance(..., nfe=32, solver="midpoint",
lambd=0.0, tau=0.3)` API. Upstream has no `denoise=False` argument: `lambd=0` bypasses its
denoiser branch. [Upstream inference](https://github.com/resemble-ai/resemble-enhance/blob/main/resemble_enhance/enhancer/inference.py),
[implementation](https://github.com/resemble-ai/resemble-enhance/blob/main/resemble_enhance/enhancer/enhancer.py)

Use an isolated compatible environment with Resemble Enhance and the scoring dependencies.
The full checkpoint files must already exist under `--run-dir`; the prototype does not
install packages or silently fetch missing checkpoints. Actual neural inference has NOT
been run or validated in this Windows workspace. Its dependency/OS compatibility and
runtime remain unmeasured. The adapter and rejection/fallback logic are tested separately.

```powershell
python neural_restoration.py --source SPECTRAL.wav --anchor ANCHOR.wav --candidate RESTORED.wav --output-dir output/neural-review
# Or, in a compatible environment with a provisioned checkpoint:
python neural_restoration.py --source SPECTRAL.wav --anchor ANCHOR.wav --run-dir MODEL_DIRECTORY --output-dir output/neural-inference
```

A candidate with failed timing/similarity checks falls back to the deterministic baseline.
The selected file is mastered AFTER the neural candidate selection. This corrects the
ordering ambiguity in the proposal: restoration after delivery mastering would invalidate
the loudness and peak checks. A machine gate pass never marks shipping as approved.

`--mode denoise_reference` exposes the separate denoiser for an explicitly requested
reference-cleaning experiment. It writes a candidate and similarity report, does not
master a reference as a delivery file, and never swaps it into the voice library. Audition
it and pass the accepted source through the existing reference preparation workflow.

## Local evidence and limitations

On existing `narration_0005.wav` with the prepared anchor above:

| Measurement | Before | After match EQ |
|---|---:|---:|
| Common-band amplitude centroid | 1598.85 Hz | 1728.64 Hz |
| Anchor centroid | 1860.92 Hz | 1860.92 Hz |
| Broad spectral distance | 2.166 dB | 1.440 dB |
| Resemblyzer mean similarity | 0.83482 | 0.85360 |

All eight similarity windows improved in this example. These measurements use the method
above; they do not reproduce the pasted note's unverified 2241/1086 Hz figures. The raw
existing narration's exact generation provenance is not established by these measurements.

Artifacts: `output/advanced-audio-01/spectral.wav`, `spectral.json`, `similarity.json`, and
`output/advanced-comparison-01/index.html`. One example is insufficient to establish
general improvement; review different speakers, dialogue, sibilance, and long passages.

A fresh faithful-profile generation was attempted. The public fallback model downloaded,
but the voice-cloning weights were unavailable to the local Hugging Face access. The run
stopped rather than substituting a catalog voice. Thus no new faithful-versus-balanced
listening claim is made. Authorized access to Kyutai's voice-cloning weights is needed
to finish that particular generation experiment.
