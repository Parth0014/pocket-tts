"""Regression fixtures mirroring structures observed in real Ghost posts.

URLs are intentionally replaced with inert placeholders. These fixtures
verify structure and narration policy, not network behavior.
"""

from narration_content.normalizer import normalize_ghost_html


def test_real_callout_card_preserves_text_and_not_emoji():
    html = """
    <p>Before the callout.</p>
    <div class="kg-card kg-callout-card kg-callout-card-blue">
      <div class="kg-callout-emoji">💡</div>
      <div class="kg-callout-text">
        There are studies suggesting whether there are more or less
        basic emotions. Regardless, fear is one of them however long
        or short the list may be.
      </div>
    </div>
    <p>After the callout.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Before the callout.",
        },
        {
            "type": "callout",
            "text": (
                "There are studies suggesting whether there are "
                "more or less basic emotions. Regardless, fear is "
                "one of them however long or short the list may be."
            ),
        },
        {
            "type": "paragraph",
            "text": "After the callout.",
        },
    ]


def test_real_image_credit_only_caption_is_silent():
    html = """
    <p>Before image.</p>
    <figure class="kg-card kg-image-card kg-card-hascaption">
      <img
        src="/redacted.jpg"
        class="kg-image"
        alt=""
        loading="lazy"
        width="450"
        height="502"
      >
      <figcaption>
        <a href="/redacted" rel="noreferrer">
          <span style="white-space: pre-wrap;">Image Credit</span>
        </a>
      </figcaption>
    </figure>
    <p>After image.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Before image.",
        },
        {
            "type": "paragraph",
            "text": "After image.",
        },
    ]


def test_real_gallery_card_is_silent_without_losing_surrounding_article():
    html = """
    <p>
      Gratitude helped me realize there is nothing wrong with slowing down.
    </p>
    <figure class="kg-card kg-gallery-card kg-width-wide">
      <div class="kg-gallery-container">
        <div class="kg-gallery-row">
          <div class="kg-gallery-image">
            <img src="/one.jpg" alt="">
          </div>
          <div class="kg-gallery-image">
            <img src="/two.jpg" alt="">
          </div>
        </div>
      </div>
    </figure>
    <p>
      Today, gratitude has become part of my daily routine.
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": (
                "Gratitude helped me realize there is nothing "
                "wrong with slowing down."
            ),
        },
        {
            "type": "paragraph",
            "text": (
                "Today, gratitude has become part of my daily routine."
            ),
        },
    ]


def test_real_embed_card_iframe_is_silent_and_article_order_survives():
    html = """
    <p>Here's a video you can follow to do it:</p>
    <figure class="kg-card kg-embed-card">
      <iframe
        width="200"
        height="113"
        src="/redacted-embed"
        title="Body Scan Practice"
      ></iframe>
    </figure>
    <h3 id="write-your-emotions">
      2. Write your emotions and how you feel
    </h3>
    <p>
      I find writing to be a rewarding exercise.
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Here's a video you can follow to do it:",
        },
        {
            "type": "heading",
            "level": 3,
            "text": "2. Write your emotions and how you feel",
        },
        {
            "type": "paragraph",
            "text": "I find writing to be a rewarding exercise.",
        },
    ]


def test_real_bookmark_card_is_silent_and_following_content_survives():
    html = """
    <p>
      In fact, it's called The Attitude of Gratitude Course.
    </p>
    <figure class="kg-card kg-bookmark-card">
      <a class="kg-bookmark-container" href="/redacted">
        <div class="kg-bookmark-content">
          <div class="kg-bookmark-title">
            The Attitude of Gratitude Course
          </div>
          <div class="kg-bookmark-description"></div>
          <div class="kg-bookmark-metadata">
            <span class="kg-bookmark-author">Gratitude School</span>
            <span class="kg-bookmark-publisher">Aarushi Tewari</span>
          </div>
        </div>
      </a>
    </figure>
    <p>
      Here are the points that I tackle and talk about in the course:
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": (
                "In fact, it's called The Attitude of Gratitude Course."
            ),
        },
        {
            "type": "paragraph",
            "text": (
                "Here are the points that I tackle and talk about "
                "in the course:"
            ),
        },
    ]


def test_real_file_card_metadata_is_silent():
    html = """
    <h2>
      Here's a free reflective worksheet for gentle journaling
    </h2>
    <div class="kg-card kg-file-card">
      <a
        class="kg-file-card-container"
        href="/redacted.pdf"
        title="Download"
        download
      >
        <div class="kg-file-card-contents">
          <div class="kg-file-card-title">
            Inner Safety Worksheet - The Gratitude App
          </div>
          <div class="kg-file-card-caption"></div>
          <div class="kg-file-card-metadata">
            <div class="kg-file-card-filename">
              Inner Safety Worksheet - The Gratitude App.pdf
            </div>
            <div class="kg-file-card-filesize">3 MB</div>
          </div>
        </div>
      </a>
    </div>
    <p>
      I hope this helps you create more capacity within yourself.
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "heading",
            "level": 2,
            "text": (
                "Here's a free reflective worksheet for gentle journaling"
            ),
        },
        {
            "type": "paragraph",
            "text": (
                "I hope this helps you create more capacity within yourself."
            ),
        },
    ]


def test_real_continue_reading_button_is_silent():
    html = """
    <p>
      Gratitude taught me how to see my life with more care.
    </p>
    <div class="kg-card kg-button-card kg-align-left">
      <a href="/redacted" class="kg-btn kg-btn-accent">
        Continue Reading: Inspirational Gratitude Stories
      </a>
    </div>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": (
                "Gratitude taught me how to see my life with more care."
            ),
        }
    ]


def test_real_standalone_code_element_is_not_narrated():
    html = """
    <p>
      Android bundles use a different packaging format.
    </p>
    <code>.aab</code>
    <p>
      The resulting download can be smaller.
    </p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": (
                "Android bundles use a different packaging format."
            ),
        },
        {
            "type": "paragraph",
            "text": (
                "The resulting download can be smaller."
            ),
        },
    ]


def test_real_editorial_ordered_list_remains_structured():
    html = """
    <p>
      Here are the points that I tackle and talk about in the course:
    </p>
    <h2 id="how-to-build-an-attitude-of-gratitude">
      How to Build An Attitude of Gratitude
    </h2>
    <ol>
      <li>Understanding true gratitude</li>
      <li>
        Finding more and more
        <a href="/redacted">things to be grateful for</a>
      </li>
      <li>Keep being consistent</li>
      <li>Feeling grateful during hard times</li>
      <li>BONUS Course Resources</li>
    </ol>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": (
                "Here are the points that I tackle and talk about "
                "in the course:"
            ),
        },
        {
            "type": "heading",
            "level": 2,
            "text": "How to Build An Attitude of Gratitude",
        },
        {
            "type": "list",
            "ordered": True,
            "items": [
                "Understanding true gratitude",
                "Finding more and more things to be grateful for",
                "Keep being consistent",
                "Feeling grateful during hard times",
                "BONUS Course Resources",
            ],
        },
    ]


def test_real_mixed_sequence_preserves_narration_order_across_silent_cards():
    html = """
    <p>Opening paragraph.</p>

    <figure class="kg-card kg-gallery-card">
      <div class="kg-gallery-container">
        <img src="/silent.jpg">
      </div>
    </figure>

    <div class="kg-card kg-callout-card kg-callout-card-blue">
      <div class="kg-callout-emoji">💡</div>
      <div class="kg-callout-text">
        Important article callout.
      </div>
    </div>

    <figure class="kg-card kg-embed-card">
      <iframe src="/silent"></iframe>
    </figure>

    <blockquote>
      “Stay curious.”<br>
      — Megha
    </blockquote>

    <div class="kg-card kg-button-card">
      <a class="kg-btn">Continue Reading: More Stories</a>
    </div>

    <p>Closing paragraph.</p>
    """

    assert normalize_ghost_html(html) == [
        {
            "type": "paragraph",
            "text": "Opening paragraph.",
        },
        {
            "type": "callout",
            "text": "Important article callout.",
        },
        {
            "type": "quote",
            "text": "Stay curious.",
            "speaker": "Megha",
        },
        {
            "type": "paragraph",
            "text": "Closing paragraph.",
        },
    ]