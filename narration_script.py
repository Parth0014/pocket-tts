"""Build the exact text and metadata consumed by the narration chunker.

Extraction answers "what did the HTML contain?"; this module answers
"how should those blocks be spoken?". Keeping those stages separate lets
quote policy change without coupling it to the HTML parser.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Iterable, Iterator

BASE_DIR = Path(__file__).resolve().parent

# Deterministic rotation keeps repeated attributions from sounding mechanical
# without making generated text (and therefore cache keys) vary between runs.
_LEAD_IN_VERBS = ("says", "shares", "reflects", "feels")
_QUOTE_MODES = frozenset({"preserve", "exclude", "two_voice"})


def _lead_in_for_quote(speaker: str, verb_cycle: Iterator[str]) -> str:
    return f"{speaker} {next(verb_cycle)},"


def build_narration_blocks(
    blocks: Iterable[dict], quote_mode: str = "preserve"
) -> list[dict]:
    """Convert extractor blocks into ordered, narration-ready blocks.

    ``quote_mode`` controls whether quotes remain in the script:

    * ``preserve`` keeps each quote and its optional spoken attribution.
    * ``exclude`` removes quote blocks.
    * ``two_voice`` produces the same text and block metadata as ``preserve``.
      It is a generation policy: the generator routes these ``quote`` blocks
      to its configured secondary voice. This function does not claim that
      changing a block type alone changes a voice.

    Every returned block has this stable shape::

        {
            "block_type": "paragraph" | "heading" | "list" | "quote",
            "text": "...",
            "speaker": "Name" | None,
        }

    Unsupported upstream block types are ignored defensively. Input order is
    preserved, and the input dictionaries are never mutated.
    """
    if quote_mode not in _QUOTE_MODES:
        choices = ", ".join(sorted(_QUOTE_MODES))
        raise ValueError(
            f"Unknown quote_mode {quote_mode!r}; expected one of: {choices}"
        )

    verb_cycle = itertools.cycle(_LEAD_IN_VERBS)
    narration_blocks: list[dict] = []

    for block in blocks:
        block_type = block.get("type")

        if block_type == "quote":
            if quote_mode == "exclude":
                continue

            quote_text = block.get("text")
            if not isinstance(quote_text, str) or not quote_text.strip():
                continue

            speaker = block.get("speaker")
            if speaker:
                spoken_text = (
                    f'{_lead_in_for_quote(speaker, verb_cycle)} "{quote_text}"'
                )
            else:
                # With no attribution, speak only the author's words. Do not
                # invent a speaker or consume a verb from the deterministic
                # rotation.
                spoken_text = quote_text

            narration_blocks.append(
                {
                    "block_type": "quote",
                    "text": spoken_text,
                    "speaker": speaker,
                }
            )
            continue

        if block_type in {"paragraph", "heading", "list"}:
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            narration_blocks.append(
                {
                    "block_type": block_type,
                    "text": text,
                    "speaker": None,
                }
            )

    return narration_blocks


if __name__ == "__main__":
    from extractor import extract_blocks

    sample_path = BASE_DIR / "sample.html"
    html = sample_path.read_text(encoding="utf-8")

    extracted = extract_blocks(html)
    narration = build_narration_blocks(extracted, quote_mode="preserve")

    print(f"{len(narration)} narration blocks (preserve mode):\n")
    for index, block in enumerate(narration, 1):
        tag = f"[{block['block_type']}]"
        print(f"{index:2d} {tag:10s} {block['text'][:100]}")
