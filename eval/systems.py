"""Which systems are evaluated, and how each is put together from the committed files.

A system is described by a `Spec` (plain data), so the tuning script can build variants
with other settings from the same code. `system_specs()` is the list in the report:

Version 1 (unchanged code and vectors; `--systems v1` runs only these):
    keyword              BM25 over the full listing text
    vector               nomic-embed-text on the full listing text
    hybrid               RRF (k = 60, equal weights, top 100 of each) of the two

Version 2, one change at a time:
    nomic_compact        nomic-embed-text on the compact text (core/text.py)
    nomic_chunks         nomic-embed-text, best of the listing's ~500-character pieces
    bge_compact          bge-m3 on the compact text
    bge_chunks           bge-m3, best piece
    keyword_qu           keyword + query understanding (place / company boost)
    bge_chunks_qu        bge-m3 best piece + query understanding
    hybrid_bge_compact   keyword + bge-m3 compact, equal weights
    hybrid_bge_chunks    keyword + bge-m3 best piece, equal weights
    hybrid_alt_weights   the same with the vector ranking at half weight (fusion-weight check)
    keyword_rerank       v1 keyword, top 30 reranked by bge-reranker-v2-m3

Version 2 as the app runs it (cityu_jobs.config: embedding model, pieces, fusion weights,
boost weights), i.e. the app's three search modes and the optional reranker:
    app_keyword          = keyword_qu
    app_vector           = bge_chunks_qu
    app_hybrid           keyword + bge-m3 best piece, config.HYBRID_WEIGHTS, + query understanding
    app_hybrid_rerank    app_hybrid, top 30 reranked

The version 2 settings are the app's own, chosen on the dev queries by eval/tune.py;
nothing here has its own copy of a tuned number.
"""

from dataclasses import dataclass, field, replace
from functools import cached_property

from cityu_jobs import config
from cityu_jobs.core.query import CompanyIndex
from eval import retrievers
from eval.common import (
    BGE_MODEL,
    CHUNK_VECTORS,
    CHUNK_VECTORS_BGE,
    CORPUS,
    DOC_VECTORS,
    DOC_VECTORS_BGE,
    DOC_VECTORS_COMPACT,
    EMBED_MODEL,
    QUERIES,
    QUERY_VECTOR_FILES,
    RERANK_SCORES,
    load_corpus,
    load_queries,
    load_vectors,
    read_jsonl,
)

# Document vector sets: name -> (first-piece file, further-pieces file or None, embedding model).
VECTOR_SETS = {
    "full": (DOC_VECTORS, None, EMBED_MODEL),
    "nomic_compact": (DOC_VECTORS_COMPACT, None, EMBED_MODEL),
    "nomic_chunks": (DOC_VECTORS_COMPACT, CHUNK_VECTORS, EMBED_MODEL),
    "bge_compact": (DOC_VECTORS_BGE, None, BGE_MODEL),
    "bge_chunks": (DOC_VECTORS_BGE, CHUNK_VECTORS_BGE, BGE_MODEL),
}


def app_vector_set() -> str:
    """The vector set that matches the app's settings (config.DEFAULT_EMBED_MODEL, config.EMBED_CHUNKS)."""
    family = "bge" if "bge" in config.DEFAULT_EMBED_MODEL else "nomic"
    return f"{family}_{'chunks' if config.EMBED_CHUNKS else 'compact'}"


@dataclass(frozen=True)
class Spec:
    name: str
    label: str = field(default="", compare=False)  # one line for the report
    keyword: bool = False
    vectors: str | None = None         # a key of VECTOR_SETS
    weights: tuple[float, float] = (1.0, 1.0)  # (keyword, vector) in the fusion
    w_location: float = 0.0            # query understanding boosts (0 = off)
    w_company: float = 0.0
    strip: bool = False                # search without the recognised place / company words
    rerank: int = 0                    # rerank this many top results (0 = no reranker)
    v1: bool = False                   # built with the original v1 classes

    @property
    def needs_vectors(self) -> bool:
        return self.vectors is not None

    @property
    def first_stage(self) -> "Spec":
        return replace(self, name=self.name.removesuffix("_rerank"), rerank=0)


def _qu() -> dict:
    return {"w_location": config.BOOST_LOCATION, "w_company": config.BOOST_COMPANY, "strip": config.QUERY_STRIP_NAMES}


ALT_WEIGHTS = (1.0, 0.5)  # hybrid_alt_weights: the vector ranking at half weight


def system_specs() -> list[Spec]:
    """The evaluated systems, with the app's current settings."""
    weights, qu, n, app = tuple(config.HYBRID_WEIGHTS), _qu(), config.RERANK_TOP_N, app_vector_set()
    w = f"{weights[0]:g}:{weights[1]:g}"
    alt = f"{ALT_WEIGHTS[0]:g}:{ALT_WEIGHTS[1]:g}"
    boosts = f"place boost {config.BOOST_LOCATION:g}, company boost {config.BOOST_COMPANY:g}"
    return [
        Spec("keyword", "v1 keyword: BM25 on the full text", keyword=True, v1=True),
        Spec("vector", "v1 vector: nomic-embed-text, full text", vectors="full", v1=True),
        Spec("hybrid", "v1 hybrid: RRF of the two, equal weights", keyword=True, vectors="full", v1=True),
        Spec("nomic_compact", "nomic-embed-text, compact text", vectors="nomic_compact"),
        Spec("nomic_chunks", "nomic-embed-text, best ~500-character piece", vectors="nomic_chunks"),
        Spec("bge_compact", "bge-m3, compact text", vectors="bge_compact"),
        Spec("bge_chunks", "bge-m3, best piece", vectors="bge_chunks"),
        Spec("keyword_qu", f"keyword + query understanding ({boosts})", keyword=True, **qu),
        Spec("bge_chunks_qu", "bge-m3 best piece + query understanding", vectors="bge_chunks", **qu),
        Spec("hybrid_bge_compact", "keyword + bge-m3 compact, weights 1:1", keyword=True, vectors="bge_compact"),
        Spec("hybrid_bge_chunks", "keyword + bge-m3 best piece, weights 1:1", keyword=True, vectors="bge_chunks"),
        Spec("hybrid_alt_weights", f"keyword + bge-m3 best piece, weights {alt}", keyword=True, vectors="bge_chunks",
             weights=ALT_WEIGHTS),
        Spec("keyword_rerank", f"v1 keyword, top {n} reranked", keyword=True, rerank=n),
        Spec("app_keyword", "app, keyword mode (= keyword_qu)", keyword=True, **qu),
        Spec("app_vector", f"app, vector mode ({app.replace('_', ' ')} + query understanding)", vectors=app, **qu),
        Spec("app_hybrid", f"app, hybrid mode (keyword + {app.replace('_', ' ')}, weights {w}, + query understanding)",
             keyword=True, vectors=app, weights=weights, **qu),
        Spec("app_hybrid_rerank", f"app, hybrid mode, top {n} reranked", keyword=True, vectors=app, weights=weights,
             rerank=n, **qu),
    ]


# The system the report calls "best v2": what the app does by default.
def default_system() -> str:
    mode = {"keyword": "app_keyword", "vector": "app_vector", "hybrid": "app_hybrid"}[config.SEARCH_MODE]
    return f"{mode}_rerank" if config.RERANK and mode == "app_hybrid" else mode


V1_NAMES = ("keyword", "vector", "hybrid")


def select(which: str) -> list[Spec]:
    """`which`: "all", "v1", or comma-separated system names."""
    specs = system_specs()
    if which == "all":
        return specs
    names = V1_NAMES if which == "v1" else tuple(n.strip() for n in which.split(",") if n.strip())
    by_name = {s.name: s for s in specs}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise SystemExit(f"unknown system {unknown}; choose from {list(by_name)}")
    return [by_name[n] for n in names]


class Resources:
    """The committed inputs, loaded on first use and shared by every system."""

    def __init__(self, corpus_path=CORPUS, queries_path=QUERIES, rerank_path=RERANK_SCORES):
        self._corpus_path, self._queries_path, self._rerank_path = corpus_path, queries_path, rerank_path
        self._vectors: dict[str, object] = {}
        self._retrievers: dict[str, object] = {}
        self.scorer = None  # set to a live scorer by `eval.run --live-rerank`

    @cached_property
    def docs(self) -> list[dict]:
        return load_corpus(self._corpus_path)

    @cached_property
    def doc_ids(self) -> list[str]:
        return [d["doc_id"] for d in self.docs]

    @cached_property
    def all_queries(self) -> list[dict]:
        """Every line of queries.jsonl: row i of each query-vector file belongs to query i."""
        return load_queries(self._queries_path)

    @cached_property
    def facts(self) -> dict[str, tuple[str, str, str]]:
        return {d["doc_id"]: (d["company"], d["location"], d["text"]) for d in self.docs}

    @cached_property
    def companies(self) -> CompanyIndex:
        return CompanyIndex(self.docs)

    @cached_property
    def keyword(self) -> retrievers.KeywordRetriever:
        return retrievers.KeywordRetriever(self.docs)

    @cached_property
    def compact(self) -> dict[str, str]:
        """{doc_id: the compact text}: what the reranker reads."""
        from eval.embed import compact_texts

        return dict(zip(self.doc_ids, compact_texts(self.docs)))

    @cached_property
    def stored_scores(self) -> retrievers.StoredScores:
        return retrievers.StoredScores(read_jsonl(self._rerank_path), self.all_queries)

    def query_vectors(self) -> dict[str, object]:
        """{embedding model: matrix with one row per line of queries.jsonl}."""
        return {model: load_vectors(path) for model, path in QUERY_VECTOR_FILES.items() if path.exists()}

    @cached_property
    def chunk_owners(self) -> list[int]:
        from eval.embed import chunk_owners

        return chunk_owners(self.docs)

    def vector(self, name: str):
        if name not in self._retrievers:
            first, further, _model = VECTOR_SETS[name]
            if further is not None:
                self._retrievers[name] = retrievers.ChunkRetriever(
                    self.doc_ids, load_vectors(first), load_vectors(further), self.chunk_owners)
            else:
                self._retrievers[name] = retrievers.VectorRetriever(self.doc_ids, load_vectors(first))
        return self._retrievers[name]

    def build(self, spec: Spec, vector=None, scorer=None):
        """The system for `spec`. `vector` / `scorer` override the committed vectors / stored scores (tuning)."""
        if spec.v1:
            if spec.keyword and spec.needs_vectors:
                return retrievers.HybridRetriever(self.keyword, _V1Vector(self.vector(spec.vectors)))
            return self.keyword if spec.keyword else _V1Vector(self.vector(spec.vectors))
        first = retrievers.V2Retriever(
            spec.first_stage.name,
            self.facts,
            self.companies,
            keyword=self.keyword if spec.keyword else None,
            vector=(vector or self.vector(spec.vectors)) if spec.needs_vectors else None,
            model=VECTOR_SETS[spec.vectors][2] if spec.needs_vectors else None,
            weights=spec.weights,
            w_location=spec.w_location,
            w_company=spec.w_company,
            strip=spec.strip,
        )
        if not spec.rerank:
            return first
        return retrievers.RerankRetriever(spec.name, first, scorer or self.scorer or self.stored_scores, spec.rerank)

    def build_all(self, specs: list[Spec]) -> dict:
        return {s.name: self.build(s) for s in specs}


class _V1Vector:
    """The v1 vector system, given either its own query vector or {model: vector}."""

    name = "vector"

    def __init__(self, inner: retrievers.VectorRetriever):
        self.inner = inner

    def search(self, query_text: str, query_vecs, k: int = 10) -> list[str]:
        return self.inner.search(query_text, retrievers.pick_vector(query_vecs, EMBED_MODEL), k)


def query_vector_rows(queries: list[dict], all_queries: list[dict], matrices: dict) -> list[dict]:
    """For each of `queries`: {embedding model: its vector}, found by qid in the full query file.

    Lets `eval.run --queries` score a subset (the 42 v1 queries) with the committed vectors.
    """
    row = {q["qid"]: i for i, q in enumerate(all_queries)}
    text = {q["qid"]: q["query"] for q in all_queries}
    out = []
    for q in queries:
        if q["qid"] not in row or text[q["qid"]] != q["query"]:
            raise SystemExit(f"{q['qid']}: not in eval/data/queries.jsonl with the same text, so it has no query vector")
        out.append({model: matrix[row[q["qid"]]] for model, matrix in matrices.items()})
    return out
