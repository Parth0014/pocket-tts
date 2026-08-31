import re

from chunking import build_chunks_from_blocks, generation_settings_for


def _raises(expected, callback):
    try:
        callback()
    except expected as exc:
        return exc
    except Exception as exc:  # pragma: no cover - failure-reporting branch
        raise AssertionError(
            f"expected {expected.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"expected {expected.__name__} to be raised")


def _word_count(text):
    return len(text.split())


def test_chunks_preserve_order_budget_and_block_metadata():
    blocks = [
        {
            "block_type": "paragraph",
            "text": "Alpha beta gamma. Delta epsilon.",
            "speaker": None,
        },
        {
            "block_type": "quote",
            "text": "Zeta eta theta iota.",
            "speaker": "Ada",
        },
        {"block_type": "heading", "text": "   ", "speaker": None},
        {"block_type": "list", "text": "Kappa lambda.", "speaker": None},
    ]

    chunks = build_chunks_from_blocks(_word_count, blocks, budget=3)

    assert [chunk["text"] for chunk in chunks] == [
        "Alpha beta gamma.",
        "Delta epsilon.",
        "Zeta eta theta",
        "iota.",
        "Kappa lambda.",
    ]
    assert [chunk["block_index"] for chunk in chunks] == [0, 0, 1, 1, 3]
    assert [chunk["block_type"] for chunk in chunks] == [
        "paragraph",
        "paragraph",
        "quote",
        "quote",
        "list",
    ]
    assert [chunk["speaker"] for chunk in chunks] == [
        None,
        None,
        "Ada",
        "Ada",
        None,
    ]
    assert [chunk["paragraph_end"] for chunk in chunks] == [
        False,
        True,
        False,
        True,
        True,
    ]
    assert all(_word_count(chunk["text"]) <= 3 for chunk in chunks)

    # Joining chunks from each block recovers its normalized text exactly.
    for block_index in (0, 1, 3):
        rebuilt = " ".join(
            chunk["text"]
            for chunk in chunks
            if chunk["block_index"] == block_index
        )
        expected = re.sub(r"\s+", " ", blocks[block_index]["text"].strip())
        assert rebuilt == expected


def test_oversized_sentences_split_at_clause_then_word_boundaries():
    blocks = [
        {
            "block_type": "paragraph",
            "text": "Alpha beta, gamma delta; epsilon zeta eta.",
            "speaker": None,
        }
    ]

    chunks = build_chunks_from_blocks(_word_count, blocks, budget=2)

    assert [chunk["text"] for chunk in chunks] == [
        "Alpha beta,",
        "gamma delta;",
        "epsilon zeta",
        "eta.",
    ]
    assert all(_word_count(chunk["text"]) <= 2 for chunk in chunks)
    assert [chunk["paragraph_end"] for chunk in chunks] == [
        False,
        False,
        False,
        True,
    ]


def test_indivisible_oversized_token_fails_instead_of_breaking_budget():
    blocks = [
        {
            "block_type": "paragraph",
            "text": "ok abcdefgh",
            "speaker": None,
        }
    ]

    error = _raises(
        ValueError,
        lambda: build_chunks_from_blocks(len, blocks, budget=4),
    )

    assert "single whitespace-delimited text unit" in str(error)
    assert "abcdefgh" in str(error)
    assert "budget=4" in str(error)


def test_invalid_budgets_and_metadata_fail_early():
    block = {"block_type": "paragraph", "text": "Hello.", "speaker": None}
    for budget in (0, -1, True, 3.5):
        error = _raises(
            ValueError,
            lambda budget=budget: build_chunks_from_blocks(
                _word_count, [block], budget=budget
            ),
        )
        assert "positive integer" in str(error)

    error = _raises(
        ValueError,
        lambda: build_chunks_from_blocks(
            _word_count,
            [{"block_type": "audio", "text": "Leak", "speaker": None}],
            budget=3,
        ),
    )
    assert "invalid block_type" in str(error)


def test_generation_settings_distinguish_quotes_from_narration():
    quote = generation_settings_for({"block_type": "quote"})
    assert quote == {
        "role": "quote",
        "extra_lead_pause_ms": 250,
        "extra_trail_pause_ms": 250,
    }

    expected_narration = {
        "role": "narration",
        "extra_lead_pause_ms": 0,
        "extra_trail_pause_ms": 0,
    }
    for block_type in ("paragraph", "heading", "list"):
        assert generation_settings_for({"block_type": block_type}) == expected_narration

    # A caller cannot corrupt later records by mutating a returned settings dict.
    quote["role"] = "changed"
    assert generation_settings_for({"block_type": "quote"})["role"] == "quote"


def test_generation_settings_reject_missing_or_unknown_block_types():
    for record in ({}, {"block_type": "audio"}):
        error = _raises(ValueError, lambda record=record: generation_settings_for(record))
        assert "Invalid block_type" in str(error)
