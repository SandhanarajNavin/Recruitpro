"""Reranking (funnel stage 3).

Vector retrieval compresses a whole candidate into one vector, which blurs detail —
a reranker looks at the JD and the candidate together. A hosted cross-encoder is the
production answer; here there are two implementations with the same interface:

- ``LexicalReranker``  — weighted required-skill coverage plus token overlap. Free,
  deterministic, and surprisingly hard to beat on keyword-dense technical resumes.
- ``GeminiReranker``   — one batched call that scores JD↔candidate relevance. Reads
  the whole candidate summary rather than a bag of terms.

Both are ordering-only: nothing here reaches the final score, which keeps the
audit trail on the scoring engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai import lexicon as lex
from app.ai.llm import LLMError, llm_available, run_structured
from app.ai.schemas import ParsedJobDescription, RerankResponse
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RerankCandidate:
    candidate_id: str
    title: str
    years: float
    skills: list[str]
    domains: list[str]
    summary: str


@dataclass
class RerankResult:
    candidate_id: str
    relevance: float
    rationale: str


SYSTEM = """You order candidates by how well they fit a role.

Score each candidate 0-100 for overall relevance to the job description. This is an
ordering signal, not a hiring decision — later stages do the detailed assessment.

Judge each candidate against the job description only, never against the other
candidates. Weigh required skills and depth of relevant experience most heavily.
Give a one-line rationale grounded in what the candidate summary actually says."""


class LexicalReranker:
    name = "lexical-rerank-v1"

    def rank(
        self, job: ParsedJobDescription, candidates: list[RerankCandidate]
    ) -> list[RerankResult]:
        total_weight = sum(entry.importance for entry in job.required_skills) or 1
        job_tokens = set(
            lex.tokenise(
                " ".join(
                    [job.title, job.seniority]
                    + [entry.skill for entry in job.required_skills]
                    + job.preferred_skills
                    + job.domains
                    + job.responsibilities
                )
            )
        )

        results: list[RerankResult] = []
        for candidate in candidates:
            owned = {lex.normalise(skill) for skill in candidate.skills}
            matched = [
                entry for entry in job.required_skills if lex.normalise(entry.skill) in owned
            ]
            coverage = sum(entry.importance for entry in matched) / total_weight

            candidate_tokens = set(
                lex.tokenise(f"{candidate.title} {candidate.summary} {' '.join(candidate.skills)}")
            )
            overlap = (
                len(job_tokens & candidate_tokens) / len(job_tokens) if job_tokens else 0.0
            )

            years_ratio = (
                min(1.0, candidate.years / job.min_years_experience)
                if job.min_years_experience
                else 0.8
            )

            relevance = 100.0 * (0.60 * coverage + 0.25 * overlap + 0.15 * years_ratio)
            results.append(
                RerankResult(
                    candidate_id=candidate.candidate_id,
                    relevance=round(relevance, 2),
                    rationale=(
                        f"Covers {len(matched)}/{len(job.required_skills)} required skills "
                        f"({coverage * 100:.0f}% of weighted importance); "
                        f"{overlap * 100:.0f}% JD term overlap."
                    ),
                )
            )

        results.sort(key=lambda result: result.relevance, reverse=True)
        return results


class GeminiReranker:
    name = "gemini-rerank-v1"

    def rank(
        self, job: ParsedJobDescription, candidates: list[RerankCandidate]
    ) -> list[RerankResult]:
        blocks = []
        for candidate in candidates:
            blocks.append(
                f"<candidate id=\"{candidate.candidate_id}\">\n"
                f"Title: {candidate.title}\n"
                f"Years: {candidate.years:.0f}\n"
                f"Skills: {', '.join(candidate.skills)}\n"
                f"Domains: {', '.join(candidate.domains)}\n"
                f"Summary: {candidate.summary}\n"
                f"</candidate>"
            )

        prompt = (
            "<job>\n"
            f"Title: {job.title}\nSeniority: {job.seniority}\n"
            f"Minimum years: {job.min_years_experience}\n"
            "Required: "
            + ", ".join(f"{e.skill} (importance {e.importance}/5)" for e in job.required_skills)
            + "\nPreferred: "
            + ", ".join(job.preferred_skills)
            + "\nDomains: "
            + ", ".join(job.domains)
            + "\n</job>\n\n"
            + "\n".join(blocks)
            + f"\n\nReturn a relevance score for all {len(candidates)} candidates."
        )

        response = run_structured(
            system=SYSTEM,
            prompt=prompt,
            schema=RerankResponse,
            effort="medium",
            max_tokens=8_000,
        )

        by_id = {entry.candidate_id: entry for entry in response.rankings}
        results = []
        for candidate in candidates:
            entry = by_id.get(candidate.candidate_id)
            if entry is None:
                # A candidate the model skipped keeps a neutral score rather than
                # being silently dropped from the funnel.
                results.append(
                    RerankResult(candidate.candidate_id, 50.0, "Not scored by the reranker.")
                )
            else:
                results.append(
                    RerankResult(candidate.candidate_id, entry.relevance, entry.rationale)
                )
        results.sort(key=lambda result: result.relevance, reverse=True)
        return results


def rerank(
    job: ParsedJobDescription, candidates: list[RerankCandidate]
) -> tuple[list[RerankResult], str]:
    """Returns ordered candidates and the reranker that produced the order."""
    if not candidates:
        return [], "none"

    provider = settings.reranker_provider
    use_model = provider == "gemini" or (provider == "auto" and llm_available())

    if use_model:
        try:
            reranker = GeminiReranker()
            return reranker.rank(job, candidates), reranker.name
        except LLMError as exc:
            logger.warning("Rerank fell back to lexical: %s", exc)

    reranker = LexicalReranker()
    return reranker.rank(job, candidates), reranker.name
