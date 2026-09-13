"""Multi-label role classification (architecture doc §8, §16.1).

A candidate is assigned one ``primary_role`` and any number of ``secondary_roles``.
The primary role feeds the role scoring factor; the secondary roles widen retrieval
without diluting the primary signal — a backend engineer who also does data work
should surface for a data role, but not outrank a dedicated data engineer.

Deterministic first. The rules engine scores each role from skill overlap and title
words and is what runs with no credentials; a model is consulted only to break ties
the rules leave genuinely ambiguous, and never to override a confident rule match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import lexicon as lex
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Canonical role taxonomy. Each role is defined by the skills that evidence it and
#: the title words that name it, so a match is always traceable to something on the
#: resume rather than to a model's impression of it.
ROLE_DEFINITIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "AI Engineer": {
        "skills": ("LLM", "NLP", "PyTorch", "TensorFlow", "Machine Learning", "Deep Learning",
                   "Computer Vision", "MLOps", "scikit-learn", "pgvector"),
        "titles": ("ai", "ml", "machine learning", "nlp", "applied scientist"),
    },
    "Data Engineer": {
        "skills": ("Airflow", "Spark", "dbt", "Kafka", "Snowflake", "BigQuery", "Hadoop",
                   "Data Modeling", "SQL"),
        "titles": ("data engineer", "etl", "analytics engineer", "data platform"),
    },
    "Data Scientist": {
        "skills": ("Pandas", "NumPy", "scikit-learn", "Machine Learning", "SQL", "Python"),
        "titles": ("data scientist", "statistician", "quantitative"),
    },
    "Backend Engineer": {
        "skills": ("FastAPI", "Django", "Flask", "Spring Boot", "Express", "Rails", "gRPC",
                   "REST", "PostgreSQL", "MySQL", "Microservices", "Distributed Systems",
                   "Java", "Go", "Python", "Kafka", "Redis"),
        # Deliberately not "software engineer": a generic title should not presume
        # backend. The skill signal decides those.
        "titles": ("backend", "back-end", "server", "api", "platform engineer",
                   "java engineer"),
    },
    "Frontend Engineer": {
        "skills": ("React", "Vue", "Angular", "Svelte", "Next.js", "TypeScript", "Redux",
                   "Tailwind CSS", "Accessibility", "Figma"),
        "titles": ("frontend", "front-end", "ui engineer", "web developer"),
    },
    "Full Stack Engineer": {
        "skills": ("React", "Node.js", "TypeScript", "PostgreSQL", "REST", "Next.js"),
        "titles": ("full stack", "fullstack", "full-stack"),
    },
    "DevOps Engineer": {
        "skills": ("Kubernetes", "Docker", "Terraform", "Ansible", "Jenkins",
                   "GitHub Actions", "CI/CD", "Prometheus", "Grafana", "Linux", "AWS"),
        "titles": ("devops", "sre", "site reliability", "infrastructure", "platform"),
    },
    "Security Engineer": {
        "skills": ("Penetration Testing", "Threat Modeling", "OAuth", "SAML"),
        "titles": ("security", "appsec", "infosec"),
    },
    "Mobile Engineer": {
        "skills": ("Swift", "Kotlin", "React Native"),
        "titles": ("mobile", "ios", "android"),
    },
    "QA Engineer": {
        "skills": ("Jest", "Playwright", "Cypress", "Selenium"),
        "titles": ("qa", "quality", "test engineer", "sdet"),
    },
}

#: Matching this many of a role's skills is treated as a full skill signal.
#: Dividing by len(role_skills) instead would bias towards narrowly-defined roles:
#: 2 of 4 QA skills would beat 2 of 12 backend skills for a clear backend engineer.
SKILL_SATURATION = 4

#: Below this, the classification is too weak to assert a primary role.
MIN_PRIMARY_CONFIDENCE = 0.25
#: A role must reach this share of the winner to count as a secondary role.
SECONDARY_RATIO = 0.55

TITLE_WEIGHT = 2.0
SKILL_WEIGHT = 1.0


@dataclass
class RoleClassification:
    primary_role: str | None
    secondary_roles: list[str] = field(default_factory=list)
    confidence: float = 0.0
    engine: str = "offline-roles-v1"
    #: Per-role raw scores, kept so a classification can be explained.
    scores: dict[str, float] = field(default_factory=dict)


def classify_offline(
    *, current_title: str | None, skills: list[str], experience_titles: list[str] | None = None
) -> RoleClassification:
    """Score every role from skill overlap and title words, then rank them."""
    owned = {lex.normalise(skill) for skill in skills}
    title_text = lex.normalise(
        " ".join(filter(None, [current_title or "", *(experience_titles or [])]))
    )

    raw: dict[str, float] = {}
    for role, definition in ROLE_DEFINITIONS.items():
        role_skills = definition["skills"]
        overlap = sum(1 for skill in role_skills if lex.normalise(skill) in owned)
        skill_signal = min(1.0, overlap / SKILL_SATURATION)

        title_hits = sum(1 for word in definition["titles"] if word in title_text)
        title_signal = min(1.0, title_hits / 2)

        score = skill_signal * SKILL_WEIGHT + title_signal * TITLE_WEIGHT
        if score > 0:
            raw[role] = round(score, 4)

    if not raw:
        return RoleClassification(primary_role=None, scores={})

    ranked = sorted(raw.items(), key=lambda item: item[1], reverse=True)
    top_role, top_score = ranked[0]

    # Normalise against the theoretical maximum (full skill overlap + title match)
    # so confidence means the same thing regardless of how many roles matched.
    confidence = round(min(1.0, top_score / (SKILL_WEIGHT + TITLE_WEIGHT)), 2)
    if confidence < MIN_PRIMARY_CONFIDENCE:
        return RoleClassification(
            primary_role=None, confidence=confidence, scores=dict(ranked)
        )

    secondary = [
        role for role, score in ranked[1:] if score >= top_score * SECONDARY_RATIO
    ][:3]

    return RoleClassification(
        primary_role=top_role,
        secondary_roles=secondary,
        confidence=confidence,
        scores=dict(ranked),
    )


def classify(
    *, current_title: str | None, skills: list[str], experience_titles: list[str] | None = None
) -> RoleClassification:
    """Public entrypoint. Deterministic today; §16.1 allows a model tie-breaker later.

    A model is deliberately not called here yet: the rules engine is traceable, free
    and already decisive on the cases seen so far. Adding an LLM call per candidate
    to the ingestion path would cost real money for a marginal gain.
    """
    return classify_offline(
        current_title=current_title, skills=skills, experience_titles=experience_titles
    )
