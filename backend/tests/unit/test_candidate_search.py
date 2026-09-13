"""Free-text candidate search: role matching, spacing and misspellings.

The assistant used to answer "I couldn't find any candidates matching 'fullstack
developer'" for a repository that held a Full Stack Developer, because the query
only ever looked at names and emails. These pin the matching rules that replaced
that, at the level that does not need a database.
"""

from __future__ import annotations

import pytest

from app.services.candidate_service import _fuzzy_matches, _role_variants, _squash_text


class TestSquashText:
    def test_separators_and_case_are_removed(self):
        assert _squash_text("Full-Stack Developer") == "fullstackdeveloper"
        assert _squash_text("Full Stack Developer") == "fullstackdeveloper"

    def test_a_query_with_no_letters_or_digits_squashes_empty(self):
        # The caller relies on this to skip the role clause entirely.
        assert _squash_text("  -- ") == ""


class TestRoleVariants:
    def test_adjacent_words_are_joined(self):
        # "fullstack" must be reachable, or a typo spanning the word boundary
        # ("fulstak") cannot match either half on its own.
        assert {"full", "stack", "fullstack", "fullstackdeveloper"} <= _role_variants(
            "Full Stack Developer"
        )


class TestFuzzyMatches:
    ROLES = ["Full Stack Developer", "Full Stack Engineer"]

    @pytest.mark.parametrize(
        "query",
        ["fullstack developer", "fulstak", "fulstak developer", "fullstak", "devlopr"],
    )
    def test_misspellings_reach_the_role(self, query):
        assert _fuzzy_matches(query, self.ROLES)

    @pytest.mark.parametrize("query", ["plumber", "accountant", "phlebotomist"])
    def test_unrelated_words_do_not(self, query):
        assert not _fuzzy_matches(query, self.ROLES)

    def test_every_word_must_match(self):
        # "engineer" alone should not satisfy a search for a DevOps engineer.
        assert not _fuzzy_matches("devops engineer", ["Frontend Engineer"])
        assert _fuzzy_matches("devops engineer", ["DevOps Engineer"])

    def test_no_targets_and_no_query_never_match(self):
        assert not _fuzzy_matches("fulstak", [])
        assert not _fuzzy_matches("", self.ROLES)
        assert not _fuzzy_matches("fulstak", [None, ""])
