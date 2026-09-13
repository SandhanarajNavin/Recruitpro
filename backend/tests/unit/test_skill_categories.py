"""Skill bucketing for the candidate profile.

Every case here is one that was actually wrong at some point while this was being
written. Substring matching put "Serverless" under Frontend because it contains
"less"; adding a trailing word boundary then dropped "MongoDB", "HTML5" and
"microservices" into "Other"; and an ungrouped alternation applied the boundaries
to only the first and last pattern in each category.
"""

from __future__ import annotations

import pytest

from app.ai.skill_categories import CATEGORY_ORDER, categorise, group


class TestSubstringTraps:
    @pytest.mark.parametrize(
        ("skill", "expected"),
        [
            # "serverless" contains "less", the CSS preprocessor.
            ("Serverless Architecture", "Backend"),
            ("LESS", "Frontend"),
            # "django" and "mongodb" both contain "go".
            ("Django", "Backend"),
            ("MongoDB", "Backend"),
            ("Go", "Backend"),
            ("Golang", "Backend"),
            # A pattern plus a suffix must still match.
            ("HTML5", "Frontend"),
            ("CSS3", "Frontend"),
            ("Microservices Architecture", "Backend"),
            # "dom" was removed precisely so this does not read as Frontend.
            ("Domain Driven Design", "Other"),
        ],
    )
    def test_boundaries(self, skill: str, expected: str):
        assert categorise(skill) == expected


class TestCategoryPrecedence:
    def test_cloud_wins_over_backend_for_managed_services(self):
        """A recruiter scans "Azure Cosmos DB" as cloud, not as a database."""
        assert categorise("Azure Cosmos DB") == "Cloud & DevOps"

    def test_git_and_github_are_tooling(self):
        assert categorise("Git") == "Cloud & DevOps"
        assert categorise("GitHub") == "Cloud & DevOps"


class TestGrouping:
    def test_duplicates_collapse_across_punctuation_and_case(self):
        grouped = group(["Node.js", "nodejs", "NODE.JS", "  node.js  "])
        assert grouped["Backend"] == ["Node.js"]

    def test_unknown_skills_are_kept_not_dropped(self):
        """Dropping them would hide a candidate's strongest skill."""
        grouped = group(["Firebase", "Rising Star Award"])
        assert grouped["Other"] == ["Firebase", "Rising Star Award"]

    def test_empty_categories_are_omitted(self):
        grouped = group(["Python", "FastAPI"])
        assert set(grouped) == {"Backend"}

    def test_categories_come_back_in_display_order(self):
        grouped = group(["PyTorch", "Docker", "React", "Firebase", "Python"])
        assert list(grouped) == [
            category for category in CATEGORY_ORDER if category in grouped
        ]

    def test_blank_and_whitespace_entries_are_ignored(self):
        assert group(["", "   ", "Python"]) == {"Backend": ["Python"]}

    def test_input_order_is_preserved_within_a_category(self):
        grouped = group(["MySQL", "Python", "FastAPI"])
        assert grouped["Backend"] == ["MySQL", "Python", "FastAPI"]

    def test_empty_input_yields_no_categories(self):
        assert group([]) == {}
