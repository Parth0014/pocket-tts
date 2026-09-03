# Narration Normalizer Processor V3

Processor V3 makes canonical narration text speech-safe and removes the
remaining runtime extraction fork.

## Emoji policy

Decorative emoji and emoji presentation/sequence code points are removed from
canonical narration text before narration hashing and storage. Ordinary text,
curly quotes and punctuation such as em dashes remain. Emoji-only narration
blocks become empty and are omitted.

Because this changes semantic narration text, `PROCESSOR_VERSION` advances from
2 to 3 and newly processed Narration Documents use `p000003`.

## TTS runtime extraction

The real `generate_narration.py` path no longer calls the independent legacy
`extractor.extract_blocks()` parser. Worker V1 transport still points at
immutable raw Ghost HTML for compatibility, but the worker runs that HTML
through `narration_content.normalizer.normalize_ghost_html()` and adapts the
canonical blocks to the existing chunker.

Canonical preview and generated audio therefore share one implementation for
Ghost-card filtering, trailing-footer filtering, quote attribution, document
order and emoji removal.

During a normalizer/worker processor release the worker mapping must remain
disabled and the worker FIFO/DLQ must be empty.
