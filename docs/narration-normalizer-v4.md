# Narration Normalizer V4

Processor V4 fixes a footer-boundary case discovered in the live Team Studio.

## Problem

Some Gratefulness story posts place the valid final editorial provenance
sentence and the beginning of the author CTA in the same Ghost paragraph.

Example shape:

`This is <name>'s story ... preserve its truest form. I'd love to hear your story...`

Processor V3 could remove later footer blocks but could not safely split this
mixed paragraph. As a result, email/share/tagline copy could still reach both
the Studio narration preview and TTS.

## V4 boundary

After the existing V3 normalization/emoji/footer behavior:

1. Search normalized text blocks for the final sentence containing both
   `story` and `truest form`.
2. Preserve that entire sentence.
3. Remove any text after that sentence in the same block.
4. Remove every later block.

The boundary is sentence-aware. It does **not** blindly cut at the words
`truest form`. This preserves variants such as:

`This is Jadwiga's story, told beautifully by her and curated in its truest
form by me to share with you.`

## Safety

If no matching editorial provenance sentence exists, V4 makes no additional
cut. Existing normalization remains unchanged.

Processor version is `4`, producing the immutable document namespace
`p000004`.
