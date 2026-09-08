# Independent audio enhancement for Pocket TTS

Research date: 2026-09-07. Scope: Adobe Podcast research, repository inspection, and proposed implementation. No audio audition, model benchmark, or production change was performed.

## Recommendation

Build a deterministic mastering stage first, evaluate optional neural restoration second, and improve narration delivery alongside both. A cleaner, warmer waveform does not by itself provide meaningful emphasis, character intention, suspense, or emotional timing. Those require better generated performances.

## What Adobe provides and what is public

Adobe Podcast Enhance Speech v2 targets noisy, reverberant, distant speech and offers controls over enhancement and background sound. The current product page advertises audio/video processing, bulk enhancement, and speech/music/ambience adjustment. These are useful interaction patterns for an independent product, rather than a specification of its underlying model. [Product](https://podcast.adobe.com/en/enhancespeech)

Adobe's published HiFi-GAN-2 research describes waveform-to-waveform restoration of moderate noise, reverb, and EQ distortion toward studio speech. This supports a broader restoration approach than noise filtering alone. It does not establish that the current commercial v2 uses that exact architecture, weights, training recipe, or processing chain. [Research](https://research.adobe.com/publication/hifi-gan-2-studio-quality-speech-enhancement-via-generative-adversarial-networks-conditioned-on-acoustic-features/)

Adobe also acknowledges that maximum enhancement can sound unnatural and recommends adjusting strength. We should similarly provide bypass and conservative settings. [Adobe guide](https://podcast.adobe.com/guides/how-enhance-speech-can-improve-your-recording-sound-quality)

The reviewed material does not supply a reproducible current Adobe model. This design uses no Adobe service or weights and makes no claim to reproduce its output. Subscription pricing and upload quotas do not affect this implementation.

## What this repository already does

- `generate_narration.py:1029`: trims chunk edges, optionally changes tempo with FFmpeg, and applies fades.
- `generate_narration.py:1538`: applies one shared gain so the largest sample reaches 0.95. This preserves relative chunk dynamics, but does not establish perceptual loudness or control intersample peaks.
- `generate_narration.py:1586`: concatenates speech and semantic pauses, then writes PCM16 audio. This is the main integration point for mastering.
- `narration_quality.py`: provides role temperatures, pause profiles, and basic signal diagnostics. Diagnostics explicitly do not establish word accuracy or emotion.
- `narration_preview.py`: already supports local comparison sessions; extend this for mastering auditions.
- `pyproject.toml`: pins Pocket TTS 2.1.0 and already includes imageio-ffmpeg. Do not assume current upstream API documentation applies to this pinned version.

No code inspection can establish why a particular recording sounds flat. We need to distinguish dull timbre, constant volume, repetitive intonation, excessive slowing, and poorly placed pauses by listening.

## Proposed processing architecture

```text
Text and narrative boundaries
  -> reference selection and Pocket TTS generation
  -> raw chunk cache and content checks
  -> optional neural restoration, with context across processing windows
  -> existing trim / conservative tempo / edge fades
  -> pause-aware assembly, retained as a floating-point premaster
  -> gentle EQ / de-essing / compression
  -> whole-program loudness normalization and true-peak control
  -> encode, remeasure, publish artifact and report
```

For a neural experiment applied to an already assembled premaster, restore it before EQ and mastering and verify silence lengths, alignment, and duration. Avoid independent short-window enhancement that creates voice changes or seams. Compensate model latency and use context overlap according to the chosen model.

## First implementation: CPU mastering

Create `narration_mastering.py` with an interface such as `master_audio(samples, sample_rate, profile) -> (samples, report)`. Keep mastering profiles separate from generation profiles. Initial options: `off`, `natural`, and `warm_story`.

The following are engineering starting points for auditions, not measured optimal settings:

| Stage | Initial approach | Reason |
|---|---|---|
| Analysis | Measure integrated loudness, true peak, clipping, DC offset, duration | Decide what needs correction |
| Low-frequency cleanup | Optional high-pass around 50–70 Hz | Remove rumble without unnecessarily thinning low voices |
| Tone | Broad adjustments within roughly 1–2 dB; audition low-mid warmth and presence separately | Avoid a boomy or harsh preset |
| De-essing | Conditional reduction of excessive sibilance | Preserve intelligibility and breath texture |
| Compression | About 1.5:1–2:1, 15–30 ms attack, 100–200 ms release; tune threshold for roughly 1–3 dB reduction on strong phrases | Improve audibility while retaining expression |
| Loudness | Trial target -19 LUFS for mono; -16 LUFS for a stereo mix | Product targets to verify in the actual player, not universal delivery specifications |
| Peak control | Trial ceiling -1.5 dBTP; measure the encoded result | Leave headroom and detect intersample overs |

Use measured two-pass normalization over the assembled program; explicitly select the output sample rate. FFmpeg `loudnorm` supports integrated loudness, loudness range, true peak, and two-pass operation. Prefer linear gain where constraints allow; record when dynamic processing is used. Avoid forcing a narrow loudness range merely to hit a preset. [FFmpeg documentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)

For enabled mastering, replace the shared peak-normalization stage with appropriate headroom management rather than stacking two unrelated normalization policies. Preserve the exact legacy path when mastering is off. Keep intermediate samples floating point, and quantize once at final export with an explicit dithering policy.

Do not add automatic reverb, stereo widening, aggressive denoising, or saturation in the initial profile. Audition those separately only if the content benefits. A model requiring a higher sample rate needs resampling, but resampling alone does not restore missing detail.

## Optional open-source neural restoration

| Candidate | Verified capability | Recommendation |
|---|---|---|
| Resemble Enhance | Separate denoising and enhancement; restoration and bandwidth extension; training uses 44.1 kHz speech; repository lists MIT | First restoration candidate to benchmark against mastering alone |
| DeepFilterNet | Low-complexity full-band 48 kHz noise suppression; code offers MIT or Apache-2.0 | Use for demonstrably noisy material, potentially reference preparation; bypass on clean TTS |

Sources: [Resemble Enhance](https://github.com/resemble-ai/resemble-enhance), [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet). Pin exact code and checkpoint versions and record their applicable licenses when packaging. Code license summaries alone are not a complete inventory of checkpoint and dependency terms.

Neither is established here as Adobe-equivalent or beneficial on Pocket TTS. Benchmark runtime, memory, voice preservation, and consonant fidelity. The repository's Lambda worker is resource-conscious; a CPU mastering pass fits its design better than adding an unbenchmarked restoration model. Prototype neural processing in an isolated environment to avoid dependency conflicts, then decide whether it belongs on a separate worker.

An enhancement strength control needs model-specific behavior. Do not blindly blend dry and reconstructed waveforms: latency, phase, and reconstructed details can produce comb filtering or doubled speech. First align and verify compatibility; otherwise use discrete processing presets and A/B playback.

## Storytelling requires generation work too

Choose a clean reference with the intended delivery and consistent speaker identity. Audition reference changes rather than assuming style transfers. Preserve meaningful phrase context within model limits. Avoid global slowdown as a substitute for dramatic timing.

Use explicit narrative metadata for pauses and scene transitions, without sending unsupported direction tags as spoken text. The current temperature-based expressive profile is a sampling experiment, not a reliable emotion control. Generate a small number of alternative takes for important or weak passages and select by listening and content fidelity. Keep changes in reference conditioning from creating abrupt speaker changes.

External gain and tempo automation can shape passages modestly. They cannot reliably create word-specific emphasis or emotion absent from the performance. If reference choice, phrasing, and candidate selection remain insufficient, benchmark a TTS model with documented expressive controls as a separate backend decision.

## Reviewable implementation sequence

1. Add analysis-only reports and an isolated mastering renderer for existing WAV files. Produce raw, natural, and warm-story versions from the same input.
2. Extend the preview player with synchronized A/B comparison and loudness-matched audition files. Retain untouched originals and delivery masters separately.
3. Integrate the winning profile after assembly and before final encoding. Include input hash, profile parameters, processor version, FFmpeg version, sample rate, before/after metrics, warnings, and output hash in the existing manifest. Mastering changes must not invalidate raw generation caches.
4. Add optional restoration behind a separate setting only after it wins auditions. Revert to the validated premaster on optional restoration failure and record that fallback; do not silently publish a corrupt master.
5. Improve reference selection and weak-passage regeneration using the existing generation preview workflow.

## Acceptance tests and listening experiment

Use at least twelve 20–40 second excerpts across narration, dialogue, suspense, quiet delivery, sibilant passages, names/numbers, and chunk boundaries, plus several full-length stories for fatigue and consistency. Compare original, mastering only, and restoration plus mastering at matched playback loudness in randomized order.

Rate naturalness, speaker consistency, intelligibility, warmth, emotional engagement, and listening fatigue separately. Record preferences per clip instead of declaring a winner from a single average. Inspect flagged words manually; ASR disagreement is a triage signal, not proof of an error. Do not use an automatic quality score as evidence of storytelling quality.

Verify duration and channel preservation, finite samples, non-silent output, encoded true peaks, loudness tolerance, off-mode compatibility, deterministic DSP, and failure handling. Process a whole story to expose pumping, fatigue, and voice drift. Measure time and peak memory on the actual deployment hardware before making cost or latency promises.

If eventually training a proprietary restoration model, use rights-cleared studio speech, controlled synthetic degradations, and held-out speakers. Actual Pocket TTS defects must be represented: a model trained only on microphone noise may not repair synthetic artifacts. A human reading of the same text is not automatically a sample-aligned training target. Training a competitive general restorer is a separate research investment, not a prerequisite for the mastering layer.
