"""The systems under test. Each has `search(query_text, query_vec, k) -> list[doc_id]`.

Version 1 (kept exactly as it was, so eval/results/before_v1 stays reproducible):

- KeywordRetriever: SQLite FTS5 full-text index with BM25 ranking.
- VectorRetriever: exact cosine search over the production nomic-embed-text vectors, like
  `core/embeddings.py` but vectorised with numpy. The structured boosts that
  `core/ranking.py` adds afterwards (department, hours, level) are not part of this test.
- HybridRetriever: Reciprocal Rank Fusion of the two.

The keyword index, the query escaping and the fusion are the app's own code
(`cityu_jobs.core.search`, used when config.SEARCH_MODE is "keyword" or "hybrid"). This
module only wraps them in the eval's search() interface, so what is measured is what the
app runs. All constants were fixed before any relevance label existed; none is tuned on
the labels. Ties are broken by doc_id, so every ranking is deterministic.

This module imports numpy only inside VectorRetriever, so the keyword and fusion code
(and their tests) work without the eval dependency group.
"""

import heapq

from cityu_jobs.core.query import parse_query
from cityu_jobs.core.rerank import rerank_order
from cityu_jobs.core.search import (  # noqa: F401  (re-exported for the eval tests)
    COLUMN_WEIGHTS,
    COLUMNS,
    FUSION_DEPTH,
    RRF_K,
    TOKENIZER,
    KeywordIndex,
    cjk_bigrams,
    fts_query,
    fuse_and_boost,
    retrieval_query,
    rrf,
)

# --- keyword search -----------------------------------------------------------------


class KeywordRetriever:
    name = "keyword"

    def __init__(self, docs: list[dict]):
        self.index = KeywordIndex(docs)
        self.db = self.index.db

    def search(self, query_text: str, query_vec=None, k: int = 10) -> list[str]:
        return self.index.search(query_text, k)


# --- vector search ------------------------------------------------------------------


class VectorRetriever:
    name = "vector"

    def __init__(self, doc_ids: list[str], doc_vectors):
        import numpy as np

        if list(doc_ids) != sorted(doc_ids):
            raise ValueError("doc_ids must be sorted: the stable sort then breaks ties by doc_id")
        if len(doc_ids) != len(doc_vectors):
            raise ValueError("one vector per doc")
        self._np = np
        self.doc_ids = list(doc_ids)
        self.vectors = np.ascontiguousarray(doc_vectors, dtype=np.float32)

    def scores(self, query_vec):
        return self.vectors @ self._np.asarray(query_vec, dtype=self._np.float32)  # cosine: rows are unit length

    def ranking(self, query_vec, k: int | None = None) -> list[str]:
        """Doc ids by cosine, best first; all of them when k is None."""
        # Stable sort of -score: equal scores keep corpus order, i.e. doc_id order.
        order = self._np.argsort(-self.scores(query_vec), kind="stable")[:k]
        return [self.doc_ids[i] for i in order]

    def search(self, query_text: str, query_vec, k: int = 10) -> list[str]:
        return self.ranking(query_vec, k)


# --- hybrid -------------------------------------------------------------------------


class HybridRetriever:
    name = "hybrid"

    def __init__(self, keyword: KeywordRetriever, vector: VectorRetriever, depth: int = FUSION_DEPTH, k: int = RRF_K):
        self.keyword, self.vector, self.depth, self.k = keyword, vector, depth, k

    def search(self, query_text: str, query_vec, k: int = 10) -> list[str]:
        lists = [
            self.keyword.search(query_text, query_vec, self.depth),
            self.vector.search(query_text, query_vec, self.depth),
        ]
        return rrf(lists, self.k, self.depth)[:k]


def build_systems(docs: list[dict], doc_vectors) -> dict:
    """{"keyword": ..., "vector": ..., "hybrid": ...} over the committed corpus."""
    keyword = KeywordRetriever(docs)
    vector = VectorRetriever([d["doc_id"] for d in docs], doc_vectors)
    return {"keyword": keyword, "vector": vector, "hybrid": HybridRetriever(keyword, vector)}


# --- version 2 ------------------------------------------------------------------------
# Everything below wraps the app's version 2 code (core/text.py, core/query.py,
# core/search.fuse_and_boost, core/rerank.rerank_order); see systems.py for the list of
# systems that are evaluated.


def pick_vector(query_vecs, model: str):
    """A system's query vector: `query_vecs` is one vector (v1 callers) or {embedding model: vector}."""
    return query_vecs[model] if isinstance(query_vecs, dict) else query_vecs


class ChunkRetriever(VectorRetriever):
    """A listing's score is the cosine of its best piece (core/text.chunk_texts).

    `doc_vectors` are the first pieces (the compact texts); `chunk_vectors[i]` is a further
    piece of the listing in corpus row `owners[i]`.
    """

    name = "vector_chunks"

    def __init__(self, doc_ids: list[str], doc_vectors, chunk_vectors, owners: list[int]):
        super().__init__(doc_ids, doc_vectors)
        np = self._np
        if len(chunk_vectors) != len(owners):
            raise ValueError("one owner row per chunk vector")
        self.chunks = np.ascontiguousarray(chunk_vectors, dtype=np.float32)
        self.owners = np.asarray(owners, dtype=np.int64)

    def scores(self, query_vec):
        np = self._np
        query = np.asarray(query_vec, dtype=np.float32)
        best = self.vectors @ query
        if len(self.owners):
            np.maximum.at(best, self.owners, self.chunks @ query)
        return best


class V2Retriever:
    """Weighted fusion of a keyword and/or a vector ranking, plus the place / company boost.

    The rankings are full (every matching listing), and the scoring is the app's
    `search.fuse_and_boost`. With one ranking, weights (1, 1) and no boost this is that
    ranking unchanged.
    """

    def __init__(
        self,
        name: str,
        facts: dict[str, tuple[str, str, str]],
        companies,
        keyword: KeywordRetriever | None = None,
        vector: VectorRetriever | None = None,
        model: str | None = None,
        weights: tuple[float, float] = (1.0, 1.0),
        w_location: float = 0.0,
        w_company: float = 0.0,
        strip: bool = False,
    ):
        if keyword is None and vector is None:
            raise ValueError("a system needs a keyword or a vector ranking")
        self.name, self.facts, self.companies = name, facts, companies
        self.keyword, self.vector, self.model = keyword, vector, model
        self.weights, self.w_location, self.w_company, self.strip = weights, w_location, w_company, strip

    def scores(self, query_text: str, query_vecs) -> dict[str, float]:
        parsed = parse_query(query_text, self.companies)
        rankings, weights = [], []
        if self.keyword is not None:
            rankings.append(self.keyword.index.search(retrieval_query(parsed, self.strip)))
            weights.append(self.weights[0] if self.vector is not None else 1.0)
        if self.vector is not None:
            rankings.append(self.vector.ranking(pick_vector(query_vecs, self.model)))
            weights.append(self.weights[1] if self.keyword is not None else 1.0)
        return fuse_and_boost(rankings, weights, parsed, self.facts, self.w_location, self.w_company)

    def search(self, query_text: str, query_vecs=None, k: int = 10) -> list[str]:
        scores = self.scores(query_text, query_vecs)
        return heapq.nsmallest(k, scores, key=lambda d: (-scores[d], d))


class StoredScores:
    """Reranker scores read from eval/data/rerank_scores.jsonl.gz: no torch or GPU needed."""

    def __init__(self, rows: list[dict], queries: list[dict]):
        text_of = {q["qid"]: q["query"] for q in queries}
        self.by_query: dict[str, dict[str, float]] = {}
        for row in rows:
            if row["qid"] in text_of:
                self.by_query.setdefault(text_of[row["qid"]], {})[row["doc_id"]] = row["score"]

    def __call__(self, query_text: str, doc_ids: list[str]) -> list[float]:
        known = self.by_query.get(query_text, {})
        missing = [d for d in doc_ids if d not in known]
        if missing:
            raise KeyError(f"no stored reranker score for {len(missing)} candidates of {query_text!r} "
                           f"(e.g. {missing[0]}): run eval/rerank_scores.py")
        return [known[d] for d in doc_ids]


class RerankRetriever:
    """The top `depth` of a first-stage system, re-ordered by cross-encoder scores (core/rerank.rerank_order)."""

    def __init__(self, name: str, first, scorer, depth: int):
        self.name, self.first, self.scorer, self.depth = name, first, scorer, depth

    def candidates(self, query_text: str, query_vecs) -> list[str]:
        return self.first.search(query_text, query_vecs, self.depth)

    def search(self, query_text: str, query_vecs=None, k: int = 10) -> list[str]:
        head = self.first.search(query_text, query_vecs, max(self.depth, k))
        scores = self.scorer(query_text, head[: self.depth])
        return rerank_order(head, scores, self.depth)[:k]
