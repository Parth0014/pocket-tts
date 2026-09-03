"""Ghost HTML -> canonical narration-semantic blocks.

This module is the V2 normalization path.

It intentionally does not call the legacy root-level ``extractor.py`` and
does not participate in the currently deployed V6 worker path.

Input:
    exact Ghost Content API ``post.html`` string

Output:
    semantic blocks accepted by ``narration_content.document.build_document``

Generation policy such as voice selection and quote_mode is deliberately
outside this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

_HEADING_NAMES = {
    "h1": 1,
    "h2": 2,
    "h3": 3,
    "h4": 4,
    "h5": 5,
    "h6": 6,
}

_HARD_SILENT_TAGS = {
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "aside",
    "form",
    "input",
    "select",
    "textarea",
    "iframe",
    "svg",
    "canvas",
    "audio",
    "video",
}

_CONTAINER_OWNED_CONTENT = {
    "blockquote",
    "ul",
    "ol",
    "figure",
}

_QUOTE_PAIRS = (
    ('"', '"'),
    ("“", "”"),
    ("‘", "’"),
    ("'", "'"),
)

_ATTRIBUTION_RE = re.compile(
    r"^[~\-–—]\s*(?P<speaker>.+?)\s*$"
)

_CREDIT_PATTERNS = (
    re.compile(
        r"^(?:image|photo|photograph|picture|illustration|graphic)"
        r"\s+(?:credit|credits|source)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:credit|credits|source)\s*[:\-–—]",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:photo|image|illustration)\s+by\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^courtesy\s+of\b",
        re.IGNORECASE,
    ),
)

_CTA_PREFIXES = (
    "continue reading",
    "read more",
    "learn more",
    "explore",
    "download",
    "subscribe",
    "sign up",
    "join now",
    "get started",
    "click here",
    "visit",
    "view more",
)


_EMOJI_RANGES = (
    (0x2600, 0x27BF),
    (0x1F1E6, 0x1F1FF),
    (0x1F300, 0x1FAFF),
)

_EMOJI_SEQUENCE_CODEPOINTS = frozenset(
    {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
)


def _is_narration_emoji(char: str) -> bool:
    codepoint = ord(char)
    if codepoint in _EMOJI_SEQUENCE_CODEPOINTS:
        return True
    if 0x1F3FB <= codepoint <= 0x1F3FF:
        return True
    if 0xE0020 <= codepoint <= 0xE007F:
        return True
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)


def _strip_narration_emoji(value: str) -> str:
    # Replace rather than concatenate across an emoji boundary: hi🤍there -> hi there.
    return "".join(
        " " if _is_narration_emoji(char) else char
        for char in value
    )


def _normalize_text(value: str) -> str:
    """Remove decorative emoji and collapse insignificant whitespace."""
    return re.sub(r"\s+", " ", _strip_narration_emoji(value)).strip()


def _classes(tag: Tag) -> set[str]:
    value = tag.get("class") or []
    return {
        str(item)
        for item in value
    }


def _has_class(tag: Tag, class_name: str) -> bool:
    return class_name in _classes(tag)


def _nearest_card(tag: Tag) -> Tag | None:
    """Return the nearest Ghost kg-card containing ``tag``."""
    current: Tag | None = tag

    while isinstance(current, Tag):
        if "kg-card" in _classes(current):
            return current

        parent = current.parent
        current = parent if isinstance(parent, Tag) else None

    return None


def _inside_hard_silent_container(tag: Tag) -> bool:
    current = tag.parent

    while isinstance(current, Tag):
        if current.name in _HARD_SILENT_TAGS:
            return True

        current = current.parent

    return False


def _inside_container_owned_content(tag: Tag) -> bool:
    """True when an ancestor is emitted by a higher-level block parser."""
    current = tag.parent

    while isinstance(current, Tag):
        if current.name in _CONTAINER_OWNED_CONTENT:
            return True

        current = current.parent

    return False


def _simple_text(tag: Tag) -> str:
    """Extract visible text without inventing spaces around inline markup.

    BeautifulSoup ``get_text(" ")`` inserts a separator between every text
    node. That can incorrectly turn:

        <em>ordinary</em>.

    into:

        ordinary .

    Instead, preserve literal text-node boundaries, add a boundary only for
    explicit line breaks or block-like inner wrappers, then normalize
    insignificant whitespace.
    """
    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue

            if not isinstance(child, Tag):
                continue

            if child.name in _HARD_SILENT_TAGS:
                continue

            if child.name == "br":
                parts.append(" ")
                continue

            walk(child)

            if child.name in {
                "p",
                "div",
                "li",
            }:
                parts.append(" ")

    walk(tag)

    return _normalize_text(
        "".join(parts)
    )


def _collect_line_aware_text(tag: Tag) -> list[str]:
    """Collect visible text while preserving explicit/block line boundaries."""
    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue

            if not isinstance(child, Tag):
                continue

            if child.name in _HARD_SILENT_TAGS:
                continue

            if child.name == "br":
                parts.append("\n")
                continue

            walk(child)

            if child.name in {"p", "div"}:
                parts.append("\n")

    walk(tag)

    raw = "".join(parts)

    lines = [
        _normalize_text(line)
        for line in raw.splitlines()
    ]

    return [
        line
        for line in lines
        if line
    ]


def _looks_like_speaker(value: str) -> bool:
    """Conservative speaker-name check for explicit quote attribution."""
    value = _normalize_text(value)

    if not value:
        return False

    if len(value) > 100:
        return False

    if len(value.split()) > 12:
        return False

    if not any(char.isalpha() for char in value):
        return False

    if any(char in "?!:;" for char in value):
        return False

    allowed_punctuation = set(
        " .'’,-–—&()"
    )

    for char in value:
        if char.isalpha():
            continue

        if char in allowed_punctuation:
            continue

        return False

    return True


def _strip_outer_quote_marks(value: str) -> str:
    """Remove surrounding quote delimiters without touching inner quotes.

    Real Ghost content contains both matched quote pairs and occasional
    mixed/repeated Unicode closing quote characters. Canonical narration
    text should not retain those presentation delimiters.
    """
    value = value.strip()

    quote_chars = {
        '"',
        "'",
        "“",
        "”",
        "‘",
        "’",
    }

    while value and value[0] in quote_chars:
        value = value[1:].lstrip()

    while value and value[-1] in quote_chars:
        value = value[:-1].rstrip()

    return value


def _parse_quote(tag: Tag) -> dict | None:
    lines = _collect_line_aware_text(tag)

    if not lines:
        return None

    speaker: str | None = None

    if len(lines) >= 2:
        match = _ATTRIBUTION_RE.fullmatch(
            lines[-1]
        )

        if match:
            candidate = _normalize_text(
                match.group("speaker")
            )

            if _looks_like_speaker(candidate):
                speaker = candidate
                lines = lines[:-1]

    text = _normalize_text(
        " ".join(lines)
    )

    text = _strip_outer_quote_marks(text)

    if not text:
        return None

    return {
        "type": "quote",
        "text": text,
        "speaker": speaker,
    }


def _list_item_own_text(tag: Tag) -> str:
    """Extract one li's own text, excluding nested ul/ol content."""
    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue

            if not isinstance(child, Tag):
                continue

            if child.name in {"ul", "ol"}:
                continue

            if child.name in _HARD_SILENT_TAGS:
                continue

            if child.name == "br":
                parts.append(" ")
                continue

            walk(child)

            if child.name in {
                "p",
                "div",
            }:
                parts.append(" ")

    walk(tag)

    return _normalize_text(
        "".join(parts)
    )


def _direct_list_items(tag: Tag) -> list[Tag]:
    return [
        child
        for child in tag.children
        if (
            isinstance(child, Tag)
            and child.name == "li"
        )
    ]


def _nested_direct_lists(tag: Tag) -> Iterable[Tag]:
    for child in tag.children:
        if not isinstance(child, Tag):
            continue

        if child.name in {"ul", "ol"}:
            yield child


def _flatten_list_items(tag: Tag) -> list[str]:
    items: list[str] = []

    for item in _direct_list_items(tag):
        own_text = _list_item_own_text(item)

        if own_text:
            items.append(own_text)

        for nested in _nested_direct_lists(item):
            items.extend(
                _flatten_list_items(nested)
            )

    return items


def _is_fragment_toc(tag: Tag) -> bool:
    """Prove that an entire list tree is same-document fragment navigation.

    V1 chooses content preservation over aggressive TOC removal.

    A list is silent only when every direct item:

    - has exactly one anchor belonging to this list level;
    - contains no own text outside that anchor;
    - links to a ``#fragment`` target; and
    - has only nested lists that independently satisfy the same rule.

    If any part is ambiguous or editorial, the whole list is retained.
    """
    items = _direct_list_items(tag)

    if not items:
        return False

    for item in items:
        own_text = _list_item_own_text(item)

        anchors = [
            anchor
            for anchor in item.find_all(
                "a",
                href=True,
            )
            if anchor.find_parent(
                ["ul", "ol"]
            ) is tag
        ]

        if len(anchors) != 1:
            return False

        anchor = anchors[0]

        href = str(
            anchor.get("href") or ""
        ).strip()

        if not href.startswith("#"):
            return False

        anchor_text = _simple_text(anchor)

        if (
            not own_text
            or own_text != anchor_text
        ):
            return False

        for nested in _nested_direct_lists(item):
            if not _is_fragment_toc(nested):
                return False

    return True


def _parse_list(tag: Tag) -> dict | None:
    if _is_fragment_toc(tag):
        return None

    items = _flatten_list_items(tag)

    if not items:
        return None

    return {
        "type": "list",
        "ordered": tag.name == "ol",
        "items": items,
    }


def _parse_callout(tag: Tag) -> dict | None:
    text_container = tag.find(
        class_="kg-callout-text"
    )

    if isinstance(text_container, Tag):
        text = _simple_text(
            text_container
        )
    else:
        # Fallback for structurally valid but older/different Ghost markup:
        # copy only non-emoji textual descendants.
        parts: list[str] = []

        def walk(node: Tag) -> None:
            for child in node.children:
                if isinstance(child, NavigableString):
                    parts.append(str(child))
                    continue

                if not isinstance(child, Tag):
                    continue

                if _has_class(
                    child,
                    "kg-callout-emoji",
                ):
                    continue

                if child.name in _HARD_SILENT_TAGS:
                    continue

                walk(child)
                parts.append(" ")

        walk(tag)

        text = _normalize_text(
            "".join(parts)
        )

    if not text:
        return None

    return {
        "type": "callout",
        "text": text,
    }


def _is_credit_only_caption(text: str) -> bool:
    """Return True only for conservative credit/source-only captions.

    A credit marker at the beginning is not enough by itself to discard a
    caption. If the remainder looks sentence-like or substantial, preserve
    the full caption rather than risk losing editorial narration.
    """
    text = _normalize_text(text)

    if not text:
        return True

    matched = None

    for pattern in _CREDIT_PATTERNS:
        matched = pattern.search(text)

        if matched:
            break

    if matched is None:
        return False

    remainder = text[
        matched.end() :
    ].strip(" :-–—")

    if not remainder:
        return True

    # Sentence punctuation is evidence that editorial prose follows
    # the credit/source marker. Preserve rather than discard.
    if re.search(
        r"[.!?](?:\s|$)",
        remainder,
    ):
        return False

    # Keep the exclusion deliberately narrow. A short source/name is
    # treated as metadata; longer text is retained to avoid content loss.
    return len(
        remainder.split()
    ) <= 8


def _parse_caption(tag: Tag) -> dict | None:
    text = _simple_text(tag)

    if not text:
        return None

    if _is_credit_only_caption(text):
        return None

    return {
        "type": "caption",
        "text": text,
    }


def _is_editorial_prompt(text: str) -> bool:
    text = _normalize_text(text)

    if not text:
        return False

    lowered = text.casefold()

    if any(
        lowered == prefix
        or lowered.startswith(prefix + " ")
        or lowered.startswith(prefix + ":")
        for prefix in _CTA_PREFIXES
    ):
        return False

    # V1 deliberately uses a conservative, deterministic rule:
    # a Ghost button becomes narration only when it is explicitly phrased
    # as a question.
    return text.endswith("?")


def _parse_button_card(tag: Tag) -> dict | None:
    candidate = tag.find(
        class_="kg-btn"
    )

    if not isinstance(candidate, Tag):
        candidate = tag.find(
            ["a", "button"]
        )

    if not isinstance(candidate, Tag):
        return None

    text = _strip_outer_quote_marks(
        _simple_text(candidate)
    )

    if not _is_editorial_prompt(text):
        return None

    return {
        "type": "prompt",
        "text": text,
    }


def _caption_allowed_inside_card(card: Tag | None) -> bool:
    if card is None:
        return True

    classes = _classes(card)

    return "kg-image-card" in classes


_TRAILING_NON_NARRATION_EXACT = frozenset(
    {
        "share this story",
    }
)

_TRAILING_NON_NARRATION_PREFIXES = (
    "every story is a reminder that ",
)

_TRAILING_STORY_SOLICITATION_PREFIX = (
    "i would love to hear your story"
)


def _block_text_for_tail_policy(block: dict) -> str | None:
    text = block.get("text")

    if not isinstance(text, str):
        return None

    normalized = _normalize_text(text)

    return normalized if normalized else None


def _is_trailing_non_narration_block(block: dict) -> bool:
    """Recognize conservative Ghost/site footer copy only at document tail."""
    text = _block_text_for_tail_policy(block)

    if text is None:
        return False

    folded = text.casefold()

    if folded in _TRAILING_NON_NARRATION_EXACT:
        return True

    if any(
        folded.startswith(prefix)
        for prefix in _TRAILING_NON_NARRATION_PREFIXES
    ):
        return True

    if folded.startswith(
        _TRAILING_STORY_SOLICITATION_PREFIX
    ):
        return (
            "write to me at" in folded
            or "@" in text
        )

    return False


def _trim_trailing_non_narration_blocks(
    blocks: list[dict],
) -> list[dict]:
    """Trim only one contiguous recognized non-narration suffix."""
    end = len(blocks)

    while (
        end > 0
        and _is_trailing_non_narration_block(
            blocks[end - 1]
        )
    ):
        end -= 1

    return blocks[:end]


def normalize_ghost_html(html: str) -> list[dict]:
    """Normalize exact Ghost post HTML into Narration Document V1 blocks."""
    if not isinstance(html, str):
        raise TypeError(
            f"html must be str, got {type(html).__name__}"
        )

    if not html.strip():
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    blocks: list[dict] = []

    for tag in soup.find_all(True):
        if tag.name in _HARD_SILENT_TAGS:
            continue

        if _inside_hard_silent_container(tag):
            continue

        classes = _classes(tag)

        # Ghost callout card: emit once from the card container.
        if "kg-callout-card" in classes:
            block = _parse_callout(tag)

            if block is not None:
                blocks.append(block)

            continue

        # Ghost button card: emit only conservative editorial prompts.
        if "kg-button-card" in classes:
            block = _parse_button_card(tag)

            if block is not None:
                blocks.append(block)

            continue

        # Captions are supported for regular figures and Ghost image cards.
        if tag.name == "figcaption":
            card = _nearest_card(tag)

            if not _caption_allowed_inside_card(card):
                continue

            block = _parse_caption(tag)

            if block is not None:
                blocks.append(block)

            continue

        # Any remaining content inside a Ghost card is silent in V1.
        # This deliberately covers gallery/embed/bookmark/file cards and
        # unknown future kg-card structures rather than guessing.
        if _nearest_card(tag) is not None:
            continue

        if tag.name == "blockquote":
            if _inside_container_owned_content(tag):
                continue

            block = _parse_quote(tag)

            if block is not None:
                blocks.append(block)

            continue

        if tag.name in {"ul", "ol"}:
            if _inside_container_owned_content(tag):
                continue

            block = _parse_list(tag)

            if block is not None:
                blocks.append(block)

            continue

        if tag.name in _HEADING_NAMES:
            if _inside_container_owned_content(tag):
                continue

            text = _simple_text(tag)

            if text:
                blocks.append(
                    {
                        "type": "heading",
                        "level": _HEADING_NAMES[
                            tag.name
                        ],
                        "text": text,
                    }
                )

            continue

        if tag.name == "p":
            if _inside_container_owned_content(tag):
                continue

            text = _simple_text(tag)

            if text:
                blocks.append(
                    {
                        "type": "paragraph",
                        "text": text,
                    }
                )

    return _trim_trailing_non_narration_blocks(
        blocks
    )
