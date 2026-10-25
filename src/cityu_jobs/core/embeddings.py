"""Semantic search: embed jobs and the student's query, then compare them by cosine similarity.

An embedding model maps a text to a vector (a list of numbers, 768 for nomic-embed-text)
so that texts with similar *meaning* point in similar directions, even with no words in
common ("build websites" vs "frontend developer"). Cosine similarity measures how close
two directions are: 1 = same direction, 0 = unrelated.

At a few hundred jobs, comparing the query against every job in plain Python takes
milliseconds, so no vector database or numpy is needed.
"""

import math
from collections.abc import Callable, Iterable

from cityu_jobs.core.ai.providers import EmbeddingProvider
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job, embedding_text

# Texts sent per request: fewer HTTP round trips, without making one request huge.
BATCH_SIZE = 16


def embed_jobs(
    jobs: Iterable[Job],
    cache: Cache,
    embedder: EmbeddingProvider,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, list[float]]:
    """Return {job_id: vector} for every job, embedding only those missing from the cache.

    A cached vector is reused only if it was made from the job's current content by the
    current embedding model. Jobs must already be in the cache.
    """
    jobs = list(jobs)
    vectors: dict[str, list[float]] = {}
    todo = []
    for job in jobs:
        cached = cache.get_embedding(job.job_id, job.content_hash, embedder.embed_model)
        if cached is not None:
            vectors[job.job_id] = cached
        else:
            todo.append(job)

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        for job, vector in zip(batch, embedder.embed([embedding_text(j) for j in batch], kind="document")):
            cache.set_embedding(job.job_id, vector, embedder.embed_model, job.content_hash)
            vectors[job.job_id] = vector
        if progress:
            progress(min(start + BATCH_SIZE, len(todo)), len(todo))
    return vectors


def embed_query(text: str, embedder: EmbeddingProvider) -> list[float]:
    """Embed the student's free-text query (marked as a "query", not a "document")."""
    return embedder.embed([text], kind="query")[0]


def cosine(a: list[float], b: list[float]) -> float:
    """cos(angle) = (a · b) / (|a| |b|). Returns 0 for an all-zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def similarities(query_vector: list[float], job_vectors: dict[str, list[float]]) -> dict[str, float]:
    """{job_id: cosine similarity between the query and that job}."""
    return {job_id: cosine(query_vector, v) for job_id, v in job_vectors.items()}
