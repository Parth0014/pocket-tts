from narration_content.document import PROCESSOR_VERSION, build_document
from narration_content.normalizer import normalize_ghost_html


def texts(html):
    return [
        block["text"]
        for block in normalize_ghost_html(html)
        if isinstance(block.get("text"), str)
    ]


def test_processor_v3_strips_decorative_emoji_from_narration_text():
    values = texts(
        """
        <p>Listen to your body today \U0001f90d</p>
        <p>Keep going \u2728 and stay grounded.</p>
        <p>Grateful \U0001f64f\U0001f3fd for this moment.</p>
        """
    )

    assert values == [
        "Listen to your body today",
        "Keep going and stay grounded.",
        "Grateful for this moment.",
    ]


def test_emoji_only_paragraph_becomes_silent():
    assert normalize_ghost_html("<p>\U0001f90d \u2728 \U0001f64f\U0001f3fd</p>") == []


def test_punctuation_and_em_dash_are_not_removed():
    assert texts("<p>Hope \u2014 patience, courage &amp; gratitude.</p>") == [
        "Hope \u2014 patience, courage & gratitude."
    ]


def test_new_documents_use_processor_v3():
    assert PROCESSOR_VERSION == 3
    document = build_document(
        post_id="post",
        content_hash="a" * 64,
        blocks=[{"type": "paragraph", "text": "Gratitude."}],
    )
    assert document["processor_version"] == 3
