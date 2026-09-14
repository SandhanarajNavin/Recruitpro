"""Tests for the deterministic core: text analysis, parsing, scoring, retrieval math.

These deliberately need no database and no credentials. The parts of the system that
decide a candidate's rank are pure functions, and this is the file that proves it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from seed_data import SAMPLE_CANDIDATES, SAMPLE_JOB_DESCRIPTION

from app.ai import lexicon as lex
from app.ai.embeddings import HashingEmbedder, cosine
from app.ai.llm import harden_schema
from app.ai.llm.evaluator import (
    CATEGORY_EXPERIENCE,
    CATEGORY_INDUSTRY,
    CATEGORY_REQUIRED_SKILLS,
    evaluate_offline,
)
from app.ai.llm.explainer import ExplanationInput, explain_shortlist
from app.ai.ranking.candidate_ranker import LexicalReranker, RerankCandidate
from app.ai.schemas import ParsedJobDescription, ParsedResume, RequiredSkill
from app.parsers.extractor import ExtractionError, extract_text
from app.parsers.jd_parser import hard_filters_from, parse_job_description_offline
from app.parsers.resume_parser import parse_resume_offline, profile_text_for_embedding
from app.services.resume_service import UploadRejected, validate_upload
from app.services.scoring import (
    CATEGORY_SEMANTIC,
    SCORING_CATEGORIES,
    compute_score,
    recommendation_for,
    similarity_to_score,
)

FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ── lexicon ───────────────────────────────────────────────────────────────
class TestLexicon:
    def test_word_boundaries_do_not_overmatch(self):
        found = lex.find_terms("We use Google Cloud and Golang tooling.", lex.SKILL_LEXICON)
        assert "Go" not in found, "'Go' must not match inside 'Google'/'Golang'"

    def test_punctuation_heavy_skills_match(self):
        text = "Built with C++, .NET and a CI/CD pipeline; also C# services."
        found = lex.find_terms(text, lex.SKILL_LEXICON)
        assert {"C++", ".NET", "CI/CD", "C#"} <= set(found)

    def test_dotted_skill_matches(self):
        assert "Next.js" in lex.find_terms("Shipped a Next.js app", lex.SKILL_LEXICON)
        assert "Node.js" in lex.find_terms("Node.js services", lex.SKILL_LEXICON)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Senior Software Engineer", 4),
            ("Staff Engineer, Infrastructure", 5),
            ("Junior Full-Stack Developer", 3),
            ("Head of Engineering", 6),
            ("Software Engineer", 3),
        ],
    )
    def test_seniority_ladder(self, text, expected):
        # "Junior ... Developer" hits both rank 2 and rank 3 words; max wins.
        assert lex.seniority_rank(text) == expected

    def test_timeline_merges_overlapping_spans(self):
        # Two concurrent roles across the same 4 years must count once.
        text = "Role A (2016 - 2020)\nRole B (2018 - 2020)"
        assert lex.years_from_timeline(text, today=FIXED_NOW) == 4

    def test_timeline_sums_disjoint_spans(self):
        text = "Role A (2010 - 2013)\nRole B (2016 - 2020)"
        assert lex.years_from_timeline(text, today=FIXED_NOW) == 7

    def test_timeline_handles_present(self):
        assert lex.years_from_timeline("Role (2020 - Present)", today=FIXED_NOW) == 6

    def test_timeline_rejects_impossible_ranges(self):
        assert lex.years_from_timeline("Role (2030 - 2035)", today=FIXED_NOW) == 0

    def test_continuation_lines_are_merged(self):
        text = "- Cut settlement discrepancies\n    by 92% across the ledger\n- Other bullet"
        lines = lex.logical_lines(text)
        assert any("92%" in line and "discrepancies" in line for line in lines)

    def test_contact_extraction(self):
        contact = lex.extract_contact("Reach me at jo.smith+cv@example.co.uk or 555-123-4567")
        assert contact["email"] == "jo.smith+cv@example.co.uk"
        assert contact["phone"] is not None


# ── resume parsing ────────────────────────────────────────────────────────
class TestResumeParsing:
    def test_parses_the_strongest_sample_candidate(self):
        _, _name, resume = SAMPLE_CANDIDATES[0]
        parsed = parse_resume_offline(resume)

        # The sample resumes open with a job title, not a name, so `guess_name`
        # correctly declines. The candidate's name comes from the upload filename
        # instead — see `resume_service.accept_upload`.
        assert parsed.full_name is None
        assert parsed.total_years_experience >= 8
        assert {"TypeScript", "PostgreSQL", "Kubernetes", "AWS"} <= set(parsed.skills)
        assert "payments" in parsed.domains
        assert parsed.education, "B.Tech line should be picked up"
        assert parsed.certifications, "AWS certification line should be picked up"
        assert any("92%" in item for item in parsed.achievements)

    def test_every_sample_resume_parses_to_something_usable(self):
        for _, name, resume in SAMPLE_CANDIDATES:
            parsed = parse_resume_offline(resume)
            assert parsed.skills, f"{name}: no skills extracted"
            assert parsed.total_years_experience > 0, f"{name}: no experience computed"

    def test_reads_a_name_header_when_the_resume_has_one(self):
        resume = (
            "Jordan Alvarez\n"
            "jordan.alvarez@example.com | +1 555 010 2030\n\n"
            "Senior Backend Engineer (2018 - 2024)\n"
            "- Built Node.js services on PostgreSQL, cut p99 latency by 40%\n"
        )
        parsed = parse_resume_offline(resume)
        assert parsed.full_name == "Jordan Alvarez"
        assert parsed.email == "jordan.alvarez@example.com"

    def test_declines_to_guess_a_name_from_a_heading(self):
        resume = (
            "PROFESSIONAL EXPERIENCE SUMMARY\n"
            "Senior Engineer (2019 - 2024)\n"
            "- Shipped things\n"
        )
        assert parse_resume_offline(resume).full_name is None

    def test_embedding_text_excludes_boilerplate(self):
        _, _, resume = SAMPLE_CANDIDATES[0]
        text = profile_text_for_embedding(parse_resume_offline(resume))
        assert "Skills:" in text
        assert len(text) < len(resume), "embedding text should be denser than the raw resume"


# ── JD parsing ────────────────────────────────────────────────────────────
class TestJobDescriptionParsing:
    def test_extracts_requirements_from_the_sample_jd(self):
        parsed = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)

        assert parsed.min_years_experience == 6
        assert lex.seniority_name(4) in parsed.seniority
        skills = {entry.skill for entry in parsed.required_skills}
        assert {"TypeScript", "Node.js", "PostgreSQL", "AWS", "Kubernetes"} <= skills
        assert "payments" in parsed.domains
        assert parsed.responsibilities

    def test_preferred_and_required_are_disjoint(self):
        parsed = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        required = {entry.skill for entry in parsed.required_skills}
        assert not (required & set(parsed.preferred_skills))

    def test_hard_filters_only_gate_on_critical_skills(self):
        parsed = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        filters = hard_filters_from(parsed)
        gated = set(filters["must_have_skills"])
        top = {entry.skill for entry in parsed.required_skills if entry.importance >= 5}
        assert gated == top
        # A gate must never be the whole required list, or the funnel has no funnel.
        assert len(gated) < len(parsed.required_skills)


# ── embeddings ────────────────────────────────────────────────────────────
class TestEmbeddings:
    def test_deterministic_across_instances(self):
        left = HashingEmbedder(dim=256).embed("Senior Python engineer, PostgreSQL")
        right = HashingEmbedder(dim=256).embed("Senior Python engineer, PostgreSQL")
        assert left == right, "vectors must be stable across processes and instances"

    def test_unit_length(self):
        vector = HashingEmbedder(dim=256).embed("TypeScript Next.js PostgreSQL")
        assert pytest.approx(sum(value * value for value in vector), abs=1e-9) == 1.0

    def test_empty_text_is_a_zero_vector(self):
        assert HashingEmbedder(dim=64).embed("   ") == [0.0] * 64

    def test_relevant_resume_beats_irrelevant_one(self):
        embedder = HashingEmbedder(dim=2048)
        job = embedder.embed(profile_text_for_embedding(parse_resume_offline(
            SAMPLE_CANDIDATES[0][2]
        )))
        near = embedder.embed(profile_text_for_embedding(parse_resume_offline(
            SAMPLE_CANDIDATES[4][2]  # Aisha Bello — full-stack, same stack
        )))
        far = embedder.embed(profile_text_for_embedding(parse_resume_offline(
            SAMPLE_CANDIDATES[9][2]  # Marcus Feld — Java/banking
        )))
        assert cosine(job, near) > cosine(job, far)


# ── reranking ─────────────────────────────────────────────────────────────
class TestReranker:
    def _candidates(self):
        job = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        entries = []
        for _, name, resume in SAMPLE_CANDIDATES:
            parsed = parse_resume_offline(resume)
            entries.append(
                RerankCandidate(
                    candidate_id=name,
                    title=parsed.current_title or "",
                    years=parsed.total_years_experience,
                    skills=parsed.skills,
                    domains=parsed.domains,
                    summary=profile_text_for_embedding(parsed),
                )
            )
        return job, entries

    def test_orders_payments_fullstack_above_frontend_only(self):
        job, entries = self._candidates()
        ranked = LexicalReranker().rank(job, entries)
        order = [entry.candidate_id for entry in ranked]
        assert order.index("Priya Raghunathan") < order.index("Mei-Lin Chow")
        assert order.index("Priya Raghunathan") < order.index("Grace Lindqvist")

    def test_scores_are_bounded_and_descending(self):
        job, entries = self._candidates()
        ranked = LexicalReranker().rank(job, entries)
        scores = [entry.relevance for entry in ranked]
        assert scores == sorted(scores, reverse=True)
        assert all(0 <= score <= 100 for score in scores)


# ── evaluation + scoring ──────────────────────────────────────────────────
class TestScoring:
    def _evaluate(self, index: int):
        job = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        profile = parse_resume_offline(SAMPLE_CANDIDATES[index][2])
        return job, evaluate_offline(job, profile)

    def test_evaluator_returns_no_overall_score(self):
        _, evaluation = self._evaluate(0)
        # The composite must come from the scoring engine, never the evaluator.
        assert not hasattr(evaluation, "composite_score")
        assert not hasattr(evaluation, "overall")

    def test_all_five_categories_are_scored(self):
        _, evaluation = self._evaluate(0)
        breakdown = compute_score(evaluation, cosine_similarity=0.4)
        assert set(breakdown.subscores) == set(SCORING_CATEGORIES)

    def test_composite_is_the_weighted_sum(self):
        _, evaluation = self._evaluate(0)
        breakdown = compute_score(evaluation, cosine_similarity=0.4)
        expected = sum(values["contribution"] for values in breakdown.subscores.values())
        assert breakdown.composite == pytest.approx(expected, abs=0.02)

    def test_weights_used_are_reported_per_category(self):
        _, evaluation = self._evaluate(0)
        breakdown = compute_score(evaluation, cosine_similarity=0.4)
        assert breakdown.subscores[CATEGORY_REQUIRED_SKILLS]["weight"] == pytest.approx(0.40)
        assert breakdown.subscores[CATEGORY_SEMANTIC]["weight"] == pytest.approx(0.10)

    def test_missing_category_scores_zero_not_renormalised(self):
        """A candidate with less evidence must not be inflated by dropping a weight."""
        _, evaluation = self._evaluate(0)
        evaluation.categories = [
            item for item in evaluation.categories if item.category != CATEGORY_INDUSTRY
        ]
        breakdown = compute_score(evaluation, cosine_similarity=0.0)
        assert breakdown.subscores[CATEGORY_INDUSTRY]["score"] == 0.0
        assert breakdown.subscores[CATEGORY_INDUSTRY]["weight"] == pytest.approx(0.10)

    def test_strong_candidate_outranks_weak_one(self):
        _, strong = self._evaluate(0)   # Priya — payments full-stack
        _, weak = self._evaluate(6)     # Grace — 2 years, junior
        strong_score = compute_score(strong, cosine_similarity=0.45).composite
        weak_score = compute_score(weak, cosine_similarity=0.25).composite
        assert strong_score > weak_score

    def test_missing_critical_skill_caps_the_skills_category(self):
        job = ParsedJobDescription(
            title="Senior Engineer",
            seniority="Senior",
            min_years_experience=5,
            required_skills=[
                RequiredSkill(skill="Kubernetes", importance=5),
                RequiredSkill(skill="TypeScript", importance=3),
            ],
            preferred_skills=[],
            domains=[],
            responsibilities=[],
            education=[],
            red_flags=[],
        )
        profile = parse_resume_offline(SAMPLE_CANDIDATES[2][2])  # Mei-Lin: no Kubernetes
        evaluation = evaluate_offline(job, profile)
        skills = next(
            item for item in evaluation.categories if item.category == CATEGORY_REQUIRED_SKILLS
        )
        assert skills.score <= 55.0
        assert "Kubernetes" in evaluation.missing_skills

    def test_experience_below_the_bar_scores_lower(self):
        job, _ = self._evaluate(0)
        junior = evaluate_offline(job, parse_resume_offline(SAMPLE_CANDIDATES[6][2]))
        senior = evaluate_offline(job, parse_resume_offline(SAMPLE_CANDIDATES[0][2]))
        junior_exp = next(
            i for i in junior.categories if i.category == CATEGORY_EXPERIENCE
        ).score
        senior_exp = next(
            i for i in senior.categories if i.category == CATEGORY_EXPERIENCE
        ).score
        assert junior_exp < senior_exp

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(95, "strong_hire"), (82, "strong_hire"), (70, "interview"), (55, "maybe"), (20, "pass")],
    )
    def test_recommendation_bands(self, score, expected):
        assert recommendation_for(score) == expected

    def test_similarity_mapping_is_bounded(self):
        # The suite runs on the hashing embedder, whose range is 0.0 - 0.5.
        assert similarity_to_score(None) == 0.0
        assert similarity_to_score(0.0) == 0.0
        assert similarity_to_score(0.5) == pytest.approx(100.0)
        assert similarity_to_score(0.9) == 100.0, "must clamp, not exceed 100"

    def test_the_mapping_follows_the_embedder_rather_than_a_fixed_scale(self, monkeypatch):
        """The regression this exists for.

        The mapping used to hard-code the hashing embedder's 0-0.5 scale. Swapping
        the default provider to Gemini, whose cosines sit between 0.65 and 0.94, sent
        every candidate to 100 — the category kept its full weight while telling the
        ranking nothing. Reading the range off the embedder is what prevents a repeat.
        """
        from app.ai.embeddings import embedding_service

        embedder = embedding_service.get_embedder()
        monkeypatch.setattr(embedder, "similarity_floor", 0.75)
        monkeypatch.setattr(embedder, "similarity_ceiling", 0.92)

        # Values that all pinned at 100 under the old fixed scale.
        assert similarity_to_score(0.70) == 0.0, "wrong-field cosine must not score"
        assert similarity_to_score(0.75) == 0.0
        assert similarity_to_score(0.835) == pytest.approx(50.0, abs=0.5)
        assert similarity_to_score(0.92) == pytest.approx(100.0)
        assert similarity_to_score(0.99) == 100.0

    def test_an_inverted_range_scores_zero_rather_than_inventing_a_distribution(
        self, monkeypatch
    ):
        from app.ai.embeddings import embedding_service

        embedder = embedding_service.get_embedder()
        monkeypatch.setattr(embedder, "similarity_floor", 0.9)
        monkeypatch.setattr(embedder, "similarity_ceiling", 0.9)

        assert similarity_to_score(0.95) == 0.0

    def test_every_shortlisted_candidate_is_scored_on_the_same_pool(self):
        """Scores must not depend on which other candidates are present."""
        job = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        profile = parse_resume_offline(SAMPLE_CANDIDATES[0][2])
        first = compute_score(evaluate_offline(job, profile), cosine_similarity=0.4).composite
        second = compute_score(evaluate_offline(job, profile), cosine_similarity=0.4).composite
        assert first == second


# ── explanations ──────────────────────────────────────────────────────────
class TestExplanations:
    def test_offline_explanations_are_evidence_derived(self):
        job = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        profile = parse_resume_offline(SAMPLE_CANDIDATES[0][2])
        evaluation = evaluate_offline(job, profile)
        breakdown = compute_score(evaluation, cosine_similarity=0.45)

        entry = ExplanationInput(
            candidate_id="c1",
            name="Priya Raghunathan",
            rank=1,
            composite_score=breakdown.composite,
            subscores=breakdown.subscores,
            evaluation=evaluation,
        )
        result, engine = explain_shortlist(job, [entry])

        assert engine.startswith("offline")
        explanation = result.candidates["c1"]
        assert explanation.why_match
        # Doc §12: a gap section is never empty, even for the top candidate.
        assert explanation.why_not
        assert result.panel_summary

    def test_empty_shortlist_is_handled(self):
        job = parse_job_description_offline(SAMPLE_JOB_DESCRIPTION)
        result, _ = explain_shortlist(job, [])
        assert result.candidates == {}


# ── schema hardening ──────────────────────────────────────────────────────
class TestSchemaHardening:
    """Gemini accepts an OpenAPI 3.0 subset, not full JSON Schema.

    Keys Pydantic emits that the subset rejects have to go, and $ref/$defs have to
    be inlined — a schema that still carries them is refused at the API boundary.
    """

    def test_disallowed_keys_are_stripped_everywhere(self):
        hardened = harden_schema(ParsedJobDescription.model_json_schema())

        def walk(node):
            """Yield schema nodes only — the contents of `properties` are field
            names, where a key called "title" is data rather than a keyword."""
            if isinstance(node, list):
                for item in node:
                    yield from walk(item)
            elif isinstance(node, dict):
                yield node
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        for sub in value.values():
                            yield from walk(sub)
                    else:
                        yield from walk(value)

        for node in walk(hardened):
            for banned in ("additionalProperties", "title", "default", "$defs", "$ref"):
                assert banned not in node, f"{banned} survived hardening in {node}"

    def test_references_are_inlined(self):
        """required_skills is a list of RequiredSkill, which Pydantic emits as a $ref."""
        hardened = harden_schema(ParsedJobDescription.model_json_schema())
        items = hardened["properties"]["required_skills"]["items"]
        assert items["type"] == "object"
        assert set(items["properties"]) == {"skill", "importance"}

    def test_optional_fields_become_nullable_not_anyof(self):
        """Pydantic renders `X | None` as anyOf; Gemini wants nullable instead."""
        hardened = harden_schema(ParsedResume.model_json_schema())
        summary = hardened["properties"]["summary"]
        assert "anyOf" not in summary
        assert summary.get("nullable") is True

    def test_structure_is_preserved(self):
        hardened = harden_schema(ParsedJobDescription.model_json_schema())
        assert hardened["type"] == "object"
        assert "title" in hardened["properties"]  # the JD field, not the schema key
        assert hardened["properties"]["min_years_experience"]["type"] == "integer"


# ── upload validation + extraction ────────────────────────────────────────
class TestUploadValidation:
    def test_rejects_empty_file(self):
        with pytest.raises(UploadRejected):
            validate_upload("cv.pdf", "application/pdf", b"")

    def test_rejects_oversized_file(self):
        with pytest.raises(UploadRejected, match="limit"):
            validate_upload("cv.pdf", "application/pdf", b"%PDF" + b"x" * (11 * 1024 * 1024))

    def test_rejects_unsupported_type(self):
        with pytest.raises(UploadRejected, match="Unsupported"):
            validate_upload("cv.exe", "application/x-msdownload", b"MZ\x90\x00")

    def test_rejects_pdf_with_wrong_magic_bytes(self):
        """A caller-supplied content type is not evidence about the bytes."""
        with pytest.raises(UploadRejected, match="%PDF"):
            validate_upload("cv.pdf", "application/pdf", b"<html>not a pdf</html>" * 20)

    def test_accepts_plain_text(self):
        validate_upload("cv.txt", "text/plain", b"x" * 500)

    def test_extraction_rejects_near_empty_output(self):
        with pytest.raises(ExtractionError, match="scanned image"):
            extract_text(b"tiny", "text/plain", "cv.txt")

    def test_extracts_plain_text(self):
        _, _, resume = SAMPLE_CANDIDATES[0]
        text = extract_text(resume.encode("utf-8"), "text/plain", "cv.txt")
        assert "Northwind Payments" in text
