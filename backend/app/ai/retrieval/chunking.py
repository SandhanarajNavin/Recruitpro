"""Splitting resume text into retrievable passages.

Paragraph-packing rather than fixed-width slicing. A resume is already sectioned —
blank lines separate roles, bullet groups and headings — so those boundaries carry
real meaning, and cutting every N characters routinely severs a bullet from the job
title it belongs to. Packing whole paragraphs up to a budget keeps each passage
readable on its own, which matters because a passage is quoted back to the recruiter,
not just scored.

Chunks overlap by a tail of the previous chunk so a fact spanning a boundary is still
findable from either side.
"""

from __future__ import annotations

import re

#: Characters, not tokens. The embedder takes text and the resumes here run
#: 800-5,500 characters, so a ~700 budget gives a handful of passages per resume:
#: small enough that a hit points at one role rather than the whole career, large
#: enough to keep a title and its bullets together.
MAX_CHARS = 700

#: Carried from the end of the previous chunk. Roughly one bullet.
OVERLAP_CHARS = 120

#: Below this a passage is a heading offcut ("EXPERIENCE") with nothing to retrieve.
MIN_CHARS = 40

_BLANK_LINE = re.compile(r"\n\s*\n+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")

#: A resume whose non-empty lines average fewer characters than this has been
#: shredded by text extraction rather than written that way. The PDF extractor in
#: use emits a blank line between every *word* for some files: one real resume here
#: has 606 "lines" averaging 6.5 characters, against 55 for a healthy plain-text
#: one. The gap is wide enough that a threshold in the middle is safe.
_SHREDDED_LINE_LENGTH = 25

#: Below this many lines the average is noise — a genuinely short resume can have a
#: few brief lines without being shredded.
_SHREDDED_MIN_LINES = 20


def _reflow(text: str) -> str:
    """Undo word-per-line text extraction, leaving well-formed text alone.

    Returned as a single paragraph on purpose: the blank lines in a shredded file
    fall between words, so they carry no structure worth preserving and every one of
    them would otherwise become its own passage.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < _SHREDDED_MIN_LINES:
        return text

    average = sum(len(line.strip()) for line in lines) / len(lines)
    if average >= _SHREDDED_LINE_LENGTH:
        return text
    return _WHITESPACE.sub(" ", text).strip()


def _hard_split(text: str, limit: int) -> list[str]:
    """Break one oversized paragraph, preferring sentence boundaries."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence

    if current.strip():
        pieces.append(current.strip())

    # A single sentence longer than the budget — a wall-of-text resume with no
    # punctuation. Slice it rather than emitting something the embedder truncates.
    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            out.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            out.append(piece)
    return out


def chunk_resume(
    text: str,
    *,
    max_chars: int = MAX_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
    min_chars: int = MIN_CHARS,
) -> list[str]:
    """Resume text as overlapping passages, in document order.

    Returns an empty list for text with nothing in it — an unparsed or image-only
    resume must produce no passages rather than one empty one.
    """
    if not text or not text.strip():
        return []

    # Lines inside a paragraph are joined with a space rather than kept as newlines.
    # Resume line breaks are layout, not meaning, and the PDF extractor in use emits
    # one word per line for some files — preserving those turns a quoted passage into
    # a vertical list of words, and makes the vector partly a function of newlines.
    #
    # Paragraphs are budgeted at max_chars - overlap_chars so a carried tail cannot
    # push a chunk past the limit and into the embedder's truncation.
    budget = max(1, max_chars - overlap_chars)
    paragraphs: list[str] = []
    for block in _BLANK_LINE.split(_reflow(text).strip()):
        collapsed = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if collapsed:
            paragraphs.extend(_hard_split(collapsed, budget))

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > max_chars:
            chunks.append(current)
            # Overlap on a whitespace boundary, so the carried tail does not begin
            # mid-word and skew the vector.
            tail = current[-overlap_chars:]
            space = tail.find(" ")
            current = tail[space + 1 :] if space != -1 else ""
            current = f"{current}\n{paragraph}".strip() if current else paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph

    if current:
        chunks.append(current)

    # Keep a sole short chunk: a two-line resume still deserves to be searchable.
    # Only drop shorts when there is something better alongside them.
    kept = [chunk for chunk in chunks if len(chunk) >= min_chars]
    return kept or chunks[:1]
