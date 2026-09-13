"""JD parser (architecture doc §9): free text → structured requirements.

Gemini when credentials exist, deterministic lexicon parsing otherwise. Both return
``ParsedJobDescription``, so the caller never branches on which one ran.
"""

from __future__ import annotations

import re

from app.ai import lexicon as lex
from app.ai.llm import LLMError, llm_available, run_structured
from app.ai.schemas import ParsedJobDescription, RequiredSkill
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SYSTEM = """You extract hiring requirements from a job description.

Rules:
- Separate genuinely required skills from preferred ones. Only treat a skill as
  required when the description says so — a skill mentioned once in passing is
  preferred, not required.
- Set importance 5 only for a skill the role cannot be done without; 3 is the
  normal weight for a stated requirement.
- min_years_experience is the number the description states. If it states none,
  infer from the seniority wording and say so in `seniority`.
- Extract responsibilities as short phrases taken from the description.
- red_flags are signals that should disqualify or heavily penalise a candidate,
  only if the description actually implies them.
- Never invent a requirement that is not supported by the text."""


def _required_block(job_description: str) -> str:
    """The paragraph(s) that actually state requirements, if the JD has such a block."""
    blocks = re.split(r"\n(?=[A-Z])", job_description)
    keep = [
        block
        for block in blocks
        if re.search(r"require|must have|essential|you have|qualification", block, re.IGNORECASE)
    ]
    return "\n".join(keep)


def parse_job_description_offline(job_description: str) -> ParsedJobDescription:
    all_skills = lex.find_terms(job_description, lex.SKILL_LEXICON)
    required_block = _required_block(job_description)
    required_names = lex.find_terms(required_block, lex.SKILL_LEXICON) if required_block else []

    normalised = lex.normalise(job_description)
    required: list[RequiredSkill] = []
    for skill in all_skills:
        if required_names and skill not in required_names:
            continue
        mentions = len(re.findall(re.escape(lex.normalise(skill)), normalised))
        # More mentions reads as more load-bearing; cap at 5.
        required.append(RequiredSkill(skill=skill, importance=min(5, 3 + min(2, mentions - 1))))

    if not required:
        required = [RequiredSkill(skill=skill, importance=3) for skill in all_skills]

    required_set = {entry.skill for entry in required}
    first_line = next(
        (line.strip() for line in job_description.split("\n") if line.strip()), "Open role"
    )

    # Rank from the title line, not the whole document. A responsibility bullet like
    # "Lead the migration of our checkout services" uses "lead" as a verb, and
    # scanning the full text reads that as a staff-level role — which then shows the
    # recruiter the wrong seniority and wrongly flags senior candidates as mismatched.
    rank = lex.seniority_rank(first_line)
    if rank == lex.DEFAULT_SENIORITY_RANK:
        # The title carried no level word; fall back to the body.
        rank = lex.seniority_rank(job_description)

    return ParsedJobDescription(
        title=first_line[:90],
        seniority=f"{lex.seniority_name(rank)} (inferred from the description wording)",
        min_years_experience=lex.stated_years(job_description) or max(0, (rank - 2) * 2),
        required_skills=required,
        preferred_skills=[skill for skill in all_skills if skill not in required_set],
        domains=lex.find_terms(job_description, lex.DOMAIN_LEXICON),
        responsibilities=lex.find_responsibilities(job_description),
        education=lex.find_education(lex.logical_lines(job_description)),
        red_flags=[],
    )


def parse_job_description(job_description: str) -> tuple[ParsedJobDescription, str]:
    """Returns the parsed requirements and the name of the parser that produced them."""
    if llm_available():
        try:
            parsed = run_structured(
                system=SYSTEM,
                prompt=f"<job_description>\n{job_description}\n</job_description>",
                schema=ParsedJobDescription,
                effort="low",
                model=settings.extraction_model,
                max_tokens=8_000,
            )
            # Report the model that actually ran, not the headline one. These
            # passes use the cheaper extraction model (doc §25), and recording
            # recruiter_model here made parser_model provenance a lie — §20
            # wants the stored version to be reproducible.
            return parsed, settings.extraction_model
        except LLMError as exc:
            logger.warning("JD parse fell back to the offline parser: %s", exc)

    return parse_job_description_offline(job_description), "offline-lexicon-v1"


def hard_filters_from(parsed: ParsedJobDescription) -> dict[str, object]:
    """Gates applied in SQL before retrieval (funnel stage 1).

    Only importance-5 skills and the stated minimum years become hard gates — a gate
    is a candidate the score must not be able to rescue, so the bar for adding one
    is deliberately high.
    """
    return {
        "must_have_skills": [e.skill for e in parsed.required_skills if e.importance >= 5],
        "min_years": parsed.min_years_experience,
    }


def job_embedding_text(parsed: ParsedJobDescription) -> str:
    """The JD text a job vector is built from.

    Mirrors `resume_parser.profile_text_for_embedding`: same emphasis on titles and
    skills so the candidate and job vectors live in a comparable space.
    """
    parts = [
        parsed.title,
        parsed.seniority,
        "Required: " + ", ".join(entry.skill for entry in parsed.required_skills),
        "Preferred: " + ", ".join(parsed.preferred_skills),
        "Domains: " + ", ".join(parsed.domains),
        " ".join(parsed.responsibilities),
    ]
    return "\n".join(part for part in parts if part.strip())
