"""Metric functions on cases small enough to compute by hand. No numpy needed."""

import math

import pytest

from eval.metrics import (
    cohen_kappa,
    dcg,
    evaluate,
    mrr_at_k,
    ndcg_at_k,
    paired_bootstrap,
    percentile,
    recall_at_k,
)

QRELS = {"a": 2, "b": 0, "c": 1, "d": 2}  # relevant: a, c, d


def test_recall_counts_relevant_found_over_all_judged_relevant():
    assert recall_at_k(["a", "b", "x"], QRELS) == pytest.approx(1 / 3)
    assert recall_at_k(["d", "c", "a"], QRELS) == 1.0
    # Only the top k count.
    assert recall_at_k(["b", "x", "a"], QRELS, k=2) == 0.0


def test_mrr_uses_first_relevant_and_ignores_unjudged():
    assert mrr_at_k(["x", "b", "c"], QRELS) == pytest.approx(1 / 3)  # x unjudged, b judged 0
    assert mrr_at_k(["a"], QRELS) == 1.0
    assert mrr_at_k(["b", "x"], QRELS) == 0.0
    assert mrr_at_k(["x", "y", "a"], QRELS, k=2) == 0.0


def test_graded_ndcg_by_hand():
    # Ranking a(2), b(0), c(1): DCG = 3/log2(2) + 0 + 1/log2(4) = 3.5
    # Ideal from all judged labels 2, 2, 1: 3 + 3/log2(3) + 1/2
    assert dcg([2, 0, 1]) == pytest.approx(3.5)
    ideal = 3 + 3 / math.log2(3) + 0.5
    assert ndcg_at_k(["a", "b", "c"], QRELS) == pytest.approx(3.5 / ideal)
    assert ndcg_at_k(["a", "d", "c"], QRELS) == pytest.approx(1.0)
    # A label-2 doc is worth more than a label-1 doc at the same rank.
    assert ndcg_at_k(["d"], QRELS) > ndcg_at_k(["c"], QRELS)


def test_ndcg_ideal_is_cut_at_k():
    qrels = {"a": 1, "b": 1, "c": 1}
    assert ndcg_at_k(["a"], qrels, k=1) == 1.0


def test_query_without_relevant_docs_is_excluded_not_zero():
    no_rel = {"a": 0, "b": 0}
    assert evaluate(["a", "b"], no_rel) is None
    assert recall_at_k(["a"], no_rel) is None
    assert mrr_at_k(["a"], no_rel) is None
    assert ndcg_at_k(["a"], no_rel) is None
    assert evaluate([], {}) is None


def test_evaluate_keys():
    result = evaluate(["a", "b", "c"], QRELS)
    assert set(result) == {"recall@10", "mrr@10", "ndcg@10"}
    assert result["mrr@10"] == 1.0


def test_percentile_matches_numpy_linear_method():
    values = [1, 2, 3, 4, 10]
    assert percentile(values, 50) == 3
    assert percentile(values, 95) == pytest.approx(4 + 0.8 * 6)  # position 3.8
    assert percentile([5], 95) == 5
    with pytest.raises(ValueError):
        percentile([], 50)


def test_cohen_kappa():
    assert cohen_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == 1.0
    # observed 3/4, chance (2*1 + 2*3)/16 = 0.5 -> kappa 0.5
    assert cohen_kappa([0, 0, 1, 1], [0, 1, 1, 1]) == pytest.approx((0.75 - 0.5) / 0.5)
    assert cohen_kappa([0, 1], [1, 0]) == pytest.approx(-1.0)
    assert cohen_kappa([], []) is None


def test_paired_bootstrap_is_deterministic_and_brackets_the_mean():
    diffs = [0.1, -0.05, 0.2, 0.0, 0.15, 0.3]
    a = paired_bootstrap(diffs, resamples=2000, seed=7)
    b = paired_bootstrap(diffs, resamples=2000, seed=7)
    assert a == b
    assert a["low"] <= a["mean"] <= a["high"]
    assert a["mean"] == pytest.approx(sum(diffs) / len(diffs))
    same = paired_bootstrap([0.2] * 5, resamples=100, seed=1)
    assert same["low"] == same["high"] == pytest.approx(0.2)
    assert paired_bootstrap([])["n"] == 0
