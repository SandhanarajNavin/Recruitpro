"""Unit tests for skill normalisation and role classification (doc §8).

No database and no credentials — these cover the pure functions that decide what a
skill *is* and what role a candidate holds, which is where a silent wrong answer
would corrupt every downstream filter.
"""

from __future__ import annotations

import pytest

from app.ai.classifier import (
    MIN_PRIMARY_CONFIDENCE,
    ROLE_DEFINITIONS,
    classify,
)
from app.ai.skill_normalizer import (
    canonical_industry_name,
    canonical_skill_name,
    slugify,
)


class TestSlugify:
    def test_collapses_case_and_spacing(self):
        assert slugify("  React   JS ") == "react js"

    def test_keeps_meaningful_punctuation(self):
        # "C++", ".NET" and "CI/CD" are distinct skills; stripping these characters
        # would merge them into their neighbours.
        assert slugify("C++") == "c++"
        assert slugify(".NET") == ".net"
        assert slugify("CI/CD") == "ci/cd"


class TestSkillNormalisation:
    @pytest.mark.parametrize(
        ("surface", "expected"),
        [
            ("ReactJS", "React"),
            ("react.js", "React"),
            ("React", "React"),
            ("Postgres", "PostgreSQL"),
            ("postgresql", "PostgreSQL"),
            ("k8s", "Kubernetes"),
            ("golang", "Go"),
            ("NodeJS", "Node.js"),
            ("large language models", "LLM"),
            ("scikit learn", "scikit-learn"),
        ],
    )
    def test_variants_collapse_onto_one_canonical_name(self, surface, expected):
        assert canonical_skill_name(surface) == expected

    def test_unknown_skill_returns_none_rather_than_guessing(self):
        """An unrecognised token must not create a near-duplicate canonical row."""
        assert canonical_skill_name("Blorptech 9000") is None
        assert canonical_skill_name("") is None

    def test_java_does_not_collapse_into_javascript(self):
        """The merge that would silently corrupt every hard filter naming either."""
        assert canonical_skill_name("Java") == "Java"
        assert canonical_skill_name("JavaScript") == "JavaScript"

    def test_industry_variants_collapse(self):
        assert canonical_industry_name("fin-tech") == "fintech"
        assert canonical_industry_name("e commerce") == "e-commerce"
        assert canonical_industry_name("cyber security") == "cybersecurity"
        assert canonical_industry_name("not an industry") is None


class TestRoleClassification:
    def test_frontend_engineer(self):
        result = classify(
            current_title="Senior Frontend Engineer",
            skills=["React", "TypeScript", "Next.js", "Redux", "Tailwind CSS"],
        )
        assert result.primary_role == "Frontend Engineer"
        assert result.confidence >= MIN_PRIMARY_CONFIDENCE

    def test_ai_engineer(self):
        result = classify(
            current_title="Machine Learning Engineer",
            skills=["PyTorch", "LLM", "NLP", "Machine Learning", "Python"],
        )
        assert result.primary_role == "AI Engineer"

    def test_classifies_from_skills_when_title_is_generic(self):
        """A generic title must not decide the role on its own."""
        result = classify(
            current_title="Senior Software Engineer",
            skills=["Kubernetes", "Terraform", "Docker", "Prometheus", "CI/CD"],
        )
        assert result.primary_role == "DevOps Engineer"

    def test_skill_signal_is_not_biased_by_role_list_length(self):
        """Roles defined with more skills must not be penalised.

        Normalising by len(role_skills) made 2 of 4 QA skills outrank 2 of 12
        backend skills for an obvious backend engineer.
        """
        result = classify(
            current_title="Senior Java Engineer",
            skills=["Java", "Spring Boot", "PostgreSQL", "Kafka", "REST"],
        )
        assert result.primary_role == "Backend Engineer"

    def test_secondary_roles_reported_for_a_hybrid_profile(self):
        result = classify(
            current_title="Full-Stack Engineer",
            skills=["React", "Node.js", "TypeScript", "PostgreSQL", "Next.js", "REST"],
        )
        assert result.primary_role == "Full Stack Engineer"
        assert all(role in ROLE_DEFINITIONS for role in result.secondary_roles)
        assert result.primary_role not in result.secondary_roles

    def test_no_evidence_yields_no_role_rather_than_a_guess(self):
        result = classify(current_title=None, skills=[])
        assert result.primary_role is None
        assert result.confidence == 0.0

    def test_confidence_is_bounded(self):
        result = classify(
            current_title="Backend Engineer",
            skills=list({s for d in ROLE_DEFINITIONS.values() for s in d["skills"]}),
        )
        assert 0.0 <= result.confidence <= 1.0
