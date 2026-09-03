from narration_content.document import PROCESSOR_VERSION, build_document
from narration_content.normalizer import normalize_ghost_html

CONTENT_HASH = "a" * 64


def _texts(blocks):
    return [
        block["text"]
        for block in blocks
        if isinstance(block.get("text"), str)
    ]


def test_jadwiga_style_footer_suffix_is_trimmed_after_editorial_ending():
    html = """
    <p>Article body.</p>
    <p>Share this story with someone who needs a reminder to listen today.</p>
    <blockquote>Step back, turn to nature.<br>— Jadwiga</blockquote>
    <p>
      This is Jadwiga's story, told beautifully by her and curated in its
      truest form by me to share with you.
    </p>
    <p>I would love to hear your story. Write to me at preeti@gratefulness.me ✨</p>
    <p>Share this story</p>
    <p>Every story is a reminder that a grateful heart is a magnet for miracles.</p>
    """

    values = _texts(normalize_ghost_html(html))

    assert values[-1] == (
        "This is Jadwiga's story, told beautifully by her and curated "
        "in its truest form by me to share with you."
    )
    assert (
        "Share this story with someone who needs a reminder to listen today."
        in values
    )
    assert not any(value.startswith("I would love to hear your story.") for value in values)
    assert "Share this story" not in values
    assert not any(value.startswith("Every story is a reminder that ") for value in values)


def test_footer_like_wording_in_middle_is_preserved():
    html = """
    <p>I would love to hear your story. Write to me at editor@example.com.</p>
    <p>This is still article content after that sentence.</p>
    """

    assert _texts(normalize_ghost_html(html)) == [
        "I would love to hear your story. Write to me at editor@example.com.",
        "This is still article content after that sentence.",
    ]


def test_editorial_share_with_someone_is_preserved():
    html = "<p>Share this story with someone who needs hope today.</p>"
    assert _texts(normalize_ghost_html(html)) == [
        "Share this story with someone who needs hope today."
    ]


def test_processor_version_is_two_for_new_documents():
    assert PROCESSOR_VERSION == 2
    document = build_document(
        post_id="ghost-post",
        content_hash=CONTENT_HASH,
        blocks=[{"type": "paragraph", "text": "Article."}],
    )
    assert document["processor_version"] == 2
