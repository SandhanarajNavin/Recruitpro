"""Resume parser (architecture doc §7 step 5): resume text → candidate profile.

This runs once per resume, in a worker, with no job description in scope.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.ai import lexicon as lex
from app.ai.llm import LLMError, llm_available, run_structured
from app.ai.schemas import ParsedResume
from app.core.config import settings
from app.core.logging import get_logger
from app.parsers.experience import total_experience_years

logger = get_logger(__name__)

SYSTEM = """You extract a structured profile from resume text.

Rules:
- Report only what the resume states. Never infer a skill from a job title, and
  never add a technology the text does not mention.
- experience: one entry per PROFESSIONAL EXPERIENCE role.
  - Extract start_year and start_month, and end_year and end_month, for every role
    that carries dates. Months matter — "Feb '25" is start_year 2025,
    start_month 2.
  - Parse abbreviated forms such as "Sep '24", "Feb '25", "03/2024".
  - For "Present", "Current" or an open-ended role, set is_current true and leave
    end_year and end_month null. Do not substitute a date you have guessed.
  - Omit month when the resume genuinely gives only a year.
  - Do NOT include education, certification or personal-project dates here.
- total_years_experience:
  - Leave this null unless the resume states a total in words, such as
    "6+ years of experience". Report that stated figure and nothing else.
  - Do not add up the employment spans yourself — the dates you extract above are
    totalled separately. A computed total here will be discarded.
- achievements:
  - Only include lines containing a concrete number, percentage, measurable
    result, performance improvement, quantity, scale, or other explicitly
    measurable outcome.
  - Do not treat dates, years of experience, or job titles alone as achievements.
- skills:
  - Extract technologies, tools, frameworks, platforms, programming languages,
    methodologies, and practices explicitly named in the resume.
  - Deduplicate skills while preserving the resume's terminology.
  - Do not infer skills from job titles, responsibilities, or context.
- If a field is genuinely absent, return null or an empty list — do not guess.
- Never return 0 for total_years_experience merely because you cannot parse a
  date. If employment dates are present but parsing fails, return null."""


def parse_resume_offline(text: str) -> ParsedResume:
    lines = lex.logical_lines(text)
    contact = lex.extract_contact(text)
    timeline_years = lex.years_from_timeline(text)
    stated = lex.stated_years(text)

    return ParsedResume(
        full_name=lex.guess_name(text),
        email=contact["email"],
        phone=contact["phone"],
        location=None,
        current_title=lex.find_title(lines),
        total_years_experience=float(max(timeline_years, stated or 0)),
        summary=None,
        skills=lex.find_terms(text, lex.SKILL_LEXICON),
        domains=lex.find_terms(text, lex.DOMAIN_LEXICON),
        # The offline parser does not attempt to segment employment history into
        # structured entries; that is the one thing the LLM parser adds materially.
        experience=[],
        education=lex.find_education(lines),
        certifications=lex.find_certifications(lines),
        projects=[],
        achievements=lex.find_achievements(lines),
    )


def parse_resume(text: str) -> tuple[ParsedResume, str]:
    """Returns the parsed profile and the parser that produced it."""
    if llm_available():
        try:
            parsed = run_structured(
                system=SYSTEM,
                # Today's date is stated explicitly. A model has no reliable notion
                # of "now" — left to itself it resolved "Feb '25 – Present" against
                # its training cutoff and returned 0.33 years for what is actually
                # ~1.6, understating the candidate five-fold.
                prompt=(
                    f"<today>{datetime.now(UTC).date().isoformat()}</today>\n"
                    f"<resume>\n{text}\n</resume>"
                ),
                schema=ParsedResume,
                effort="low",
                model=settings.extraction_model,
                max_tokens=8_000,
            )
            # Contact details are mechanical; trust the regex over the model when the
            # model omitted them, since a dropped email costs the recruiter a lookup.
            fallback = lex.extract_contact(text)
            if not parsed.email:
                parsed.email = fallback["email"]
            if not parsed.phone:
                parsed.phone = fallback["phone"]

            # Years are computed from the extracted dates, never taken from the
            # model. Asking it to total overlapping spans against "today" gave a
            # different answer on each run of identical input. The model's own
            # figure is kept only when it read an explicit total off the page and
            # no dated roles exist to compute from.
            stated = parsed.total_years_experience
            computed = total_experience_years(parsed.experience)
            if computed is not None:
                parsed.total_years_experience = computed
            elif stated is None:
                # No dated roles and no stated total: fall back to scanning the raw
                # text before giving up, then to 0 so the NOT NULL column is safe.
                parsed.total_years_experience = float(
                    max(lex.years_from_timeline(text), lex.stated_years(text) or 0)
                )
            if stated is not None and computed is not None and abs(stated - computed) >= 1:
                logger.info(
                    "Resume states %.1f years, dates compute to %.1f; using computed.",
                    stated, computed,
                )
            # Report the model that actually ran, not the headline one. These
            # passes use the cheaper extraction model (doc §25), and recording
            # recruiter_model here made parser_model provenance a lie — §20
            # wants the stored version to be reproducible.
            return parsed, settings.extraction_model
        except LLMError as exc:
            logger.warning("Resume parse fell back to the offline parser: %s", exc)

    return parse_resume_offline(text), "offline-lexicon-v1"


def profile_text_for_embedding(parsed: ParsedResume) -> str:
    """The canonical text a candidate vector is built from.

    Deliberately not the raw resume: formatting noise, addresses and boilerplate
    dilute the vector. Skills and titles are repeated because they carry most of the
    retrieval signal.
    """
    # The years field is nullable now, and this is a public helper — a profile that
    # never went through parse_resume can still reach it. Omit the line rather than
    # formatting None, and rather than writing "0 years" into the vector.
    years = parsed.total_years_experience
    parts = [
        parsed.current_title or "",
        f"{years:.0f} years experience" if years is not None else "",
        "Skills: " + ", ".join(parsed.skills),
        "Domains: " + ", ".join(parsed.domains),
        " ".join(entry.title + " " + entry.company for entry in parsed.experience),
        " ".join(parsed.achievements),
        " ".join(parsed.education + parsed.certifications),
    ]
    return "\n".join(part for part in parts if part.strip())
