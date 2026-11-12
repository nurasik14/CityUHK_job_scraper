"""Ranking metrics, agreement and bootstrap, in plain Python (no numpy), so they are easy to
check by hand and give the same numbers on every machine.

Conventions (see README):
- `qrels` for one query is {doc_id: label} with graded labels 0, 1, 2.
- Relevant means label >= 1. A retrieved doc without a label counts as not relevant
  (the standard pooling assumption).
- A query with no relevant doc has no defined recall/MRR/nDCG: `evaluate` returns None and
  the caller counts it separately instead of averaging in a 0.
"""

import math
import random
from collections import Counter

K = 10
RELEVANT = 1  # minimum label that counts as relevant


def recall_at_k(ranked: list[str], qrels: dict[str, int], k: int = K) -> float | None:
    """Relevant docs in the top k / all judged relevant docs. Can't reach 1 if more than k are relevant."""
    relevant = {d for d, label in qrels.items() if label >= RELEVANT}
    if not relevant:
        return None
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def mrr_at_k(ranked: list[str], qrels: dict[str, int], k: int = K) -> float | None:
    """1 / rank of the first relevant doc in the top k, or 0 if there is none."""
    if not any(label >= RELEVANT for label in qrels.values()):
        return None
    for rank, doc_id in enumerate(ranked[:k], start=1):
        if qrels.get(doc_id, 0) >= RELEVANT:
            return 1.0 / rank
    return 0.0


def dcg(labels: list[int]) -> float:
    """Sum of (2^label - 1) / log2(rank + 1), rank from 1: a "2" is worth 3 times a "1"."""
    return math.fsum((2**label - 1) / math.log2(rank + 1) for rank, label in enumerate(labels, start=1))


def ndcg_at_k(ranked: list[str], qrels: dict[str, int], k: int = K) -> float | None:
    """DCG of the top k / DCG of the best possible top k from all judged labels."""
    ideal = dcg(sorted(qrels.values(), reverse=True)[:k])
    if ideal == 0:
        return None
    return dcg([qrels.get(d, 0) for d in ranked[:k]]) / ideal


def evaluate(ranked: list[str], qrels: dict[str, int], k: int = K) -> dict | None:
    """{"recall@10", "mrr@10", "ndcg@10"} for one query, or None if it has no relevant doc."""
    if not any(label >= RELEVANT for label in qrels.values()):
        return None
    return {
        f"recall@{k}": recall_at_k(ranked, qrels, k),
        f"mrr@{k}": mrr_at_k(ranked, qrels, k),
        f"ndcg@{k}": ndcg_at_k(ranked, qrels, k),
    }


def mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def percentile(values: list[float], p: float) -> float:
    """Linear interpolation between closest ranks (numpy's default method), p in 0..100."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Agreement beyond chance between two raters: (p_observed - p_chance) / (1 - p_chance)."""
    if len(a) != len(b):
        raise ValueError("ratings must be paired")
    if not a:
        return None
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    count_a, count_b = Counter(a), Counter(b)
    chance = math.fsum(count_a[c] * count_b[c] for c in set(a) | set(b)) / (n * n)
    if chance == 1:
        return 1.0 if observed == 1 else None  # both raters used one single label
    return (observed - chance) / (1 - chance)


def paired_bootstrap(diffs: list[float], resamples: int = 10_000, seed: int = 0, level: float = 0.95) -> dict:
    """Mean of per-query differences and a percentile bootstrap CI (queries resampled with replacement).

    Uses Python's own Mersenne Twister via `randrange`, so the CI is identical everywhere for a
    given seed.
    """
    if not diffs:
        return {"mean": None, "low": None, "high": None, "n": 0}
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(math.fsum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    tail = (1 - level) / 2 * 100
    return {
        "mean": mean(diffs),
        "low": percentile(means, tail),
        "high": percentile(means, 100 - tail),
        "n": n,
        "resamples": resamples,
        "seed": seed,
    }
