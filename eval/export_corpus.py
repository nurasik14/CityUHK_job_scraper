"""Export the public internship listings from the app's local cache into eval/data/.

    uv run --group eval python eval/export_corpus.py [--cache .data/cache.json]
    uv run --group eval python eval/export_corpus.py --add-fields     # v2: see below

Writes:
- corpus.jsonl.gz: one listing per line (doc_id, source, company, title, location, url, text,
  and since v2 job_type, department, description, requirements), sorted by doc_id, gzip
  header without name or time so the bytes are reproducible.
  `text` is the full listing text (`cityu_jobs.core.models.full_text`): what keyword search
  indexes, what version 1 embedded, and what the relevance judge was shown.
- doc_vectors.npy: the production nomic-embed-text document vectors, L2-normalised, one row
  per corpus line. Cached vectors are reused when model and content hash match; only
  missing ones are embedded through Ollama (same code path as the app).
- meta.json ("corpus" section): counts, model, file sizes and sha256 of both files.

Only `intern_tech` and `intern_bigtech` (public company job boards) are exported. AIMS and
ITC STEM listings need a university login and are never written here.

`--add-fields` (retrieval v2) upgrades the existing corpus file in place instead of
exporting again: the app's cache has moved on since the 2026-11-06 snapshot (listings were
edited or closed), and the labels belong to that snapshot. It keeps every line's doc_id,
order and `text` byte-identical and adds the four fields the compact embedding text needs.
They are read back out of `text` itself, which is "title, company, job type, department,
description" joined by newlines (no listing in this corpus has separate requirements), and
cross-checked against the cache for every listing the cache still holds unchanged.
doc_vectors.npy is not touched.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import (
    CORPUS,
    DOC_VECTORS,
    EMBED_MODEL,
    META,
    PUBLIC_ID_PREFIX,
    PUBLIC_SOURCES,
    load_corpus,
    normalise_rows,
    ollama_digest,
    read_json,
    require_numpy,
    sha256_file,
    update_meta,
    write_jsonl_gz,
)

MAX_FLOAT32_BYTES = 20 * 1024 * 1024  # above this, store float16 (see README)
EMBED_BATCH = 16
# The listing fields core/text.py builds the compact embedding text from (added in v2).
TEXT_FIELDS = ("job_type", "department", "description", "requirements")
# Every public internship listing has these two values (core/internships/scraper.py).
V1_JOB_TYPE, V1_DEPARTMENT = "Internship", "Tech industry"


def fields_from_text(doc: dict) -> dict:
    """job_type, department, description, requirements of a v1 corpus line, read out of its `text`.

    v1 `text` = title, company, job nature, department, description, requirements joined by
    newlines, empty parts skipped. In this corpus job nature and department are constants
    and requirements are always empty, so the description is whatever follows the header.
    """
    head = "\n".join(p for p in (doc["title"], doc["company"], V1_JOB_TYPE, V1_DEPARTMENT) if p)
    if not doc["text"].startswith(head):
        raise ValueError(f"{doc['doc_id']}: text does not start with title/company/job type/department")
    rest = doc["text"][len(head):]
    if rest and not rest.startswith("\n"):
        raise ValueError(f"{doc['doc_id']}: unexpected text after the header")
    return {"job_type": V1_JOB_TYPE, "department": V1_DEPARTMENT, "description": rest[1:], "requirements": ""}


def add_fields(docs: list[dict], cache_data: dict | None = None) -> tuple[list[dict], dict]:
    """The same corpus lines with TEXT_FIELDS added; `text` and everything else untouched.

    With the app's cache, every listing it still holds with the same full text is
    cross-checked field by field. Returns (docs, stats).
    """
    from cityu_jobs.core.models import Job, full_text, listing_fields

    out, stats = [], Counter()
    for doc in docs:
        fields = fields_from_text(doc)
        parts = (doc["title"], doc["company"], fields["job_type"], fields["department"], fields["description"])
        assert "\n".join(p for p in parts if p) == doc["text"], doc["doc_id"]
        entry = (cache_data or {}).get("jobs", {}).get(doc["doc_id"])
        if entry is None:
            stats["not_in_cache"] += 1
        else:
            job = Job.from_dict(entry["job"])
            if full_text(job) != doc["text"]:
                stats["changed_in_cache_since_export"] += 1
            else:
                cached = listing_fields(job)
                assert all(cached[k] == fields[k] for k in TEXT_FIELDS), doc["doc_id"]
                assert cached["company"] == doc["company"], doc["doc_id"]
                stats["cross_checked_with_cache"] += 1
                # `location` is not part of `text`; the corpus keeps the snapshot's value.
                stats["location_changed_in_cache_since_export"] += cached["location"] != doc["location"]
        out.append({**doc, **fields})
    return out, dict(stats)


def upgrade_corpus(cache_path: Path) -> None:
    """--add-fields: rewrite corpus.jsonl.gz with TEXT_FIELDS and update its entry in meta.json."""
    before = load_corpus()
    if all(k in before[0] for k in TEXT_FIELDS):
        sys.exit("corpus.jsonl.gz already has the v2 text fields")
    cache_data = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
    docs, stats = add_fields(before, cache_data)
    assert [d["doc_id"] for d in docs] == [d["doc_id"] for d in before]
    assert all(a["text"] == b["text"] for a, b in zip(docs, before))
    old_sha = sha256_file(CORPUS)
    write_jsonl_gz(CORPUS, docs)
    corpus = read_json(META)["corpus"]
    corpus["files"][CORPUS.name] = {"bytes": CORPUS.stat().st_size, "sha256": sha256_file(CORPUS)}
    corpus["fields_added_v2"] = {
        "fields": list(TEXT_FIELDS),
        "how": "read out of `text` (title, company, job type, department, description); doc_ids, order and `text` unchanged",
        "sha256_before": old_sha,
        **stats,
    }
    update_meta("corpus", corpus)
    print(f"added {', '.join(TEXT_FIELDS)} to {len(docs)} docs ({stats}); "
          f"corpus sha256 {old_sha[:12]} -> {corpus['files'][CORPUS.name]['sha256'][:12]}")


def select_public(cache_data: dict) -> tuple[list[dict], list[list[float] | None], dict]:
    """(docs, cached vectors or None per doc, stats). Pure function over the cache dict."""
    from cityu_jobs.core.models import Job, full_text, listing_fields

    docs, vectors = [], []
    stats = Counter()
    for job_id in sorted(cache_data["jobs"]):
        entry = cache_data["jobs"][job_id]
        source = entry["job"]["source"]
        if source not in PUBLIC_SOURCES:
            stats["excluded_non_public"] += 1
            continue
        if not entry.get("active"):
            stats["excluded_inactive"] += 1
            continue
        job = Job.from_dict(entry["job"])
        # Hard guarantees: never export a login-gated listing.
        assert job.source in PUBLIC_SOURCES and job.job_id.startswith(PUBLIC_ID_PREFIX), job.job_id
        docs.append(
            {
                "doc_id": job.job_id,
                "source": job.source,
                "company": job.company or "",
                "title": job.title,
                "location": job.location or "",
                "url": job.url or "",
                "text": full_text(job),
                **{k: listing_fields(job)[k] for k in TEXT_FIELDS},
            }
        )
        stats[f"docs_{source}"] += 1
        emb = entry.get("embedding") or {}
        if emb.get("model") == EMBED_MODEL and emb.get("content_hash") == job.content_hash and emb.get("vector"):
            vectors.append(emb["vector"])
        else:
            vectors.append(None)
    return docs, vectors, dict(stats)


def main() -> None:
    from cityu_jobs import config

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, default=config.CACHE_FILE, help="the app's cache.json")
    parser.add_argument("--add-fields", action="store_true",
                        help="add the v2 text fields to the existing corpus file instead of exporting again")
    args = parser.parse_args()
    if args.add_fields:
        upgrade_corpus(args.cache)
        return
    np = require_numpy()

    cache_data = json.loads(args.cache.read_text(encoding="utf-8"))
    docs, vectors, stats = select_public(cache_data)
    if not docs:
        sys.exit("no active public listings in the cache")
    assert all(d["source"] in PUBLIC_SOURCES and d["doc_id"].startswith(PUBLIC_ID_PREFIX) for d in docs)
    assert [d["doc_id"] for d in docs] == sorted(d["doc_id"] for d in docs)

    missing = [i for i, v in enumerate(vectors) if v is None]
    if missing:
        from cityu_jobs.core.ai.providers import OllamaProvider

        print(f"embedding {len(missing)} listings without a cached vector through Ollama ...")
        embedder = OllamaProvider(embed_model=EMBED_MODEL)
        for start in range(0, len(missing), EMBED_BATCH):
            batch = missing[start : start + EMBED_BATCH]
            for i, vector in zip(batch, embedder.embed([docs[i]["text"] for i in batch], kind="document")):
                vectors[i] = vector
    matrix = normalise_rows(np.asarray(vectors, dtype=np.float64))
    dtype = "float32"
    if matrix.nbytes > MAX_FLOAT32_BYTES:
        matrix, dtype = matrix.astype(np.float16), "float16"

    write_jsonl_gz(CORPUS, docs)
    with open(DOC_VECTORS, "wb") as f:
        np.save(f, matrix, allow_pickle=False)

    snapshot = {s: (cache_data.get("sources", {}).get(s) or {}).get("last_scraped_at") for s in PUBLIC_SOURCES}
    update_meta(
        "corpus",
        {
            "n_docs": len(docs),
            "docs_per_source": {s: stats.get(f"docs_{s}", 0) for s in PUBLIC_SOURCES},
            "excluded_inactive_public": stats.get("excluded_inactive", 0),
            "excluded_login_gated": stats.get("excluded_non_public", 0),
            "unique_texts": len({d["text"] for d in docs}),
            "scraped_at": snapshot,
            "embed_model": EMBED_MODEL,
            "ollama_digest_at_export": ollama_digest(),
            "vectors_reused_from_cache": len(docs) - len(missing),
            "vectors_embedded_now": len(missing),
            "dim": int(matrix.shape[1]),
            "dtype": dtype,
            "files": {
                CORPUS.name: {"bytes": CORPUS.stat().st_size, "sha256": sha256_file(CORPUS)},
                DOC_VECTORS.name: {"bytes": DOC_VECTORS.stat().st_size, "sha256": sha256_file(DOC_VECTORS)},
            },
        },
    )
    print(
        f"wrote {len(docs)} docs ({stats}); vectors reused {len(docs) - len(missing)}, embedded {len(missing)}; "
        f"{CORPUS.stat().st_size / 1e6:.1f} MB corpus, {DOC_VECTORS.stat().st_size / 1e6:.1f} MB vectors ({dtype})"
    )


if __name__ == "__main__":
    main()
