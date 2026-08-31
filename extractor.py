"""Extract narration-ready blocks from raw Ghost post HTML.

Only explicitly supported narration elements become blocks. Structural
wrappers such as div, section, and article remain transparent, while Ghost
cards and non-narrative elements are silent so their UI text cannot leak into
the spoken story.
"""

import re
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

BASE_DIR = Path(__file__).resolve().parent

# Tags whose text content is real narration and should be kept.
NARRATION_TAGS = frozenset(
    {"h1", "h2", "h3", "h4", "p", "blockquote", "ul", "ol"}
)

# Tags whose entire subtree is always non-narrative.
SKIP_TAGS = frozenset(
    {
        "style",
        "script",
        "figure",
        "img",
        "iframe",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "nav",
        "footer",
        "aside",
    }
)

_BLOCK_BOUNDARY_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "ul",
    }
)
_ATTRIBUTION_MARKERS = frozenset({"~", "-", "\u2013", "\u2014"})
_NAME_PUNCTUATION = frozenset({" ", ".", "'", "\u2019", "-"})


def _clean_whitespace(text: str) -> str:
    """Collapse whitespace after semantic block boundaries are established."""
    return re.sub(r"\s+", " ", text).strip()


def _append_line_break(pieces: list[str]) -> None:
    """Append one semantic line break without accumulating blank lines."""
    if pieces and not pieces[-1].endswith(("\n", "\r")):
        pieces.append("\n")


def _extract_text_with_line_breaks(tag) -> str:
    """Extract text while retaining br and block-element boundaries.

    BeautifulSoup's default get_text() can concatenate adjacent paragraphs.
    Keeping their boundary here both prevents joined words and lets a trailing
    attribution be recognized only when it occupies its own line.
    """
    pieces: list[str] = []

    def walk(node, *, is_root: bool = False) -> None:
        if isinstance(node, NavigableString):
            pieces.append(str(node))
            return

        if getattr(node, "name", None) == "br":
            _append_line_break(pieces)
            return

        is_boundary = not is_root and getattr(node, "name", None) in _BLOCK_BOUNDARY_TAGS
        if is_boundary:
            _append_line_break(pieces)
        for child in getattr(node, "children", ()):
            walk(child)
        if is_boundary:
            _append_line_break(pieces)

    walk(tag, is_root=True)
    return "".join(pieces)


def _is_speaker_name(value: str) -> bool:
    """Return whether a trailing attribution payload looks like a name.

    Unicode letter and combining-mark categories support names such as
    accented names without accepting arbitrary punctuation or prose.
    """
    if not 1 <= len(value) <= 80 or not value[0].isalpha():
        return False

    has_letter = False
    for char in value:
        category = unicodedata.category(char)
        if char.isalpha():
            has_letter = True
        elif category.startswith("M") or char in _NAME_PUNCTUATION:
            continue
        else:
            return False
    return has_letter


def _split_trailing_attribution(raw_text: str) -> tuple[str, str | None]:
    """Remove a valid attribution only when it is the final non-empty line."""
    lines = raw_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return "", None

    candidate = lines[-1].strip()
    if candidate and candidate[0] in _ATTRIBUTION_MARKERS:
        speaker = candidate[1:].strip()
        if _is_speaker_name(speaker):
            return "\n".join(lines[:-1]), speaker

    return "\n".join(lines), None


def _parse_blockquote(tag) -> dict:
    """Build a quote block and extract optional speaker metadata."""
    raw_text = _extract_text_with_line_breaks(tag)
    quote_text, speaker = _split_trailing_attribution(raw_text)
    quote_text = _clean_whitespace(quote_text)
    quote_text = quote_text.strip("\"'\u201c\u201d\u2018\u2019")
    return {"type": "quote", "speaker": speaker, "text": quote_text}


def _list_item_text(tag) -> str:
    """Extract one list item's own text, excluding its nested lists."""
    pieces: list[str] = []

    def walk(node, *, is_root: bool = False) -> None:
        if isinstance(node, NavigableString):
            pieces.append(str(node))
            return

        name = getattr(node, "name", None)
        if name in {"ul", "ol"}:
            return
        if name == "br":
            _append_line_break(pieces)
            return

        is_boundary = not is_root and name in _BLOCK_BOUNDARY_TAGS
        if is_boundary:
            _append_line_break(pieces)
        for child in getattr(node, "children", ()):
            walk(child)

        if is_boundary:
            _append_line_break(pieces)

    walk(tag, is_root=True)
    return _clean_whitespace("".join(pieces))


def _parse_list(tag) -> dict:
    """Flatten an outer list, including nested items, into one spoken block."""
    items = []
    for item in tag.find_all("li"):
        text = _list_item_text(item)
        if text:
            items.append(text)
    return {"type": "list", "text": ". ".join(items)}


def _has_narration_ancestor(tag) -> bool:
    """Avoid emitting descendants already represented by a parent block."""
    return any(
        getattr(parent, "name", None) in NARRATION_TAGS for parent in tag.parents
    )


def extract_blocks(html: str) -> list[dict]:
    """Parse Ghost post HTML into an ordered list of typed blocks.

    Returned dictionaries have one of these shapes:

    - {"type": "heading", "level": 1-4, "text": "..."}
    - {"type": "paragraph", "text": "..."}
    - {"type": "quote", "speaker": "Name" | None, "text": "..."}
    - {"type": "list", "text": "..."}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove complete subtrees before traversal. Ghost uses the kg-card class
    # for cards, including future/unknown card types; those default to silence.
    for tag in soup.find_all(list(SKIP_TAGS)):
        if tag.parent is not None:
            tag.decompose()
    for card in soup.select(".kg-card"):
        if card.parent is not None:
            card.decompose()

    blocks: list[dict] = []
    for tag in soup.find_all(list(NARRATION_TAGS)):
        if _has_narration_ancestor(tag):
            continue

        if tag.name in {"h1", "h2", "h3", "h4"}:
            text = _clean_whitespace(_extract_text_with_line_breaks(tag))
            if text:
                blocks.append(
                    {"type": "heading", "level": int(tag.name[1]), "text": text}
                )
        elif tag.name == "p":
            text = _clean_whitespace(_extract_text_with_line_breaks(tag))
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif tag.name == "blockquote":
            block = _parse_blockquote(tag)
            if block["text"]:
                blocks.append(block)
        elif tag.name in {"ul", "ol"}:
            block = _parse_list(tag)
            if block["text"]:
                blocks.append(block)

    return blocks


if __name__ == "__main__":
    with (BASE_DIR / "sample.html").open("r", encoding="utf-8") as source:
        html = source.read()

    extracted = extract_blocks(html)
    print(f"Extracted {len(extracted)} blocks:\n")
    for index, block in enumerate(extracted, 1):
        print(f"[{index}] type={block['type']}", end="")
        if block["type"] == "quote":
            print(f" speaker={block.get('speaker')!r}")
        elif block["type"] == "heading":
            print(f" level={block['level']}")
        else:
            print()
        preview = block["text"][:90] + ("..." if len(block["text"]) > 90 else "")
        print(f"    {preview}\n")
