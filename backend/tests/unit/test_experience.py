"""Deterministic experience arithmetic.

These exist because the model was doing this maths and getting a different answer
each run. Now that it is Python, the behaviour is pinned here.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ai.schemas import EmploymentEntry
from app.parsers.experience import (
    MAX_PLAUSIBLE_YEARS,
    merge_spans,
    total_experience_years,
)

TODAY = date(2026, 9, 4)


def entry(
    start_year=None, start_month=None, end_year=None, end_month=None, is_current=False
) -> EmploymentEntry:
    return EmploymentEntry(
        title="Engineer",
        company="Acme",
        start_year=start_year,
        start_month=start_month,
        end_year=end_year,
        end_month=end_month,
        is_current=is_current,
        highlights=[],
    )


class TestTotals:
    def test_the_case_that_started_this(self):
        """Feb 2025 to today (Sep 2026). The model said 0.33, then 1.0."""
        result = total_experience_years(
            [entry(2025, 2, is_current=True)], today=TODAY
        )
        assert result == pytest.approx(1.6, abs=0.05)

    def test_closed_span_with_months(self):
        # Jan 2020 - Jul 2022 is 30 months.
        assert total_experience_years(
            [entry(2020, 1, 2022, 7)], today=TODAY
        ) == pytest.approx(2.5, abs=0.05)

    def test_year_only_span_assumes_mid_year_on_both_ends(self):
        """"2020 - 2022" reads as 2.0, the expected value across the readings."""
        assert total_experience_years([entry(2020, None, 2022, None)], today=TODAY) == 2.0

    def test_is_current_resolves_against_today_not_a_stored_date(self):
        older = total_experience_years([entry(2024, 9, is_current=True)], today=date(2025, 9, 4))
        newer = total_experience_years([entry(2024, 9, is_current=True)], today=date(2026, 9, 4))
        assert older == pytest.approx(1.0, abs=0.05)
        assert newer == pytest.approx(2.0, abs=0.05)


class TestOverlaps:
    def test_concurrent_roles_count_once(self):
        """Two parallel contracts must not double the total."""
        spans = [entry(2020, 1, 2022, 1), entry(2020, 6, 2022, 1)]
        assert total_experience_years(spans, today=TODAY) == 2.0

    def test_fully_nested_role_adds_nothing(self):
        spans = [entry(2020, 1, 2024, 1), entry(2021, 1, 2022, 1)]
        assert total_experience_years(spans, today=TODAY) == 4.0

    def test_a_one_month_gap_is_treated_as_continuous(self):
        """A notice period is not a career break."""
        spans = [entry(2020, 1, 2021, 1), entry(2021, 2, 2022, 1)]
        assert total_experience_years(spans, today=TODAY) == 2.0

    def test_a_real_break_is_not_bridged(self):
        spans = [entry(2015, 1, 2016, 1), entry(2020, 1, 2021, 1)]
        assert total_experience_years(spans, today=TODAY) == 2.0

    def test_merge_spans_is_order_independent(self):
        assert merge_spans([(10, 20), (0, 5)]) == merge_spans([(0, 5), (10, 20)])


class TestBadData:
    def test_no_entries_is_unknown_not_zero(self):
        """"No dated employment" and "no experience" are different claims."""
        assert total_experience_years([], today=TODAY) is None

    def test_entry_without_a_start_is_ignored(self):
        assert total_experience_years([entry(None, None, 2022, 1)], today=TODAY) is None

    def test_inverted_span_is_discarded(self):
        """The model produced 2025-2024 for a real role; that is a parse error."""
        assert total_experience_years([entry(2025, 1, 2024, 1)], today=TODAY) is None

    def test_future_dated_role_is_ignored(self):
        assert total_experience_years([entry(2030, 1, 2031, 1)], today=TODAY) is None

    def test_open_ended_role_is_clamped_to_today(self):
        """An end date past today must not credit experience nobody has had yet."""
        assert total_experience_years(
            [entry(2024, 1, 2099, 1)], today=TODAY
        ) == pytest.approx(2.7, abs=0.1)

    def test_implausible_total_is_rejected(self):
        result = total_experience_years([entry(1900, 1, is_current=True)], today=TODAY)
        assert result is None or result <= MAX_PLAUSIBLE_YEARS

    def test_one_bad_entry_does_not_poison_the_good_ones(self):
        spans = [entry(2020, 1, 2022, 1), entry(2025, 1, 2024, 1)]
        assert total_experience_years(spans, today=TODAY) == 2.0


class TestDeterminism:
    def test_same_input_always_gives_the_same_number(self):
        spans = [
            entry(2018, 3, 2020, 6),
            entry(2020, 7, 2023, 2),
            entry(2023, 1, is_current=True),
        ]
        results = {total_experience_years(spans, today=TODAY) for _ in range(50)}
        assert len(results) == 1

    def test_entry_order_does_not_change_the_total(self):
        spans = [entry(2018, 3, 2020, 6), entry(2020, 7, 2023, 2)]
        assert total_experience_years(spans, today=TODAY) == total_experience_years(
            list(reversed(spans)), today=TODAY
        )
