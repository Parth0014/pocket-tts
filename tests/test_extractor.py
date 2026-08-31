from extractor import extract_blocks


def test_different_subject_and_attribution_styles_are_generic():
    html = """
    <p>Joane always took her time before answering anything.</p>
    <blockquote><p>"I needed to sit with the question before I could sit with the answer."<br>- Joane</p></blockquote>
    <p>She carried that habit into everything she did.</p>
    <blockquote><p>"Rushing never got me anywhere true."<br>~ Joane</p></blockquote>
    """

    blocks = extract_blocks(html)

    assert [block["type"] for block in blocks] == [
        "paragraph",
        "quote",
        "paragraph",
        "quote",
    ]
    assert [block["speaker"] for block in blocks if block["type"] == "quote"] == [
        "Joane",
        "Joane",
    ]
    assert "Rojandree" not in " ".join(block["text"] for block in blocks)


def test_quote_without_attribution_does_not_invent_a_speaker():
    blocks = extract_blocks(
        '<blockquote><p>"Just a floating quote with no name attached."</p></blockquote>'
    )

    assert blocks == [
        {
            "type": "quote",
            "speaker": None,
            "text": "Just a floating quote with no name attached.",
        }
    ]


def test_style_script_and_player_ui_do_not_leak():
    html = """
    <style>
      .g-audio-wrap { --bg: #fdf9f3; width: 100%; margin: 32px auto; }
      .g-audio-play:hover { background: var(--accent-hover); }
    </style>
    <div class="g-audio-wrap">
      <div class="g-audio-label">Listen to this story</div>
    </div>
    <script>
    (function () {
      const root = document.currentScript.previousElementSibling;
      root.addEventListener("click", function () { audio.play(); });
    })();
    </script>
    <p>Rojandree's life took a sudden turn.</p>
    """

    blocks = extract_blocks(html)

    assert blocks == [
        {"type": "paragraph", "text": "Rojandree's life took a sudden turn."}
    ]


def test_nested_narration_elements_are_emitted_once():
    html = """
    <ul>
      <li>
        Parent item
        <h3>Nested heading</h3>
        <blockquote><p>Nested quote</p></blockquote>
        <ul><li>Child item</li></ul>
      </li>
    </ul>
    """

    blocks = extract_blocks(html)

    assert blocks == [
        {
            "type": "list",
            "text": "Parent item Nested heading Nested quote. Child item",
        }
    ]
    full_text = " ".join(block["text"] for block in blocks)
    assert full_text.count("Nested heading") == 1
    assert full_text.count("Nested quote") == 1
    assert full_text.count("Child item") == 1


def test_multiple_blockquote_paragraphs_keep_word_boundaries():
    blocks = extract_blocks(
        "<blockquote><p>First paragraph.</p><p>Second paragraph.</p></blockquote>"
    )

    assert blocks == [
        {
            "type": "quote",
            "speaker": None,
            "text": "First paragraph. Second paragraph.",
        }
    ]


def test_unicode_attribution_is_extracted_from_its_own_line():
    blocks = extract_blocks(
        "<blockquote><p>\u201cStay curious.\u201d<br>\u2014 \u00c9lodie O\u2019Neill-Smith</p></blockquote>"
    )

    assert blocks == [
        {
            "type": "quote",
            "speaker": "\u00c9lodie O\u2019Neill-Smith",
            "text": "Stay curious.",
        }
    ]


def test_inline_dash_text_is_not_mistaken_for_an_attribution():
    blocks = extract_blocks(
        "<blockquote><p>Choose courage \u2014 Always</p></blockquote>"
    )

    assert blocks == [
        {
            "type": "quote",
            "speaker": None,
            "text": "Choose courage \u2014 Always",
        }
    ]


def test_unknown_ghost_cards_are_silent_but_structural_wrappers_are_transparent():
    html = """
    <section class="story-section">
      <div class="layout-wrapper"><p>Keep this narration.</p></div>
      <div class="kg-card kg-future-card"><p>Do not narrate card UI.</p></div>
    </section>
    """

    assert extract_blocks(html) == [
        {"type": "paragraph", "text": "Keep this narration."}
    ]
