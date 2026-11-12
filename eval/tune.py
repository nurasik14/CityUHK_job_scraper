"""Choose the version 2 settings on the dev queries. Never reads a test query's labels.

    uv run --group eval --group rerank python eval/tune.py               # print the tables
    uv run --group eval --group rerank python eval/tune.py --write-pool  # + list unjudged results
    uv run --group eval --group rerank python eval/tune.py --save        # + write results/tuning_dev.json

Needs Ollama (to embed the text variants) and the reranker; both are cached in
eval/.tuning/, so a second run needs neither.

What is decided here, in this order, each on nDCG@10 averaged over the dev queries:

1. model    nomic-embed-text or bge-m3 (vector search alone, the default compact text)
2. text     what gets embedded, for the chosen model: excerpt length, boilerplate rules
            on/off, one compact text or the best of several chunks (vector search alone)
3. boost    the place and company boost weights (mean of keyword, vector and hybrid search,
            because one pair of weights serves all three modes), then whether to search
            without the recognised words
4. fusion   the weight of the vector ranking next to the keyword ranking in hybrid search
5. rerank   which first stage feeds the reranker and how many results it re-orders

Rule against chasing noise (40 queries): the simpler / current setting stays unless the
alternative is better by at least MIN_GAIN.

Honest tuning needs judged results. Every variant's top 10 for every dev query is collected;
`--write-pool` writes the pairs without a label to eval/data/pool_dev_tuning.json, and
`eval/prelabel.py --pool eval/data/pool_dev_tuning.json` gets them judged. Repeat until this
script reports 0 unjudged results; only then are the tables final (and `--save` records them).
"""

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cityu_jobs import config
from cityu_jobs.core import text as text_rules
from cityu_jobs.core.query import parse_query
from eval import embed, retrievers
from eval.common import BGE_MODEL, DRAFTS, EMBED_MODEL, POOL_TUNING, TUNING_RESULTS, read_json, write_json
from eval.metrics import K, evaluate, mean
from eval.pool import blind_order
from eval.prelabel import read_labels
from eval.rerank_scores import CachedScorer
from eval.systems import VECTOR_SETS, Resources, Spec, query_vector_rows

MIN_GAIN = 0.005
BOOST_GRID = (0.0, 0.25, 0.5, 1.0, 2.0)
VECTOR_WEIGHT_GRID = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
RERANK_DEPTHS = (10, 20, 30)


class Tuner:
    def __init__(self):
        self.resources = Resources()
        self.queries = [q for q in self.resources.all_queries if q.get("split") == "dev"]
        if not self.queries:
            sys.exit("queries.jsonl has no dev split: run eval/split.py")
        self.vecs = query_vector_rows(self.queries, self.resources.all_queries, self.resources.query_vectors())
        self.labels = read_labels(DRAFTS)
        self.seen: dict[str, set[str]] = {q["qid"]: set() for q in self.queries}
        self.scorer = CachedScorer(self.resources.compact)
        self._stripped: dict[str, list[dict]] = {}

    # --- measuring one system -----------------------------------------------------------

    def measure(self, system, vecs=None) -> dict:
        """Mean dev metrics of a system, and how many of its top-10 results have no label yet."""
        rows, holes = [], 0
        for q, qv in zip(self.queries, vecs or self.vecs):
            top = system.search(q["query"], qv, K)
            self.seen[q["qid"]].update(top)
            labels = self.labels.get(q["qid"], {})
            holes += sum(1 for d in top if d not in labels)
            result = evaluate(top, labels) if labels else None
            if result:
                rows.append(result)
        return {
            "ndcg@10": mean([r["ndcg@10"] for r in rows]),
            "recall@10": mean([r["recall@10"] for r in rows]),
            "mrr@10": mean([r["mrr@10"] for r in rows]),
            "queries": len(rows),
            "unjudged_in_top10": holes,
        }

    def build(self, spec: Spec, vector=None):
        return self.resources.build(spec, vector=vector, scorer=self.scorer)

    # --- vectors of text variants (embedded once, cached in eval/.tuning) -------------------

    def compact_vectors(self, model: str, max_chars: int, strip: bool = True, min_titles: int | None = None):
        texts = embed.compact_texts(self.resources.docs, max_chars, strip, min_titles)
        return retrievers.VectorRetriever(self.resources.doc_ids, embed.embed_cached(model, texts, progress=False)[0])

    def chunk_vectors(self, model: str, size: int, overlap: int):
        chunks = embed.chunk_lists(self.resources.docs, size, overlap)
        extra, owners = embed.extra_chunks(chunks)
        first = embed.embed_cached(model, [c[0] for c in chunks], progress=False)[0]
        return retrievers.ChunkRetriever(self.resources.doc_ids, first, embed.embed_cached(model, extra, progress=False)[0], owners)

    def stripped_vecs(self, model: str) -> list[dict]:
        """Query vectors of the dev queries without their recognised place / company words."""
        if model not in self._stripped:
            texts = []
            for q in self.queries:
                parsed = parse_query(q["query"], self.resources.companies)
                texts.append(parsed.rest if parsed.rest.strip() else q["query"])
            matrix = embed.embed_cached(model, texts, kind="query", progress=False)[0]
            self._stripped[model] = [{**qv, model: matrix[i]} for i, qv in enumerate(self.vecs)]
        return self._stripped[model]


def pick(rows: list[dict], default: str, key: str = "ndcg@10", prefer=None) -> str:
    """The best variant, unless it beats `default` by less than MIN_GAIN. Ties go to `prefer` order (then name)."""
    by_name = {r["variant"]: r for r in rows}
    order = {name: i for i, name in enumerate(prefer or [r["variant"] for r in rows])}
    best = max(rows, key=lambda r: (r[key] if r[key] is not None else -1.0, -order.get(r["variant"], 0)))
    base = by_name[default][key]
    if best[key] is None or base is None or best[key] - base < MIN_GAIN:
        return default
    return best["variant"]


def show(title: str, rows: list[dict], choice: str | None = None) -> None:
    print(f"\n== {title}")
    for r in rows:
        mark = "  <== chosen" if r["variant"] == choice else ""
        ndcg = "  n/a" if r["ndcg@10"] is None else f"{r['ndcg@10']:.3f}"
        modes = "".join(f"  {m} {v:.3f}" for m, v in (r.get("per_mode") or {}).items() if v is not None)
        print(f"  {r['variant']:34} nDCG@10 {ndcg}{modes}  queries {r['queries']:2}  unjudged {r['unjudged_in_top10']:3}{mark}")


def tune(tuner: Tuner) -> dict:
    out: dict = {"min_gain": MIN_GAIN, "dev_queries": len(tuner.queries), "stages": {}}
    nomic, bge = EMBED_MODEL, BGE_MODEL

    # 1. model ------------------------------------------------------------------------------
    default_chars = text_rules.EXCERPT_CHARS
    default_text = f"compact_{default_chars}"
    rows = [
        {"variant": nomic, **tuner.measure(tuner.build(Spec("vector_compact", vectors="nomic_compact"), tuner.compact_vectors(nomic, default_chars)))},
        {"variant": bge, **tuner.measure(tuner.build(Spec("vector_bge", vectors="bge_compact"), tuner.compact_vectors(bge, default_chars)))},
    ]
    model_choice = pick(rows, nomic)
    show(f"1. embedding model (vector search alone, {default_text})", rows, model_choice)
    out["stages"]["model"] = {"rows": rows, "choice": model_choice, "rule": "bge-m3 only if better than nomic-embed-text by MIN_GAIN"}
    vector_set = "bge_compact" if model_choice == bge else "nomic_compact"
    model = VECTOR_SETS[vector_set][2]

    # 2. text -------------------------------------------------------------------------------
    text_variants = {
        default_text: dict(max_chars=default_chars),
        "compact_300": dict(max_chars=300),
        "compact_800": dict(max_chars=800),
        f"{default_text}_no_stripping": dict(max_chars=default_chars, strip=False),
        f"{default_text}_fixed_rules_only": dict(max_chars=default_chars, min_titles=10**9),
        f"{default_text}_repeat_5_titles": dict(max_chars=default_chars, min_titles=5),
    }

    def text_table(embed_model: str) -> tuple[list[dict], str, str]:
        spec = Spec("vector_text", vectors="bge_compact" if embed_model == bge else "nomic_compact")
        rows = [{"variant": name, **tuner.measure(tuner.build(spec, tuner.compact_vectors(embed_model, **kwargs)))}
                for name, kwargs in text_variants.items()]
        compact_choice = pick(rows, default_text)
        for size, overlap in ((text_rules.CHUNK_CHARS, text_rules.CHUNK_OVERLAP), (1000, 100)):
            system = tuner.build(replace(spec, name=f"chunks_{size}"), tuner.chunk_vectors(embed_model, size, overlap))
            rows.append({"variant": f"chunks_{size}", **tuner.measure(system)})
        by = {r["variant"]: r for r in rows}
        best_chunks = max((r for r in rows if r["variant"].startswith("chunks")), key=lambda r: r["ndcg@10"] or -1.0)
        gain = (best_chunks["ndcg@10"] or 0) - (by[compact_choice]["ndcg@10"] or 0)
        return rows, compact_choice, best_chunks["variant"] if gain >= MIN_GAIN else compact_choice

    rows, text_choice, shape_choice = text_table(model)
    show(f"2. text ({model}, vector search alone)", rows, shape_choice)
    other = nomic if model == bge else bge
    other_rows, _, other_shape = text_table(other)
    show(f"2b. text, for comparison ({other}; not used for the decision)", other_rows, other_shape)
    out["stages"]["text"] = {
        "model": model, "rows": rows, "choice": shape_choice, "best_compact": text_choice,
        "other_model": other, "rows_other_model": other_rows, "choice_other_model": other_shape,
        "rule": "decided with the chosen embedding model: the default compact text unless another length / rule set, "
                "or chunks, is better by MIN_GAIN",
    }
    chosen_kwargs = text_variants[text_choice]
    vector = (tuner.chunk_vectors(model, int(shape_choice.split("_")[1]), 100 if shape_choice.endswith("1000") else text_rules.CHUNK_OVERLAP)
              if shape_choice.startswith("chunks") else tuner.compact_vectors(model, **chosen_kwargs))

    # 3. boost ------------------------------------------------------------------------------
    bases = {
        "keyword": Spec("keyword_qu", keyword=True),
        "vector": Spec("vector_qu", vectors=vector_set),
        "hybrid": Spec("hybrid_qu", keyword=True, vectors=vector_set),
    }

    def boosted(w_location: float, w_company: float, strip: bool = False) -> dict:
        per_mode = {}
        for mode, base in bases.items():
            spec = replace(base, w_location=w_location, w_company=w_company, strip=strip)
            vecs = tuner.stripped_vecs(model) if strip and base.needs_vectors else None
            per_mode[mode] = tuner.measure(tuner.build(spec, vector if base.needs_vectors else None), vecs)
        scores = [m["ndcg@10"] for m in per_mode.values()]
        return {
            "ndcg@10": None if any(s is None for s in scores) else mean(scores),
            "per_mode": {mode: m["ndcg@10"] for mode, m in per_mode.items()},
            "queries": min(m["queries"] for m in per_mode.values()),
            "unjudged_in_top10": sum(m["unjudged_in_top10"] for m in per_mode.values()),
        }

    rows = []
    for w_location in BOOST_GRID:
        for w_company in BOOST_GRID:
            rows.append({"variant": f"location {w_location:g}, company {w_company:g}", "w_location": w_location,
                         "w_company": w_company, **boosted(w_location, w_company)})
    # Among weights within MIN_GAIN of the best, the smallest boost wins; none at all if that is good enough.
    top = max(r["ndcg@10"] or -1.0 for r in rows)
    near = [r for r in rows if (r["ndcg@10"] or -1.0) >= top - MIN_GAIN]
    boost_row = min(near, key=lambda r: (r["w_location"] + r["w_company"], r["w_location"]))
    show("3a. place / company boost (mean of keyword, vector, hybrid)", rows, boost_row["variant"])
    w_location, w_company = boost_row["w_location"], boost_row["w_company"]
    strip_rows = [
        {"variant": "query as typed", **boosted(w_location, w_company, strip=False)},
        {"variant": "without the recognised words", **boosted(w_location, w_company, strip=True)},
    ]
    strip_choice = pick(strip_rows, "query as typed")
    show("3b. search with or without the recognised place / company words", strip_rows, strip_choice)
    strip = strip_choice != "query as typed"
    out["stages"]["boost"] = {
        "rows": rows, "choice": {"w_location": w_location, "w_company": w_company},
        "rule": "smallest weights within MIN_GAIN of the best mean over the three modes",
        "strip_rows": strip_rows, "strip": strip,
    }
    qu = dict(w_location=w_location, w_company=w_company, strip=strip)

    # 4. fusion -----------------------------------------------------------------------------
    rows = []
    hybrid_vecs = tuner.stripped_vecs(model) if strip else None
    for w_vector in VECTOR_WEIGHT_GRID:
        spec = Spec("hybrid_w_qu", keyword=True, vectors=vector_set, weights=(1.0, w_vector), **qu)
        rows.append({"variant": f"keyword 1 : vector {w_vector:g}", "w_vector": w_vector,
                     **tuner.measure(tuner.build(spec, vector), hybrid_vecs)})
    top = max(r["ndcg@10"] or -1.0 for r in rows)
    near = [r for r in rows if (r["ndcg@10"] or -1.0) >= top - MIN_GAIN]
    fusion_row = min(near, key=lambda r: abs(r["w_vector"] - 1.0))  # closest to plain RRF among the near-best
    vector_only = {"variant": "vector only", **tuner.measure(tuner.build(Spec("vector_qu", vectors=vector_set, **qu), vector), hybrid_vecs)}
    show("4. fusion weights (with the boost)", rows + [vector_only], fusion_row["variant"])
    weights = (1.0, fusion_row["w_vector"])
    keyword_only = next(r for r in rows if r["w_vector"] == 0.0)
    modes = [{**keyword_only, "variant": "keyword"}, {**vector_only, "variant": "vector"}, {**fusion_row, "variant": "hybrid"}]
    mode_choice = pick(modes, "keyword", prefer=["keyword", "hybrid", "vector"])
    out["stages"]["fusion"] = {"rows": rows + [vector_only], "choice": {"weights": list(weights)},
                               "rule": "weights closest to 1:1 within MIN_GAIN of the best",
                               "modes": modes, "default_mode": mode_choice,
                               "mode_rule": "keyword (needs no embedding model) unless another mode is better by MIN_GAIN"}
    print(f"   default search mode: {mode_choice}")

    # 5. rerank -----------------------------------------------------------------------------
    firsts = {
        "keyword": (Spec("keyword", keyword=True), None),
        "keyword + boost": (Spec("keyword_qu", keyword=True, **qu), None),
        "vector + boost": (Spec("vector_qu", vectors=vector_set, **qu), vector),
        "hybrid + boost": (Spec("hybrid_w_qu", keyword=True, vectors=vector_set, weights=weights, **qu), vector),
    }
    rows = []
    for label, (spec, vec) in firsts.items():
        vecs = hybrid_vecs if spec.needs_vectors else None
        rows.append({"variant": f"{label}, no reranker", "first": label, "depth": 0, **tuner.measure(tuner.build(spec, vec), vecs)})
        for depth in RERANK_DEPTHS:
            system = tuner.build(replace(spec, name=spec.name + "_rerank", rerank=depth), vec)
            rows.append({"variant": f"{label}, top {depth} reranked", "first": label, "depth": depth, **tuner.measure(system, vecs)})
    plain = max((r for r in rows if r["depth"] == 0), key=lambda r: r["ndcg@10"] or -1.0)
    reranked = max((r for r in rows if r["depth"]), key=lambda r: (r["ndcg@10"] or -1.0, r["depth"] == config.RERANK_TOP_N))
    rerank_on = (reranked["ndcg@10"] or 0) - (plain["ndcg@10"] or 0) >= MIN_GAIN
    show("5. reranker", rows, reranked["variant"] if rerank_on else plain["variant"])
    out["stages"]["rerank"] = {"rows": rows, "choice": {"on": rerank_on, "first": reranked["first"], "depth": reranked["depth"]},
                               "best_without": plain["variant"],
                               "rule": "on only if the best reranked system beats the best unreranked one by MIN_GAIN"}

    out["chosen"] = {
        "text": shape_choice, "embed_model": model_choice, "boost_location": w_location, "boost_company": w_company,
        "strip_names": strip, "hybrid_weights": list(weights), "default_mode": mode_choice,
        "rerank": rerank_on, "rerank_first_stage": reranked["first"], "rerank_top_n": reranked["depth"],
    }
    return out


def write_pool(tuner: Tuner) -> int:
    """Add every unjudged (dev query, top-10 result) seen in this run to pool_dev_tuning.json."""
    pool = read_json(POOL_TUNING) or {
        "note": "Dev-query results judged while tuning (top 10 of every variant eval/tune.py tried). "
                "Dev queries only; test queries are never pooled here.",
        "depth": K, "queries": {},
    }
    added = 0
    for qid, docs in tuner.seen.items():
        have = set(pool["queries"].get(qid, {}).get("doc_ids", []))
        new = {d for d in docs if d not in have and d not in tuner.labels.get(qid, {})}
        if new:
            pool["queries"][qid] = {"doc_ids": blind_order(qid, have | new)}
            added += len(new)
    write_json(POOL_TUNING, pool)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Choose the v2 settings on the dev queries.")
    parser.add_argument("--write-pool", action="store_true", help="add unjudged top-10 results to pool_dev_tuning.json")
    parser.add_argument("--save", action="store_true", help="write eval/results/tuning_dev.json")
    args = parser.parse_args()
    start = time.perf_counter()
    tuner = Tuner()
    out = tune(tuner)
    unjudged = sum(1 for qid, docs in tuner.seen.items() for d in docs if d not in tuner.labels.get(qid, {}))
    out["unjudged_pairs"] = unjudged
    print(f"\nchosen: {out['chosen']}")
    print(f"{unjudged} unjudged (dev query, result) pairs among everything tried; {time.perf_counter() - start:.0f} s")
    if args.write_pool:
        print(f"added {write_pool(tuner)} pairs to {POOL_TUNING.name}")
    if args.save:
        if unjudged:
            print("NOT saved: label the unjudged pairs first (--write-pool, then eval/prelabel.py --pool ...)")
        else:
            write_json(TUNING_RESULTS, out)
            print(f"wrote {TUNING_RESULTS}")


if __name__ == "__main__":
    main()
