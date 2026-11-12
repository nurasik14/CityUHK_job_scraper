"""Precompute the cross-encoder scores the reranked systems need (needs the `rerank` group and a GPU).

    uv run --group eval --group rerank python eval/rerank_scores.py

For every query and every reranked system in eval/systems.py, the top `rerank` results of
its first stage are scored with BAAI/bge-reranker-v2-m3 (the app's core/rerank.Reranker:
sentence-transformers CrossEncoder, fp16 on the GPU). The reranker reads the query and the
listing's compact text (core/text.py).

Writes eval/data/rerank_scores.jsonl.gz (qid, doc_id, score; sorted) and the "rerank"
section of meta.json. `eval.run` then reproduces the reranked systems' numbers from that
file alone, without torch or a GPU.

Scores are kept in eval/.tuning/rerank_cache.jsonl (git-ignored) as they are computed, so a
pair is only ever scored once: a GPU in half precision does not give bit-identical scores
when the same pair is scored again in a different batch, and the stored value is the one
every later step uses.
"""

import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import RERANK_SCORES, TUNING, dumps_line, file_entry, read_jsonl, update_meta, write_jsonl_gz

CACHE = TUNING / "rerank_cache.jsonl"
SCORE_DECIMALS = 6


class CachedScorer:
    """(query text, doc ids) -> scores, from the local cache, computing what is missing with the real model.

    With `live=False` a missing pair is an error instead (no torch needed).
    """

    def __init__(self, texts: dict[str, str], cache_path: Path = CACHE, live: bool = True):
        self.texts, self.cache_path, self.live = texts, cache_path, live
        self.known: dict[tuple[str, str], float] = {
            (row["query"], row["doc_id"]): row["score"] for row in read_jsonl(cache_path)
        }
        self._model = None
        self.computed = 0
        self.seconds = 0.0

    @property
    def model(self):
        if self._model is None:
            from cityu_jobs.core.rerank import Reranker

            self._model = Reranker()
        return self._model

    def __call__(self, query_text: str, doc_ids: list[str]) -> list[float]:
        missing = [d for d in doc_ids if (query_text, d) not in self.known]
        if missing:
            if not self.live:
                raise KeyError(f"no cached reranker score for {len(missing)} candidates of {query_text!r}")
            start = time.perf_counter()
            scores = self.model.scores(query_text, [self.texts[d] for d in missing])
            self.seconds += time.perf_counter() - start
            self.computed += len(missing)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "a", encoding="utf-8") as f:
                for doc_id, score in zip(missing, scores):
                    score = round(score, SCORE_DECIMALS)
                    self.known[(query_text, doc_id)] = score
                    f.write(dumps_line({"query": query_text, "doc_id": doc_id, "score": score}) + "\n")
        return [self.known[(query_text, d)] for d in doc_ids]


def model_revision(model_name: str) -> str | None:
    """The commit of the model snapshot in the local Hugging Face cache (no model is loaded)."""
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id == model_name:
                return max(repo.revisions, key=lambda r: r.last_modified).commit_hash
    except Exception:  # no cache, no huggingface_hub: the revision is simply not recorded
        pass
    return None


def model_details(scorer: CachedScorer) -> dict:
    """What produced the scores: model revision, library versions, device and precision."""
    import sentence_transformers
    import torch

    reranker = scorer.model
    inner = reranker._model
    config = getattr(getattr(inner, "model", None), "config", None)
    return {
        "model": reranker.model_name,
        "revision": getattr(config, "_commit_hash", None),
        "device": reranker.device,
        "gpu": torch.cuda.get_device_name(0) if reranker.device == "cuda" else None,
        "precision": "fp16" if reranker.fp16 else "fp32",
        "torch": torch.__version__,
        "sentence_transformers": sentence_transformers.__version__,
    }


def main() -> None:
    from cityu_jobs import config
    from eval.systems import Resources, query_vector_rows, system_specs

    resources = Resources()
    queries = resources.all_queries
    qvecs = query_vector_rows(queries, queries, resources.query_vectors())
    scorer = CachedScorer(resources.compact)
    specs = [s for s in system_specs() if s.rerank]
    rows: dict[tuple[str, str], float] = {}
    for spec in specs:
        system = resources.build(spec, scorer=scorer)
        for q, vecs in zip(queries, qvecs):
            candidates = system.candidates(q["query"], vecs)
            for doc_id, score in zip(candidates, scorer(q["query"], candidates)):
                rows[(q["qid"], doc_id)] = score
        print(f"{spec.name}: top {spec.rerank} of {len(queries)} queries scored", flush=True)
    write_jsonl_gz(RERANK_SCORES, [{"qid": qid, "doc_id": d, "score": s} for (qid, d), s in sorted(rows.items())])
    previous = (json.loads((RERANK_SCORES.parent / "meta.json").read_text(encoding="utf-8")).get("rerank") or {})
    details = model_details(scorer) if scorer.computed or not previous else {k: previous.get(k) for k in (
        "model", "revision", "device", "gpu", "precision", "torch", "sentence_transformers")}
    if not details.get("revision"):
        details["revision"] = model_revision(config.RERANK_MODEL)
    update_meta(
        "rerank",
        {
            **details,
            "max_tokens": config.RERANK_MAX_TOKENS,
            "input": "query + core/text.compact_text of the listing",
            "systems": {s.name: s.rerank for s in specs},
            "pairs": len(rows),
            "score_decimals": SCORE_DECIMALS,
            "files": {RERANK_SCORES.name: file_entry(RERANK_SCORES)},
        },
    )
    print(f"wrote {len(rows)} scores to {RERANK_SCORES.name} ({scorer.computed} computed now in {scorer.seconds:.1f} s)")


if __name__ == "__main__":
    main()
