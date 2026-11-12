"""Keyword and hybrid search in the app (core/search.py) and how recommend() uses them.

All offline: FakeProvider stands in for Ollama ("bag of words" vectors), and the FTS5
index lives in memory.
"""

from datetime import date

import pytest

from cityu_jobs import config
from cityu_jobs.core import pipeline, search
from cityu_jobs.core.ai.providers import FakeProvider
from cityu_jobs.core.embeddings import embed_jobs, embed_query, similarities
from cityu_jobs.core.ranking import Preferences, rank
from cityu_jobs.core.search import KeywordIndex, hybrid_relevance, keyword_index, keyword_relevance, rrf

from conftest import make_job

TODAY = date(2026, 10, 25)


@pytest.fixture(autouse=True)
def _fresh_index(monkeypatch):
    """make_job() hashes only `raw`, so two tests' "aims:A" could share a hash: never reuse an index across tests."""
    monkeypatch.setattr(search, "_last_index", None)


def _jobs():
    return [
        make_job("aims:A", title="Kotlin Android intern", description="Build mobile apps."),
        make_job("aims:B", title="Backend Go engineer", description="APIs and databases."),
        make_job("aims:C", title="Data analyst", description="Dashboards; some Kotlin scripts."),
        make_job("aims:D", title="Office helper", description="Filing and front desk."),
        make_job("intern:zh", title="机器学习实习生", company="Delta", location="香港",
                 description="机器学习 项目", source="intern_tech", department_code="TECH"),
    ]


def _fill(cache, jobs):
    for job in jobs:
        cache.upsert_job(job)


def _recommend(cache, query, mode, provider=None):
    return pipeline.recommend(
        Preferences("CS", query, 3, 3, search_mode=mode), provider or FakeProvider(), cache=cache, today=TODAY
    )


# --- core/search.py ------------------------------------------------------------------


def test_default_mode_comes_from_config():
    assert config.SEARCH_MODE == "hybrid"  # chosen on the evaluation's dev queries (eval/results/report.md)
    assert Preferences("CS", "q").search_mode == config.SEARCH_MODE


def test_keyword_relevance_orders_by_bm25_and_zeroes_the_rest():
    jobs = _jobs()
    scores = keyword_relevance(KeywordIndex.from_jobs(jobs), "kotlin", [j.job_id for j in jobs])
    # Title hit (A) beats body hit (C); scores are 1/(60 + rank); every other job gets 0.
    assert scores == {"aims:A": 1 / 61, "aims:C": 1 / 62, "aims:B": 0.0, "aims:D": 0.0, "intern:zh": 0.0}


def test_keyword_relevance_without_any_match_is_empty():
    jobs = _jobs()
    index = KeywordIndex.from_jobs(jobs)
    assert keyword_relevance(index, "quantum", [j.job_id for j in jobs]) == {}
    assert keyword_relevance(index, "!!! ---", [j.job_id for j in jobs]) == {}


def test_hybrid_fusion_order_by_hand():
    index = KeywordIndex.from_jobs(_jobs()[:4])
    sims = {"aims:B": 0.9, "aims:D": 0.8, "aims:A": 0.7, "aims:C": 0.1}
    # keyword: A, C. vector: B, D, A, C.
    # A = 1/61 + 1/63, C = 1/62 + 1/64, B = 1/61, D = 1/62: found by both beats found by one.
    scores = hybrid_relevance(index, "kotlin", sims)
    assert scores == pytest.approx({"aims:A": 1 / 61 + 1 / 63, "aims:B": 1 / 61, "aims:C": 1 / 62 + 1 / 64, "aims:D": 1 / 62})
    assert sorted(scores, key=lambda d: -scores[d]) == ["aims:A", "aims:C", "aims:B", "aims:D"]
    assert rrf([["aims:A", "aims:C"], ["aims:B", "aims:D", "aims:A", "aims:C"]]) == ["aims:A", "aims:C", "aims:B", "aims:D"]


def test_hybrid_jobs_outside_both_top_lists_get_zero():
    index = KeywordIndex.from_jobs(_jobs()[:4])
    sims = {"aims:B": 0.9, "aims:D": 0.8, "aims:A": 0.7, "aims:C": 0.1}
    scores = hybrid_relevance(index, "kotlin", sims, depth=1)  # keyword top 1: A; vector top 1: B
    assert scores == {"aims:A": 1 / 61, "aims:B": 1 / 61, "aims:C": 0.0, "aims:D": 0.0}


def test_rrf_depth_cuts_each_list():
    assert rrf([["a", "b", "c"]], depth=2) == ["a", "b"]
    assert rrf([["a", "b", "c"]], depth=None) == ["a", "b", "c"]


def test_index_text_is_the_full_listing_text():
    from cityu_jobs.core.models import full_text

    job = _jobs()[0]
    assert search.job_document(job) == {
        "doc_id": "aims:A", "title": "Kotlin Android intern", "company": "", "location": "", "text": full_text(job),
    }


def test_keyword_index_is_reused_until_content_changes():
    jobs = _jobs()
    first = keyword_index(jobs)
    assert keyword_index(list(reversed(jobs))) is first  # same jobs, same content
    edited = make_job("aims:A", title="Kotlin Android intern", raw={"id": "aims:A", "v": 2})
    second = keyword_index([edited, *jobs[1:]])
    assert second is not first
    assert keyword_index(jobs[:2]) is not second  # a job closed or disappeared


# --- recommend() -----------------------------------------------------------------------


def test_vector_mode_is_exactly_the_old_ranking(cache):
    """Regression: "vector" mode ranks by plain cosine, as before keyword search existed
    (for a query that names no place or company, so there is nothing to boost)."""
    jobs = _jobs()
    _fill(cache, jobs)
    provider = FakeProvider()
    query = "android kotlin apps"
    default = pipeline.recommend(Preferences("CS", query, 2, 4, search_mode="vector"), provider, cache=cache, today=TODAY)
    explicit = _recommend(cache, query, "vector", provider)

    # The old recommend(): cosine of the query to every job, straight into rank().
    prefs = Preferences("CS", query, 2, 4)
    levels = {r.job.job_id: r.level for r in default}
    sims = similarities(embed_query(query, provider), embed_jobs(jobs, cache, provider))
    old = rank(jobs, levels, sims, prefs, TODAY)

    def key(ranked):
        return [(r.job.job_id, r.score, r.breakdown, r.similarity) for r in ranked]

    assert key(default) == key(old)
    assert [r.job.job_id for r in explicit] == [r.job.job_id for r in old]
    assert all(r.search_mode == "vector" for r in default)


def test_keyword_mode_ranks_word_matches_first_without_embeddings(cache):
    _fill(cache, _jobs())
    provider = FakeProvider()
    ranked = _recommend(cache, "kotlin", "keyword", provider)
    assert [r.job.job_id for r in ranked[:2]] == ["aims:A", "aims:C"]
    assert ranked[0].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)  # best match, scaled to 1
    others = {r.job.job_id: r for r in ranked[2:]}
    assert all(r.breakdown["similarity"] == 0 and r.similarity == 0.0 for r in others.values())
    assert ranked[0].similarity == 1 / 61 and ranked[0].search_mode == "keyword"
    assert provider.embed_calls == 0  # keyword search needs no Ollama


def test_hybrid_mode_combines_both(cache):
    jobs = _jobs()
    _fill(cache, jobs)
    provider = FakeProvider()
    ranked = _recommend(cache, "kotlin mobile", "hybrid", provider)
    assert ranked[0].job.job_id == "aims:A"  # first in both lists
    assert all(r.search_mode == "hybrid" for r in ranked)
    expected = hybrid_relevance(
        KeywordIndex.from_jobs(jobs), "kotlin mobile",
        similarities(embed_query("kotlin mobile", provider), embed_jobs(jobs, cache, provider)),
    )
    assert {r.job.job_id: r.similarity for r in ranked} == expected


@pytest.mark.parametrize("mode", config.SEARCH_MODES)
def test_empty_query_has_no_similarity_term(cache, mode):
    _fill(cache, _jobs())
    ranked = _recommend(cache, "   ", mode)
    assert all(r.breakdown["similarity"] == 0 and r.similarity is None and r.search_mode is None for r in ranked)


@pytest.mark.parametrize("mode", ["keyword", "hybrid"])
def test_cjk_query(cache, mode):
    _fill(cache, _jobs())
    ranked = _recommend(cache, "机器学习", mode)
    assert ranked[0].job.job_id == "intern:zh"


@pytest.mark.parametrize("mode", ["keyword", "hybrid"])
@pytest.mark.parametrize(
    "query", ['say "hi"', "NOT AND OR NEAR", "c++ (dev) -x *y col:umn ^z", 'a"b', "'; DROP TABLE docs; --", "!!!"]
)
def test_fts_special_characters_never_crash(cache, mode, query):
    _fill(cache, _jobs())
    ranked = _recommend(cache, query, mode)
    assert len(ranked) == len(_jobs())


def test_keyword_query_matching_nothing_leaves_similarity_out(cache):
    _fill(cache, _jobs())
    ranked = _recommend(cache, "quantum", "keyword")
    assert all(r.breakdown["similarity"] == 0 and r.search_mode is None for r in ranked)


def test_closed_jobs_are_not_indexed(cache):
    closed = make_job("aims:OLD", title="Kotlin lead", closing_date=date(2026, 1, 1))
    _fill(cache, [*_jobs(), closed])
    ranked = _recommend(cache, "kotlin", "keyword")
    assert "aims:OLD" not in {r.job.job_id for r in ranked}
    assert ranked[0].job.job_id == "aims:A"


def test_unknown_mode_is_refused(cache):
    with pytest.raises(ValueError):
        _recommend(cache, "q", "telepathy")
