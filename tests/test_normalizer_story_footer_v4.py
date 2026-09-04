from narration_content.normalizer import normalize_ghost_html


def _texts(html: str) -> list[str]:
    return [
        str(block.get("text", ""))
        for block in normalize_ghost_html(html)
        if block.get("text")
    ]


def test_v4_cuts_cta_from_same_paragraph_after_truest_form_sentence():
    html = """
    <p>Megha found gratitude through difficult moments.</p>
    <p>This is Megha’s story, shared in her own words and curated with care to
    preserve its truest form. I’d love to hear your story too. Write to me at
    preeti@gratefulness.me</p>
    <p>Share this story</p>
    <p>Every story is a reminder to notice the good that already exists.</p>
    """

    texts = _texts(html)

    assert texts[-1] == (
        "This is Megha’s story, shared in her own words and curated with care "
        "to preserve its truest form."
    )
    assert "preeti@gratefulness.me" not in " ".join(texts)
    assert "Share this story" not in " ".join(texts)
    assert "Every story is a reminder" not in " ".join(texts)


def test_v4_cuts_joane_same_paragraph_cta_and_later_footer_blocks():
    html = """
    <p>Joane learned to make time to be in the moment.</p>
    <p>This is Joane’s story, shared in her own words and curated with care to
    preserve its truest form. I’d love to hear your story too. Write to me at
    preeti@gratefulness.me</p>
    <p>Share this story</p>
    <p>Every story is a reminder to notice the good that already exists.</p>
    """

    texts = _texts(html)

    assert texts[-1].endswith("preserve its truest form.")
    assert "I’d love to hear" not in " ".join(texts)
    assert "preeti@gratefulness.me" not in " ".join(texts)


def test_v4_preserves_full_jadwiga_editorial_sentence():
    html = """
    <p>Step back, turn to nature, quiet yourself down.</p>
    <p>This is Jadwiga's story, told beautifully by her and curated in its
    truest form by me to share with you.</p>
    <p>I would love to hear your story. Write to me at preeti@gratefulness.me</p>
    <p>Share this story</p>
    """

    texts = _texts(html)

    assert texts[-1] == (
        "This is Jadwiga's story, told beautifully by her and curated in its "
        "truest form by me to share with you."
    )


def test_v4_does_not_trim_post_without_story_provenance_boundary():
    html = """
    <p>A normal article paragraph.</p>
    <p>A final editorial paragraph without the provenance template.</p>
    """

    texts = _texts(html)

    assert texts == [
        "A normal article paragraph.",
        "A final editorial paragraph without the provenance template.",
    ]
