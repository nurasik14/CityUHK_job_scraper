"""Query matching: keyword search (SQLite FTS5 + BM25), fusion with vector search, boosts.

The app can rank by three kinds of "relevance to the query" (config.SEARCH_MODE):

- vector: cosine similarity of embeddings (core/embeddings.py). Finds jobs that *mean*
  the same thing even with no words in common.
- keyword: BM25 over a full-text index of the listings. Finds jobs that contain the
  words you typed: company names, places, exact tools ("Kotlin", "FPGA").
- hybrid: weighted Reciprocal Rank Fusion of the two rankings.

On top of any of them, a job in a place or from a company the query names gets a boost
(core/query.py). `relevance()` at the bottom puts it together for the pipeline.

This module is also what `eval/retrievers.py` measures, so the numbers in
eval/results/report.md are about this exact code. The keyword index, tokenizer, column
weights and RRF constant were fixed before any relevance label existed. The version 2
settings (fusion weights, boost weights; config.py) were tuned on the evaluation's dev
queries only. Ties are broken by id everywhere, so every ranking is deterministic.
Standard library only (sqlite3 ships with Python).
"""

import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping

from cityu_jobs import config
from cityu_jobs.core.models import Job, full_text
from cityu_jobs.core.query import CompanyIndex, ParsedQuery, boost, parse_query

# --- keyword search -----------------------------------------------------------------

# FTS5 tokenizer: Unicode word splitting with diacritics folded ("café" = "cafe"), then the
# Porter stemmer, so "developer"/"development"/"develop" and "internships"/"internship"
# match. Listings and queries are mostly English, where these inflections are common.
TOKENIZER = "porter unicode61 remove_diacritics 2"

# BM25 weight per column. The title says what the job is; company and location are short
# fields that a query word either names or not, so a hit there is strong evidence; the
# long body text (which also repeats title and company) gets the base weight.
COLUMNS = ("title", "company", "location", "text")
COLUMN_WEIGHTS = {"title": 3.0, "company": 2.0, "location": 2.0, "text": 1.0}

# Chinese, Japanese kana and Korean: unicode61 would treat a whole run of these characters
# (no spaces between words) as one token, so "机器学习实习" could only match itself. We
# rewrite every such run into overlapping character bigrams, in both documents and queries:
# "机器学习" -> "机器 器学 学习". A one-character run stays as it is.
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+")


def cjk_bigrams(text: str) -> str:
    def split(match: re.Match) -> str:
        run = match.group(0)
        grams = [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]
        return " " + " ".join(grams) + " "

    return _CJK_RUN.sub(split, text)


def fts_query(text: str) -> str:
    """Turn free user text into a safe FTS5 query: every word quoted, OR-joined.

    Quoting makes FTS5 read operators (AND, OR, NOT, NEAR), "-", "*", ":", "(" and quotes in
    user text as plain words. Only word characters survive, so punctuation never reaches the
    parser; an embedded double quote would be doubled, which is FTS5's escape. Returns "" when
    nothing searchable is left (the caller then returns no results).
    """
    seen, terms = set(), []
    for token in re.findall(r"\w+", cjk_bigrams(text)):
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            terms.append('"' + token.replace('"', '""') + '"')
    return " OR ".join(terms)


def job_document(job: Job) -> dict:
    """The fields of a job that the keyword index sees. `text` is the whole listing (models.full_text)."""
    return {
        "doc_id": job.job_id,
        "title": job.title,
        "company": job.company or "",
        "location": job.location or "",
        "text": full_text(job),
    }


class KeywordIndex:
    """An in-memory FTS5 table over `docs` (dicts with "doc_id" and the COLUMNS)."""

    def __init__(self, docs: Iterable[dict]):
        # The web app builds an index in one search thread and may reuse it in the next one,
        # so allow other threads; the lock keeps them from using the connection at once.
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        cols = ", ".join(COLUMNS)
        self.db.execute(f"CREATE VIRTUAL TABLE docs USING fts5(doc_id UNINDEXED, {cols}, tokenize='{TOKENIZER}')")
        self.db.executemany(
            f"INSERT INTO docs (doc_id, {cols}) VALUES (?, {', '.join('?' for _ in COLUMNS)})",
            ([d["doc_id"], *(cjk_bigrams(d.get(c) or "") for c in COLUMNS)] for d in docs),
        )
        self.db.commit()
        weights = ", ".join(str(COLUMN_WEIGHTS[c]) for c in COLUMNS)
        # bm25() is "lower is better" in SQLite. doc_id (UNINDEXED) takes the first weight slot.
        self._sql = f"SELECT doc_id FROM docs WHERE docs MATCH ? ORDER BY bm25(docs, 0.0, {weights}), doc_id LIMIT ?"

    @classmethod
    def from_jobs(cls, jobs: Iterable[Job]) -> "KeywordIndex":
        return cls(job_document(j) for j in jobs)

    def search(self, query: str, limit: int | None = None) -> list[str]:
        """Ids of the docs that contain at least one query word, best BM25 first.

        `limit=None` returns every match.
        """
        match = fts_query(query)
        if not match:
            return []
        with self._lock:
            return [row[0] for row in self.db.execute(self._sql, (match, -1 if limit is None else limit))]


# --- one index per set of jobs --------------------------------------------------------

# CityU's own schemes (AIMS, ITC STEM) have no location field: the work is on campus.
_CAMPUS_SOURCES = ("aims_", "itc_")
CAMPUS_LOCATION = "Hong Kong"


def job_location(job: Job) -> str:
    """Where a job is, for the place boost: its location, or Hong Kong for CityU's own schemes."""
    return job.location or (CAMPUS_LOCATION if job.source.startswith(_CAMPUS_SOURCES) else "")


class SearchIndex:
    """Everything query matching needs to know about a set of jobs, built once and reused.

    - `keyword`: the FTS5 index (built on first use: vector mode never needs it).
    - `companies`: the company names a query may mention (core/query.py).
    - `facts`: {job_id: (company, location, full text)} for the place / company boost.
    - `embedding_texts`: {job_id: the compact text(s) that get embedded} (core/embeddings.py),
      also what the optional reranker reads (the first one). Built on first use.
    """

    def __init__(self, jobs: Iterable[Job]):
        self._jobs = list(jobs)
        self.facts = {j.job_id: (j.company or "", job_location(j), full_text(j)) for j in self._jobs}
        self.companies = CompanyIndex({"company": c, "text": t} for c, _, t in self.facts.values())
        self._keyword: KeywordIndex | None = None
        self._texts: dict[str, list[str]] | None = None
        self._lock = threading.Lock()

    @property
    def keyword(self) -> KeywordIndex:
        with self._lock:
            if self._keyword is None:
                self._keyword = KeywordIndex.from_jobs(self._jobs)
            return self._keyword

    @property
    def embedding_texts(self) -> dict[str, list[str]]:
        from cityu_jobs.core.embeddings import embedding_texts  # here: embeddings.py imports the cache, not needed above

        with self._lock:
            if self._texts is None:
                self._texts = embedding_texts(self._jobs)
            return self._texts


# Building the keyword index for ~5,700 listings takes about 0.5 s (a search then takes
# ~30 ms), so the last index is kept and reused while the same jobs, with the same content,
# are ranked.
_last_index: tuple[tuple, SearchIndex] | None = None
_last_index_lock = threading.Lock()


def search_index(jobs: Iterable[Job]) -> SearchIndex:
    """A SearchIndex over `jobs`, rebuilt only when a job or its content hash changes."""
    global _last_index
    jobs = list(jobs)
    key = tuple(sorted((j.job_id, j.content_hash) for j in jobs))
    with _last_index_lock:
        if _last_index is None or _last_index[0] != key:
            _last_index = (key, SearchIndex(jobs))
        return _last_index[1]


def keyword_index(jobs: Iterable[Job]) -> KeywordIndex:
    """The KeywordIndex over `jobs` (kept with the rest of their SearchIndex)."""
    return search_index(jobs).keyword


# --- fusion -------------------------------------------------------------------------

RRF_K = 60  # the constant from Cormack et al. (2009); the usual default, not tuned here
FUSION_DEPTH = 100  # version 1 hybrid: each system contributes its top 100


def weighted_rrf_scores(
    rankings: list[list[str]],
    weights: list[float] | tuple[float, ...] | None = None,
    k: int = RRF_K,
    depth: int | None = FUSION_DEPTH,
) -> dict[str, float]:
    """score(d) = sum over rankings of weight / (k + rank of d), rank from 1, top `depth` of each.

    `weights=None` gives every ranking weight 1: plain Reciprocal Rank Fusion. A weight says
    how much one search is trusted: with (1.0, 0.5) a first place in the second ranking is
    worth half a first place in the first.
    """
    scores: dict[str, float] = {}
    for i, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else float(weights[i])
        if weight == 0:
            continue
        for rank, doc_id in enumerate(ranking[:depth], start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return scores


def rrf_scores(rankings: list[list[str]], k: int = RRF_K, depth: int | None = FUSION_DEPTH) -> dict[str, float]:
    """Plain (unweighted) Reciprocal Rank Fusion scores."""
    return weighted_rrf_scores(rankings, None, k, depth)


def by_score(scores: dict[str, float]) -> list[str]:
    """Ids sorted by score (high first), ties by id."""
    return sorted(scores, key=lambda d: (-scores[d], d))


def rrf(rankings: list[list[str]], k: int = RRF_K, depth: int | None = FUSION_DEPTH) -> list[str]:
    """Reciprocal Rank Fusion: ids sorted by `rrf_scores` (high first), ties by id.

    Uses only ranks, so BM25 scores and cosine similarities never need to be put on one scale.
    """
    return by_score(rrf_scores(rankings, k, depth))


def vector_ranking(sims: dict[str, float], depth: int | None = None) -> list[str]:
    """Job ids by cosine similarity, best first, ties by id."""
    return sorted(sims, key=lambda d: (-sims[d], d))[:depth]


# --- version 2: weighted fusion + the place / company boost -----------------------------


def fuse_and_boost(
    rankings: list[list[str]],
    weights: list[float] | tuple[float, ...] | None,
    parsed: ParsedQuery | None,
    facts: Mapping[str, tuple[str, str, str]],
    w_location: float,
    w_company: float,
    k: int = RRF_K,
) -> dict[str, float]:
    """{id: relevance} from one or more full rankings plus the query-understanding boost.

        relevance(d) = sum_i weight_i / (k + rank_i(d))  +  boost(d) / (k + 1)
        boost(d)     = w_location * (in a named place + remote, if asked) + w_company * (a named company)

    So a boost of 1 is worth exactly a first place in one more search, and the same formula
    serves keyword (one ranking), vector (one ranking) and hybrid (two). Rankings are used
    in full, not cut at 100: a listing in the right city that the search ranked 300th should
    still come before one ranked 400th. `facts` is {id: (company, location, text)} for every
    listing; one that no ranking contains can still get a boost (never a filter, see query.py).
    The evaluation's v2 systems and the app both go through this function.
    """
    scores = weighted_rrf_scores(rankings, weights, k, depth=None)
    if parsed is not None and parsed.has_boost and (w_location or w_company):
        for doc_id, (company, location, text) in facts.items():
            bonus = boost(parsed, company, location, text, w_location, w_company)
            if bonus:
                scores[doc_id] = scores.get(doc_id, 0.0) + bonus / (k + 1)
    return scores


def retrieval_query(parsed: ParsedQuery, strip: bool) -> str:
    """The text the searches see: the query as typed, or (strip) without the place / company words."""
    return parsed.rest if strip and parsed.rest.strip() else parsed.text


# --- relevance for ranking.rank() -----------------------------------------------------
# rank() min-max scales whatever it gets, so each mode only has to say "higher = better".


def keyword_relevance(index: KeywordIndex, query: str, job_ids: Iterable[str], k: int = RRF_K) -> dict[str, float]:
    """{job_id: 1 / (k + BM25 rank)} for matching jobs, 0 for the rest (no boost).

    The rank, not the raw BM25 score, so the value means the same thing for every query.
    Returns {} when no job contains a query word: then the query can't tell jobs apart and
    the similarity term is left out, as for an empty query.
    """
    hits = index.search(query)
    if not hits:
        return {}
    scores = dict.fromkeys(job_ids, 0.0)
    scores.update(rrf_scores([hits], k, depth=None))
    return scores


def hybrid_relevance(
    index: KeywordIndex,
    query: str,
    sims: dict[str, float],
    k: int = RRF_K,
    depth: int | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, float]:
    """(Weighted) RRF of the keyword and vector rankings, no boost; jobs in neither get 0.

    `sims` holds the cosine of every job to be ranked (its keys are the job ids).
    `weights` = (keyword, vector); None = equal.
    """
    if not sims:
        return {}
    scores = dict.fromkeys(sims, 0.0)
    scores.update(weighted_rrf_scores([index.search(query, depth), vector_ranking(sims, depth)], weights, k, depth))
    return scores


def relevance(
    mode: str,
    query: str,
    index: SearchIndex,
    sims: dict[str, float] | None = None,
    *,
    w_keyword: float | None = None,
    w_vector: float | None = None,
    w_location: float | None = None,
    w_company: float | None = None,
    strip: bool | None = None,
) -> dict[str, float]:
    """{job_id: how well the job matches `query`, higher = better} for one search mode.

    - keyword: 1 / (60 + BM25 rank); 0 for jobs without any query word.
    - vector: the cosine similarities in `sims`, untouched, when the query names no place or
      company; otherwise 1 / (60 + cosine rank), so the boost below can be added.
    - hybrid: weighted Reciprocal Rank Fusion of the two (config.HYBRID_WEIGHTS).
    In every mode a job in a place, or from a company, that the query names gets the boost
    of `fuse_and_boost` (config.BOOST_LOCATION, config.BOOST_COMPANY).
    Returns {} when nothing matches at all (the similarity term is then left out).
    The keyword arguments default to the values in config.py; the evaluation passes its own.
    """
    w_keyword = config.HYBRID_WEIGHTS[0] if w_keyword is None else w_keyword
    w_vector = config.HYBRID_WEIGHTS[1] if w_vector is None else w_vector
    w_location = config.BOOST_LOCATION if w_location is None else w_location
    w_company = config.BOOST_COMPANY if w_company is None else w_company
    strip = config.QUERY_STRIP_NAMES if strip is None else strip
    parsed = parse_query(query, index.companies)
    boosting = parsed.has_boost and bool(w_location or w_company)
    rankings, weights = [], []
    if mode in ("keyword", "hybrid"):
        rankings.append(index.keyword.search(retrieval_query(parsed, strip)))
        weights.append(w_keyword if mode == "hybrid" else 1.0)
    if mode in ("vector", "hybrid"):
        if not sims:
            return {}
        if mode == "vector" and not boosting:
            return dict(sims)
        rankings.append(vector_ranking(sims))
        weights.append(w_vector if mode == "hybrid" else 1.0)
    scores = fuse_and_boost(rankings, weights, parsed, index.facts, w_location, w_company)
    if not scores:
        return {}
    out = dict.fromkeys(index.facts, 0.0)
    out.update(scores)
    return out
