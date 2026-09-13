"""Deterministic text analysis: lexicons, date arithmetic, seniority inference.

Ported from the prototype's `lib/heuristic.ts`. This module has no dependency on any
model, which is what lets the whole system run with no credentials — and it is also
the rules half of the evaluator, so its output is used even when Gemini is available.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

SKILL_LEXICON: tuple[str, ...] = (
    "TypeScript", "JavaScript", "Python", "Go", "Java", "Kotlin", "Swift", "Rust", "Ruby",
    "C++", "C#", "PHP", "Scala", "Elixir", "SQL", "GraphQL", "React Native", "React",
    "Next.js", "Vue", "Angular", "Svelte", "Node.js", "Express", "Django", "Flask",
    "FastAPI", "Rails", "Spring Boot", ".NET", "Redux", "Tailwind CSS", "PostgreSQL",
    "MySQL", "MongoDB", "Redis", "DynamoDB", "Cassandra", "Elasticsearch", "Snowflake",
    "BigQuery", "Kafka", "RabbitMQ", "Airflow", "dbt", "Spark", "Hadoop", "AWS", "GCP",
    "Azure", "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins", "GitHub Actions",
    "CI/CD", "Prometheus", "Grafana", "Datadog", "Linux", "gRPC", "REST", "Microservices",
    "Serverless", "Machine Learning", "Deep Learning", "PyTorch", "TensorFlow", "LLM",
    "NLP", "Computer Vision", "Pandas", "NumPy", "scikit-learn", "MLOps", "Figma",
    "Accessibility", "WebSockets", "OAuth", "SAML", "Penetration Testing",
    "Threat Modeling", "Jest", "Playwright", "Cypress", "Selenium", "Agile", "Scrum",
    "Observability", "System Design", "Distributed Systems", "Performance Optimization",
    "Data Modeling", "pgvector", "Celery", "Alembic", "SQLAlchemy",
)

DOMAIN_LEXICON: tuple[str, ...] = (
    "fintech", "payments", "banking", "insurance", "healthcare", "healthtech", "biotech",
    "e-commerce", "retail", "marketplace", "logistics", "supply chain", "gaming", "adtech",
    "martech", "edtech", "SaaS", "B2B", "B2C", "cybersecurity", "telecom", "automotive",
    "energy", "climate", "media", "streaming", "travel", "real estate", "government",
    "manufacturing", "developer tools", "infrastructure",
)

SENIORITY_LADDER: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("intern", "trainee", "graduate")),
    (2, ("junior", "associate", "entry level")),
    (3, ("mid-level", "mid level", "engineer ii", "software engineer")),
    (4, ("senior", "sr.", "sr ")),
    (5, ("staff", "lead", "principal")),
    (6, ("director", "head of", "vp", "cto", "chief")),
)

#: Returned when no ladder word is found — "mid-level" is the neutral assumption.
DEFAULT_SENIORITY_RANK = 3

SENIORITY_NAMES = {
    1: "Intern",
    2: "Junior",
    3: "Mid-level",
    4: "Senior",
    5: "Staff / Lead",
    6: "Leadership",
}

_SMART_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

_IMPACT_VERBS = re.compile(
    r"(%|million|billion|reduced|increased|improved|lifted|grew|cut|saved|scaled|shipped"
    r"|migrated|launched|automated)",
    re.IGNORECASE,
)
_RESPONSIBILITY_VERBS = re.compile(
    r"\b(build|own|lead|design|ship|drive|scale|mentor|deliver)\b", re.IGNORECASE
)
_EDUCATION = re.compile(
    r"\b(b\.?sc|b\.?tech|b\.?e\b|bachelor|m\.?sc|m\.?tech|master|mba|ph\.?d|diplom|degree)\b",
    re.IGNORECASE,
)
_CERTIFICATION = re.compile(r"certified|certification|\bcka\b|\bcissp\b|\bpmp\b", re.IGNORECASE)
_TITLE_WORDS = re.compile(
    r"engineer|developer|scientist|manager|architect|designer|analyst|lead", re.IGNORECASE
)
_DATE_RANGE = re.compile(
    r"(\d{4})\s*(?:-|–|—|to)\s*(present|current|now|\d{4})", re.IGNORECASE
)
_STATED_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs|yr)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BULLET_START = re.compile(r"^\s*[\-*•]")
_PHONE = re.compile(r"(?:(?:\+\d{1,3})?[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}")


def normalise(text: str) -> str:
    return text.lower().translate(_SMART_QUOTES)


def find_terms(text: str, lexicon: tuple[str, ...] | list[str]) -> list[str]:
    """Word-ish boundary match so "Go" does not hit "Google", while "C++",
    ".NET" and "CI/CD" still match."""
    haystack = normalise(text)
    found: list[str] = []
    for term in lexicon:
        pattern = rf"(^|[^a-z0-9+#.]){re.escape(normalise(term))}($|[^a-z0-9+#])"
        if re.search(pattern, haystack):
            found.append(term)
    return found


def seniority_rank(text: str) -> int:
    """Highest ladder level mentioned in `text`.

    Scope the input deliberately: pass a title, not a whole document. Ladder words
    like "lead" double as ordinary verbs in responsibility bullets.
    """
    haystack = normalise(text)
    best = DEFAULT_SENIORITY_RANK
    for rank, words in SENIORITY_LADDER:
        if any(word in haystack for word in words):
            best = max(best, rank)
    return best


def seniority_name(rank: int) -> str:
    return SENIORITY_NAMES.get(rank, "Mid-level")


def logical_lines(text: str) -> list[str]:
    """Resumes arrive with soft-wrapped bullets. Merge indented continuation lines back
    onto their bullet so a quantified achievement is not cut in half."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            out.append("")
            continue
        is_continuation = bool(re.match(r"^\s{2,}\S", raw)) and bool(out) and out[-1] != ""
        if is_continuation:
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return [line for line in out if line]


def stated_years(text: str) -> int | None:
    match = _STATED_YEARS.search(normalise(text))
    return int(match.group(1)) if match else None


def years_from_timeline(text: str, *, today: datetime | None = None) -> int:
    """Sums '2019 - 2023' / '2021 - Present' spans, merging overlaps so concurrent
    roles are not double counted."""
    current_year = (today or datetime.now(UTC)).year
    spans: list[tuple[int, int]] = []

    for match in _DATE_RANGE.finditer(text):
        start = int(match.group(1))
        raw_end = match.group(2).lower()
        end = int(raw_end) if raw_end.isdigit() else current_year
        if start >= 1970 and end >= start and end <= current_year + 1:
            spans.append((start, end))

    if not spans:
        return 0

    spans.sort()
    total = 0
    cursor_start, cursor_end = spans[0]
    for start, end in spans[1:]:
        if start <= cursor_end:
            cursor_end = max(cursor_end, end)
        else:
            total += cursor_end - cursor_start
            cursor_start, cursor_end = start, end
    return total + (cursor_end - cursor_start)


def strip_bullet(line: str) -> str:
    return re.sub(r"^[\s\-*•]+", "", line).strip()


def extract_contact(text: str) -> dict[str, str | None]:
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    return {
        "email": email.group(0) if email else None,
        "phone": phone.group(0).strip() if phone else None,
    }


def guess_name(text: str) -> str | None:
    """First non-empty line that looks like a person's name rather than a heading.

    Deliberately conservative: a wrong guess is visible to the recruiter and easy to
    correct, but a guess drawn from a bullet or a skills line is noise.
    """
    for line in logical_lines(text)[:5]:
        # A bullet is body content, never a name header — without this check a line
        # like "- Shipped things" reads as a two-word name.
        if _BULLET_START.match(line):
            continue

        candidate = strip_bullet(line)
        words = candidate.split()
        if not (2 <= len(words) <= 4):
            continue
        # Every word capitalised. Rejects sentence fragments while still allowing
        # ALL-CAPS name headers.
        if not all(word[:1].isupper() for word in words):
            continue
        if _EMAIL.search(candidate) or any(ch.isdigit() for ch in candidate):
            continue
        if _TITLE_WORDS.search(candidate) or ":" in candidate:
            continue
        if candidate == candidate.upper() and len(candidate) > 24:
            continue
        return candidate
    return None


def find_education(lines: list[str]) -> list[str]:
    return [line for line in lines if _EDUCATION.search(line)][:4]


def find_certifications(lines: list[str]) -> list[str]:
    return [line for line in lines if _CERTIFICATION.search(line)][:4]


def find_achievements(lines: list[str]) -> list[str]:
    """A quantified line is the closest offline proxy for demonstrated impact."""
    out = []
    for line in lines:
        cleaned = strip_bullet(line)
        if any(ch.isdigit() for ch in cleaned) and _IMPACT_VERBS.search(cleaned):
            out.append(cleaned)
    return out[:6]


def find_responsibilities(text: str) -> list[str]:
    out = []
    for line in logical_lines(text):
        cleaned = strip_bullet(line)
        if len(cleaned) > 25 and _RESPONSIBILITY_VERBS.search(cleaned):
            out.append(cleaned)
    return out[:8]


def find_title(lines: list[str]) -> str | None:
    for line in lines:
        if _TITLE_WORDS.search(line) and len(line) < 120:
            return strip_bullet(line)
    return None


def tokenise(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, used by the offline embedder and reranker."""
    return re.findall(r"[a-z0-9+#.]+", normalise(text))
