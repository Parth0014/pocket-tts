import re

from chunking import _split_sentences, build_chunks_from_blocks, generation_settings_for


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


def test_sentence_boundaries_preserve_titles_initials_and_acronyms():
    assert _split_sentences(
        "Dr. A. B. Smith visited the U.S. Navy. He met Prof. Jones at 3.14 p.m. Then left."
    ) == [
        "Dr. A. B. Smith visited the U.S. Navy.",
        "He met Prof. Jones at 3.14 p.m.",
        "Then left.",
    ]
    assert _split_sentences("Bring supplies, e.g. water, etc. They matter.") == [
        "Bring supplies, e.g. water, etc.",
        "They matter.",
    ]


def test_sentence_boundaries_keep_closing_quotes_and_dialogue_attribution():
    assert _split_sentences('\u201cReally?\u201d she asked. \u201cYes!\u201d He smiled.') == [
        '\u201cReally?\u201d she asked.', '\u201cYes!\u201d', 'He smiled.',
    ]
    assert _split_sentences('He said "Go." Next came silence. (It lasted!) Then rain.') == [
        'He said "Go."', 'Next came silence.', '(It lasted!)', 'Then rain.',
    ]


def test_sentence_boundaries_preserve_ellipses_and_normalize_only_whitespace():
    text = "  He paused...\nthen continued.\tWait\u2026 what? I thought . . . no. Gone... Forever. "
    sentences = _split_sentences(text)
    assert sentences == [
        "He paused... then continued.", "Wait\u2026 what?", "I thought . . . no.", "Gone...", "Forever.",
    ]
    assert " ".join(sentences) == re.sub(r"\s+", " ", text.strip())
    assert _split_sentences(" \n\t ") == []
    assert _split_sentences("A final thought without punctuation") == ["A final thought without punctuation"]


def test_chunking_keeps_a_title_with_its_name_when_the_whole_sentence_fits():
    text = "We arrived. Dr. Smith waited."
    chunks = build_chunks_from_blocks(
        _word_count, [{"block_type": "paragraph", "text": text}], budget=3
    )
    assert [chunk["text"] for chunk in chunks] == ["We arrived.", "Dr. Smith waited."]
    assert [chunk["paragraph_end"] for chunk in chunks] == [False, True]


def test_quoted_sentences_split_at_sentence_endings_before_word_fallback():
    text = '\u201cWe must leave.\u201d Tomorrow will be better.'
    chunks = build_chunks_from_blocks(
        _word_count, [{"block_type": "quote", "text": text, "speaker": "Ada"}], budget=4
    )
    assert [chunk["text"] for chunk in chunks] == ['\u201cWe must leave.\u201d', "Tomorrow will be better."]
    assert all(chunk["speaker"] == "Ada" for chunk in chunks)


def test_abbreviation_heuristics_never_relax_budgets_or_drop_text():
    text = 'Dr. A. B. Smith\tjoined the U.S. Navy. \u201cWait... really?\u201d she asked. It cost 3.14 dollars.'
    for budget in (1, 2, 3, 5, 10, 50):
        chunks = build_chunks_from_blocks(
            _word_count, [{"block_type": "paragraph", "text": text}], budget=budget
        )
        assert all(_word_count(chunk["text"]) <= budget for chunk in chunks)
        assert " ".join(chunk["text"] for chunk in chunks) == re.sub(r"\s+", " ", text.strip())
        assert sum(chunk["paragraph_end"] for chunk in chunks) == 1


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
