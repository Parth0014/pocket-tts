from worker_document import (
    canonical_blocks_to_worker_blocks,
    extract_worker_blocks,
)


def test_worker_uses_canonical_footer_and_emoji_policy():
    html = """
    <p>Article body \U0001f90d</p>
    <blockquote>
      <p>Listen to yourself. \u2728</p>
      <p>\u2014 Jadwiga</p>
    </blockquote>
    <p>
      This is Jadwiga's story, told beautifully by her and curated in its
      truest form by me to share with you.
    </p>
    <p>I would love to hear your story. Write to me at preeti@example.com \u2728</p>
    <p>Share this story</p>
    <p>Every story is a reminder that a grateful heart is a magnet for miracles.</p>
    """

    blocks = extract_worker_blocks(html)
    assert blocks[-1]["text"] == (
        "This is Jadwiga's story, told beautifully by her and curated "
        "in its truest form by me to share with you."
    )

    joined = " ".join(block["text"] for block in blocks)
    assert "I would love to hear your story" not in joined
    assert "Every story is a reminder" not in joined
    assert "\U0001f90d" not in joined
    assert "\u2728" not in joined

    quotes = [block for block in blocks if block["type"] == "quote"]
    assert len(quotes) == 1
    assert quotes[0]["speaker"] == "Jadwiga"
    assert quotes[0]["text"] == "Listen to yourself."


def test_worker_adapter_flattens_lists_and_narrates_supported_cards():
    blocks = canonical_blocks_to_worker_blocks(
        [
            {"type": "list", "ordered": False, "items": ["First", "Second"]},
            {"type": "callout", "text": "A useful callout."},
            {"type": "caption", "text": "A substantive caption."},
            {"type": "prompt", "text": "What matters today?"},
        ]
    )

    assert blocks == [
        {"type": "list", "text": "First. Second"},
        {"type": "paragraph", "text": "A useful callout."},
        {"type": "paragraph", "text": "A substantive caption."},
        {"type": "paragraph", "text": "What matters today?"},
    ]
