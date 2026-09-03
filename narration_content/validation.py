"""Strict validation for Narration Document V1."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .document import (
    DOCUMENT_FIELDS_BY_TYPE,
    SCHEMA_VERSION,
    expected_role,
)
from .hashing import compute_narration_hash


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "post_id",
        "content_hash",
        "narration_hash",
        "processor_version",
        "blocks",
    }
)


class NarrationDocumentValidationError(ValueError):
    """Raised when a Narration Document violates the frozen V1 contract."""


def _fail(message: str) -> None:
    raise NarrationDocumentValidationError(message)


def _require_exact_fields(
    mapping: Mapping,
    expected: frozenset[str],
    *,
    context: str,
) -> None:
    actual = frozenset(mapping.keys())

    missing = expected - actual
    unknown = actual - expected

    if missing:
        _fail(
            f"{context} missing fields: "
            f"{', '.join(sorted(missing))}"
        )

    if unknown:
        _fail(
            f"{context} unknown fields: "
            f"{', '.join(sorted(unknown))}"
        )


def _require_text(value, *, field: str) -> None:
    if not isinstance(value, str):
        _fail(f"{field} must be a string")

    if not value.strip():
        _fail(f"{field} must not be blank")

    if value != value.strip():
        _fail(
            f"{field} must not contain leading or trailing whitespace"
        )


def _validate_block(block, index: int) -> None:
    context = f"blocks[{index - 1}]"

    if not isinstance(block, Mapping):
        _fail(f"{context} must be an object")

    block_type = block.get("type")

    if block_type not in DOCUMENT_FIELDS_BY_TYPE:
        _fail(
            f"{context}.type is unsupported: {block_type!r}"
        )

    _require_exact_fields(
        block,
        DOCUMENT_FIELDS_BY_TYPE[block_type],
        context=context,
    )

    expected_id = f"b{index:06d}"

    if block["block_id"] != expected_id:
        _fail(
            f"{context}.block_id must be {expected_id!r}"
        )

    required_role = expected_role(block_type)

    if block["role"] != required_role:
        _fail(
            f"{context}.role must be {required_role!r}"
        )

    if block_type == "heading":
        level = block["level"]

        if type(level) is not int or not 1 <= level <= 6:
            _fail(
                f"{context}.level must be an integer from 1 through 6"
            )

        _require_text(
            block["text"],
            field=f"{context}.text",
        )
        return

    if block_type == "quote":
        _require_text(
            block["text"],
            field=f"{context}.text",
        )

        speaker = block["speaker"]

        if speaker is not None:
            _require_text(
                speaker,
                field=f"{context}.speaker",
            )

        return

    if block_type == "list":
        ordered = block["ordered"]

        if type(ordered) is not bool:
            _fail(
                f"{context}.ordered must be boolean"
            )

        items = block["items"]

        if not isinstance(items, list):
            _fail(
                f"{context}.items must be an array"
            )

        if not items:
            _fail(
                f"{context}.items must not be empty"
            )

        for item_index, item in enumerate(items):
            _require_text(
                item,
                field=f"{context}.items[{item_index}]",
            )

        return

    _require_text(
        block["text"],
        field=f"{context}.text",
    )


def validate_document(
    document,
    *,
    verify_hash: bool = True,
) -> dict:
    """Validate the exact Narration Document V1 shape.

    Returns the original object on success so callers may use:

        document = validate_document(document)

    Validation does not mutate the document.
    """
    if not isinstance(document, Mapping):
        _fail("document must be an object")

    _require_exact_fields(
        document,
        _TOP_LEVEL_FIELDS,
        context="document",
    )

    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != SCHEMA_VERSION
    ):
        _fail(
            f"schema_version must be integer {SCHEMA_VERSION}"
        )

    post_id = document["post_id"]

    if not isinstance(post_id, str) or not post_id.strip():
        _fail("post_id must be a non-empty string")

    if post_id != post_id.strip():
        _fail(
            "post_id must not contain leading or trailing whitespace"
        )

    content_hash = document["content_hash"]

    if (
        not isinstance(content_hash, str)
        or not _HASH_RE.fullmatch(content_hash)
    ):
        _fail(
            "content_hash must be exactly 64 lowercase hexadecimal characters"
        )

    narration_hash = document["narration_hash"]

    if (
        not isinstance(narration_hash, str)
        or not _HASH_RE.fullmatch(narration_hash)
    ):
        _fail(
            "narration_hash must be exactly 64 lowercase hexadecimal characters"
        )

    processor_version = document["processor_version"]

    if (
        type(processor_version) is not int
        or processor_version < 1
    ):
        _fail(
            "processor_version must be a positive integer"
        )

    blocks = document["blocks"]

    if not isinstance(blocks, list):
        _fail("blocks must be an array")

    for index, block in enumerate(blocks, start=1):
        _validate_block(block, index)

    if verify_hash:
        expected = compute_narration_hash(document)

        if narration_hash != expected:
            _fail(
                "narration_hash does not match canonical semantic content"
            )

    return document