# Narration quality: evidence and system design

Research date: 6 September 2026. This document separates verified PocketTTS behavior, findings in this project's original pipeline, and engineering choices that require listening tests. It does not claim that a passing software test proves expressive narration.

The practical direction is to improve the reference, preserve complete thoughts, reduce unnecessary pacing changes, and let the listener compare short previews before generating a whole article. Temperature alone cannot solve voice identity, narration, and continuity together.

## What Kyutai actually supports

### The 15-second assumption

The current official export documentation says that its voice export command processes the first **30 seconds**. A selected 15-second reference is a useful project target, not a universal Kyutai requirement or a guarantee that those particular seconds are optimal. [Official export-voice documentation](https://kyutai-labs.github.io/pocket-tts/CLI%20Commands/export_voice/)

In the pinned 2.1.0 implementation, `get_state_for_audio_prompt(..., truncate=False)` processes the supplied recording; `truncate=True` caps file input at 30 seconds. Its documentation describes conditioning on voice, style, and prosody. Audio is converted to the model's sample rate and mono. [PocketTTS 2.1.0 implementation](https://github.com/kyutai-labs/pocket-tts/blob/v2.1.0/pocket_tts/models/tts_model.py)

Kyutai recommends cleaning reference audio because its recording quality can carry into the output. This supports improving selection and recording conditions; it does not establish that every denoiser preserves voice identity. [Official project documentation](https://kyutai-labs.github.io/pocket-tts/)

**Design inference:** prefer a clean, uninterrupted passage from one speaker in the intended delivery. A quiet explanatory voice may remain a quiet explanatory voice after conditioning. A lively reference is a sensible experiment, but there is no demonstrated rule that the passage with the widest pitch range makes the best narrator.

### Temperature, voice similarity, and expression

The repository pins `pocket-tts==2.1.0`. Its package defaults are temperature `0.7`, one LSD decode step, no noise clamp, EOS threshold `-4.0`, and 50 text tokens per internal chunk. A source comment says the April English model supports larger chunks. These are package defaults, not tuned values for this particular reference. [Versioned default parameters](https://github.com/kyutai-labs/pocket-tts/blob/v2.1.0/pocket_tts/default_parameters.py)

More recent Kyutai work explicitly changes the April English model's recommended temperature to `0.3`. The maintainer reports a modest preference for 0.3 over 0.7: 53.5% of 400 audio-quality judgments and 52.7% of 300 voice-similarity judgments, with word error rates of 0.82% versus 0.92% in their automated evaluation. The evaluated model was `english_2026-04`; this is not a claim about every language or every voice. It also was not a test of dramatic storytelling. [Maintainer's evaluation and merged change, PR 223](https://github.com/kyutai-labs/pocket-tts/pull/223)

Therefore, the attached script's assertion that 0.3 caused flatness is not established. Use 0.3 as an evidence-backed English comparison point, and compare a moderately more variable profile on the user's actual text. A setting such as 0.6 or 0.8 can produce a different take; the numerical increase does not mean the model better understands the story.

In 2.1.0 the sampling noise has standard deviation `sqrt(temp)`. `noise_clamp` changes the sampled noise distribution through a truncated normal; it is not a speaker-identity controller. LSD steps control repeated flow evaluations. The implementation contains no mechanism saying that a clamp value of 2.2 protects a particular voice or that extra steps supply emotional intent. [Versioned flow sampler](https://github.com/kyutai-labs/pocket-tts/blob/v2.1.0/pocket_tts/models/flow_lm.py)

**Design inference:** leave noise clamping off in the baseline. Compare one versus two decode steps before paying for five on every chunk. Adopt extra computation only when actual listening or a useful quality check improves. Do not describe decode steps as having no possible quality downside or promise a monotonic benefit.

### Chunk length, continuity, and output completion

`generate_audio(max_tokens=...)` controls **input-text chunking**, not the output token limit. `copy_state=True` starts each internal chunk from a copy of the reference state. `copy_state=False` mutates the supplied state; it is not a documented narrative-memory guarantee. Source comments identify conditioning later chunks on earlier generated audio as future work. Text preparation adjusts capitalization/punctuation; generation length is estimated from text tokens. Exhausting that estimate without EOS normally warns, but `KPOCKET_TTS_ERROR_WITHOUT_EOS=1` makes it fail. These behaviors were also checked in the installed 2.1.0 package. [Versioned generation implementation](https://github.com/kyutai-labs/pocket-tts/blob/v2.1.0/pocket_tts/models/tts_model.py)

**Design inference:** increasing the application's chunk budget without passing a corresponding `max_tokens` can leave the old internal splitting in place. Preserve two or more related sentences together when they fit. Try bounded larger chunks on April English, with a smaller fallback for missing words or unstable outputs. Count with the actual tokenizer, include normalization headroom, and put the internal limit in the cache identity. An application budget of roughly 80–120 tokens is an experiment, not an upstream quality guarantee.

Keep independent reference states for reproducible caching. Simply retaining every generated chunk in one mutable state would make results depend on all preceding generation, complicate retries, and allow an earlier poor take to affect later audio. A future continuity mode needs bounded history, speaker resets, and its own listening evaluation.

### Pacing and emotional instructions

The public API exposes audio conditioning and generation parameters, without a documented parameter for directing sadness, suspense, intimacy, or narrative intent. The project also lists explicit silence instructions in text as unsupported. [Python API](https://kyutai-labs.github.io/pocket-tts/API%20Reference/python-api/), [official unsupported-features list](https://kyutai-labs.github.io/pocket-tts/#unsupported-features)

**Design inference:** do not inject `[whisper]`, stage directions, SSML, or instructions to an actor into text expecting reliable control; they can contaminate the spoken passage. Preserve punctuation and paragraph structure, let suitable reference delivery influence prosody, and add deliberate pauses during assembly where supported. An editorial rewrite for speech should be explicit and reviewable, because better spoken prose can also change the author's wording.

## Findings in this project's original pipeline

These findings come from the workspace's `generate_narration.py`, `chunking.py`, and the supplied Downloads version before the quality changes. They are observations about configuration or code, not conclusions from listening to every generated file.

| Finding | Likely consequence | Design response |
| --- | --- | --- |
| The anchor always starts at the beginning; a low-energy endpoint is sought near 15 seconds. | A noisy intro, silence, or an unsuitable opening delivery can dominate conditioning. The cut can extend beyond 15 seconds. | Rank contiguous candidate windows across the recording and offer an explicit start override. |
| Reference channels are averaged without considering their contents. | An interviewer, noisy channel, or opposite-polarity channels can reduce usable speech. | Inspect channel quality and avoid blind averaging when it damages the signal. Expose the selected channel. |
| The external text budget is 44 tokens. | Long sentences are divided into clauses or word groups, interrupting their intended intonation. | Preserve complete thoughts in bounded larger chunks and expose the exact generated text. |
| The renderer applies 0.86 speed globally. | It lengthens speech by approximately 16%; this can make already restrained delivery feel drawn out. | Start at native speed, with optional user pacing. |
| Quote temperature is substantially above narration temperature. | Quotation boundaries can change vocal behavior even when the same narrator is intended. | Use consistent defaults; make alternate quote delivery an explicit option. |
| Quote lead and trail pause additions apply around every quote chunk. | A long quotation receives extra pauses inside itself. | Apply quotation entry/exit treatment at semantic boundaries. |
| Edge activity uses an absolute amplitude threshold. | Quiet endings, breaths, or low-energy consonants may be treated as silence. | Use conservative trimming and keep an unprocessed cache for comparison. |
| Cache validation checks structure, sample rate, hash, and finite samples. | A structurally valid WAV can still omit words or sound poor. | Treat technical validity and perceptual quality as different checks. |
| Final gain is shared across chunks. | Relative performance dynamics are preserved better than with independent peak normalization. | Retain this behavior; do not flatten every sentence to identical loudness. |

## Reference preparation contract

The following is the intended engineering contract; consult implementation tests and the README for the exact controls present in a release.

1. Decode the raw local recording, validate sample count and finite values, and keep the original untouched. Bound accepted duration and file size so an accidental very large upload has a predictable failure.
2. Inspect individual channels. Choose a useful speech channel when downmixing would damage it. Report the selected channel and any decision to downmix.
3. Scan overlapping **contiguous** windows near the target duration. Prefer speech coverage, room for natural breaths, adequate level, few clipped samples, and lower noise proxies. Penalize long silence, unreliable edges, and heavily distorted segments.
4. Refine boundaries toward low-energy transitions within a strict duration limit. A shorter natural passage can be preferable to forcing exactly 15 seconds and cutting a word. Do not concatenate unrelated syllables or remove every internal pause to manufacture 15 seconds of dense speech.
5. Make only conservative signal changes: suitable sample-rate conversion, optional DC removal, small edge fades, and bounded gain where necessary. Do not silently pitch-shift, time-stretch, heavily compress, or aggressively denoise the identity reference.
6. Save a previewable mono WAV plus a manifest: raw file hash, processed hash, source start/end, source and output sample rates, channel decision, applied changes, quality proxies, warnings, and processing version.
7. Reuse this artifact and its cached voice state for generation. Different references and processing settings must not collide. Keep the selected audio available for listening, not only an opaque embedding.

These scores are **heuristics**, not proof of a good reference. Energy cannot establish that a window contains one person, excludes music, matches the intended accent, or has engaging emotional delivery. A selection report should say what was measured. Avoid calling a noise-floor contrast metric true SNR without a clean/noise ground truth. Low-noise music and a clean second speaker are known failure cases for simple acoustic ranking.

The user workflow should be: upload once, hear the selected excerpt, optionally adjust its start, compare short narration previews, then generate the full article with the chosen settings. When no usable speech is found, ask for a clearer reference through a concrete error; do not produce a misleading quality score and carry on.

## Generation and delivery contract

Use a small set of clearly described profiles. A faithful baseline starts with April English at temperature 0.3, native speed, conservative sampler settings, and complete sentences where practical. A storytelling profile may use slightly different temperature, chunk size, and pause timing, but its name describes the intended comparison rather than a new capability inside PocketTTS.

Expose the script actually being spoken. Preserve question marks, exclamations, and dialogue attribution. Expand numbers, dates, acronyms, and names carefully in the existing normalization system. Inspect abbreviation and decimal splitting: naive splitting after every period can separate `Dr. Smith` or mishandle a list marker. Keep headings separate without imposing a long dramatic pause on every small list item.

Make previews representative: include a normal paragraph, a short emotional sentence, dialogue or a question, and one sentence with difficult names/numbers. Keep reference and seed fixed for the first comparison. Let users hear the reference next to each candidate and retain the selected profile/settings in the final manifest. A quality profile needs more than a waveform graphic: the decision is auditory.

Technical failure checks should reject empty, non-finite, effectively silent, or prematurely terminated audio. Duration and activity warnings can identify suspicious clips but cannot certify pronunciation or speaker similarity. Optional ASR comparison can identify omissions, repetitions, and substitutions; report when that check is unavailable. Do not label a generation as text-verified merely because it has a plausible duration.

Retries should be bounded and attributable. Record the attempted seed/settings and reason. Fix a bad boundary by regenerating the complete sentence or paragraph; repairing a single isolated word can introduce a new audible seam. Do not keep resampling indefinitely or silently weaken identity requirements to obtain a technically valid WAV.

Keep raw generation separately from rendered output so trimming and pacing can be revised without resynthesis. Record the real model/config, package version, processed reference identity, text, tokenizer limit, seed, sampler settings, playback speed, pause policy, and quality-check results. A profile name alone is insufficient for reproduction.

## Evaluation before calling the result better

Kyutai's PocketTTS paper uses cleaned reference recordings, ASR word error rate, and separate human pairwise judgments for audio quality and speaker similarity. Those are separate axes; its benchmark is not a validation of this project's article narration or automatic reference selection. [CALM paper, Appendix F](https://arxiv.org/html/2509.06926v3#A6)

Use this practical local evaluation, designed for the user's actual concerns:

| Dimension | Listen or check for | Acceptance evidence |
| --- | --- | --- |
| Spoken text | Missing words, repetitions, wrong names/numbers, clipped final phrases | Exact script available; manual comparison or ASR plus review |
| Voice identity | Timbre, accent, apparent speaker consistency, characteristic delivery | Reference-versus-output listening, independently of whether a take is attractive |
| Storytelling | Meaningful emphasis, questions, emotional transitions, sense of addressing a listener | Blind preference between candidate and baseline on narrative material |
| Continuity | Pitch/rate jumps and unnatural breaths at every join | Boundary listening plus at least one uninterrupted multiminute sample |
| Sound quality | Buzzing, metallic artifacts, clipping, excessive room noise | Headphone and normal-speaker listening at comparable loudness |
| Experience | Time to first useful preview, predictable completion, recoverable failure | Timed preview/full-run workflow and a retry exercise |

Start with the same article excerpt, original reference, text normalization, and seed; change one factor at a time. Compare (a) original versus selected reference, (b) forced 0.86 speed versus native speed, (c) 44-token versus larger complete-thought chunks, and (d) 0.3 versus a moderate variation setting. Once those effects are understood, compare the combined candidate against the original pipeline. This avoids giving all credit to temperature when a better reference or pacing change caused the improvement.

For a useful small listening study, use at least three references and six excerpts covering calm exposition, dialogue, suspense, an emotional transition, difficult pronunciation, and long sentences. Compare at matched perceived loudness, randomize A/B labels, allow ties, and rate identity separately from engagement. Try multiple seeds on finalists, then listen to a full article to catch fatigue and drift. These counts are a practical starting protocol, not a claim of statistical validation.

Do not promote a candidate that sounds lively but loses content or speaker identity. Retain the old render as the baseline. Report preference counts, known failure cases, and which checks actually ran. If this process cannot achieve the required acting quality, the remaining constraint may be the model and reference rather than missing post-processing; evaluate a different engine on the same clips before expanding this pipeline's promises.

## Version and research limitations

The workspace and installed Conda environment both report PocketTTS 2.1.0. Official releases already include 3.x, including a later temperature-default change and API naming changes. The `english` alias in 2.1.0 already uses the April English model; selecting that alias is not an upgrade to a new narrator. Keep dependency migration separate from acoustic tuning so failures and improvements remain attributable. [Official release history](https://github.com/kyutai-labs/pocket-tts/releases)

The technical report landing page did not expose its body to the research fetch; the accessible primary paper, official API documentation, maintainers' evaluation, and versioned source were used instead. No third-party benchmark or social-media anecdote is treated here as proof. Acoustic reference scores and software tests cannot establish that the final output sounds like a skilled human storyteller; the preview and listening protocol close that gap.
