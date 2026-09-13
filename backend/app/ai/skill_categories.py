"""Grouping skills into the buckets the candidate profile displays.

The taxonomy has a ``category`` column that nothing ever populated, so every skill
was uncategorised. Rather than have the LLM assign a category per resume — which
would drift between runs and cost a call — the mapping lives here as data: a
deterministic lookup, cheap and reviewable.

Unrecognised skills land in "Other" rather than being dropped. A candidate whose
strongest skill is missing from this table would otherwise appear not to have it.
"""

from __future__ import annotations

import re

#: Display order, and also match order: the first category to hit wins. Cloud is
#: tested before Backend so "Azure Cosmos DB" reads as cloud infrastructure rather
#: than as a database, which is how a recruiter scanning the column expects it.
CATEGORY_ORDER = ("Frontend", "Cloud & DevOps", "Backend", "Data & AI", "Other")

#: Substring patterns per category, tested against the lowercased skill name.
#: Ordered within each category from most to least specific; the first category
#: with a hit wins, so ordering between categories matters where terms overlap
#: (e.g. "react native" is Frontend, but "node" is Backend).
_PATTERNS: dict[str, tuple[str, ...]] = {
    "Frontend": (
        "react", "vue", "angular", "svelte", "next.js", "nextjs", "nuxt",
        "javascript", "typescript", "html", "css", "sass", "scss", "less",
        "tailwind", "bootstrap", "material ui", "mui", "chakra",
        "redux", "mobx", "zustand", "jquery", "webpack", "vite", "babel",
        "framer", "storybook", "figma", "accessibility", "wcag",
        "responsive", "web component", "jsx", "styled-component",
        "flutter", "swiftui", "jetpack compose", "android", "ios",
    ),
    "Backend": (
        "node", "express", "nest", "django", "flask", "fastapi", "rails",
        "spring", "laravel", "symfony", "phoenix", "gin", "fiber",
        "python", "java", "golang", "go", "c#", ".net", "php", "ruby",
        "rust", "scala", "kotlin", "elixir", "perl",
        "rest", "graphql", "grpc", "soap", "api", "microservice",
        "serverless", "websocket", "signalr", "socket.io",
        "jwt", "oauth", "authentication", "authorization",
        "sql", "postgres", "mysql", "mariadb", "sqlite", "oracle",
        "mongo", "redis", "cassandra", "dynamodb", "cosmos",
        "rabbitmq", "kafka", "celery", "sqs", "event grid", "pub/sub",
        "swagger", "openapi", "postman", "orm", "sqlalchemy", "prisma",
    ),
    "Cloud & DevOps": (
        "aws", "azure", "gcp", "google cloud", "cloudflare", "heroku",
        "docker", "kubernetes", "k8s", "helm", "openshift",
        "terraform", "ansible", "puppet", "chef", "pulumi", "cloudformation",
        "ci/cd", "cicd", "jenkins", "github action", "gitlab ci", "circleci",
        "argo", "travis", "teamcity", "bamboo",
        "prometheus", "grafana", "datadog", "new relic", "splunk", "elk",
        "nginx", "apache", "load balanc", "linux", "unix", "bash", "shell",
        "git", "github", "bitbucket", "devops", "sre", "observability",
        "lambda", "ec2", "s3", "eks", "ecs", "fargate", "app service",
    ),
    "Data & AI": (
        "machine learning", "deep learning", "neural", "cnn", "rnn", "lstm",
        "transformer", "llm", "gpt", "openai", "gemini", "claude", "bert",
        "langchain", "rag", "embedding", "vector", "pinecone", "weaviate",
        "pytorch", "tensorflow", "keras", "scikit", "sklearn", "xgboost",
        "pandas", "numpy", "scipy", "matplotlib", "seaborn",
        "spark", "hadoop", "hive", "databricks", "airflow", "dbt",
        "etl", "elt", "data warehouse", "snowflake", "redshift", "bigquery",
        "nlp", "computer vision", "opencv", "yolo", "segmentation",
        "mlops", "mlflow", "feature engineering", "model optimi",
        "power bi", "tableau", "looker", "analytics", "statistic",
    ),
}


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation per category, anchored at the start of a word.

    A leading boundary only, deliberately. It is what stops "Serverless" matching
    "less" and "Django" matching "go". A trailing boundary as well would be too
    strict: "MongoDB", "HTML5" and "microservices" are all a pattern plus a suffix,
    and every one of them fell through to "Other" while it was there.

    The alternation must be grouped — `(?<!x)a|b|c` binds the lookbehind to the
    first branch only.
    """
    alternatives = sorted((re.escape(p.strip()) for p in patterns), key=len, reverse=True)
    return re.compile(rf"(?<![a-z0-9])(?:{'|'.join(alternatives)})")


_MATCHERS: dict[str, re.Pattern[str]] = {
    category: _compile(patterns) for category, patterns in _PATTERNS.items()
}


def categorise(skill: str) -> str:
    """Bucket one skill name. Case-insensitive, "Other" when nothing matches."""
    needle = skill.lower().strip()
    for category in CATEGORY_ORDER:
        matcher = _MATCHERS.get(category)
        if matcher is not None and matcher.search(needle):
            return category
    return "Other"


def _dedupe_key(skill: str) -> str:
    """Collapse punctuation and spacing, so "Node.js" and "NodeJS" are one skill."""
    return re.sub(r"[^a-z0-9]", "", skill.lower())


def group(skills: list[str]) -> dict[str, list[str]]:
    """Group skills into display buckets, preserving input order within each.

    Empty categories are omitted, so a purely backend candidate does not render
    four empty rows. Duplicates are collapsed case-insensitively — parsers
    sometimes emit both "Node.js" and "NodeJS" for one resume.
    """
    buckets: dict[str, list[str]] = {}
    seen: set[str] = set()
    for skill in skills:
        cleaned = re.sub(r"\s+", " ", str(skill)).strip()
        if not cleaned:
            continue
        key = _dedupe_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        buckets.setdefault(categorise(cleaned), []).append(cleaned)

    return {
        category: buckets[category] for category in CATEGORY_ORDER if category in buckets
    }
