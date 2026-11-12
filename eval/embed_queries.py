"""Embed the labelled queries (needs local Ollama).

    uv run --group eval python eval/embed_queries.py

Each query is embedded on its own, exactly like the app embeds a student's search
(`OllamaProvider.embed([text], kind="query")`): nomic-embed-text gets its "search_query: "
prefix, bge-m3 gets none (the model was trained without instructions).

Writes, one L2-normalised float32 row per line of queries.jsonl:
- eval/data/query_vectors.npy          nomic-embed-text
- eval/data/query_vectors_bge_m3.npy   bge-m3
and the "queries" section of meta.json.

Append-only: rows that already exist are kept byte for byte and only the queries after them
are embedded. That is what keeps the version 1 results reproducible after new queries were
added (Ollama does not promise bit-identical vectors between runs). Queries must therefore
only ever be appended to queries.jsonl.
"""

import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import (
    QUERIES,
    QUERY_VECTOR_FILES,
    file_entry,
    load_queries,
    normalise_rows,
    ollama_digest,
    require_numpy,
    update_meta,
)


def embed_missing(model: str, path: Path, queries: list[dict]) -> tuple[int, int]:
    """Extend `path` to one row per query. Returns (rows kept, rows embedded now)."""
    from cityu_jobs.core.ai.providers import OllamaProvider

    np = require_numpy()
    kept = np.load(path) if path.exists() else None
    done = 0 if kept is None else len(kept)
    if done > len(queries):
        sys.exit(f"{path.name} has more rows than queries.jsonl has lines")
    embedder = OllamaProvider(embed_model=model)
    vectors = [embedder.embed([q["query"]], kind="query")[0] for q in queries[done:]]
    if vectors:
        fresh = normalise_rows(np.asarray(vectors, dtype=np.float64))
        matrix = fresh if kept is None else np.vstack([kept.astype(np.float32), fresh])
        with open(path, "wb") as f:
            np.save(f, matrix, allow_pickle=False)
    return done, len(vectors)


def main() -> None:
    np = require_numpy()
    queries = load_queries()
    for q in queries:
        if not (q.get("qid") and q.get("query") and q.get("category")):
            sys.exit(f"bad query line: {q}")
    models = {}
    for model, path in QUERY_VECTOR_FILES.items():
        kept, embedded = embed_missing(model, path, queries)
        matrix = np.load(path)
        assert matrix.shape[0] == len(queries)
        models[model] = {"file": path.name, "ollama_digest": ollama_digest(model), "dim": int(matrix.shape[1]),
                         "prefix": "search_query: " if "nomic" in model else ""}
        print(f"{path.name}: kept {kept} rows, embedded {embedded} ({model})")
    update_meta(
        "queries",
        {
            "n_queries": len(queries),
            "categories": dict(sorted(Counter(q["category"] for q in queries).items())),
            "splits": dict(sorted(Counter(q.get("split", "none") for q in queries).items())),
            "embed_models": models,
            "files": {p.name: file_entry(p) for p in (QUERIES, *QUERY_VECTOR_FILES.values())},
        },
    )


if __name__ == "__main__":
    main()
