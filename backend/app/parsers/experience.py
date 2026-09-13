"""Total professional experience, computed rather than asked for.

The model extracts employment *dates*; this module turns them into a number. That
split is the same one ``services/scoring.py`` makes for the composite score, and for
the same reason: a language model is a poor calculator and a worse clock. Asking it
to total overlapping spans against "today" produced answers that moved between runs
on identical input — 0.33 years for a span that was really 1.6.

Everything here is pure and deterministic: same entries plus same ``today`` always
give the same number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.ai.schemas import EmploymentEntry
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Used when a resume gives a year but no month ("2020 - 2022"). Mid-year is the
#: expected value across the possible readings: assuming January would inflate a
#: span by up to a year, December would deflate it by as much.
ASSUMED_MONTH = 7

#: Spans closer than this are treated as one continuous period. A month between two
#: roles is a notice period, not a career break.
CONTIGUITY_GAP_MONTHS = 1

#: Nobody has this much professional experience; anything beyond it is a parse
#: artefact, usually a typo'd year like 1025 or a birth date read as a start date.
MAX_PLAUSIBLE_YEARS = 60


def _as_months(year: int, month: int | None, *, fallback: int = ASSUMED_MONTH) -> int:
    """Absolute month index, so arithmetic is plain integers."""
    return year * 12 + (month or fallback) - 1


def _span(entry: EmploymentEntry, today: date) -> tuple[int, int] | None:
    """Half-open [start, end) month range, or None when unusable."""
    if entry.start_year is None:
        return None

    start = _as_months(entry.start_year, entry.start_month)

    if entry.is_current or entry.end_year is None:
        end = _as_months(today.year, today.month, fallback=today.month)
    else:
        end = _as_months(entry.end_year, entry.end_month)

    # A span ending before it starts is a parse error, not a negative duration.
    # Swapping is tempting but guesses at intent; dropping keeps the total honest.
    if end < start:
        logger.warning(
            "Discarding inverted employment span for %s @ %s (%s-%s to %s-%s)",
            entry.title, entry.company,
            entry.start_year, entry.start_month, entry.end_year, entry.end_month,
        )
        return None

    # A role dated into the future is equally suspect; clamp to today rather than
    # crediting experience nobody has had yet.
    horizon = _as_months(today.year, today.month, fallback=today.month)
    if start > horizon:
        return None
    return start, min(end, horizon)


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of month ranges, merging overlaps and near-contiguous gaps.

    Two concurrent roles must count once — the classic way a resume with parallel
    contracts reports double the real experience.
    """
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + CONTIGUITY_GAP_MONTHS:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def total_experience_years(
    entries: list[EmploymentEntry], *, today: date | None = None
) -> float | None:
    """Total professional experience in years, or None when it cannot be derived.

    None rather than 0.0 is deliberate: "no dated employment on this resume" and
    "this person has no experience" are different claims, and the caller decides
    how to treat the first.
    """
    today = today or datetime.now(UTC).date()

    spans = [span for entry in entries if (span := _span(entry, today)) is not None]
    if not spans:
        return None

    months = sum(end - start for start, end in merge_spans(spans))
    years = round(months / 12, 1)

    if years > MAX_PLAUSIBLE_YEARS:
        logger.warning("Computed %.1f years of experience; discarding as implausible", years)
        return None
    return years
