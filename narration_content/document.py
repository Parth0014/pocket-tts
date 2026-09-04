"""Build canonical Narration Document V1 objects.

This module does not parse Ghost HTML.

The future Ghost normalizer produces semantic input blocks, and this
module turns those blocks into the strict persisted document contract by:

- assigning deterministic block IDs;
- assigning canonical roles;
- computing narration_hash;
- validating the completed document.

Generation policy such as quote_mode and voice selection does not belong
in this document.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy

SCHEMA_VERSION = 1
PROCESSOR_VERSION = 4


INPUT_FIELDS_BY_TYPE = {
    "paragraph": frozenset({"type", "text"}),
    "heading": frozenset({"type", "level", "text"}),
    "quote": frozenset({"type", "text", "speaker"}),
    "list": frozenset({"type", "ordered", "items"}),
    "callout": frozenset({"type", "text"}),
    "caption": frozenset({"type", "text"}),
    "prompt": frozenset({"type", "text"}),
}


DOCUMENT_FIELDS_BY_TYPE = {
    "paragraph": frozenset({"block_id", "type", "role", "text"}),
    "heading": frozenset(
        {"block_id", "type", "role", "level", "text"}
    ),
    "quote": frozenset(
        {"block_id", "type", "role", "text", "speaker"}
    ),
    "list": frozenset(
        {"block_id", "type", "role", "ordered", "items"}
    ),
    "callout": frozenset({"block_id", "type", "role", "text"}),
    "caption": frozenset({"block_id", "type", "role", "text"}),
    "prompt": frozenset({"block_id", "type", "role", "text"}),
}


def expected_role(block_type: str) -> str:
    """Return the only valid V1 role for a block type."""
    return "quote" if block_type == "quote" else "narration"


def _prepare_block(block: Mapping, index: int) -> dict:
    """Convert one semantic normalizer block into document shape."""
    if not isinstance(block, Mapping):
        raise TypeError(
            f"block {index} must be a mapping, got "
            f"{type(block).__name__}"
        )

    block_type = block.get("type")

    if block_type not in INPUT_FIELDS_BY_TYPE:
        raise ValueError(
            f"block {index} has unsupported type: {block_type!r}"
        )

    expected = INPUT_FIELDS_BY_TYPE[block_type]
    actual = frozenset(block.keys())

    missing = expected - actual
    unknown = actual - expected

    if missing:
        raise ValueError(
            f"block {index} is missing fields: "
            f"{', '.join(sorted(missing))}"
        )

    if unknown:
        raise ValueError(
            f"block {index} has unknown fields: "
            f"{', '.join(sorted(unknown))}"
        )

    prepared = deepcopy(dict(block))
    prepared["block_id"] = f"b{index:06d}"
    prepared["role"] = expected_role(block_type)

    # Rebuild in a predictable human-readable order. JSON canonicalization
    # does not rely on insertion order, but stable display is useful.
    if block_type == "heading":
        return {
            "block_id": prepared["block_id"],
            "type": block_type,
            "role": prepared["role"],
            "level": prepared["level"],
            "text": prepared["text"],
        }

    if block_type == "quote":
        return {
            "block_id": prepared["block_id"],
            "type": block_type,
            "role": prepared["role"],
            "text": prepared["text"],
            "speaker": prepared["speaker"],
        }

    if block_type == "list":
        return {
            "block_id": prepared["block_id"],
            "type": block_type,
            "role": prepared["role"],
            "ordered": prepared["ordered"],
            "items": deepcopy(prepared["items"]),
        }

    return {
        "block_id": prepared["block_id"],
        "type": block_type,
        "role": prepared["role"],
        "text": prepared["text"],
    }


def build_document(
    *,
    post_id: str,
    content_hash: str,
    blocks: Iterable[Mapping],
    processor_version: int = PROCESSOR_VERSION,
) -> dict:
    """Build and validate one Narration Document V1.

    ``blocks`` are semantic normalizer blocks without block_id or role.
    This function owns those derived fields so callers cannot create
    conflicting identities or roles.
    """
    numbered_blocks = [
        _prepare_block(block, index)
        for index, block in enumerate(blocks, start=1)
    ]

    document = {
        "schema_version": SCHEMA_VERSION,
        "post_id": post_id,
        "content_hash": content_hash,
        "narration_hash": "",
        "processor_version": processor_version,
        "blocks": numbered_blocks,
    }

    # Local imports avoid a module-level document <-> hashing dependency.
    from .hashing import compute_narration_hash
    from .validation import validate_document

    document["narration_hash"] = compute_narration_hash(document)

    validate_document(document)

    return document
