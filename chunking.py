from __future__ import annotations

import operator
import re
from pathlib import Path
from typing import Callable, Iterable

BASE_DIR = Path(__file__).resolve().parent
_BLOCK_TYPES = frozenset({"paragraph", "heading", "list", "quote"})
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:\-\u2013\u2014])\s+")
_SENTENCE_END_RE = re.compile(
    r"(?P<ending>\.(?: *\.)+|[.!?\u2026]+)(?P<closers>[\"'\u201d\u2019\u00bb)\]}]*) +(?=\S)"
)
_PREFIX_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "rev", "hon", "sr", "jr", "st", "mt",
    "gen", "col", "lt", "capt", "sgt", "sen", "rep", "gov", "pres",
})
_OTHER_ABBREVIATIONS = frozenset({
    "etc", "vs", "approx", "dept", "fig", "no", "vol", "pp", "inc", "ltd",
    "e.g", "i.e", "a.m", "p.m", "ph.d", "m.d", "b.sc", "m.sc",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})
_SENTENCE_STARTERS = frozenset({
    "I", "He", "She", "It", "We", "They", "You", "This", "That", "These", "Those",
    "Then", "However", "Meanwhile", "Nevertheless", "Finally",
})


def _validated_budget(budget: int) -> int:
    """Return an integer budget, rejecting booleans and invalid values."""
    if isinstance(budget, bool):
        raise ValueError("budget must be a positive integer")
    try:
        value = operator.index(budget)
    except TypeError as exc:
        raise ValueError("budget must be a positive integer") from exc
    if value <= 0:
        raise ValueError("budget must be a positive integer")
    return value


def _token_count(count_tokens_fn: Callable[[str], int], text: str) -> int:
    """Call a token counter and validate its contract."""
    count = count_tokens_fn(text)
    if isinstance(count, bool):
        raise TypeError("count_tokens_fn must return a non-negative integer")
    try:
        value = operator.index(count)
    except TypeError as exc:
        raise TypeError(
            "count_tokens_fn must return a non-negative integer"
        ) from exc
    if value < 0:
        raise ValueError("count_tokens_fn returned a negative token count")
    return value


def _split_sentences(paragraph_text: str) -> list[str]:
    """Find English sentence boundaries without changing spoken punctuation.

    This deliberately conservative heuristic protects titles, initials, and
    common abbreviations, and keeps lowercase dialogue attribution or ellipsis
    continuations attached. Dotted acronyms can also end sentences: only common
    sentence starters are recognized after them. Ambiguous cases (``U.S. Navy``
    versus ``U.S. Congress passed ...``) may remain grouped; the token-budget
    splitter still bounds the result. This is not a linguistic sentence parser.
    """
    collapsed = re.sub(r"\s+", " ", paragraph_text.strip())
    sentences = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(collapsed):
        following = collapsed[match.end():].lstrip("\"'\u201c\u2018\u00ab([{ ")
        next_word_match = re.match(r"\w+", following)
        next_word = next_word_match.group() if next_word_match else ""
        ending = match.group("ending")

        if ending == ".":
            token_match = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)*)$", collapsed[:match.start()])
            token = token_match.group() if token_match else ""
            if token.lower() in _PREFIX_ABBREVIATIONS:
                continue
            if len(token) == 1 and token.isupper():
                continue
            dotted_acronym = bool(re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", token))
            if dotted_acronym:
                if token.lower() in {"e.g", "i.e"} or next_word not in _SENTENCE_STARTERS:
                    continue
            if token.lower() in _OTHER_ABBREVIATIONS and next_word and not next_word[0].isupper():
                continue

        # "Wait!" she said. / He paused... then continued.
        if (match.group("closers") or ending.count(".") > 1 or "\u2026" in ending) and (
            next_word and next_word[0].islower()
        ):
            continue
        sentences.append(collapsed[start:match.end("closers")])
        start = match.end()
    if start < len(collapsed):
        sentences.append(collapsed[start:])
    return sentences


def _raise_oversized_atomic_unit(
    count_tokens_fn: Callable[[str], int], unit: str, budget: int
) -> None:
    token_count = _token_count(count_tokens_fn, unit)
    preview = unit if len(unit) <= 60 else f"{unit[:57]}..."
    raise ValueError(
        "Cannot split a single whitespace-delimited text unit to fit the "
        f"token budget: {preview!r} uses {token_count} tokens, budget={budget}. "
        "Remove or rewrite the token (for example, a long URL or identifier)."
    )


def _split_oversized_unit(
    count_tokens_fn: Callable[[str], int], unit: str, budget: int
) -> list[str]:
    """Split a text unit at clauses and then words until each piece fits.

    Text is never split inside a whitespace-delimited token because feeding
    fragments of one word to separate TTS calls changes its pronunciation. If
    such an atomic unit exceeds the budget, fail clearly instead of silently
    returning an invalid over-budget chunk.
    """
    budget = _validated_budget(budget)
    unit = unit.strip()
    if not unit:
        return []
    if _token_count(count_tokens_fn, unit) <= budget:
        return [unit]

    clauses = [piece.strip() for piece in _CLAUSE_SPLIT_RE.split(unit) if piece.strip()]
    if len(clauses) > 1:
        pieces: list[str] = []
        for clause in clauses:
            pieces.extend(_split_oversized_unit(count_tokens_fn, clause, budget))
        return pieces

    words = unit.split()
    if len(words) <= 1:
        _raise_oversized_atomic_unit(count_tokens_fn, unit, budget)

    pieces: list[str] = []
    current = ""
    for word in words:
        if _token_count(count_tokens_fn, word) > budget:
            _raise_oversized_atomic_unit(count_tokens_fn, word, budget)

        candidate = word if not current else f"{current} {word}"
        if _token_count(count_tokens_fn, candidate) <= budget:
            current = candidate
        else:
            # ``word`` was checked above, so flushing ``current`` always
            # leaves a valid next piece.
            pieces.append(current)
            current = word

    if current:
        pieces.append(current)
    return pieces


def build_chunks_from_blocks(
    count_tokens_fn: Callable[[str], int],
    narration_blocks: Iterable[dict],
    budget: int,
) -> list[dict]:
    """Build ordered, budget-safe chunk records from narration blocks.

    The returned records have the shape expected by the generator::

        {
            "text": "...",
            "paragraph_end": bool,
            "block_type": "paragraph" | "heading" | "list" | "quote",
            "speaker": "Name" | None,
            "block_index": int,
        }

    ``block_index`` is the original input position, including positions of
    empty blocks that were skipped. The final chunk from every non-empty block
    has ``paragraph_end=True`` so every semantic boundary receives a full
    pause. No returned chunk exceeds ``budget``; an indivisible oversized text
    token raises ``ValueError`` rather than violating that guarantee.
    """
    if not callable(count_tokens_fn):
        raise TypeError("count_tokens_fn must be callable")
    budget = _validated_budget(budget)

    chunk_records: list[dict] = []

    for block_index, block in enumerate(narration_blocks):
        block_type = block.get("block_type")
        if block_type not in _BLOCK_TYPES:
            choices = ", ".join(sorted(_BLOCK_TYPES))
            raise ValueError(
                f"Block {block_index} has invalid block_type {block_type!r}; "
                f"expected one of: {choices}"
            )

        text = block.get("text")
        if text is None or (isinstance(text, str) and not text.strip()):
            continue
        if not isinstance(text, str):
            raise TypeError(f"Block {block_index} text must be a string")

        units: list[str] = []
        for sentence in _split_sentences(text):
            units.extend(_split_oversized_unit(count_tokens_fn, sentence, budget))

        block_chunks: list[str] = []
        current = ""
        for unit in units:
            candidate = unit if not current else f"{current} {unit}"
            if _token_count(count_tokens_fn, candidate) <= budget:
                current = candidate
            else:
                if current:
                    block_chunks.append(current)
                # _split_oversized_unit guarantees this unit fits.
                current = unit
        if current:
            block_chunks.append(current)

        for chunk_index, chunk_text in enumerate(block_chunks):
            # Keep the budget invariant close to the public output boundary.
            # This also catches a malformed or non-deterministic token counter.
            if _token_count(count_tokens_fn, chunk_text) > budget:
                raise RuntimeError("token counter changed while chunks were built")
            chunk_records.append(
                {
                    "text": chunk_text,
                    "paragraph_end": chunk_index == len(block_chunks) - 1,
                    "block_type": block_type,
                    "speaker": block.get("speaker"),
                    "block_index": block_index,
                }
            )

    return chunk_records


def generation_settings_for(record: dict) -> dict:
    """Return semantic routing and pause treatment for one chunk.

    ``role`` identifies quote content; it does not itself promise a distinct
    voice. The generator maps this role to a model/voice according to the
    selected quote mode. In particular, ``two_voice`` may use the configured
    secondary voice while ``preserve`` can retain the narrator voice.
    """
    block_type = record.get("block_type")
    if block_type not in _BLOCK_TYPES:
        choices = ", ".join(sorted(_BLOCK_TYPES))
        raise ValueError(
            f"Invalid block_type {block_type!r}; expected one of: {choices}"
        )

    if block_type == "quote":
        return {
            "role": "quote",
            "extra_lead_pause_ms": 250,
            "extra_trail_pause_ms": 250,
        }
    return {
        "role": "narration",
        "extra_lead_pause_ms": 0,
        "extra_trail_pause_ms": 0,
    }


if __name__ == "__main__":
    from extractor import extract_blocks
    from narration_script import build_narration_blocks

    # Real usage supplies the active PocketTTS model's tokenizer.
    def fake_count_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    sample_path = BASE_DIR / "sample.html"
    html = sample_path.read_text(encoding="utf-8")

    extracted = extract_blocks(html)
    narration = build_narration_blocks(extracted, quote_mode="preserve")
    chunks = build_chunks_from_blocks(fake_count_tokens, narration, budget=44)

    print(f"{len(chunks)} chunks:\n")
    for index, chunk in enumerate(chunks, 1):
        settings = generation_settings_for(chunk)
        marker = " <-- QUOTE" if chunk["block_type"] == "quote" else ""
        print(
            f"{index:2d} [{chunk['block_type']:9s}] "
            f"tokens~{fake_count_tokens(chunk['text']):3d} "
            f"role={settings['role']:9s} "
            f"para_end={chunk['paragraph_end']}{marker}"
        )
        print(f"    {chunk['text'][:100]}")
