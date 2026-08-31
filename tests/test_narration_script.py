from copy import deepcopy

from narration_script import build_narration_blocks


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


def _mixed_blocks():
    return [
        {"type": "heading", "level": 2, "text": "A beginning"},
        {"type": "paragraph", "text": "The story opens."},
        {"type": "quote", "speaker": "Ada", "text": "First thought."},
        {"type": "list", "text": "One. Two."},
        {"type": "quote", "speaker": None, "text": "No attribution."},
        {"type": "quote", "speaker": "Grace", "text": "Second thought."},
        {"type": "embed", "text": "This unsupported card stays silent."},
        {"type": "paragraph", "text": "   "},
    ]


def test_preserve_keeps_order_metadata_and_deterministic_lead_ins():
    source = _mixed_blocks()
    untouched = deepcopy(source)

    result = build_narration_blocks(source, quote_mode="preserve")

    assert result == [
        {"block_type": "heading", "text": "A beginning", "speaker": None},
        {
            "block_type": "paragraph",
            "text": "The story opens.",
            "speaker": None,
        },
        {
            "block_type": "quote",
            "text": 'Ada says, "First thought."',
            "speaker": "Ada",
        },
        {"block_type": "list", "text": "One. Two.", "speaker": None},
        {
            "block_type": "quote",
            "text": "No attribution.",
            "speaker": None,
        },
        {
            "block_type": "quote",
            "text": 'Grace shares, "Second thought."',
            "speaker": "Grace",
        },
    ]
    assert source == untouched


def test_exclude_removes_quotes_without_disturbing_other_blocks():
    result = build_narration_blocks(_mixed_blocks(), quote_mode="exclude")

    assert [block["block_type"] for block in result] == [
        "heading",
        "paragraph",
        "list",
    ]
    assert [block["text"] for block in result] == [
        "A beginning",
        "The story opens.",
        "One. Two.",
    ]
    assert build_narration_blocks(
        [{"type": "quote", "text": "Only a quote.", "speaker": None}],
        quote_mode="exclude",
    ) == []


def test_two_voice_has_same_script_shape_as_preserve():
    blocks = _mixed_blocks()

    preserve = build_narration_blocks(blocks, quote_mode="preserve")
    two_voice = build_narration_blocks(blocks, quote_mode="two_voice")

    # Voice selection happens later in the generator. The script must not
    # invent a fake block type to imply that a voice changed here.
    assert two_voice == preserve
    assert {block["block_type"] for block in two_voice} <= {
        "paragraph",
        "heading",
        "list",
        "quote",
    }


def test_invalid_quote_modes_fail_clearly():
    for invalid in ("", "two-voice", "PRESERVE", None):
        error = _raises(
            ValueError,
            lambda invalid=invalid: build_narration_blocks(
                _mixed_blocks(), quote_mode=invalid
            ),
        )
        assert "Unknown quote_mode" in str(error)
        assert "two_voice" in str(error)


def test_blank_and_malformed_text_is_not_sent_to_tts():
    blocks = [
        {"type": "paragraph", "text": ""},
        {"type": "heading", "text": None},
        {"type": "quote", "text": "\n\t", "speaker": "Ada"},
        {"type": "list", "text": "Kept"},
    ]

    assert build_narration_blocks(blocks) == [
        {"block_type": "list", "text": "Kept", "speaker": None}
    ]
