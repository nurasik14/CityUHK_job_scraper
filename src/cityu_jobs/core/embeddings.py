"""Semantic search: embed jobs and the student's query, then compare them by cosine similarity.

An embedding model maps a text to a vector (a list of numbers, 1024 for bge-m3) so that
texts with similar *meaning* point in similar directions, even with no words in common
("build websites" vs "frontend developer"). Cosine similarity measures how close two
directions are: 1 = same direction, 0 = unrelated.

What gets embedded for a job is not its whole text but a compact one (core/text.py): title,
company, location, job type, department and ~500 characters of the description with company
boilerplate removed. With config.EMBED_CHUNKS a long description is cut into several such
pieces, each embedded with the job's header, and the job's similarity is that of its best
piece. So a job has one or more vectors.

At a few thousand jobs, comparing the query against every vector in plain Python takes a
fraction of a second, so no vector database or numpy is needed.
"""

import hashlib
import math
from collections.abc import Callable, Iterable

from cityu_jobs import config
from cityu_jobs.core import text as text_rules
from cityu_jobs.core.ai.providers import EmbeddingProvider
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job, listing_fields

# Texts sent per request: fewer HTTP round trips, without making one request huge.
BATCH_SIZE = 16


def embedding_texts(jobs: Iterable[Job], chunks: bool | None = None) -> dict[str, list[str]]:
    """{job_id: the text(s) to embed}: one compact text, or (chunks) the pieces of core/text.chunk_texts.

    Company boilerplate is found across *these* jobs (text.repeated_paragraphs), so pass all
    the jobs being searched, not one at a time.
    """
    chunks = config.EMBED_CHUNKS if chunks is None else chunks
    fields = {job.job_id: listing_fields(job) for job in jobs}
    boilerplate = text_rules.repeated_paragraphs(fields.values())
    if chunks:
        return {job_id: text_rules.chunk_texts(f, boilerplate) for job_id, f in fields.items()}
    return {job_id: [text_rules.compact_text(f, boilerplate)] for job_id, f in fields.items()}


def text_hash(texts: list[str]) -> str:
    """Fingerprint of exactly what was embedded for a job (and of the text format version)."""
    joined = f"v{text_rules.EMBED_TEXT_VERSION}\x1e" + "\x1e".join(texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def embed_jobs(
    jobs: Iterable[Job],
    cache: Cache,
    embedder: EmbeddingProvider,
    progress: Callable[[int, int], None] | None = None,
    texts: dict[str, list[str]] | None = None,
) -> dict[str, list[list[float]]]:
    """Return {job_id: its vectors} for every job, embedding only those missing from the cache.

    Cached vectors are reused only if they were made by the current embedding model, from
    the job's current content *and* from exactly the text(s) that would be embedded now. So
    an edited listing, another model, a new text format (compact text instead of the full
    text, pieces instead of one text) or a changed boilerplate rule all lead to new vectors,
    with nothing to delete by hand. Jobs must already be in the cache. `texts` is
    `embedding_texts(jobs)` if the caller already has it.
    """
    jobs = list(jobs)
    texts = embedding_texts(jobs) if texts is None else texts
    vectors: dict[str, list[list[float]]] = {}
    todo: list[tuple[Job, int]] = []  # (job, index of one of its texts)
    for job in jobs:
        cached = cache.get_embeddings(job.job_id, job.content_hash, embedder.embed_model, text_hash(texts[job.job_id]))
        if cached is not None:
            vectors[job.job_id] = cached
        else:
            todo += [(job, i) for i in range(len(texts[job.job_id]))]

    fresh: dict[str, list[list[float]]] = {}
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        embedded = embedder.embed([texts[job.job_id][i] for job, i in batch], kind="document")
        for (job, _i), vector in zip(batch, embedded):
            fresh.setdefault(job.job_id, []).append(vector)
            if len(fresh[job.job_id]) == len(texts[job.job_id]):  # all of this job's pieces are in
                cache.set_embeddings(job.job_id, fresh[job.job_id], embedder.embed_model, job.content_hash,
                                     text_hash(texts[job.job_id]))
                vectors[job.job_id] = fresh.pop(job.job_id)
        if progress:
            progress(min(start + BATCH_SIZE, len(todo)), len(todo))
    return vectors


def embed_query(text: str, embedder: EmbeddingProvider) -> list[float]:
    """Embed the student's free-text query (marked as a "query", not a "document")."""
    return embedder.embed([text], kind="query")[0]


# math.sumprod (Python 3.12+) does the multiply-and-add loop in C, ~10x faster than a generator.
_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(x * y for x, y in zip(a, b)))


def cosine(a: list[float], b: list[float]) -> float:
    """cos(angle) = (a · b) / (|a| |b|). Returns 0 for an all-zero vector."""
    norm = math.sqrt(_dot(a, a)) * math.sqrt(_dot(b, b))
    return _dot(a, b) / norm if norm else 0.0


def similarities(query_vector: list[float], job_vectors: dict[str, list]) -> dict[str, float]:
    """{job_id: cosine similarity between the query and that job}.

    A job with several vectors (pieces) gets the similarity of its best piece. A value may
    also be a single vector.
    """
    out = {}
    for job_id, vectors in job_vectors.items():
        if vectors and not isinstance(vectors[0], (list, tuple)):
            vectors = [vectors]
        out[job_id] = text_rules.max_similarity(cosine(query_vector, v) for v in vectors) if vectors else 0.0
    return out
