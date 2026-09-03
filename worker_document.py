"""Canonical Narration Document adapter for the real TTS runtime.

Worker V1 transport still supplies immutable raw Ghost HTML, but the runtime
uses the same shared normalizer as Content Sync, Studio Bridge and Production
Manager. This prevents preview/audio extraction drift.
"""

from __future__ import annotations

from narration_content.normalizer import normalize_ghost_html

_TEXT_BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "quote", "callout", "caption", "prompt"}
)


def _flatten_list_items(items) -> str:
    if not isinstance(items, list):
        return ""
    normalized = [
        item.strip()
        for item in items
        if isinstance(item, str) and item.strip()
    ]
    return ". ".join(normalized)


def canonical_blocks_to_worker_blocks(blocks: list[dict]) -> list[dict]:
    """Adapt canonical blocks to the existing TTS chunker block shapes."""
    result: list[dict] = []

    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")

        if block_type == "list":
            text = _flatten_list_items(block.get("items"))
            if text:
                result.append({"type": "list", "text": text})
            continue

        if block_type not in _TEXT_BLOCK_TYPES:
            continue

        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        if block_type == "quote":
            result.append(
                {
                    "type": "quote",
                    "speaker": block.get("speaker"),
                    "text": text,
                }
            )
            continue

        if block_type == "heading":
            result.append(
                {
                    "type": "heading",
                    "level": block.get("level", 2),
                    "text": text,
                }
            )
            continue

        # callout/caption/prompt all route to the main narrator; the existing
        # chunker only needs main-vs-quote routing.
        result.append({"type": "paragraph", "text": text})

    return result


def extract_worker_blocks(html: str) -> list[dict]:
    return canonical_blocks_to_worker_blocks(normalize_ghost_html(html))
