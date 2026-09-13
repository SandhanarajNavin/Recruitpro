"""Ranking metrics (architecture doc §27).

Pure functions over a ranked list of candidate ids and a map of graded relevance.
No database, no model, no configuration — so a metric can be unit-tested against a
worked example rather than trusted.

Relevance is graded rather than binary because "not a match" and "could work" are
different answers, and a metric that flattens them rewards a system for surfacing
weak candidates alongside strong ones:

    2  strong match — would shortlist
    1  partial      — plausible, worth a look
    0  not relevant — should not surface
"""

from __future__ import annotations

import math

#: At or above this grade a candidate counts as relevant for the binary metrics.
RELEVANT_AT = 1


def precision_at_k(ranked: list[str], relevance: dict[str, int], k: int) -> float:
    """Fraction of the top k that are relevant.

    Divided by k rather than by len(top) on purpose: a system that returns three
    results when asked for five has not earned the same precision as one that
    returned five good ones. The exception is a k larger than the whole pool, where
    dividing by k would penalise the system for the pool being small.
    """
    if k <= 0 or not ranked:
        return 0.0
    top = ranked[:k]
    hits = sum(1 for cid in top if relevance.get(cid, 0) >= RELEVANT_AT)
    return hits / min(k, len(ranked))


def recall_at_k(ranked: list[str], relevance: dict[str, int], k: int) -> float:
    """Share of all relevant candidates that appear in the top k."""
    total_relevant = sum(1 for grade in relevance.values() if grade >= RELEVANT_AT)
    if total_relevant == 0:
        # Nothing to find. 1.0 rather than 0.0: a system cannot be penalised for
        # failing to retrieve what does not exist, and this case is real — a JD
        # nobody in the pool matches is a legitimate golden-set entry.
        return 1.0
    hits = sum(1 for cid in ranked[:k] if relevance.get(cid, 0) >= RELEVANT_AT)
    return hits / total_relevant


def reciprocal_rank(ranked: list[str], relevance: dict[str, int]) -> float:
    """1/position of the first relevant result, or 0 if none appears.

    Answers "how far does a recruiter scroll before the first useful person" —
    the metric that matches how a shortlist is actually read.
    """
    for index, cid in enumerate(ranked, start=1):
        if relevance.get(cid, 0) >= RELEVANT_AT:
            return 1.0 / index
    return 0.0


def dcg_at_k(ranked: list[str], relevance: dict[str, int], k: int) -> float:
    """Discounted cumulative gain, using the 2^grade - 1 formulation so a strong
    match is worth meaningfully more than a partial one."""
    return sum(
        (2 ** relevance.get(cid, 0) - 1) / math.log2(index + 1)
        for index, cid in enumerate(ranked[:k], start=1)
    )


def ndcg_at_k(ranked: list[str], relevance: dict[str, int], k: int) -> float:
    """DCG against the best possible ordering. 1.0 means perfectly ranked.

    The only metric here that rewards putting strong matches *above* partial ones,
    rather than merely including both.
    """
    ideal_order = sorted(relevance, key=lambda cid: relevance[cid], reverse=True)
    ideal = dcg_at_k(ideal_order, relevance, k)
    if ideal == 0:
        return 1.0
    return dcg_at_k(ranked, relevance, k) / ideal


def evaluate_ranking(
    ranked: list[str], relevance: dict[str, int], ks: tuple[int, ...] = (3, 5)
) -> dict[str, float]:
    """All metrics for one ranked list."""
    scores: dict[str, float] = {"mrr": round(reciprocal_rank(ranked, relevance), 4)}
    for k in ks:
        scores[f"p@{k}"] = round(precision_at_k(ranked, relevance, k), 4)
        scores[f"r@{k}"] = round(recall_at_k(ranked, relevance, k), 4)
        scores[f"ndcg@{k}"] = round(ndcg_at_k(ranked, relevance, k), 4)
    return scores


def mean_scores(per_job: list[dict[str, float]]) -> dict[str, float]:
    """Macro-average across jobs: every JD counts equally regardless of how many
    relevant candidates it has, so one well-populated role cannot mask failures on
    the rest."""
    if not per_job:
        return {}
    keys = per_job[0].keys()
    return {
        key: round(sum(scores.get(key, 0.0) for scores in per_job) / len(per_job), 4)
        for key in keys
    }
