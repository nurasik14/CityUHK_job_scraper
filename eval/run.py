"""Run every evaluated system on the labelled queries and score them.

    uv run --group eval python -m eval.run                  # committed data only: no Ollama, torch or GPU
    uv run --group eval python -m eval.run --systems v1 \\
        --queries eval/results/before_v1/queries_v1.jsonl \\
        --qrels eval/results/before_v1/qrels_v1.jsonl --out /tmp/v1     # reproduce the first evaluation
    uv run --group eval --group rerank python -m eval.run --live-embed --live-rerank   # also time the real models

Reads only committed files (eval/data/), checks their sha256 against meta.json, and writes
eval/results/summary.json, report.md and per_query.jsonl. The quality numbers are the same
on every run; latency depends on the machine and its load.

The systems are listed in eval/systems.py. Queries carry a `split`: everything that was
tuned was tuned on "dev"; "test" is the headline. `--live-embed` and `--live-rerank` add
end-to-end timings with the real embedding model (Ollama) and the real cross-encoder (GPU),
and save them to eval/results/live_latency.json, which later runs show in the report.
"""

import argparse
import os
import platform
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import retrievers
from eval.common import (
    CORPUS,
    LIVE_LATENCY,
    META,
    POOL,
    POOL_TUNING,
    QRELS,
    QUERIES,
    QUERY_VECTOR_FILES,
    RERANK_SCORES,
    RESULTS,
    TUNING_RESULTS,
    atomic_write_bytes,
    load_queries,
    read_json,
    read_jsonl,
    require_numpy,
    sha256_file,
    write_json,
    write_jsonl,
)
from eval.metrics import K, evaluate, mean, paired_bootstrap, percentile
from eval.split import GROUPS, group_of
from eval.systems import VECTOR_SETS, Resources, Spec, default_system, query_vector_rows, select

METRICS = (f"recall@{K}", f"mrr@{K}", f"ndcg@{K}")
WARMUP_QUERIES = 3
REPEATS = 5
LIVE_REPEATS = 3
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261107
SPLITS = ("test", "dev")
# Systems shown in the per-group and per-query tables (when present in the run).
KEY_SYSTEMS = ("keyword", "vector", "hybrid", "bge_chunks", "app_keyword", "app_vector", "app_hybrid", "app_hybrid_rerank")
# "Each change on its own": (what changed, system with the change, system without it).
ABLATIONS = (
    ("compact text instead of the full text (nomic-embed-text)", "nomic_compact", "vector"),
    ("best piece instead of one compact text (nomic-embed-text)", "nomic_chunks", "nomic_compact"),
    ("best piece instead of one compact text (bge-m3)", "bge_chunks", "bge_compact"),
    ("bge-m3 instead of nomic-embed-text (compact text)", "bge_compact", "nomic_compact"),
    ("query understanding on keyword search", "keyword_qu", "keyword"),
    ("query understanding on vector search (bge-m3 best piece)", "bge_chunks_qu", "bge_chunks"),
    ("query understanding on hybrid search", "app_hybrid", "hybrid_bge_chunks"),
    ("fusing keyword with bge-m3 (best piece), vs keyword alone", "hybrid_bge_chunks", "keyword"),
    ("fusing keyword with bge-m3 (best piece), vs bge-m3 alone", "hybrid_bge_chunks", "bge_chunks"),
    ("vector ranking at half weight in the fusion, vs equal weights", "hybrid_alt_weights", "hybrid_bge_chunks"),
    ("reranking the top 30 of v1 keyword search", "keyword_rerank", "keyword"),
    ("reranking the top 30 of the app's hybrid search", "app_hybrid_rerank", "app_hybrid"),
)


# --- inputs ---------------------------------------------------------------------------


def needed_files(specs: list[Spec]) -> list[Path]:
    """Every committed file the selected systems read (the metrics always use the stored reranker scores)."""
    files = [CORPUS, QUERIES]
    for spec in specs:
        if spec.needs_vectors:
            first, further, model = VECTOR_SETS[spec.vectors]
            files += [first, QUERY_VECTOR_FILES[model]] + ([further] if further else [])
        if spec.rerank:
            files.append(RERANK_SCORES)
    return list(dict.fromkeys(files))


def recorded_hashes(meta: dict) -> dict[str, str]:
    """{file name: sha256} for every file meta.json describes."""
    out = {}
    for section in ("corpus", "queries", "rerank"):
        for name, entry in ((meta.get(section) or {}).get("files") or {}).items():
            out[name] = entry["sha256"]
    for name, entry in (meta.get("embeddings") or {}).items():
        out[name] = entry["sha256"]
    return out


def check_data(specs: list[Spec]) -> dict:
    """sha256 of every input against meta.json; exits on a mismatch (wrong or edited data)."""
    meta = read_json(META)
    if not meta:
        sys.exit("eval/data/meta.json is missing")
    expected = recorded_hashes(meta)
    for path in needed_files(specs):
        if not path.exists():
            sys.exit(f"{path.name} is missing: see eval/README.md, 'Rebuilding the data'")
        if path.name not in expected:
            sys.exit(f"{path.name} is not recorded in meta.json")
        actual = sha256_file(path)
        if actual != expected[path.name]:
            sys.exit(f"{path.name}: sha256 {actual} does not match meta.json; re-run the export steps or restore the file")
    return meta


def load_qrels(path: Path) -> tuple[dict[str, dict[str, int]], list[dict]]:
    rows = read_jsonl(path)
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        if r["label"] not in (0, 1, 2):
            sys.exit(f"bad label in {path}: {r}")
        qrels[r["qid"]][r["doc_id"]] = r["label"]
    return dict(qrels), rows


# --- running and timing ---------------------------------------------------------------


def rank_all(systems: dict, queries: list[dict], qvecs, k: int = K) -> dict[str, dict[str, list[str]]]:
    return {name: {q["qid"]: system.search(q["query"], qvecs[i], k) for i, q in enumerate(queries)}
            for name, system in systems.items()}


def _stats(samples_ns: list[int]) -> dict:
    ms = [s / 1e6 for s in samples_ns]
    return {"p50_ms": percentile(ms, 50), "p95_ms": percentile(ms, 95), "n": len(ms)}


def measure_latency(systems: dict, queries: list[dict], qvecs, warmup: int = WARMUP_QUERIES, repeats: int = REPEATS) -> dict:
    """Time of the search call only (query vector precomputed, reranker scores looked up):
    warm-up, then every query `repeats` times."""
    for i, q in enumerate(queries[:warmup]):
        for system in systems.values():
            system.search(q["query"], qvecs[i], K)
    samples = {name: [] for name in systems}
    for _ in range(repeats):
        for i, q in enumerate(queries):
            for name, system in systems.items():
                start = time.perf_counter_ns()
                system.search(q["query"], qvecs[i], K)
                samples[name].append(time.perf_counter_ns() - start)
    return {name: _stats(samples[name]) for name in systems}


class LiveScorer:
    """The real cross-encoder, timed; also remembers its scores to compare with the stored ones."""

    def __init__(self, texts: dict[str, str]):
        from cityu_jobs.core.rerank import Reranker

        self.texts = texts
        self.model = Reranker()
        self.samples_ns: list[int] = []
        self.sizes: list[int] = []
        self.scores: dict[tuple[str, str], float] = {}
        self.record = False

    def __call__(self, query_text: str, doc_ids: list[str]) -> list[float]:
        start = time.perf_counter_ns()
        scores = self.model.scores(query_text, [self.texts[d] for d in doc_ids])
        if self.record:
            self.samples_ns.append(time.perf_counter_ns() - start)
            self.sizes.append(len(doc_ids))
            self.scores.update({(query_text, d): s for d, s in zip(doc_ids, scores)})
        return scores


def measure_live(resources: Resources, specs: list[Spec], queries: list[dict], qvecs, rankings: dict,
                 live_embed: bool, live_rerank: bool, warmup: int = WARMUP_QUERIES, repeats: int = LIVE_REPEATS) -> dict:
    """End-to-end latency with the real models: Ollama query embedding and/or the cross-encoder on the GPU."""
    from cityu_jobs.core.ai.providers import OllamaProvider

    np = require_numpy()
    embedders: dict[str, OllamaProvider] = {}
    scorer = LiveScorer(resources.compact) if live_rerank else None
    out: dict = {"end_to_end": {}, "repeats": repeats, "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "hardware": hardware(gpu=True)}

    def embed(model: str, text: str):
        if model not in embedders:
            embedders[model] = OllamaProvider(embed_model=model)
        vec = np.asarray(embedders[model].embed([text], kind="query")[0], dtype=np.float32)
        return vec / np.linalg.norm(vec)

    if live_embed:
        embed_samples = {}
        for model in sorted({VECTOR_SETS[s.vectors][2] for s in specs if s.needs_vectors}):
            for q in queries[:warmup]:
                embed(model, q["query"])
            samples = []
            for _ in range(repeats):
                for q in queries:
                    start = time.perf_counter_ns()
                    embed(model, q["query"])
                    samples.append(time.perf_counter_ns() - start)
            embed_samples[model] = _stats(samples)
        out["query_embedding"] = embed_samples

    for spec in specs:
        needs_embed, needs_rerank = spec.needs_vectors and live_embed, bool(spec.rerank) and live_rerank
        if not (needs_embed or needs_rerank):
            continue
        system = resources.build(spec, scorer=scorer if needs_rerank else None)
        model = VECTOR_SETS[spec.vectors][2] if spec.needs_vectors else None

        def once(i: int, q: dict):
            vecs = {model: embed(model, q["query"])} if needs_embed else qvecs[i]
            return system.search(q["query"], vecs, K)

        for i, q in enumerate(queries[:warmup]):
            once(i, q)
        samples, same_top10 = [], 0
        for repeat in range(repeats):
            if scorer is not None:
                scorer.record = needs_rerank
            for i, q in enumerate(queries):
                start = time.perf_counter_ns()
                top = once(i, q)
                samples.append(time.perf_counter_ns() - start)
                if repeat == 0 and needs_rerank and not needs_embed:
                    same_top10 += top == rankings[spec.name][q["qid"]]
        if scorer is not None:
            scorer.record = False
        entry = {**_stats(samples), "includes": [w for w, on in (("query embedding", needs_embed), ("cross-encoder", needs_rerank)) if on]}
        if needs_rerank and not needs_embed:
            entry["same_top10_as_stored_scores"] = f"{same_top10}/{len(queries)}"
        out["end_to_end"][spec.name] = entry

    if scorer is not None and scorer.samples_ns:
        full = [ns for ns, size in zip(scorer.samples_ns, scorer.sizes) if size == max(scorer.sizes)]
        stored = resources.stored_scores.by_query
        diffs = [abs(score - stored[q][d]) for (q, d), score in scorer.scores.items() if d in stored.get(q, {})]
        out["rerank"] = {
            **_stats(full),
            "candidates": max(scorer.sizes),
            "model": scorer.model.model_name,
            "device": scorer.model.device,
            "precision": "fp16" if scorer.model.fp16 else "fp32",
            "max_abs_difference_to_stored_scores": max(diffs) if diffs else None,
            "pairs_compared": len(diffs),
        }
    return out


def hardware(gpu: bool = False) -> dict:
    np = require_numpy()
    cpu = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            cpu = next((line.split(":", 1)[1].strip() for line in f if line.startswith("model name")), None)
    except OSError:
        pass
    info = {
        "platform": platform.platform(),
        "cpu": cpu or platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "sqlite": sqlite3.sqlite_version,
    }
    if gpu:
        try:
            import torch

            info["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
            info["torch"] = torch.__version__
        except ImportError:
            pass
    return info


# --- scoring --------------------------------------------------------------------------


def score(queries: list[dict], rankings: dict, qrels: dict) -> tuple[list[dict], dict]:
    """Per-query rows (one per query and system) and the counts of judged / excluded queries."""
    rows, counts = [], {"no_relevant": [], "not_judged": []}
    for q in queries:
        qid, labels = q["qid"], qrels.get(q["qid"])
        if not labels:
            counts["not_judged"].append(qid)
        elif not any(v >= 1 for v in labels.values()):
            counts["no_relevant"].append(qid)
        for name in rankings:
            top = rankings[name][qid]
            result = evaluate(top, labels) if labels else None
            row = {
                "qid": qid,
                "query": q["query"],
                "category": q["category"],
                "system": name,
                "top10": top,
                "n_judged": len(labels or {}),
                "n_relevant": sum(1 for v in (labels or {}).values() if v >= 1),
                "n_label_2": sum(1 for v in (labels or {}).values() if v == 2),
                "unjudged_in_top10": sum(1 for d in top if d not in (labels or {})) if labels else None,
                "included": result is not None,
                **{m: (result or {}).get(m) for m in METRICS},
            }
            if "split" in q:
                row["split"] = q["split"]
            rows.append(row)
    return rows, counts


def aggregate(rows: list[dict], names) -> dict:
    out = {}
    for name in names:
        mine = [r for r in rows if r["system"] == name and r["included"]]
        out[name] = {m: mean([r[m] for r in mine]) for m in METRICS} | {"n_queries": len(mine)}
    return out


def per_category(rows: list[dict], names, key=lambda r: r["category"]) -> dict:
    cats = sorted({key(r) for r in rows})
    return {
        cat: {
            "n_queries": len({r["qid"] for r in rows if key(r) == cat and r["included"]}),
            **{
                name: {m: mean([r[m] for r in rows if key(r) == cat and r["system"] == name and r["included"]]) for m in METRICS}
                for name in names
            },
        }
        for cat in cats
    }


def difference(rows: list[dict], a: str, b: str) -> dict:
    """Paired bootstrap of nDCG@10(a) - nDCG@10(b) over the scored queries in `rows`."""
    ndcg = {(r["qid"], r["system"]): r[f"ndcg@{K}"] for r in rows if r["included"] and r["system"] in (a, b)}
    qids = sorted({qid for qid, _ in ndcg})
    return paired_bootstrap([ndcg[(q, a)] - ndcg[(q, b)] for q in qids], BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)


def bootstrap_v1(rows: list[dict], names) -> dict:
    """The two comparisons of the first evaluation (same keys as before_v1/summary.json)."""
    if not {"keyword", "vector", "hybrid"} <= set(names):
        return {}
    return {f"hybrid-{other}": difference(rows, "hybrid", other) for other in ("vector", "keyword")}


def label_coverage(qrels: dict, qids: set[str]) -> dict:
    """How much of the pool has labels (unlabelled pooled docs count as 0)."""
    pool = read_json(POOL)
    if not pool:
        return {}
    pooled = {qid: set(p["doc_ids"]) for qid, p in pool["queries"].items() if qid in qids}
    labelled = {qid: pooled[qid] & set(qrels.get(qid, {})) for qid in pooled}
    tuning = read_json(POOL_TUNING) or {"queries": {}}
    extra = sum(len(set(p["doc_ids"]) - pooled.get(qid, set())) for qid, p in tuning["queries"].items() if qid in qids)
    return {
        "pool_depth": pool["depth"],
        "pool_systems": pool["systems"],
        "pooled_pairs": sum(len(v) for v in pooled.values()),
        "labelled_pairs": sum(len(v) for v in labelled.values()),
        "queries_fully_labelled": sum(1 for qid in pooled if pooled[qid] and labelled[qid] == pooled[qid]),
        "dev_tuning_pairs_outside_pool": extra,
    }


def labelling_stats() -> dict:
    """Who judged, from the labels file: model ids, calls, retries."""
    from eval.common import DRAFTS

    rows = read_jsonl(DRAFTS)
    if not rows:
        return {}
    return {
        "calls": len(rows),
        "pairs": sum(len(r["labels"]) for r in rows),
        "model_ids": sorted({m for r in rows for m in r.get("model_ids", [])}),
        "judge": sorted({r.get("model") for r in rows if r.get("model")}),
        "calls_needing_a_retry": sum(1 for r in rows if r.get("attempts", 1) > 1),
        "first": min(r["created_at"] for r in rows),
        "last": max(r["created_at"] for r in rows),
    }


# --- report ---------------------------------------------------------------------------


def fmt(x, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def signed(x) -> str:
    return "n/a" if x is None else f"{x:+.3f}"


def results_table(section: dict, specs: list[Spec], latency: dict, live: dict | None) -> list[str]:
    e2e = (live or {}).get("end_to_end") or {}
    lines = [
        "| system | what it is | recall@10 | MRR@10 | nDCG@10 | search p50 ms | search p95 ms"
        + (" | end-to-end p50 ms | end-to-end p95 ms |" if e2e else " |"),
        "|---|---|---|---|---|---|---" + ("|---|---|" if e2e else "|"),
    ]
    for spec in specs:
        m, lat = section["metrics"][spec.name], latency[spec.name]
        row = (f"| {spec.name} | {spec.label} | {fmt(m['recall@10'])} | {fmt(m['mrr@10'])} | {fmt(m['ndcg@10'])} | "
               f"{fmt(lat['p50_ms'], 2)} | {fmt(lat['p95_ms'], 2)} |")
        if e2e:
            ee = e2e.get(spec.name)
            needs_model = spec.needs_vectors or spec.rerank
            row += (f" {fmt(ee['p50_ms'], 1)} | {fmt(ee['p95_ms'], 1)} |" if ee and (not spec.rerank or "cross-encoder" in ee["includes"])
                    and (not spec.needs_vectors or "query embedding" in ee["includes"])
                    else " n/a | n/a |" if needs_model else " = search | = search |")
        lines.append(row)
    return lines


def comparison_rows(items: list[dict]) -> list[str]:
    lines = ["| comparison | nDCG@10 | nDCG@10 | difference | 95% CI | queries |", "|---|---|---|---|---|---|"]
    for c in items:
        b = c["bootstrap"]
        verdict = "" if b["low"] is None else (" (clear)" if b["low"] > 0 or b["high"] < 0 else " (not clear)")
        lines.append(f"| {c['what']} | {c['a']} {fmt(c['a_ndcg'])} | {c['b']} {fmt(c['b_ndcg'])} | {signed(b['mean'])} | "
                     f"[{signed(b['low'])}, {signed(b['high'])}]{verdict} | {b['n']} |")
    return lines


def tuning_lines(tuning: dict) -> list[str]:
    c, stages = tuning["chosen"], tuning["stages"]

    def best(rows, variant):
        return next((r for r in rows if r["variant"] == variant), None)

    text_rows = stages["text"]["rows"]
    model_rows = {r["variant"]: r["ndcg@10"] for r in stages["model"]["rows"]}
    lines = [
        f"Tuned with `eval/tune.py` on the {tuning['dev_queries']} dev queries only, on nDCG@10. To avoid chasing noise, the simpler "
        f"or existing setting was kept unless an alternative was better by at least {tuning['min_gain']}. Every result the "
        "tuning looked at (top 10 of every variant) was judged first, so no variant was penalised for unjudged results.",
        "",
        "The numbers in this table are the ones the decisions were made on. They differ a little from the dev table "
        "above: the final pool (top 20 of every system) later added judged listings for the dev queries too, which "
        "changes each query's ideal ranking, and the committed bge-m3 vectors are stored as float16. `compact_N` = one "
        "compact text with N characters of description; `chunks_N` = pieces of N characters, best piece counts.",
        "",
        "| decision | chosen | dev evidence (nDCG@10) |",
        "|---|---|---|",
        f"| embedding model | {c['embed_model']} | vector search alone, compact text: "
        + ", ".join(f"{k} {fmt(v)}" for k, v in model_rows.items()) + " |",
        f"| what is embedded | {c['text']} | {stages['text']['model']}, vector search alone: "
        + ", ".join(f"{r['variant']} {fmt(r['ndcg@10'])}" for r in text_rows) + " |",
    ]
    boost_rows = stages["boost"]["rows"]
    none = next(r for r in boost_rows if r["w_location"] == 0 and r["w_company"] == 0)
    chosen = next(r for r in boost_rows if r["w_location"] == c["boost_location"] and r["w_company"] == c["boost_company"])
    top = max(boost_rows, key=lambda r: r["ndcg@10"])
    lines.append(
        f"| place / company boost | place {c['boost_location']:g}, company {c['boost_company']:g} | mean of the three search modes: "
        f"no boost {fmt(none['ndcg@10'])}, chosen {fmt(chosen['ndcg@10'])}, best on the grid {fmt(top['ndcg@10'])} ({top['variant']}); "
        f"per mode with the chosen weights: " + ", ".join(f"{m} {fmt(v)}" for m, v in chosen["per_mode"].items())
        + "; without: " + ", ".join(f"{m} {fmt(v)}" for m, v in none["per_mode"].items()) + " |"
    )
    strip_rows = stages["boost"]["strip_rows"]
    lines.append(f"| search without the recognised words | {'yes' if c['strip_names'] else 'no'} | "
                 + ", ".join(f"{r['variant']} {fmt(r['ndcg@10'])}" for r in strip_rows) + " |")
    fusion = stages["fusion"]
    lines.append(f"| fusion weights (keyword : vector) | {c['hybrid_weights'][0]:g} : {c['hybrid_weights'][1]:g} | "
                 + ", ".join(f"{r['variant'].replace('keyword 1 : vector ', '1:')} {fmt(r['ndcg@10'])}" for r in fusion["rows"]) + " |")
    lines.append(f"| default search mode | {c['default_mode']} | "
                 + ", ".join(f"{r['variant']} {fmt(r['ndcg@10'])}" for r in fusion["modes"]) + " (each with the boost) |")
    rerank_rows = stages["rerank"]["rows"]
    plain, reranked = best(rerank_rows, stages["rerank"]["best_without"]), max(
        (r for r in rerank_rows if r["depth"]), key=lambda r: r["ndcg@10"])
    lines.append(
        f"| reranker | {'on' if c['rerank'] else 'off by default'}; first stage: {c['rerank_first_stage']}, top {c['rerank_top_n']} | "
        f"best without {fmt(plain['ndcg@10'])} ({plain['variant']}), best with {fmt(reranked['ndcg@10'])} ({reranked['variant']}); "
        + ", ".join(f"{r['variant']} {fmt(r['ndcg@10'])}" for r in rerank_rows if r["depth"] in (0, 30)) + " |"
    )
    return lines


def report(summary: dict, rows: list[dict], queries: list[dict], specs: list[Spec]) -> str:
    s, c = summary, summary["counts"]
    sections, latency, live = s["sections"], s["latency"]["search"], s["latency"].get("live")
    names = [spec.name for spec in specs]
    has_splits = "test" in sections
    headline = "test" if has_splits else "all"
    lines = [
        "# Retrieval evaluation results",
        "",
        f"Generated by `{s['command']}`. Corpus: {s['data']['corpus']['n_docs']} public internship listings "
        f"(scraped {s['data']['corpus']['scraped_on']}); {c['queries']} queries; qrels `{s['data']['qrels']['file']}` "
        f"({s['data']['qrels']['rows']} judgments, sha256 `{s['data']['qrels']['sha256'][:12]}…`).",
        "",
        f"Queries scored: **{c['included']}** of {c['queries']}. Excluded: {len(c['no_relevant'])} with no relevant doc"
        + (f" ({', '.join(c['no_relevant'])})" if c["no_relevant"] else "")
        + f", {len(c['not_judged'])} not judged yet.",
    ]
    cov = s.get("label_coverage") or {}
    if cov and cov["labelled_pairs"] < cov["pooled_pairs"]:
        lines += ["", f"> **Incomplete labels:** {cov['labelled_pairs']} of {cov['pooled_pairs']} pooled (query, doc) pairs "
                  f"labelled, {cov['queries_fully_labelled']} queries fully labelled. These numbers are not final."]
    note = (
        "Means over the scored queries. Relevant = label 1 or 2; nDCG uses gains 2^label − 1. Retrieved docs without a "
        "label count as not relevant. Search latency = the search call only, with the query vector precomputed and, for "
        f"reranked systems, the reranker scores looked up ({WARMUP_QUERIES} warm-up queries, then each query {REPEATS} times)."
    )
    if live:
        note += (f" End-to-end adds the real models, measured {live['measured_at'][:10]} on the machine below "
                 f"(each query {live['repeats']} times): the Ollama query embedding for systems that use vectors and the "
                 "cross-encoder on the GPU for reranked systems. Keyword-only systems need neither.")
    if has_splits:
        best = s["best_v2"]
        lines += [
            "",
            f"All settings of version 2 "
            f"were tuned on the {sections['dev']['n_queries']} **dev** queries; the {sections['test']['n_queries']} **test** "
            f"queries were scored once, at the end, and are the headline. `{best}` is what the app does by default.",
            "",
            f"## Headline: test queries ({sections['test']['n_queries']})",
            "",
            *results_table(sections["test"], specs, latency, live),
            "",
            note,
            "",
            "## Before and after (test queries)",
            "",
            *comparison_rows(s["comparisons"]["before_after"]),
            "",
            f"Difference = first system − second system, per query, then averaged. 95% CI from a paired bootstrap "
            f"({BOOTSTRAP_RESAMPLES:,} resamples of the test queries with replacement, seed {BOOTSTRAP_SEED}). "
            "\"Clear\" means the interval does not contain 0; \"not clear\" means this test set can't tell the two apart.",
            "",
            "## Each change on its own (test queries)",
            "",
            *comparison_rows(s["comparisons"]["ablations"]),
            "",
            f"## Dev queries ({sections['dev']['n_queries']}): tuned on these",
            "",
            "The settings were chosen to do well here, so these numbers flatter version 2. Shown for transparency.",
            "",
            *results_table(sections["dev"], specs, latency, None),
            "",
            f"## All {sections['all']['n_queries']} queries (dev + test)",
            "",
            "Includes the dev queries the settings were tuned on.",
            "",
            *results_table(sections["all"], specs, latency, None),
            "",
        ]
        tuning = s.get("tuning")
        if tuning:
            lines += ["## What was tuned on dev, and what was chosen", "", *tuning_lines(tuning), ""]
    else:
        lines += ["", "## Main results", "", *results_table(sections["all"], specs, latency, live), "", note, ""]
    if s["bootstrap_ndcg@10"]:
        lines += [
            "## Version 1 systems against each other" + (" (all queries)" if has_splits else " (paired bootstrap)"),
            "",
            "| comparison | mean difference | 95% CI | queries |",
            "|---|---|---|---|",
        ]
        for key, b in s["bootstrap_ndcg@10"].items():
            lines.append(f"| {key.replace('-', ' − ')} | {fmt(b['mean'])} | [{fmt(b['low'])}, {fmt(b['high'])}] | {b['n']} |")
        lines += ["", f"nDCG@10, {BOOTSTRAP_RESAMPLES:,} resamples of the scored queries with replacement, seed {BOOTSTRAP_SEED}.", ""]

    key = [n for n in KEY_SYSTEMS if n in names] or names
    groups = s["per_group"][headline]
    lines += [
        f"## nDCG@10 per query group ({headline} queries)",
        "",
        "| group | queries | " + " | ".join(key) + " |",
        "|---|---|" + "---|" * len(key),
    ]
    for group in [g for g in GROUPS if g in groups]:
        v = groups[group]
        lines.append(f"| {group} | {v['n_queries']} | " + " | ".join(fmt(v[n]["ndcg@10"]) for n in key) + " |")
    lines += ["", "Groups are broad (see eval/split.py); the small ones hold 3 to 6 queries, so read them as examples.", ""]

    lines += [
        "## Per query",
        "",
        "| qid | split | query | category | judged | relevant | of which 2 | " + " | ".join(key) + " |",
        "|---|---|---|---|---|---|---|" + "---|" * len(key),
    ]
    by = {(r["qid"], r["system"]): r for r in rows}
    for q in queries:
        r = by[(q["qid"], names[0])]
        query_text = q["query"].replace("|", "\\|")
        lines.append(
            f"| {q['qid']} | {q.get('split', '')} | {query_text} | {q['category']} | {r['n_judged']} | {r['n_relevant']} | {r['n_label_2']} | "
            + " | ".join(fmt(by[(q["qid"], n)]["ndcg@10"]) for n in key)
            + " |"
        )
    lines += ["", "nDCG@10 per system. The other systems' per-query numbers are in `per_query.jsonl`.", ""]

    lab = s.get("labelling") or {}
    if cov:
        unjudged = c["unjudged_in_top10"]
        lines += [
            "## Labels",
            "",
            f"- Pool: the union of the top {cov['pool_depth']} of all {len(cov['pool_systems'])} systems for every query: "
            f"{cov['pooled_pairs']} (query, listing) pairs, {cov['labelled_pairs']} of them labelled "
            f"({cov['queries_fully_labelled']} of {c['queries']} queries completely).",
            f"- {cov['dev_tuning_pairs_outside_pool']} more pairs were judged for dev queries while tuning (top 10 of variants that "
            "are not in the final list). They are in the qrels too.",
            f"- Results in a top 10 without a label (counted as not relevant): "
            + (", ".join(f"{n} {v}" for n, v in unjudged.items() if v) or "none, for every system") + ".",
        ]
        if lab:
            lines.append(
                f"- Judge: {', '.join(lab['judge'])} (model id {', '.join(lab['model_ids']) or 'not recorded'}), "
                f"{lab['calls']} calls for {lab['pairs']} pairs, {lab['calls_needing_a_retry']} calls needed their one retry. "
                "One rubric (2 = clearly what was asked, 1 = partly, 0 = not relevant), candidates shown in a blind order."
            )
        lines += [
            "",
        ]
    hw = s["hardware"]
    lines += [
        "## Machine",
        "",
        f"{hw['cpu']} ({hw['cpu_count']} logical CPUs), {hw['platform']}, Python {hw['python']}, numpy {hw['numpy']}, "
        f"SQLite {hw['sqlite']}. Keyword index build: {fmt(s['latency']['keyword_index_build_ms'], 0)} ms. "
        "Latency changes with hardware and load; the quality metrics do not.",
    ]
    if live:
        lhw = live["hardware"]
        parts = []
        for model, st in (live.get("query_embedding") or {}).items():
            parts.append(f"query embedding with {model} through Ollama: p50 {fmt(st['p50_ms'], 1)} ms, p95 {fmt(st['p95_ms'], 1)} ms")
        rr = live.get("rerank")
        if rr:
            parts.append(
                f"reranking {rr['candidates']} candidates with {rr['model']} ({rr['precision']}, {rr['device']}, warm): "
                f"p50 {fmt(rr['p50_ms'], 1)} ms, p95 {fmt(rr['p95_ms'], 1)} ms; live scores differ from the stored ones by at most "
                f"{rr['max_abs_difference_to_stored_scores']:.4f} ({rr['pairs_compared']} pairs)"
            )
        same = [f"{n}: {e['same_top10_as_stored_scores']}" for n, e in live["end_to_end"].items() if "same_top10_as_stored_scores" in e]
        if same:
            parts.append("queries whose live-reranked top 10 equals the one from stored scores: " + ", ".join(same))
        lines += ["", f"Live timings ({live['measured_at'][:10]}, GPU: {lhw.get('gpu') or 'none'}, torch {lhw.get('torch', 'n/a')}): "
                  + "; ".join(parts) + "."]
    lines.append("")
    return "\n".join(lines)


# --- main -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the evaluated search systems against the relevance labels.")
    parser.add_argument("--queries", type=Path, default=QUERIES, help="queries to score (default eval/data/queries.jsonl); "
                        "a subset of that file is fine, e.g. eval/results/before_v1/queries_v1.jsonl")
    parser.add_argument("--qrels", type=Path, default=QRELS, help="relevance labels (default eval/data/qrels.jsonl)")
    parser.add_argument("--systems", default="all", help='"all" (default), "v1", or comma-separated names from eval/systems.py')
    parser.add_argument("--out", type=Path, default=RESULTS, help="output folder (default eval/results)")
    parser.add_argument("--live-embed", action="store_true", help="also time real query embedding (needs Ollama)")
    parser.add_argument("--live-rerank", action="store_true", help="also run and time the real cross-encoder (needs the rerank group)")
    args = parser.parse_args()

    specs = select(args.systems)
    names = [s.name for s in specs]
    meta = check_data(specs)
    if not args.qrels.exists():
        sys.exit(f"{args.qrels} not found: label the pool with eval/prelabel.py first")
    resources = Resources()
    queries = load_queries(args.queries)
    qvecs = query_vector_rows(queries, resources.all_queries, resources.query_vectors())
    start = time.perf_counter_ns()
    resources.keyword  # noqa: B018  (build the keyword index now, to time it)
    build_ms = (time.perf_counter_ns() - start) / 1e6
    systems = resources.build_all(specs)

    qrels, qrel_rows = load_qrels(args.qrels)
    unknown = sorted(set(qrels) - {q["qid"] for q in resources.all_queries})
    if unknown:
        sys.exit(f"qrels has queries not in queries.jsonl: {unknown}")
    qids = {q["qid"] for q in queries}
    qrel_rows = [r for r in qrel_rows if r["qid"] in qids] if args.queries != QUERIES else qrel_rows
    rankings = rank_all(systems, queries, qvecs)
    rows, counts = score(queries, rankings, qrels)
    latency = {"search": measure_latency(systems, queries, qvecs), "keyword_index_build_ms": build_ms}
    if args.live_embed or args.live_rerank:
        live = measure_live(resources, specs, queries, qvecs, rankings, args.live_embed, args.live_rerank)
        if args.out == RESULTS:
            write_json(LIVE_LATENCY, live)
        latency["live"] = live
    elif args.out == RESULTS and args.systems == "all" and LIVE_LATENCY.exists():
        latency["live"] = read_json(LIVE_LATENCY)

    has_splits = all("split" in q for q in queries)
    sections = {"all": rows}
    if has_splits:
        sections = {split: [r for r in rows if r["split"] == split] for split in SPLITS} | sections
    best = default_system()
    comparisons = {}
    if has_splits and best in names:
        test_rows = sections["test"]
        test_metrics = aggregate(test_rows, names)

        def compare(what: str, a: str, b: str) -> dict:
            return {"what": what, "a": a, "b": b, "a_ndcg": test_metrics[a]["ndcg@10"], "b_ndcg": test_metrics[b]["ndcg@10"],
                    "bootstrap": difference(test_rows, a, b)}

        before_after = [compare(f"{best} (app default) vs v1 keyword", best, "keyword"),
                        compare(f"{best} (app default) vs v1 hybrid", best, "hybrid"),
                        compare(f"{best} (app default) vs v1 vector", best, "vector")]
        if "app_hybrid_rerank" in names and best != "app_hybrid_rerank":
            before_after.append(compare("app hybrid + reranker vs v1 keyword", "app_hybrid_rerank", "keyword"))
        comparisons = {
            "split": "test",
            "before_after": before_after,
            "ablations": [compare(what, a, b) for what, a, b in ABLATIONS if a in names and b in names],
        }

    included = sorted({r["qid"] for r in rows if r["included"]})
    command = "uv run --group eval python -m eval.run"
    for flag, value, default in (("--systems", args.systems, "all"), ("--queries", args.queries, QUERIES), ("--qrels", args.qrels, QRELS)):
        if value != default:
            command += f" {flag} {value}"
    command += (" --live-embed" if args.live_embed else "") + (" --live-rerank" if args.live_rerank else "")
    from cityu_jobs import config

    summary = {
        "command": command,
        "data": {
            "corpus": {
                "n_docs": meta["corpus"]["n_docs"],
                "scraped_on": min(v for v in meta["corpus"]["scraped_at"].values() if v)[:10],
                "sha256": meta["corpus"]["files"][CORPUS.name]["sha256"],
            },
            "files": {p.name: sha256_file(p) for p in needed_files(specs)},
            "queries": {"file": args.queries.name, "sha256": sha256_file(args.queries)},
            "qrels": {"file": args.qrels.name, "rows": len(qrel_rows), "sha256": sha256_file(args.qrels)},
        },
        "systems": [{"name": spec.name, "what": spec.label} for spec in specs],
        "best_v2": best if best in names else None,
        "settings": {
            "k": K,
            "keyword": {"tokenizer": retrievers.TOKENIZER, "column_weights": retrievers.COLUMN_WEIGHTS, "cjk": "character bigrams"},
            "hybrid_v1": {"method": "reciprocal rank fusion", "rrf_k": retrievers.RRF_K, "depth": retrievers.FUSION_DEPTH},
            "v2": {
                "embed_model": config.DEFAULT_EMBED_MODEL, "embed_chunks": config.EMBED_CHUNKS,
                "hybrid_weights": list(config.HYBRID_WEIGHTS), "boost_location": config.BOOST_LOCATION,
                "boost_company": config.BOOST_COMPANY, "strip_names": config.QUERY_STRIP_NAMES,
                "search_mode": config.SEARCH_MODE, "rerank_default": config.RERANK, "rerank_top_n": config.RERANK_TOP_N,
                "rerank_model": config.RERANK_MODEL,
            },
            "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED},
            "latency": {"warmup_queries": WARMUP_QUERIES, "repeats": REPEATS},
        },
        "counts": {
            "queries": len(queries),
            "judged": len(queries) - len(counts["not_judged"]),
            "included": len(included),
            "no_relevant": counts["no_relevant"],
            "not_judged": counts["not_judged"],
            "judged_pairs": len(qrel_rows),
            "relevant_pairs": sum(1 for r in qrel_rows if r["label"] >= 1),
            "unjudged_in_top10": {n: sum(r["unjudged_in_top10"] or 0 for r in rows if r["system"] == n and r["included"]) for n in names},
        },
        "label_coverage": label_coverage(qrels, qids) if args.systems == "all" else {},
        "labelling": labelling_stats() if args.systems == "all" else {},
        "metrics": aggregate(rows, names),
        "per_category": per_category(rows, names),
        "bootstrap_ndcg@10": bootstrap_v1(rows, names),
        "sections": {name: {"n_queries": len({r["qid"] for r in part if r["included"]}), "metrics": aggregate(part, names)}
                     for name, part in sections.items()},
        "per_group": {name: per_category(part, names, key=lambda r: group_of(r["category"])) for name, part in sections.items()},
        "comparisons": comparisons,
        "tuning": read_json(TUNING_RESULTS) if has_splits and args.systems == "all" else None,
        "latency": latency,
        "hardware": hardware(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / "summary.json", summary)
    write_jsonl(args.out / "per_query.jsonl", rows)
    atomic_write_bytes(args.out / "report.md", report(summary, rows, queries, specs).encode("utf-8"))

    print(f"{len(included)} of {len(queries)} queries scored ({len(counts['no_relevant'])} without a relevant doc, "
          f"{len(counts['not_judged'])} not judged)\n")
    for title, part in summary["sections"].items():
        print(f"[{title}: {part['n_queries']} queries]")
        print(f"{'system':22}  {'recall@10':>9}  {'MRR@10':>7}  {'nDCG@10':>7}  {'p50 ms':>7}  {'p95 ms':>7}")
        for name in names:
            mm, ll = part["metrics"][name], latency["search"][name]
            print(f"{name:22}  {fmt(mm['recall@10']):>9}  {fmt(mm['mrr@10']):>7}  {fmt(mm['ndcg@10']):>7}  "
                  f"{fmt(ll['p50_ms'], 2):>7}  {fmt(ll['p95_ms'], 2):>7}")
        print()
    for key, b in summary["bootstrap_ndcg@10"].items():
        print(f"nDCG@10 {key}: {fmt(b['mean'])}  95% CI [{fmt(b['low'])}, {fmt(b['high'])}]")
    for item in (comparisons.get("before_after") or []):
        b = item["bootstrap"]
        print(f"test nDCG@10 {item['what']}: {signed(b['mean'])}  95% CI [{signed(b['low'])}, {signed(b['high'])}]")
    print(f"\nwrote {args.out / 'report.md'}")


if __name__ == "__main__":
    main()
