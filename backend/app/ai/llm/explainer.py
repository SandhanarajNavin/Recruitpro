"""Explanation generation (architecture doc §12).

The guardrail here is structural, not a prompt instruction: this module is handed
the evidence record and the computed scores, and never the resume text or the
original file. It therefore has nothing to invent a qualification from.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.llm import LLMError, llm_available, run_structured
from app.ai.schemas import (
    CandidateEvaluation,
    CandidateExplanation,
    ParsedJobDescription,
    ShortlistExplanations,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SYSTEM = """You write the recruiter-facing explanation for a ranked shortlist.

You are given, for each candidate, the evidence a prior stage collected and the score
the scoring engine computed. You have NOT been given the resumes.

Hard rules:
- Use only the supplied evidence strings and category scores. You must not add any
  qualification, employer, skill, certification or number that does not appear in
  the evidence you were given.
- why_match: 3-5 short bullets, strongest first, each tied to a concrete piece of the
  supplied evidence. A recruiter reads these before anything else — make them
  specific, never generic praise.
- why_not: the real gaps — missing required skills, experience below the bar, missing
  preferred skills, or evidence too thin to judge. Phrase each so a hiring manager
  could probe it in an interview. Never leave this empty just because a candidate
  ranked first; if the only gap is thin evidence, say that.
- verdict: one or two sentences on why this candidate sits at this rank, explicitly
  comparative ("stronger production Kubernetes evidence than the candidates below,
  but no fintech exposure"). This is the only place comparison is allowed.
- panel_summary: 2-4 sentences on the shape of this pool — where it is strong, where
  the search may need to widen, and any tie the hiring manager should break
  themselves.
- Do not restate the score as a justification for itself.
- Never reference age, gender, nationality, ethnicity or any protected
  characteristic, and never speculate about them."""


@dataclass
class ExplanationInput:
    candidate_id: str
    name: str
    rank: int
    composite_score: float
    subscores: dict[str, dict[str, float]]
    evaluation: CandidateEvaluation


def _fallback_explanation(entry: ExplanationInput) -> CandidateExplanation:
    """Rule-derived explanation, used when no model is available or the call fails.

    Built from the same evidence record the model would have seen, so the shape of
    the output does not change with the engine.
    """
    why_match = list(entry.evaluation.strengths)
    for assessment in entry.evaluation.categories:
        for verdict in assessment.verdicts:
            if verdict.met and verdict.evidence:
                line = f"{verdict.requirement}: {verdict.evidence[0]}"
                if line not in why_match:
                    why_match.append(line)

    why_not = list(entry.evaluation.concerns)
    for assessment in entry.evaluation.categories:
        for verdict in assessment.verdicts:
            if not verdict.met:
                line = f"{verdict.requirement} — {verdict.reasoning}"
                if line not in why_not:
                    why_not.append(line)
            elif verdict.confidence == "low":
                why_not.append(f"{verdict.requirement} — evidence is thin, worth probing")

    if not why_not:
        why_not = ["No gap detected against the stated requirements; verify in interview."]

    strongest = max(
        entry.subscores.items(), key=lambda item: item[1].get("contribution", 0.0), default=None
    )
    verdict = (
        f"Ranked #{entry.rank} on a composite of {entry.composite_score:.1f}."
        + (
            f" The largest contribution came from {strongest[0].replace('_', ' ')}."
            if strongest
            else ""
        )
    )

    return CandidateExplanation(
        why_match=why_match[:6],
        why_not=why_not[:6],
        verdict=verdict,
    )


def _prompt(job: ParsedJobDescription, entries: list[ExplanationInput]) -> str:
    blocks = []
    for entry in entries:
        verdict_lines = []
        for assessment in entry.evaluation.categories:
            verdict_lines.append(
                f"  [{assessment.category}] score {assessment.score:.0f} — {assessment.reasoning}"
            )
            for verdict in assessment.verdicts:
                mark = "MET" if verdict.met else "NOT MET"
                evidence = "; ".join(verdict.evidence) or "no supporting evidence found"
                verdict_lines.append(
                    f"    - {verdict.requirement}: {mark} "
                    f"(confidence {verdict.confidence}) — {evidence}"
                )

        subscore_lines = ", ".join(
            f"{name} {values['score']:.0f}×{values['weight']:.2f}"
            for name, values in entry.subscores.items()
        )

        blocks.append(
            f'<candidate id="{entry.candidate_id}" name="{entry.name}" rank="{entry.rank}">\n'
            f"Composite score: {entry.composite_score:.1f}\n"
            f"Weighted categories: {subscore_lines}\n"
            f"Matched skills: {', '.join(entry.evaluation.matched_skills) or 'none'}\n"
            f"Missing skills: {', '.join(entry.evaluation.missing_skills) or 'none'}\n"
            "Evidence:\n" + "\n".join(verdict_lines) + "\n"
            "</candidate>"
        )

    return (
        "<job>\n"
        f"Title: {job.title}\nSeniority: {job.seniority}\n"
        f"Minimum years: {job.min_years_experience}\n"
        "Required: "
        + ", ".join(f"{e.skill} ({e.importance}/5)" for e in job.required_skills)
        + "\nPreferred: "
        + ", ".join(job.preferred_skills)
        + "\n</job>\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite the panel summary and one explanation per candidate, keyed by "
        "candidate id."
    )


def explain_shortlist(
    job: ParsedJobDescription, entries: list[ExplanationInput]
) -> tuple[ShortlistExplanations, str]:
    """Returns explanations for every entry and the engine that produced them."""
    if not entries:
        empty = ShortlistExplanations(
            panel_summary="No candidates reached the shortlist.", candidates={}
        )
        return empty, "none"

    if llm_available():
        try:
            result = run_structured(
                system=SYSTEM,
                prompt=_prompt(job, entries),
                schema=ShortlistExplanations,
                effort="high",
                max_tokens=12_000,
            )
            # Any candidate the model skipped gets the rule-derived explanation
            # rather than an empty card.
            for entry in entries:
                if entry.candidate_id not in result.candidates:
                    result.candidates[entry.candidate_id] = _fallback_explanation(entry)
            return result, settings.recruiter_model
        except LLMError as exc:
            logger.warning("Explanation fell back to rule-derived text: %s", exc)

    top = entries[0]
    summary = (
        f"{len(entries)} candidates shortlisted. {top.name} leads on a composite of "
        f"{top.composite_score:.1f}. Scores are computed from weighted category "
        f"evidence — review the evidence before acting on a rank."
    )
    return (
        ShortlistExplanations(
            panel_summary=summary,
            candidates={entry.candidate_id: _fallback_explanation(entry) for entry in entries},
        ),
        "offline-rules-v1",
    )
