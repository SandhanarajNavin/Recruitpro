"""Splitting resume text into passages.

A passage is quoted back to a recruiter verbatim, so these pin readability as much as
size: the boundaries, the size ceiling, and the reflow that undoes word-per-line text
extraction.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval.chunking import MAX_CHARS, chunk_resume

RESUME = """PRIYA RAGHUNATHAN
Senior Software Engineer, Payments

EXPERIENCE

Acme Payments — Senior Software Engineer (2020 - Present)
- Migrated the ledger from a monolith to event-sourced services
- Cut settlement latency from 400ms to 60ms

Globex — Software Engineer (2017 - 2020)
- Built the reconciliation pipeline in Python and Kafka

EDUCATION
BSc Computer Science, University of Madras
"""


class TestBoundaries:
    def test_empty_text_yields_no_passages(self):
        # An image-only resume must produce nothing, not one empty passage.
        assert chunk_resume("") == []
        assert chunk_resume("   \n\n \t ") == []

    def test_a_short_resume_still_produces_one_passage(self):
        # Otherwise a two-line resume would be unsearchable.
        assert chunk_resume("Marcus Feld. Senior Java Engineer.") == [
            "Marcus Feld. Senior Java Engineer."
        ]

    def test_passages_cover_the_content(self):
        chunks = chunk_resume(RESUME)
        joined = " ".join(chunks)
        for fact in ("event-sourced services", "Kafka", "University of Madras"):
            assert fact in joined, f"{fact!r} fell out of the passages"

    def test_no_passage_exceeds_the_budget(self):
        # The embedder truncates silently, so an oversized passage is text that
        # looks indexed and is not.
        long_resume = RESUME * 6
        assert all(len(chunk) <= MAX_CHARS for chunk in chunk_resume(long_resume))

    def test_overlap_carries_context_across_a_boundary(self):
        chunks = chunk_resume(RESUME * 4)
        assert len(chunks) > 1
        # Consecutive passages share text, so a fact split by a boundary is findable
        # from either side.
        assert any(
            chunks[index][-40:].strip() and chunks[index][-40:].strip() in chunks[index + 1]
            for index in range(len(chunks) - 1)
        )

    def test_overlap_does_not_begin_mid_word(self):
        for chunk in chunk_resume(RESUME * 4):
            assert chunk == chunk.strip()


class TestReflow:
    """The PDF extractor in use emits a blank line between every word for some
    files. Left alone, every word became its own passage."""

    def test_word_per_line_extraction_is_reflowed(self):
        # Shaped like the real case: one resume here extracted to 606 "lines"
        # averaging 6.5 characters, against 55 for a healthy plain-text file.
        shredded = "\n\n".join(RESUME.split())
        chunks = chunk_resume(shredded)

        assert "PRIYA RAGHUNATHAN Senior Software Engineer, Payments" in chunks[0]
        # Reads as prose rather than a vertical list of words.
        assert all("\n" not in chunk for chunk in chunks)

    def test_reflow_needs_enough_lines_to_be_confident(self):
        """The guard against misfiring on a genuinely brief resume.

        A few short lines are not evidence of shredding — a real resume can be five
        terse lines — so below the line threshold the text is left as written. At
        that length it is one passage either way, and the embedder splits on
        whitespace regardless, so the newlines cost nothing.
        """
        brief = "\n\n".join(["Jane", "Doe", "QA", "Engineer", "Selenium"])
        assert chunk_resume(brief) == ["Jane\nDoe\nQA\nEngineer\nSelenium"]

    def test_well_formed_text_is_left_alone(self):
        # Real paragraph structure must survive: it is what the packing uses.
        chunks = chunk_resume(RESUME)
        assert any("EXPERIENCE" in chunk for chunk in chunks)
        assert any("Globex" in chunk for chunk in chunks)


class TestPathologicalInput:
    def test_a_wall_of_text_with_no_punctuation_is_still_split(self):
        wall = "word " * 2000
        chunks = chunk_resume(wall)
        assert len(chunks) > 1
        assert all(len(chunk) <= MAX_CHARS for chunk in chunks)

    @pytest.mark.parametrize("text", ["...", "•", "—" * 100])
    def test_punctuation_only_input_does_not_crash(self, text):
        chunk_resume(text)
