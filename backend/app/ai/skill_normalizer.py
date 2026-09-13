"""Skill and industry normalisation (architecture doc §8, §16.1).

"ReactJS", "React.js" and "React" are one skill; "Postgres" and "PostgreSQL" are one
skill. Until they collapse onto a canonical row, a skill filter means something
slightly different for every resume it touches, and ``candidate_skills`` cannot be
joined on.

Normalisation is deliberately conservative and deterministic. A wrong merge is worse
than a missed one: merging "Java" into "JavaScript" silently corrupts every hard
filter that mentions either. Per §16.1 a model may *propose* a canonical name for an
unknown surface form, but it never overrides an entry in the alias table.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import lexicon as lex
from app.core.logging import get_logger
from app.db.models.taxonomy import (
    CandidateIndustry,
    CandidateSkill,
    Industry,
    JobSkill,
    Skill,
)

logger = get_logger(__name__)

#: Surface form -> canonical name. Only unambiguous merges belong here.
SKILL_ALIASES: dict[str, str] = {
    "reactjs": "React",
    "react.js": "React",
    "react js": "React",
    "nodejs": "Node.js",
    "node": "Node.js",
    "node js": "Node.js",
    "nextjs": "Next.js",
    "next js": "Next.js",
    "vuejs": "Vue",
    "vue.js": "Vue",
    "angularjs": "Angular",
    "postgres": "PostgreSQL",
    "psql": "PostgreSQL",
    "mongo": "MongoDB",
    "k8s": "Kubernetes",
    "gh actions": "GitHub Actions",
    "github action": "GitHub Actions",
    "amazon web services": "AWS",
    "google cloud": "GCP",
    "google cloud platform": "GCP",
    "microsoft azure": "Azure",
    "ml": "Machine Learning",
    "machine-learning": "Machine Learning",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "torch": "PyTorch",
    "tensor flow": "TensorFlow",
    "large language model": "LLM",
    "large language models": "LLM",
    "llms": "LLM",
    "natural language processing": "NLP",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "continuous integration": "CI/CD",
    "rest api": "REST",
    "restful": "REST",
    "graph ql": "GraphQL",
    "dotnet": ".NET",
    "c sharp": "C#",
    "golang": "Go",
}

INDUSTRY_ALIASES: dict[str, str] = {
    "fin-tech": "fintech",
    "financial technology": "fintech",
    "financial services": "banking",
    "health tech": "healthtech",
    "health-tech": "healthtech",
    "medical": "healthcare",
    "ecommerce": "e-commerce",
    "e commerce": "e-commerce",
    "online retail": "e-commerce",
    "supply-chain": "supply chain",
    "cyber security": "cybersecurity",
    "info sec": "cybersecurity",
    "infosec": "cybersecurity",
    "ad tech": "adtech",
    "mar tech": "martech",
    "ed tech": "edtech",
    "software as a service": "SaaS",
    "devtools": "developer tools",
    "dev tools": "developer tools",
}

_PUNCT = re.compile(r"[^a-z0-9+#./ -]")


def slugify(text: str) -> str:
    """Match key for a surface form. Collapses case, punctuation and spacing."""
    cleaned = _PUNCT.sub(" ", lex.normalise(text).strip())
    return re.sub(r"\s+", " ", cleaned).strip()


def canonical_skill_name(surface: str) -> str | None:
    """Canonical name for a surface form, or None if it is not recognised.

    Returning None rather than guessing is the point: an unrecognised token stays out
    of the dictionary instead of creating a near-duplicate canonical row.
    """
    key = slugify(surface)
    if not key:
        return None
    if key in SKILL_ALIASES:
        return SKILL_ALIASES[key]
    for known in lex.SKILL_LEXICON:
        if slugify(known) == key:
            return known
    return None


def canonical_industry_name(surface: str) -> str | None:
    key = slugify(surface)
    if not key:
        return None
    if key in INDUSTRY_ALIASES:
        return INDUSTRY_ALIASES[key]
    for known in lex.DOMAIN_LEXICON:
        if slugify(known) == key:
            return known
    return None


def get_or_create_skill(session: Session, surface: str) -> Skill | None:
    """Resolve a surface form to a dictionary row, creating it on first sight."""
    canonical = canonical_skill_name(surface)
    if canonical is None:
        return None
    slug = slugify(canonical)
    skill = session.execute(select(Skill).where(Skill.slug == slug)).scalars().first()
    if skill is None:
        skill = Skill(canonical_name=canonical, slug=slug, aliases=[])
        session.add(skill)
        session.flush()

    # Record the surface form so the dictionary documents what it has absorbed.
    surface_key = slugify(surface)
    if surface_key != slug and surface_key not in skill.aliases:
        skill.aliases = [*skill.aliases, surface_key]
    return skill


def get_or_create_industry(session: Session, surface: str) -> Industry | None:
    canonical = canonical_industry_name(surface)
    if canonical is None:
        return None
    slug = slugify(canonical)
    industry = session.execute(select(Industry).where(Industry.slug == slug)).scalars().first()
    if industry is None:
        industry = Industry(canonical_name=canonical, slug=slug, aliases=[])
        session.add(industry)
        session.flush()

    surface_key = slugify(surface)
    if surface_key != slug and surface_key not in industry.aliases:
        industry.aliases = [*industry.aliases, surface_key]
    return industry


def sync_candidate_skills(session: Session, candidate_id: uuid.UUID, skills: list[str]) -> int:
    """Replace the normalised skill edges for one candidate. Idempotent by design.

    Re-ingesting a resume must not accumulate duplicate edges, so the existing set is
    replaced wholesale rather than appended to.
    """
    session.query(CandidateSkill).filter(CandidateSkill.candidate_id == candidate_id).delete(
        synchronize_session=False
    )
    seen: set[uuid.UUID] = set()
    for surface in skills:
        skill = get_or_create_skill(session, surface)
        if skill is None or skill.id in seen:
            continue
        seen.add(skill.id)
        session.add(
            CandidateSkill(candidate_id=candidate_id, skill_id=skill.id, source_text=surface[:160])
        )
    return len(seen)


def sync_candidate_industries(
    session: Session, candidate_id: uuid.UUID, domains: list[str]
) -> int:
    session.query(CandidateIndustry).filter(
        CandidateIndustry.candidate_id == candidate_id
    ).delete(synchronize_session=False)
    seen: set[uuid.UUID] = set()
    for surface in domains:
        industry = get_or_create_industry(session, surface)
        if industry is None or industry.id in seen:
            continue
        seen.add(industry.id)
        session.add(CandidateIndustry(candidate_id=candidate_id, industry_id=industry.id))
    return len(seen)


def sync_job_skills(
    session: Session,
    job_id: uuid.UUID,
    required: list[tuple[str, int]],
    preferred: list[str],
) -> int:
    """Replace the skill edges for one job. ``required`` carries (skill, importance)."""
    session.query(JobSkill).filter(JobSkill.job_id == job_id).delete(synchronize_session=False)
    seen: set[uuid.UUID] = set()
    for surface, importance in required:
        skill = get_or_create_skill(session, surface)
        if skill is None or skill.id in seen:
            continue
        seen.add(skill.id)
        session.add(
            JobSkill(job_id=job_id, skill_id=skill.id, required=True, weight=int(importance))
        )
    for surface in preferred:
        skill = get_or_create_skill(session, surface)
        if skill is None or skill.id in seen:
            continue
        seen.add(skill.id)
        session.add(JobSkill(job_id=job_id, skill_id=skill.id, required=False, weight=1))
    return len(seen)
