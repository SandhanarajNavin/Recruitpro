"""Text normalisation shared by the extractors."""

from __future__ import annotations

#: Below this many characters an extraction is treated as having failed. A scanned
#: image PDF yields a handful of stray glyphs rather than nothing at all, so an
#: emptiness check alone would let it through.
MIN_USABLE_CHARS = 80


def normalise_lines(text: str) -> str:
    """Strip trailing whitespace per line, preserving paragraph structure."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def too_little_text(text: str, minimum: int = MIN_USABLE_CHARS) -> bool:
    return len(text.strip()) < minimum
