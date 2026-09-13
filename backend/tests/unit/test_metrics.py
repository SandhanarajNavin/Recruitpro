"""Ranking metrics, checked against worked examples.

A metric nobody verified is worse than no metric: it produces a number that looks
like evidence. Every case here is one you can compute by hand from the docstring.
"""

from __future__ import annotations

import pytest

from app.evaluation.metrics import (
    evaluate_ranking,
    mean_scores,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

# a=strong, b=strong, c=partial, d/e=irrelevant
RELEVANCE = {"a": 2, "b": 2, "c": 1, "d": 0, "e": 0}


class TestPrecision:
    def test_all_relevant(self):
        assert precision_at_k(["a", "b", "c"], RELEVANCE, 3) == 1.0

    def test_two_of_three(self):
        assert precision_at_k(["a", "d", "b"], RELEVANCE, 3) == pytest.approx(2 / 3)

    def test_partial_counts_as_relevant(self):
        """Grade 1 is 'worth a look' — it should not be scored as a miss."""
        assert precision_at_k(["c"], RELEVANCE, 1) == 1.0

    def test_unknown_id_is_irrelevant(self):
        """Anyone not in the golden set was never judged, so they do not count."""
        assert precision_at_k(["zzz"], RELEVANCE, 1) == 0.0

    def test_short_list_is_not_penalised_for_a_small_pool(self):
        """Two results for k=5 where only two exist is perfect, not 40%."""
        assert precision_at_k(["a", "b"], RELEVANCE, 5) == 1.0

    def test_empty_ranking(self):
        assert precision_at_k([], RELEVANCE, 5) == 0.0


class TestRecall:
    def test_finds_all_three_relevant(self):
        assert recall_at_k(["a", "b", "c"], RELEVANCE, 3) == 1.0

    def test_finds_one_of_three(self):
        assert recall_at_k(["a", "d", "e"], RELEVANCE, 3) == pytest.approx(1 / 3)

    def test_nothing_relevant_exists_is_perfect_recall(self):
        """A JD nobody matches is a real golden-set entry. The system cannot be
        marked down for failing to find what is not there."""
        assert recall_at_k(["d", "e"], {"d": 0, "e": 0}, 5) == 1.0

    def test_cut_off_excludes_later_hits(self):
        assert recall_at_k(["d", "e", "a"], RELEVANCE, 2) == 0.0


class TestReciprocalRank:
    def test_first_position(self):
        assert reciprocal_rank(["a", "d"], RELEVANCE) == 1.0

    def test_third_position(self):
        assert reciprocal_rank(["d", "e", "a"], RELEVANCE) == pytest.approx(1 / 3)

    def test_no_relevant_result(self):
        assert reciprocal_rank(["d", "e"], RELEVANCE) == 0.0


class TestNdcg:
    def test_ideal_order_scores_one(self):
        assert ndcg_at_k(["a", "b", "c"], RELEVANCE, 3) == 1.0

    def test_strong_below_partial_is_penalised(self):
        """The one metric that cares about ordering among relevant results."""
        good = ndcg_at_k(["a", "c"], RELEVANCE, 2)
        bad = ndcg_at_k(["c", "a"], RELEVANCE, 2)
        assert bad < good

    def test_irrelevant_first_is_worse(self):
        assert ndcg_at_k(["d", "a", "b"], RELEVANCE, 3) < ndcg_at_k(["a", "b", "d"], RELEVANCE, 3)

    def test_nothing_relevant_exists_scores_one(self):
        assert ndcg_at_k(["d"], {"d": 0}, 3) == 1.0


class TestAggregation:
    def test_evaluate_ranking_reports_every_metric(self):
        scores = evaluate_ranking(["a", "b", "c"], RELEVANCE, ks=(3, 5))
        assert set(scores) == {"mrr", "p@3", "r@3", "ndcg@3", "p@5", "r@5", "ndcg@5"}

    def test_mean_is_per_job_not_per_candidate(self):
        """Macro-average: a job with many relevant candidates must not outweigh one
        with few, or a single well-populated role hides failures elsewhere."""
        averaged = mean_scores([{"p@3": 1.0}, {"p@3": 0.0}])
        assert averaged["p@3"] == 0.5

    def test_mean_of_nothing_is_empty(self):
        assert mean_scores([]) == {}
