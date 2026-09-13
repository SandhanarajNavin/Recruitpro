"""Requirement evaluation (funnel stage 4).

The evaluator answers "does this candidate meet this requirement, and what in the
resume says so" — it never produces an overall score. That arithmetic belongs to
``services/scoring.py`` so a recruiter can trace any number back to a weight and an
evidence string.

Four categories are assessed here — required skills, experience, role and industry
(architecture doc §12, table 6). The fifth scoring factor, semantic similarity, comes
from the retrieval stage rather than from a judgement, and is injected by the scoring
engine.
"""

from __future__ import annotations

from app.ai import lexicon as lex
from app.ai.llm import LLMError, llm_available, run_structured
from app.ai.schemas import (
    CandidateEvaluation,
    CategoryAssessment,
    ParsedJobDescription,
    ParsedResume,
    RequirementVerdict,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CATEGORY_REQUIRED_SKILLS = "required_skills"
CATEGORY_EXPERIENCE = "experience"
CATEGORY_ROLE = "role"
CATEGORY_INDUSTRY = "industry"

EVALUATED_CATEGORIES = (
    CATEGORY_REQUIRED_SKILLS,
    CATEGORY_EXPERIENCE,
    CATEGORY_ROLE,
    CATEGORY_INDUSTRY,
)

SYSTEM = f"""You assess one candidate against one job's requirements and produce evidence.

Return exactly these four categories, using these ids:
- {CATEGORY_REQUIRED_SKILLS}: coverage of the required skills, weighted by importance.
- {CATEGORY_EXPERIENCE}: depth and length of relevant experience vs the minimum.
- {CATEGORY_ROLE}: role compatibility — does the candidate's current and prior job
  titles, seniority and responsibilities match the role being hired for. Education
  and certifications are evidence for this category, not a category of their own.
- {CATEGORY_INDUSTRY}: industry/domain fit. Exact industry match scores high, an
  adjacent or transferable industry scores mid, and no stated industry is neutral
  rather than a penalty.

Calibration:
- Use the full 0-100 range honestly. An average applicant is 50-60, not 80. Do not
  compress everyone into 70-90 — a shortlist whose scores do not separate is useless.
- Meeting a stated minimum is about 70; substantially exceeding it with relevant
  depth approaches 95.
- Penalise clearly over-levelled candidates as well as under-levelled ones: a
  director applying to a mid-level role is a scope and retention risk, not a bonus.

Hard rules:
- Do NOT produce an overall or composite score. Score each category 0-100 only.
- Every verdict needs `evidence`: short strings taken from the resume text. If you
  cannot quote something that supports a verdict, set met=false and
  confidence="low" — an unevidenced claim is worse than an admitted gap.
- A missing skill of importance 5 must cap the {CATEGORY_REQUIRED_SKILLS} score at 55.
- Never penalise {CATEGORY_INDUSTRY} for information the resume simply does not state.
- Judge the candidate against the job only, never against other candidates.
- Never credit a skill, qualification or certification the resume does not state.
- Do not consider or mention age, gender, nationality, ethnicity, marital status,
  photographs, or any other protected characteristic. Assess only demonstrated
  capability against the stated requirements."""


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, round(value, 2)))


def evaluate_offline(job: ParsedJobDescription, profile: ParsedResume) -> CandidateEvaluation:
    owned = {lex.normalise(skill) for skill in profile.skills}
    matched = [entry for entry in job.required_skills if lex.normalise(entry.skill) in owned]
    missing = [entry for entry in job.required_skills if lex.normalise(entry.skill) not in owned]

    total_weight = sum(entry.importance for entry in job.required_skills) or 1
    matched_weight = sum(entry.importance for entry in matched)

    preferred_matched = [
        skill for skill in job.preferred_skills if lex.normalise(skill) in owned
    ]
    preferred_bonus = (
        (len(preferred_matched) / len(job.preferred_skills)) * 8 if job.preferred_skills else 0.0
    )

    skills_score = (matched_weight / total_weight) * 100 + preferred_bonus
    if any(entry.importance >= 5 for entry in missing):
        # Same rule the LLM evaluator is instructed to follow, so the two engines
        # stay comparable.
        skills_score = min(skills_score, 55.0)

    skill_verdicts = [
        RequirementVerdict(
            requirement=entry.skill,
            category=CATEGORY_REQUIRED_SKILLS,
            met=True,
            confidence="high",
            evidence=[f"Resume lists {entry.skill}"],
            reasoning=f"Required skill (importance {entry.importance}/5) present in the profile.",
        )
        for entry in matched
    ] + [
        RequirementVerdict(
            requirement=entry.skill,
            category=CATEGORY_REQUIRED_SKILLS,
            met=False,
            confidence="high" if entry.importance >= 4 else "medium",
            evidence=[],
            reasoning=f"No mention of {entry.skill} anywhere in the resume text.",
        )
        for entry in missing
    ]

    years = float(profile.total_years_experience)
    if job.min_years_experience:
        ratio = years / job.min_years_experience
        experience_score = min(95.0, 70 + (ratio - 1) * 30) if ratio >= 1 else ratio * 70
    else:
        ratio = years / 5
        experience_score = min(95.0, ratio * 70)

    experience_verdict = RequirementVerdict(
        requirement=f"{job.min_years_experience}+ years of experience",
        category=CATEGORY_EXPERIENCE,
        met=years >= job.min_years_experience,
        confidence="high" if years else "low",
        evidence=[f"Dated roles span roughly {years:.0f} years"] if years else [],
        reasoning=f"{years:.0f} years against a {job.min_years_experience}-year minimum.",
    )

    # ── role compatibility ────────────────────────────────────────────
    # Seniority distance dominates: a title that reads right at the wrong level is a
    # worse match than an adjacent title at the right level.
    job_rank = lex.seniority_rank(f"{job.seniority} {job.title}")
    candidate_rank = lex.seniority_rank(profile.current_title or "")
    rank_gap = abs(job_rank - candidate_rank)
    rank_factor = {0: 1.0, 1: 0.75, 2: 0.45}.get(rank_gap, 0.2)

    job_title_tokens = set(lex.tokenise(job.title))
    candidate_title_tokens = set(lex.tokenise(profile.current_title or ""))
    title_overlap = (
        len(job_title_tokens & candidate_title_tokens) / len(job_title_tokens)
        if job_title_tokens
        else 0.0
    )
    role_score = 35 + rank_factor * 40 + title_overlap * 25

    role_verdicts = [
        RequirementVerdict(
            requirement=f"Role: {job.title} ({job.seniority})",
            category=CATEGORY_ROLE,
            met=rank_gap <= 1,
            confidence="high" if profile.current_title else "low",
            evidence=[f"Current title: {profile.current_title}"] if profile.current_title else [],
            reasoning=(
                f'"{profile.current_title or "no title stated"}" against a '
                f"{job.seniority} {job.title}; seniority gap {rank_gap}."
            ),
        )
    ]
    # Credentials are evidence for role fit rather than a scored category of their own.
    if profile.education or profile.certifications:
        role_verdicts.append(
            RequirementVerdict(
                requirement="Education / certifications",
                category=CATEGORY_ROLE,
                met=True,
                confidence="medium",
                evidence=(profile.education + profile.certifications)[:3],
                reasoning="Formal credentials on file, counted as role evidence.",
            )
        )
    if profile.achievements:
        role_verdicts.append(
            RequirementVerdict(
                requirement="Demonstrated ownership and impact",
                category=CATEGORY_ROLE,
                met=True,
                confidence="medium",
                evidence=profile.achievements[:3],
                reasoning=f"{len(profile.achievements)} quantified outcome(s) in the resume.",
            )
        )

    # ── industry fit ──────────────────────────────────────────────────
    candidate_domains = {lex.normalise(domain) for domain in profile.domains}
    matched_domains = [d for d in job.domains if lex.normalise(d) in candidate_domains]
    if job.domains:
        domain_overlap = len(matched_domains) / len(job.domains)
        # Exact coverage lands at 100, no overlap at 40 — a different industry is a
        # weaker signal, not a disqualification.
        industry_score = 40 + domain_overlap * 60
    else:
        domain_overlap = 0.0
        industry_score = 60.0  # unknown, per doc §12: neutral rather than penalised

    industry_verdicts = [
        RequirementVerdict(
            requirement=f"Industry: {domain}",
            category=CATEGORY_INDUSTRY,
            met=lex.normalise(domain) in candidate_domains,
            confidence="medium",
            evidence=(
                [f"Background in {domain}"]
                if lex.normalise(domain) in candidate_domains
                else []
            ),
            reasoning="Industry named in the job description.",
        )
        for domain in job.domains[:5]
    ] or [
        RequirementVerdict(
            requirement="Industry requirement",
            category=CATEGORY_INDUSTRY,
            met=True,
            confidence="low",
            evidence=[],
            reasoning="The job description names no industry, so this is scored neutral.",
        )
    ]

    return CandidateEvaluation(
        categories=[
            CategoryAssessment(
                category=CATEGORY_REQUIRED_SKILLS,
                score=_clamp(skills_score),
                reasoning=(
                    f"Covers {len(matched)}/{len(job.required_skills)} required skills "
                    f"({matched_weight / total_weight * 100:.0f}% of weighted importance) "
                    f"plus {len(preferred_matched)} preferred."
                ),
                verdicts=skill_verdicts,
            ),
            CategoryAssessment(
                category=CATEGORY_EXPERIENCE,
                score=_clamp(experience_score),
                reasoning=experience_verdict.reasoning,
                verdicts=[experience_verdict],
            ),
            CategoryAssessment(
                category=CATEGORY_ROLE,
                score=_clamp(role_score),
                reasoning=role_verdicts[0].reasoning,
                verdicts=role_verdicts,
            ),
            CategoryAssessment(
                category=CATEGORY_INDUSTRY,
                score=_clamp(industry_score),
                reasoning=(
                    f"Shares {domain_overlap * 100:.0f}% of the industries the role names."
                    if job.domains
                    else "The job description names no industry, so this is scored neutral."
                ),
                verdicts=industry_verdicts,
            ),
        ],
        matched_skills=[entry.skill for entry in matched],
        missing_skills=[entry.skill for entry in missing],
        strengths=_offline_strengths(job, profile, matched),
        concerns=_offline_concerns(job, profile, missing),
    )


def _offline_strengths(job, profile, matched) -> list[str]:
    out = []
    if matched:
        out.append(
            "Hands-on with " + ", ".join(entry.skill for entry in matched[:3])
        )
    years = float(profile.total_years_experience)
    if years >= job.min_years_experience:
        out.append(f"Clears the experience bar ({years:.0f}y vs {job.min_years_experience}y)")
    out.extend(profile.achievements[:2])
    return [item for item in out if item]


def _offline_concerns(job, profile, missing) -> list[str]:
    out = [
        f"No evidence of {entry.skill} (importance {entry.importance}/5)"
        for entry in missing[:3]
    ]
    years = float(profile.total_years_experience)
    if years < job.min_years_experience:
        out.append(
            f"Below the stated experience bar ({years:.0f}y vs {job.min_years_experience}y)"
        )
    job_rank = lex.seniority_rank(f"{job.seniority} {job.title}")
    candidate_rank = lex.seniority_rank(profile.current_title or "")
    if abs(job_rank - candidate_rank) >= 2:
        out.append(
            f'Seniority mismatch: "{profile.current_title}" against a {job.seniority} role'
        )
    return out


def _prompt(job: ParsedJobDescription, profile: ParsedResume, resume_text: str) -> str:
    required = "\n".join(
        f"- {entry.skill} (importance {entry.importance}/5)" for entry in job.required_skills
    )
    return (
        "<job>\n"
        f"Title: {job.title}\n"
        f"Seniority: {job.seniority}\n"
        f"Minimum years: {job.min_years_experience}\n"
        f"Required skills:\n{required}\n"
        f"Preferred skills: {', '.join(job.preferred_skills)}\n"
        f"Domains: {', '.join(job.domains)}\n"
        f"Responsibilities:\n" + "\n".join(f"- {item}" for item in job.responsibilities) + "\n"
        f"Education asked for: {', '.join(job.education) or 'none stated'}\n"
        "</job>\n\n"
        "<candidate_profile>\n"
        f"Current title: {profile.current_title}\n"
        f"Total years: {profile.total_years_experience}\n"
        f"Skills: {', '.join(profile.skills)}\n"
        f"Domains: {', '.join(profile.domains)}\n"
        f"Education: {', '.join(profile.education)}\n"
        f"Certifications: {', '.join(profile.certifications)}\n"
        "</candidate_profile>\n\n"
        "<resume_text>\n"
        f"{resume_text}\n"
        "</resume_text>"
    )


def evaluate(
    job: ParsedJobDescription, profile: ParsedResume, resume_text: str
) -> tuple[CandidateEvaluation, str]:
    """Returns the evaluation and the engine that produced it."""
    if llm_available():
        try:
            evaluation = run_structured(
                system=SYSTEM,
                prompt=_prompt(job, profile, resume_text),
                schema=CandidateEvaluation,
                effort="high",
                max_tokens=12_000,
            )
            missing_categories = set(EVALUATED_CATEGORIES) - {
                assessment.category for assessment in evaluation.categories
            }
            if missing_categories:
                raise LLMError(f"Evaluator omitted categories: {sorted(missing_categories)}")
            return evaluation, settings.recruiter_model
        except LLMError as exc:
            logger.warning("Evaluation fell back to the rules engine: %s", exc)

    return evaluate_offline(job, profile), "offline-rules-v1"
