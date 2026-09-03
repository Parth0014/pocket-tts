import pytest

from narration_content.document import build_document
from narration_content.normalizer import normalize_ghost_html


CONTENT_HASH = "a" * 64


def test_paragraphs_and_h1_through_h6_preserve_document_order():
    html = """
    <h1>Opening</h1>
    <p>First paragraph.</p>
    <h6>Deep heading</h6>
    <p>Last paragraph.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "heading",
            "level": 1,
            "text": "Opening",
        },
        {
            "type": "paragraph",
            "text": "First paragraph.",
        },
        {
            "type": "heading",
            "level": 6,
            "text": "Deep heading",
        },
        {
            "type": "paragraph",
            "text": "Last paragraph.",
        },
    ]


def test_inline_formatting_preserves_visible_text_without_html():
    html = """
    <p>
        Gratitude <strong>changes</strong>
        the <em>ordinary</em>.
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Gratitude changes the ordinary.",
        }
    ]


def test_quote_final_own_line_attribution_becomes_speaker():
    html = """
    <blockquote>
      Gratitude has always guided me.<br>
      ~ Élodie O’Neill-Smith
    </blockquote>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "quote",
            "text": "Gratitude has always guided me.",
            "speaker": "Élodie O’Neill-Smith",
        }
    ]


def test_quote_final_paragraph_attribution_becomes_speaker():
    html = """
    <blockquote class="kg-blockquote-alt">
      <p>Stay curious.</p>
      <p>— Megha</p>
    </blockquote>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "quote",
            "text": "Stay curious.",
            "speaker": "Megha",
        }
    ]


def test_inline_dash_inside_quote_is_not_attribution():
    html = """
    <blockquote>
      Hope — even when things are difficult — remains.
    </blockquote>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "quote",
            "text": "Hope — even when things are difficult — remains.",
            "speaker": None,
        }
    ]


def test_ordered_list_preserves_structured_items():
    html = """
    <ol>
      <li>First practice</li>
      <li>Second practice</li>
    </ol>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "list",
            "ordered": True,
            "items": [
                "First practice",
                "Second practice",
            ],
        }
    ]


def test_nested_list_items_flatten_in_document_order():
    html = """
    <ul>
      <li>
        Outer one
        <ul>
          <li>Nested A</li>
          <li>Nested B</li>
        </ul>
      </li>
      <li>Outer two</li>
    </ul>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "list",
            "ordered": False,
            "items": [
                "Outer one",
                "Nested A",
                "Nested B",
                "Outer two",
            ],
        }
    ]


def test_fragment_only_table_of_contents_list_is_silent():
    html = """
    <ul>
      <li><a href="#gratitude">Gratitude</a></li>
      <li><a href="#practice">Practice</a></li>
    </ul>
    <h2 id="gratitude">Gratitude</h2>
    <p>Article content.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "heading",
            "level": 2,
            "text": "Gratitude",
        },
        {
            "type": "paragraph",
            "text": "Article content.",
        },
    ]


def test_normal_linked_editorial_list_is_retained():
    html = """
    <ul>
      <li><a href="https://example.com/a">Practice A</a></li>
      <li><a href="https://example.com/b">Practice B</a></li>
    </ul>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "list",
            "ordered": False,
            "items": [
                "Practice A",
                "Practice B",
            ],
        }
    ]


def test_callout_keeps_text_and_drops_decorative_emoji():
    html = """
    <div class="kg-card kg-callout-card kg-callout-blue">
      <div class="kg-callout-emoji">💡</div>
      <div class="kg-callout-text">
        There are studies suggesting there may be more basic emotions.
      </div>
    </div>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "callout",
            "text": (
                "There are studies suggesting there may be "
                "more basic emotions."
            ),
        }
    ]


def test_substantive_image_caption_is_retained():
    html = """
    <figure class="kg-card kg-image-card kg-card-hascaption">
      <img src="photo.jpg">
      <figcaption>
        Jadwiga shared photos showing a beautiful transformation.
      </figcaption>
    </figure>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "caption",
            "text": (
                "Jadwiga shared photos showing a beautiful "
                "transformation."
            ),
        }
    ]


@pytest.mark.parametrize(
    "caption",
    [
        "Image Credit: Example Photographer",
        "Photo Credit — Example Photographer",
        "Image Source: Example Publication",
        "Credit: Example Photographer",
        "Photo by Example Photographer",
        "Courtesy of Example Archive",
    ],
)
def test_credit_only_captions_are_silent(caption):
    html = f"""
    <figure class="kg-card kg-image-card kg-card-hascaption">
      <img src="photo.jpg">
      <figcaption>{caption}</figcaption>
    </figure>
    """

    assert normalize_ghost_html(html) == []


def test_question_button_card_becomes_prompt():
    html = """
    <div class="kg-card kg-button-card">
      <a class="kg-btn kg-btn-accent" href="/journal">
        What am I still carrying that isn't mine to carry?
      </a>
    </div>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "prompt",
            "text": (
                "What am I still carrying that isn't mine "
                "to carry?"
            ),
        }
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Continue Reading",
        "Read More",
        "Learn More",
        "Explore More",
        "Download",
        "Subscribe",
    ],
)
def test_cta_button_cards_are_silent(text):
    html = f"""
    <div class="kg-card kg-button-card">
      <a class="kg-btn" href="/elsewhere">{text}</a>
    </div>
    """

    assert normalize_ghost_html(html) == []


@pytest.mark.parametrize(
    "card_class",
    [
        "kg-gallery-card",
        "kg-embed-card",
        "kg-bookmark-card",
        "kg-file-card",
        "kg-audio-card",
        "kg-video-card",
        "kg-unknown-future-card",
    ],
)
def test_non_narration_ghost_cards_are_silent(card_class):
    html = f"""
    <div class="kg-card {card_class}">
      <p>This UI text must not become narration.</p>
      <a href="https://example.com">External preview</a>
      <iframe src="https://example.com/embed"></iframe>
    </div>
    <p>Real article text.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Real article text.",
        }
    ]


def test_regular_figure_substantive_caption_is_supported():
    html = """
    <figure>
      <img src="photo.jpg">
      <figcaption>A substantive editorial caption.</figcaption>
    </figure>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "caption",
            "text": "A substantive editorial caption.",
        }
    ]


def test_navigation_scripts_forms_and_iframes_are_silent():
    html = """
    <nav><p>Navigation text</p></nav>
    <script>dangerous()</script>
    <style>.x { display: block; }</style>
    <form><p>Form prompt</p><input value="x"></form>
    <iframe>Embedded UI</iframe>
    <p>Actual article content.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Actual article content.",
        }
    ]


def test_blank_content_is_omitted():
    html = """
    <p>   </p>
    <h2>   </h2>
    <blockquote>   </blockquote>
    <ul></ul>
    """

    assert normalize_ghost_html(html) == []


def test_structural_wrappers_are_transparent():
    html = """
    <section>
      <div>
        <p>Wrapped paragraph.</p>
      </div>
    </section>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Wrapped paragraph.",
        }
    ]



def test_nested_fragment_only_toc_is_silent():
    html = """
    <ul>
      <li>
        <a href="#one">One</a>
        <ul>
          <li><a href="#one-a">One A</a></li>
        </ul>
      </li>
      <li><a href="#two">Two</a></li>
    </ul>
    <p>Real article text.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Real article text.",
        }
    ]


def test_fragment_list_with_nested_editorial_content_is_retained():
    html = """
    <ul>
      <li>
        <a href="#practice">Practice</a>
        <ul>
          <li>This explanatory step is real article content.</li>
        </ul>
      </li>
    </ul>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "list",
            "ordered": False,
            "items": [
                "Practice",
                "This explanatory step is real article content.",
            ],
        }
    ]


def test_fragment_anchor_with_extra_editorial_text_is_not_toc():
    html = """
    <ul>
      <li>
        Start with <a href="#gratitude">gratitude</a> today.
      </li>
    </ul>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "list",
            "ordered": False,
            "items": [
                "Start with gratitude today.",
            ],
        }
    ]


def test_credit_prefix_with_substantive_sentence_is_retained():
    html = """
    <figure class="kg-card kg-image-card kg-card-hascaption">
      <img src="photo.jpg">
      <figcaption>
        Image Credit: Example Photographer. This image documents
        a meaningful transformation over several years.
      </figcaption>
    </figure>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "caption",
            "text": (
                "Image Credit: Example Photographer. "
                "This image documents a meaningful "
                "transformation over several years."
            ),
        }
    ]


def test_long_credit_prefixed_caption_is_preserved_conservatively():
    html = """
    <figure class="kg-card kg-image-card kg-card-hascaption">
      <img src="photo.jpg">
      <figcaption>
        Photo Credit: The community members who gathered together
        during the annual gratitude celebration in the city
      </figcaption>
    </figure>
    """

    result = normalize_ghost_html(html)

    assert len(result) == 1
    assert result[0]["type"] == "caption"
    assert result[0]["text"].startswith(
        "Photo Credit:"
    )


def test_real_ghost_quoted_button_prompt_is_retained_without_delimiters():
    html = """
    <p>You can start with this prompt:</p>
    <div class="kg-card kg-button-card kg-align-center">
      <a href="/redacted" class="kg-btn kg-btn-accent">
        "What am I still carrying that isn't mine to carry?"
      </a>
    </div>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "You can start with this prompt:",
        },
        {
            "type": "prompt",
            "text": (
                "What am I still carrying that isn't "
                "mine to carry?"
            ),
        },
    ]


def test_real_ghost_blockquote_alt_strips_outer_quotes():
    html = """
    <blockquote class="kg-blockquote-alt">
      <em>
        "Make the most of your regrets; never smother your sorrow,
        but tend and cherish it till it comes to have a separate
        and integral interest. To regret deeply is to live afresh."
        <br>- Henry David Thoreau
      </em>
    </blockquote>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "quote",
            "text": (
                "Make the most of your regrets; never smother "
                "your sorrow, but tend and cherish it till it "
                "comes to have a separate and integral interest. "
                "To regret deeply is to live afresh."
            ),
            "speaker": "Henry David Thoreau",
        }
    ]


def test_real_ghost_mixed_unicode_quote_marks_are_removed():
    html = """
    <blockquote>
      <strong>
        “I learned that running for things in life is not important.
        But what I had was my hope and my inner happiness.’’
      </strong>
      <br>~ Jadwiga
    </blockquote>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "quote",
            "text": (
                "I learned that running for things in life is "
                "not important. But what I had was my hope "
                "and my inner happiness."
            ),
            "speaker": "Jadwiga",
        }
    ]

def test_normalized_blocks_build_valid_canonical_document():
    html = """
    <h2>A Practice</h2>
    <p>Begin with one quiet breath.</p>
    <blockquote>
      Stay curious.<br>
      — Megha
    </blockquote>
    <ul>
      <li>Notice</li>
      <li>Reflect</li>
    </ul>
    """

    blocks = normalize_ghost_html(html)

    document = build_document(
        post_id="ghost-post-123",
        content_hash=CONTENT_HASH,
        blocks=blocks,
    )

    assert document["schema_version"] == 1
    assert len(document["blocks"]) == 4

    assert [
        block["block_id"]
        for block in document["blocks"]
    ] == [
        "b000001",
        "b000002",
        "b000003",
        "b000004",
    ]

    assert document["blocks"][2]["role"] == "quote"
    assert document["blocks"][3]["items"] == [
        "Notice",
        "Reflect",
    ]

def test_package_exports_normalize_ghost_html():
    import narration_content

    assert (
        narration_content.normalize_ghost_html
        is normalize_ghost_html
    )
