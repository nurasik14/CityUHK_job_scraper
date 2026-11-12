"""Build the judging pool: for each query, the union of every evaluated system's top 20.

    uv run --group eval python eval/pool.py [--out PATH]

Writes eval/data/pool.json (or PATH). "Every system" is the full list in eval/systems.py:
the three version 1 systems and all version 2 variants that appear in the report, for all
100 queries. Only pooled documents get a label; anything a system returns outside the pool
counts as not relevant (see README, "Pooling bias"). The reranked systems need
eval/data/rerank_scores.jsonl.gz (eval/rerank_scores.py) to exist first.

The candidates of a query are stored in a fixed pseudo-random order (sha256 of qid and
doc_id), not by rank: whoever labels them can't tell which system found a listing or how
high it ranked. Which systems returned each doc, and at what rank, is kept separately.
"""

import hashlib
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import POOL, write_json

POOL_DEPTH = 20


def blind_order(qid: str, doc_ids) -> list[str]:
    return sorted(doc_ids, key=lambda d: (hashlib.sha256(f"{qid}\x1f{d}".encode()).hexdigest(), d))


def build_pool(queries: list[dict], systems: dict, query_vectors, depth: int = POOL_DEPTH) -> dict:
    """{qid: {"doc_ids": [...blind order...], "systems": {doc_id: {system: rank}}}}."""
    pool = {}
    for i, q in enumerate(queries):
        found: dict[str, dict[str, int]] = {}
        for name in sorted(systems):
            for rank, doc_id in enumerate(systems[name].search(q["query"], query_vectors[i], depth), start=1):
                found.setdefault(doc_id, {})[name] = rank
        pool[q["qid"]] = {
            "doc_ids": blind_order(q["qid"], found),
            "systems": {d: found[d] for d in sorted(found)},
        }
    return pool


def main() -> None:
    import argparse

    from eval.systems import Resources, query_vector_rows, select

    parser = argparse.ArgumentParser(description="Build the judging pool.")
    parser.add_argument("--out", type=Path, default=POOL, help="where to write it (default: eval/data/pool.json)")
    parser.add_argument("--systems", default="all", help='"all" (default), "v1", or comma-separated system names')
    args = parser.parse_args()

    resources = Resources()
    queries = resources.all_queries
    qvecs = query_vector_rows(queries, queries, resources.query_vectors())
    systems = resources.build_all(select(args.systems))
    pool = build_pool(queries, systems, qvecs)
    write_json(args.out, {"depth": POOL_DEPTH, "systems": sorted(systems), "queries": pool})
    sizes = [len(p["doc_ids"]) for p in pool.values()]
    print(f"pooled {sum(sizes)} (query, doc) pairs for {len(pool)} queries: min {min(sizes)}, max {max(sizes)} per query")


if __name__ == "__main__":
    main()
