"""Deterministic hashing for Ghost source and narration semantics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .document import SCHEMA_VERSION


_HASH_FIELDS_BY_TYPE = {
    "paragraph": ("type", "role", "text"),
    "heading": ("type", "role", "level", "text"),
    "quote": ("type", "role", "text", "speaker"),
    "list": ("type", "role", "ordered", "items"),
    "callout": ("type", "role", "text"),
    "caption": ("type", "role", "text"),
    "prompt": ("type", "role", "text"),
}


def canonical_json_bytes(value) -> bytes:
    """Serialize a value using the frozen Narration Document hash rules."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return encoded.encode("utf-8")


def compute_content_hash(html: str) -> str:
    """SHA-256 of the exact UTF-8 Ghost post.html string."""
    if not isinstance(html, str):
        raise TypeError(
            f"html must be str, got {type(html).__name__}"
        )

    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _semantic_block(block: Mapping, index: int) -> dict:
    """Project one document block onto narration-semantic hash fields."""
    if not isinstance(block, Mapping):
        raise TypeError(
            f"block {index} must be a mapping, got "
            f"{type(block).__name__}"
        )

    block_type = block.get("type")

    if block_type not in _HASH_FIELDS_BY_TYPE:
        raise ValueError(
            f"block {index} has unsupported type: {block_type!r}"
        )

    fields = _HASH_FIELDS_BY_TYPE[block_type]

    missing = [
        field
        for field in fields
        if field not in block
    ]

    if missing:
        raise ValueError(
            f"block {index} is missing semantic fields: "
            f"{', '.join(missing)}"
        )

    return {
        field: block[field]
        for field in fields
    }


def semantic_hash_payload(document: Mapping) -> dict:
    """Return the exact semantic object used for narration_hash.

    Deliberately excluded:

    - block_id
    - post_id
    - content_hash
    - narration_hash
    - processor_version
    - storage metadata
    - timestamps
    """
    if not isinstance(document, Mapping):
        raise TypeError(
            "document must be a mapping"
        )

    schema_version = document.get("schema_version")

    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {SCHEMA_VERSION}"
        )

    blocks = document.get("blocks")

    if not isinstance(blocks, list):
        raise TypeError("document.blocks must be a list")

    return {
        "schema_version": SCHEMA_VERSION,
        "blocks": [
            _semantic_block(block, index)
            for index, block in enumerate(blocks, start=1)
        ],
    }


def compute_narration_hash(document: Mapping) -> str:
    """Compute SHA-256 over canonical narration-semantic JSON."""
    payload = semantic_hash_payload(document)

    return hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()