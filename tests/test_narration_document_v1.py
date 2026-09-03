from copy import deepcopy

import pytest

from narration_content import (
    NarrationDocumentValidationError,
    build_document,
    canonical_json_bytes,
    compute_content_hash,
    compute_narration_hash,
    semantic_hash_payload,
    validate_document,
)


CONTENT_HASH = "a" * 64


def _semantic_blocks():
    return [
        {
            "type": "heading",
            "level": 3,
            "text": "A New Beginning",
        },
        {
            "type": "paragraph",
            "text": "The story begins here.",
        },
        {
            "type": "quote",
            "text": "Stay curious.",
            "speaker": "Élodie O’Neill-Smith",
        },
        {
            "type": "list",
            "ordered": True,
            "items": [
                "First practice",
                "Second practice",
            ],
        },
        {
            "type": "callout",
            "text": "This idea deserves attention.",
        },
        {
            "type": "caption",
            "text": "A meaningful editorial caption.",
        },
        {
            "type": "prompt",
            "text": "What are you grateful for today?",
        },
    ]


def _document(**overrides):
    values = {
        "post_id": "ghost-post-123",
        "content_hash": CONTENT_HASH,
        "blocks": _semantic_blocks(),
    }
    values.update(overrides)
    return build_document(**values)


def test_content_hash_is_sha256_of_exact_utf8_html():
    html = "<p>Gratitude — café 🌱</p>"

    assert compute_content_hash(html) == (
        "43f46fe7d5ab23134de547c0e7a27e7a20ca511e92370460"
        "7bd32cad4ca2eb50"
    )


def test_build_document_assigns_deterministic_ids_roles_and_shapes():
    source = _semantic_blocks()
    untouched = deepcopy(source)

    document = build_document(
        post_id="ghost-post-123",
        content_hash=CONTENT_HASH,
        blocks=source,
    )

    assert document["schema_version"] == 1
    assert document["post_id"] == "ghost-post-123"
    assert document["content_hash"] == CONTENT_HASH
    assert document["processor_version"] == 1

    assert [
        block["block_id"]
        for block in document["blocks"]
    ] == [
        "b000001",
        "b000002",
        "b000003",
        "b000004",
        "b000005",
        "b000006",
        "b000007",
    ]

    assert [
        block["role"]
        for block in document["blocks"]
    ] == [
        "narration",
        "narration",
        "quote",
        "narration",
        "narration",
        "narration",
        "narration",
    ]

    assert document["blocks"][3] == {
        "block_id": "b000004",
        "type": "list",
        "role": "narration",
        "ordered": True,
        "items": [
            "First practice",
            "Second practice",
        ],
    }

    assert source == untouched

    validate_document(document)


def test_narration_hash_is_deterministic():
    first = _document()
    second = _document()

    assert first["narration_hash"] == second["narration_hash"]
    assert len(first["narration_hash"]) == 64


def test_hash_payload_contains_only_schema_and_semantic_block_fields():
    document = _document()

    payload = semantic_hash_payload(document)

    assert set(payload) == {
        "schema_version",
        "blocks",
    }

    assert "block_id" not in payload["blocks"][0]
    assert "post_id" not in payload
    assert "content_hash" not in payload
    assert "processor_version" not in payload
    assert "narration_hash" not in payload


def test_nonsemantic_envelope_changes_do_not_change_narration_hash():
    first = _document(
        post_id="post-one",
        content_hash="a" * 64,
        processor_version=1,
    )

    second = _document(
        post_id="post-two",
        content_hash="b" * 64,
        processor_version=99,
    )

    assert first["narration_hash"] == second["narration_hash"]


def test_block_ids_are_not_part_of_hash_basis():
    document = _document()

    changed = deepcopy(document)

    for index, block in enumerate(
        changed["blocks"],
        start=101,
    ):
        block["block_id"] = f"temporary-{index}"

    assert (
        compute_narration_hash(document)
        == compute_narration_hash(changed)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["blocks"][0].__setitem__(
            "level",
            4,
        ),
        lambda doc: doc["blocks"][1].__setitem__(
            "text",
            "Different paragraph.",
        ),
        lambda doc: doc["blocks"][2].__setitem__(
            "speaker",
            "Another Speaker",
        ),
        lambda doc: doc["blocks"][3].__setitem__(
            "ordered",
            False,
        ),
        lambda doc: doc["blocks"][3]["items"].append(
            "Third practice"
        ),
        lambda doc: doc["blocks"].__setitem__(
            slice(0, 2),
            list(reversed(doc["blocks"][0:2])),
        ),
    ],
)
def test_semantic_changes_change_narration_hash(mutate):
    original = _document()
    changed = deepcopy(original)

    mutate(changed)

    assert (
        compute_narration_hash(changed)
        != original["narration_hash"]
    )


def test_canonical_json_preserves_unicode_as_utf8():
    encoded = canonical_json_bytes(
        {
            "speaker": "Élodie",
            "text": "Gratitude 🌱",
        }
    )

    assert "Élodie".encode("utf-8") in encoded
    assert "🌱".encode("utf-8") in encoded
    assert b"\\u00c9" not in encoded


def test_empty_block_array_is_valid_document():
    document = build_document(
        post_id="empty-post",
        content_hash=CONTENT_HASH,
        blocks=[],
    )

    assert document["blocks"] == []

    validate_document(document)


def test_unknown_semantic_block_type_is_rejected():
    with pytest.raises(
        ValueError,
        match="unsupported type",
    ):
        build_document(
            post_id="ghost-post",
            content_hash=CONTENT_HASH,
            blocks=[
                {
                    "type": "embed",
                    "text": "Should not exist",
                }
            ],
        )


def test_builder_rejects_unknown_input_fields():
    with pytest.raises(
        ValueError,
        match="unknown fields",
    ):
        build_document(
            post_id="ghost-post",
            content_hash=CONTENT_HASH,
            blocks=[
                {
                    "type": "paragraph",
                    "text": "Hello.",
                    "url": "https://example.com",
                }
            ],
        )


def test_validation_rejects_unknown_top_level_field():
    document = _document()
    document["created_at"] = "2026-09-03T00:00:00Z"

    with pytest.raises(
        NarrationDocumentValidationError,
        match="unknown fields",
    ):
        validate_document(document)


def test_validation_rejects_unknown_block_field():
    document = _document()
    document["blocks"][0]["html"] = "<h3>Bad</h3>"

    with pytest.raises(
        NarrationDocumentValidationError,
        match="unknown fields",
    ):
        validate_document(document)


def test_validation_rejects_wrong_role():
    document = _document()
    document["blocks"][2]["role"] = "narration"

    document["narration_hash"] = compute_narration_hash(
        document
    )

    with pytest.raises(
        NarrationDocumentValidationError,
        match="role must be 'quote'",
    ):
        validate_document(document)


def test_validation_rejects_nonsequential_block_ids():
    document = _document()
    document["blocks"][1]["block_id"] = "b999999"

    with pytest.raises(
        NarrationDocumentValidationError,
        match="block_id must be 'b000002'",
    ):
        validate_document(document)


def test_validation_rejects_bad_hash_format():
    document = _document()
    document["content_hash"] = "ABC123"

    with pytest.raises(
        NarrationDocumentValidationError,
        match="content_hash",
    ):
        validate_document(document)


def test_validation_rejects_hash_mismatch():
    document = _document()

    document["blocks"][1]["text"] = (
        "The semantic content changed."
    )

    with pytest.raises(
        NarrationDocumentValidationError,
        match="does not match",
    ):
        validate_document(document)


def test_list_requires_boolean_ordered_field():
    document = _document()
    document["blocks"][3]["ordered"] = 1
    document["narration_hash"] = compute_narration_hash(
        document
    )

    with pytest.raises(
        NarrationDocumentValidationError,
        match="ordered must be boolean",
    ):
        validate_document(document)


def test_heading_supports_h1_through_h6_only():
    valid = build_document(
        post_id="heading-test",
        content_hash=CONTENT_HASH,
        blocks=[
            {
                "type": "heading",
                "level": 6,
                "text": "Deep heading",
            }
        ],
    )

    validate_document(valid)

    invalid = deepcopy(valid)
    invalid["blocks"][0]["level"] = 7
    invalid["narration_hash"] = compute_narration_hash(
        invalid
    )

    with pytest.raises(
        NarrationDocumentValidationError,
        match="1 through 6",
    ):
        validate_document(invalid)


def test_quote_speaker_may_be_null():
    document = build_document(
        post_id="quote-test",
        content_hash=CONTENT_HASH,
        blocks=[
            {
                "type": "quote",
                "text": "A floating quote.",
                "speaker": None,
            }
        ],
    )

    assert document["blocks"][0]["speaker"] is None
    validate_document(document)