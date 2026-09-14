"""The scoring engine (architecture doc §12).

The only writer of the composite score. No model call happens here, and no model
output reaches this module except as category scores it produced under instruction.
Given the same evidence and the same weights, this returns the same number every
time — which is the whole point: a rank has to be defensible to a hiring manager.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.embeddings import get_embedder
from app.ai.llm.evaluator import (
    CATEGORY_EXPERIENCE,
    CATEGORY_INDUSTRY,
    CATEGORY_REQUIRED_SKILLS,
    CATEGORY_ROLE,
)
from app.ai.schemas import CandidateEvaluation
from app.core.config import settings
from app.db.models.screening import Recommendation

CATEGORY_SEMANTIC = "semantic_similarity"

#: Order is presentation order in the UI as well as the audit trail.
SCORING_CATEGORIES = (
    CATEGORY_REQUIRED_SKILLS,
    CATEGORY_EXPERIENCE,
    CATEGORY_ROLE,
    CATEGORY_INDUSTRY,
    CATEGORY_SEMANTIC,
)

CATEGORY_LABELS = {
    CATEGORY_REQUIRED_SKILLS: "Required skills",
    CATEGORY_EXPERIENCE: "Experience",
    CATEGORY_ROLE: "Role match",
    CATEGORY_INDUSTRY: "Industry",
    CATEGORY_SEMANTIC: "Semantic similarity",
}



def subscore_rows(subscores: dict | None) -> list[dict]:
    """A stored ``subscores`` blob as presentation rows, in scoring order.

    Shared so every surface that shows a breakdown — the screening results and a
    candidate's best-fit jobs — labels and orders the categories identically. A
    category the run did not record is skipped rather than shown as zero.
    """
    stored = subscores or {}
    rows = []
    for category in SCORING_CATEGORIES:
        values = stored.get(category)
        if values is None:
            continue
        rows.append(
            {
                "category": category,
                "label": CATEGORY_LABELS[category],
                "score": float(values.get("score", 0.0)),
                "weight": float(values.get("weight", 0.0)),
                "contribution": float(values.get("contribution", 0.0)),
            }
        )
    return rows


# Recommendation bands. Deliberately not a model decision.
BAND_STRONG_HIRE = 82.0
BAND_INTERVIEW = 68.0
BAND_MAYBE = 52.0


@dataclass
class ScoreBreakdown:
    composite: float
    #: {category: {"score": s, "weight": w, "contribution": s*w}}
    subscores: dict[str, dict[str, float]]
    recommendation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "composite": self.composite,
            "subscores": self.subscores,
            "recommendation": self.recommendation,
        }


def clamp(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(100.0, value))


def similarity_to_score(cosine_similarity: float | None) -> float:
    """Map a cosine similarity onto 0-100, against the active embedder's range.

    The usable cosine range is a property of the embedding space, so the endpoints
    live on the embedder beside ``min_similarity`` rather than being fixed here.
    This function previously hard-coded the hashing embedder's scale — anchoring 0.5
    at 100 — which silently broke when the default provider became Gemini: real
    Gemini cosines sit between 0.65 and 0.94, so every candidate clamped to 100 and
    the category stopped distinguishing anyone at all while still contributing its
    full weight to every composite.

    Reading the range from the embedder is what stops that recurring on the next
    provider change: a new embedder has to state its own scale or inherit the base
    class's, and either way the failure is visible rather than a silent 100.
    """
    if cosine_similarity is None:
        return 0.0

    embedder = get_embedder()
    floor = embedder.similarity_floor
    ceiling = embedder.similarity_ceiling
    if ceiling <= floor:  # misconfigured; refuse to invent a distribution
        return 0.0
    return clamp((cosine_similarity - floor) / (ceiling - floor) * 100.0)


def compute_score(
    evaluation: CandidateEvaluation,
    *,
    cosine_similarity: float | None = None,
    weights: dict[str, float] | None = None,
) -> ScoreBreakdown:
    """Weighted mean of the five factors, using the configured weights.

    Any category the evaluator did not return scores 0 rather than being dropped —
    silently renormalising the weights would inflate a candidate for having less
    evidence, which is precisely backwards.
    """
    active_weights = weights or settings.weights.as_dict()
    by_category = {
        assessment.category: clamp(float(assessment.score))
        for assessment in evaluation.categories
    }
    by_category[CATEGORY_SEMANTIC] = similarity_to_score(cosine_similarity)

    subscores: dict[str, dict[str, float]] = {}
    composite = 0.0
    for category in SCORING_CATEGORIES:
        weight = float(active_weights.get(category, 0.0))
        score = by_category.get(category, 0.0)
        contribution = score * weight
        composite += contribution
        subscores[category] = {
            "score": round(score, 2),
            "weight": round(weight, 4),
            "contribution": round(contribution, 2),
        }

    composite = round(composite, 2)
    return ScoreBreakdown(
        composite=composite,
        subscores=subscores,
        recommendation=recommendation_for(composite),
    )


def recommendation_for(composite: float) -> str:
    if composite >= BAND_STRONG_HIRE:
        return Recommendation.STRONG_HIRE.value
    if composite >= BAND_INTERVIEW:
        return Recommendation.INTERVIEW.value
    if composite >= BAND_MAYBE:
        return Recommendation.MAYBE.value
    return Recommendation.PASS.value
