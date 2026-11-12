"""Retrieval v2 in the app: compact text, chunks, query understanding, weighted fusion,
the embedding cache key and the optional reranker. All offline (FakeProvider, no torch)."""

import builtins
from datetime import date

import pytest

from cityu_jobs import config
from cityu_jobs.core import pipeline, query, rerank, search
from cityu_jobs.core import text as text_rules
from cityu_jobs.core.ai.providers import FakeProvider
from cityu_jobs.core.embeddings import embed_jobs, embedding_texts, similarities, text_hash
from cityu_jobs.core.models import embedding_text, full_text, listing_fields
from cityu_jobs.core.places import find_places, in_place
from cityu_jobs.core.query import CompanyIndex, parse_query
from cityu_jobs.core.ranking import Preferences
from cityu_jobs.core.search import SearchIndex, fuse_and_boost, relevance, weighted_rrf_scores

from conftest import make_job

TODAY = date(2026, 10, 25)


@pytest.fixture(autouse=True)
def _fresh_index(monkeypatch):
    monkeypatch.setattr(search, "_last_index", None)


def _listing(**overrides) -> dict:
    values = dict(title="ML Intern", company="Acme", location="Singapore", job_type="Internship",
                  department="Tech industry", description="Train models.", requirements="")
    values.update(overrides)
    return values


# --- boilerplate stripping --------------------------------------------------------------


def test_notices_and_boilerplate_sections_are_removed():
    description = "\n".join([
        "About Acme",
        "Acme is the world's leading maker of anvils.",
        "You will train ranking models in PyTorch.",
        "Acme is an equal opportunity employer and does not discriminate.",
        "https://acme.example/privacy",
        "#LI-Hybrid",
        "Responsibilities",
        "- Ship features every week",
        "Benefits",
        "- Free lunch",
        "- Gym",
        "Qualifications",
        "- Python",
    ])
    body = text_rules.clean_body(_listing(description=description))
    assert body.split("\n") == [
        "You will train ranking models in PyTorch.", "Responsibilities", "- Ship features every week", "Qualifications", "- Python",
    ]


def test_stripping_is_conservative():
    # "About the team" is about the job, not the company; a company intro only loses its first paragraph.
    description = "About the team\nWe build search.\nAbout us\nWe are Acme.\nThe role: build search ranking."
    assert text_rules.clean_body(_listing(description=description)).split("\n") == [
        "About the team", "We build search.", "The role: build search ranking."]
    # A very long paragraph is kept even if it mentions an equal-opportunity statement somewhere.
    long = "You will build compilers. " * 30 + "We are an equal opportunity employer."
    assert len(long) > text_rules.NOTICE_MAX_CHARS
    assert text_rules.clean_body(_listing(description=long)) == long
    # If the rules would remove everything, nothing is removed.
    only_notice = "We are an equal opportunity employer."
    assert text_rules.clean_body(_listing(description=only_notice)) == only_notice
    assert text_rules.clean_body(_listing(description=only_notice), strip=False) == only_notice


def test_paragraphs_a_company_repeats_under_several_titles_are_boilerplate():
    intro = "At Acme, our tech is not just a tool, it is your playground, and you will learn by building at scale."
    own = "This team trains the ranking models behind Acme search, used by millions of people every day in Asia."
    assert len(intro) >= text_rules.REPEAT_MIN_CHARS and len(own) >= text_rules.REPEAT_MIN_CHARS
    listings = [_listing(title=f"Role {i}", description=f"{intro}\nWork on project {i}.") for i in range(3)]
    listings += [_listing(title="Search Intern", location=city, description=f"{intro}\n{own}") for city in ("Singapore", "Tokyo", "Seoul")]
    listings.append(_listing(company="Other", title="Role X", description=intro))
    boilerplate = text_rules.repeated_paragraphs(listings)
    # The intro is under 4 Acme titles; the team paragraph under one title in three cities (not boilerplate).
    assert text_rules.clean_body(listings[3], boilerplate) == own
    assert text_rules.clean_body(listings[0], boilerplate) == "Work on project 0."
    assert text_rules.clean_body(listings[-1], boilerplate) == intro  # another company: its own count is 1
    assert text_rules.clean_body(listings[3]) == f"{intro}\n{own}"   # without the corpus-level rule
    # Short repeated lines (headings, "- Python") are never treated as boilerplate.
    short = [_listing(title=f"R{i}", description="Requirements:\n- Python") for i in range(5)]
    assert text_rules.repeated_paragraphs(short) == frozenset()


# --- compact text and chunks ------------------------------------------------------------


def test_compact_text_has_the_header_then_a_bounded_excerpt():
    words = " ".join(f"word{i}" for i in range(400))
    listing = _listing(description=words)
    compact = text_rules.compact_text(listing)
    head, body = compact.split("\n")[:5], compact.split("\n", 5)[5]
    assert head == ["ML Intern", "Acme", "Singapore", "Internship", "Tech industry"]
    assert len(body) <= text_rules.EXCERPT_CHARS and words.startswith(body)
    assert words[len(body)] == " "  # cut at a word boundary
    assert text_rules.compact_text(listing, max_chars=100) != compact
    # Empty fields are skipped; the location is in the text (it was not in v1).
    assert text_rules.compact_text(_listing(company="", job_type="", department="", description="x")) == "ML Intern\nSingapore\nx"


def test_embedding_text_of_a_job_is_the_compact_text():
    job = make_job("intern:x", title="ML Intern", company="Acme", location="Hong Kong", job_nature="Internship",
                   department_name="Tech industry", description="word " * 400, source="intern_tech")
    assert embedding_text(job) == text_rules.compact_text(listing_fields(job))
    assert len(embedding_text(job)) < 600 < len(full_text(job))
    assert "Hong Kong" in embedding_text(job) and "Hong Kong" not in full_text(job)


def test_chunks_cover_the_description_with_the_header_on_each():
    sentences = [f"Sentence number {i} talks about topic {i}." for i in range(60)]
    listing = _listing(description=" ".join(sentences))
    chunks = text_rules.chunk_texts(listing)
    head = text_rules.header(listing)
    assert len(chunks) > 3 and all(c.startswith(head + "\n") for c in chunks)
    assert chunks[0] == text_rules.compact_text(listing)  # the first piece is the compact text
    bodies = [c[len(head) + 1:] for c in chunks]
    assert all(len(b) <= text_rules.CHUNK_CHARS for b in bodies)
    assert all(s in " ".join(bodies) for s in sentences[:-1])       # nothing in the middle is lost
    assert bodies[1][:20] in bodies[0] or bodies[0][-20:] in bodies[1]  # neighbours overlap
    assert text_rules.chunk_texts(_listing()) == [text_rules.compact_text(_listing())]  # short: one piece


def test_a_job_scores_as_its_best_piece():
    assert text_rules.max_similarity([0.1, 0.7, 0.3]) == 0.7
    sims = similarities([1.0, 0.0], {"a": [[0.0, 1.0], [1.0, 0.0]], "b": [[0.6, 0.8]], "c": [0.0, 1.0], "d": []})
    assert sims == pytest.approx({"a": 1.0, "b": 0.6, "c": 0.0, "d": 0.0})


# --- the embedding cache ----------------------------------------------------------------


def _long_job(job_id="intern:long", **overrides):
    values = dict(title="ML Intern", company="Acme", location="Singapore", source="intern_tech",
                  description=" ".join(f"Sentence {i} about topic {i}." for i in range(80)))
    values.update(overrides)
    return make_job(job_id, **values)


def test_chunked_jobs_get_several_vectors_and_are_cached(cache, monkeypatch):
    monkeypatch.setattr(config, "EMBED_CHUNKS", True)
    jobs = [_long_job(), make_job("aims:short")]
    for job in jobs:
        cache.upsert_job(job)
    provider = FakeProvider()
    vectors = embed_jobs(jobs, cache, provider)
    assert len(vectors["intern:long"]) == len(embedding_texts(jobs)["intern:long"]) > 1
    assert len(vectors["aims:short"]) == 1
    calls = provider.embed_calls
    assert embed_jobs(jobs, cache, provider) == vectors and provider.embed_calls == calls  # all cached


def test_stale_vectors_are_re_embedded_automatically(cache, monkeypatch):
    """Same listing, same model: vectors made from another text (v1 full text, other format) are redone."""
    monkeypatch.setattr(config, "EMBED_CHUNKS", False)
    job = _long_job()
    cache.upsert_job(job)
    provider = FakeProvider()
    # A vector cached before retrieval v2: one vector of the full text, no text hash.
    cache.set_embedding(job.job_id, provider.embed([full_text(job)])[0], provider.embed_model, job.content_hash)
    before = provider.embed_calls
    vectors = embed_jobs([job], cache, provider)
    assert provider.embed_calls == before + 1 and len(vectors[job.job_id]) == 1
    assert vectors[job.job_id][0] == provider.embed([embedding_text(job)])[0]  # made from the compact text
    calls = provider.embed_calls
    embed_jobs([job], cache, provider)
    assert provider.embed_calls == calls  # now cached

    monkeypatch.setattr(config, "EMBED_CHUNKS", True)  # another text shape
    assert len(embed_jobs([job], cache, provider)[job.job_id]) > 1 and provider.embed_calls > calls
    calls = provider.embed_calls
    monkeypatch.setattr(text_rules, "EMBED_TEXT_VERSION", text_rules.EMBED_TEXT_VERSION + 1)  # a new text format
    embed_jobs([job], cache, provider)
    assert provider.embed_calls > calls


def test_cache_embeddings_match_on_content_model_and_text(cache):
    job = make_job()
    cache.upsert_job(job)
    cache.set_embeddings(job.job_id, [[1.0], [2.0]], "m", job.content_hash, "t1")
    assert cache.get_embeddings(job.job_id, job.content_hash, "m", "t1") == [[1.0], [2.0]]
    assert cache.get_embeddings(job.job_id, job.content_hash, "m", "t2") is None
    assert cache.get_embeddings(job.job_id, job.content_hash, "other", "t1") is None
    assert cache.get_embeddings(job.job_id, "other-hash", "m", "t1") is None
    assert cache.get_embedding(job.job_id, job.content_hash, "m") is None  # the old single-vector reader
    assert text_hash(["a", "b"]) != text_hash(["ab"]) and text_hash(["a"]) == text_hash(["a"])


# --- query understanding ----------------------------------------------------------------


def _places(text):
    return [key for key, _, _ in find_places(text)]


def test_places_in_queries_with_aliases_and_chinese():
    assert _places("quant intern HK") == ["hong kong"] == _places("香港 實習") == _places("hong kong internship")
    assert _places("data science intern new york") == ["new york city"] == _places("NYC swe")
    assert _places("SF or bay area") == ["san francisco", "bay area"]
    assert _places("新加坡 实习") == ["singapore"] and _places("深圳") == ["shenzhen"]
    assert _places("北京 上海") == ["beijing", "shanghai"]
    assert _places("internship in tokyo or seoul") == ["tokyo", "seoul"]
    assert _places("ml intern in europe or asia") == ["europe", "asia"]
    assert _places("jobs in the US") == ["united states"] == _places("USA internship")
    assert _places("help us build websites") == []  # "us" the pronoun
    assert _places("uk fintech") == ["united kingdom"] and _places("london fintech") == ["london"]
    assert _places("north america") == ["north america"]  # not also "america"
    assert _places("react frontend intern") == []


def test_listing_locations_match_places_and_regions():
    yes = [("singapore", "SGP"), ("asia", "Singapore"), ("asia", "Hong Kong SAR"), ("europe", "Berlin; Remote"),
           ("united kingdom", "London, UK"), ("united states", "Austin, TX"), ("united states", "Remote in USA"),
           ("canada", "Toronto, ON, Canada"), ("canada", "Toronto, Ontario, CAN"), ("new york city", "NYC"),
           ("bay area", "Palo Alto, CA"), ("san francisco", "SF"), ("los angeles", "LA"), ("china", "China, Shanghai"),
           ("hong kong", "Hong Kong")]
    no = [("asia", "London, UK"), ("indiana", "Remote in USA"), ("california", "Toronto, ON, Canada"),
          ("louisiana", "LA"), ("new york city", "Rochester, NY"), ("london", "United Kingdom"),
          ("china", "Hong Kong"), ("san jose", "Costa Rica, San Jose"), ("europe", "Singapore")]
    assert all(in_place(key, location) for key, location in yes)
    assert not any(in_place(key, location) for key, location in no)


def _companies():
    return CompanyIndex([
        {"company": "Jane Street", "text": "Trading internship."},
        {"company": "Two Sigma", "text": "We sponsor a visa in a wide range of cases."},
        {"company": "Citadel Securities", "text": "Market making; visa support."},
        {"company": "Meta", "text": "Build products."},
        {"company": "TikTok", "text": "Short video."},
        {"company": "ByteDance", "text": "Research."},
        {"company": "Visa", "text": "Payments network."},
        {"company": "Micron Technology", "text": "Memory chips."},
        {"company": "Susquehanna International Group (SIG)", "text": "Options."},
        {"company": "Internship", "text": "A company with an unlucky name."},
    ])


def test_companies_in_queries():
    index = _companies()
    assert parse_query("jane street internship", index).companies == {"jane street"}
    assert parse_query("citadel or two sigma quant", index).companies == {"citadel securities", "two sigma"}
    assert parse_query("micron hardware intern", index).companies == {"micron technology"}  # without "Technology"
    assert parse_query("SIG trading", index).companies == {"susquehanna international group (sig)"}
    assert parse_query("facebook swe intern", index).companies == {"meta"}  # the alias table
    assert parse_query("字节跳动 实习", index).companies == {"bytedance"}
    # ByteDance and TikTok are not aliases of each other.
    assert parse_query("bytedance intern", index).companies == {"bytedance"}
    assert parse_query("tiktok intern", index).companies == {"tiktok"}
    # A name other companies use as an ordinary word is not a company in a query.
    assert "visa" in index.ambiguous and parse_query("work visa sponsorship", index).companies == frozenset()
    assert parse_query("summer internship", index).companies == frozenset()
    assert parse_query("hsbc technology internship", index).companies == frozenset()  # not among the listings


def test_parse_query_combines_places_remote_and_companies():
    index = _companies()
    parsed = parse_query("tiktok machine learning intern singapore", index)
    assert (parsed.places, parsed.remote, parsed.companies) == (("singapore",), False, frozenset({"tiktok"}))
    assert parsed.rest == "machine learning intern" and parsed.text == "tiktok machine learning intern singapore"
    remote = parse_query("paid internship I can do remotely from hong kong", index)
    assert remote.remote and remote.places == ("hong kong",) and remote.has_boost
    assert not parse_query("backend developer python", index).has_boost
    assert parse_query("data analyst intern remote").remote  # works without a company index


def test_remote_matches_only_listings_that_say_remote():
    parsed = parse_query("data analyst intern remote")
    assert query.facets(parsed, "A", "Remote in USA", "")[1]
    assert query.facets(parsed, "A", "New York, NY", "This is a fully remote position.")[1]
    assert not query.facets(parsed, "A", "New York, NY", "Remote sensing and remote teams.")[1]
    assert not query.facets(parse_query("data analyst intern"), "A", "Remote", "")[1]  # not asked for


# --- weighted fusion and the boost ---------------------------------------------------------


def test_weighted_rrf_by_hand():
    lists = [["a", "b"], ["b", "c"]]
    assert weighted_rrf_scores(lists) == pytest.approx({"a": 1 / 61, "b": 1 / 62 + 1 / 61, "c": 1 / 62})
    assert weighted_rrf_scores(lists, (1.0, 0.5)) == pytest.approx({"a": 1 / 61, "b": 1 / 62 + 0.5 / 61, "c": 0.5 / 62})
    assert weighted_rrf_scores(lists, (1.0, 0.0)) == pytest.approx({"a": 1 / 61, "b": 1 / 62})  # weight 0 = left out
    assert search.by_score(weighted_rrf_scores(lists, (0.0, 1.0))) == ["b", "c"]
    assert search.rrf_scores(lists) == weighted_rrf_scores(lists)


FACTS = {
    "a": ("Acme", "London, UK", "frontend"),
    "b": ("Beta", "Singapore", "frontend"),
    "c": ("TikTok", "Singapore", "ml"),
    "d": ("TikTok", "Remote in USA", "ml"),
    "e": ("Gamma", "Singapore", "never retrieved"),
}


def test_boost_rescoring_by_hand():
    index = CompanyIndex({"company": c, "text": t} for c, _, t in FACTS.values())
    parsed = parse_query("tiktok intern singapore", index)
    ranking = ["a", "b", "c", "d"]
    plain = fuse_and_boost([ranking], None, parsed, FACTS, 0.0, 0.0)
    assert plain == pytest.approx({"a": 1 / 61, "b": 1 / 62, "c": 1 / 63, "d": 1 / 64})  # no boost: the ranking itself
    scores = fuse_and_boost([ranking], None, parsed, FACTS, w_location=0.5, w_company=1.0)
    assert scores == pytest.approx({
        "a": 1 / 61,                  # neither
        "b": 1 / 62 + 0.5 / 61,       # in Singapore
        "c": 1 / 63 + 1.5 / 61,       # in Singapore and from TikTok
        "d": 1 / 64 + 1.0 / 61,       # from TikTok
        "e": 0.5 / 61,                # in no ranking, but in Singapore: a boost, never a filter
    })
    assert search.by_score(scores) == ["c", "d", "b", "a", "e"]
    # Without a place or company in the query nothing changes, whatever the weights.
    assert fuse_and_boost([ranking], None, parse_query("frontend intern", index), FACTS, 2.0, 2.0) == plain


def _jobs():
    return [
        make_job("intern:sg", title="Data analyst intern", company="Acme", location="Singapore", source="intern_tech"),
        make_job("intern:ldn", title="Data analyst intern", company="Acme", location="London, UK", source="intern_tech"),
        make_job("intern:tt", title="Office helper", company="TikTok", location="London, UK", source="intern_tech"),
        make_job("aims:campus", title="Data analyst intern", description="Help with data."),  # CityU: no location field
    ]


def test_relevance_boosts_places_and_companies_in_every_mode():
    index = SearchIndex(_jobs())
    sims = {"intern:ldn": 0.9, "intern:sg": 0.8, "intern:tt": 0.2, "aims:campus": 0.7}
    for mode in config.SEARCH_MODES:
        plain = relevance(mode, "data analyst intern", index, sims, w_location=1.0, w_company=1.0)
        boosted = relevance(mode, "data analyst intern singapore", index, sims, w_location=1.0, w_company=1.0)
        off = relevance(mode, "data analyst intern singapore", index, sims, w_location=0.0, w_company=0.0)
        assert search.by_score(boosted)[0] == "intern:sg", mode
        assert set(plain) == set(boosted) == set(off) == {j.job_id for j in _jobs()}, mode
    # Vector search alone prefers London (cosine 0.9 vs 0.8); the boost is what moves Singapore up.
    assert search.by_score(relevance("vector", "data analyst intern singapore", index, sims, w_location=0.0, w_company=0.0))[0] == "intern:ldn"
    assert relevance("vector", "data analyst intern", index, sims) == sims  # nothing to boost: plain cosine
    company = relevance("keyword", "tiktok data analyst", index, None, w_location=0.0, w_company=1.0)
    assert search.by_score(company)[0] == "intern:tt"
    # CityU's own jobs have no location field but are in Hong Kong.
    hk = relevance("keyword", "data analyst intern hong kong", index, None, w_location=1.0, w_company=0.0)
    assert search.by_score(hk)[0] == "aims:campus"
    assert relevance("keyword", "quantum", index, None) == {} and relevance("hybrid", "x", index, {}) == {}


def test_hybrid_weights_come_from_config(monkeypatch):
    index = SearchIndex(_jobs()[:3])
    sims = {"intern:tt": 0.9, "intern:ldn": 0.5, "intern:sg": 0.4}
    monkeypatch.setattr(config, "HYBRID_WEIGHTS", (1.0, 0.0))
    keyword_only = relevance("hybrid", "data analyst", index, sims)
    assert keyword_only["intern:tt"] == 0.0 and search.by_score(keyword_only)[0] != "intern:tt"  # a keyword match leads
    monkeypatch.setattr(config, "HYBRID_WEIGHTS", (0.0, 1.0))
    assert search.by_score(relevance("hybrid", "data analyst", index, sims))[0] == "intern:tt"


def test_recommend_uses_the_boost(cache):
    for job in _jobs():
        cache.upsert_job(job)
    for mode in config.SEARCH_MODES:
        ranked = pipeline.recommend(Preferences(None, "data analyst intern singapore", 1, 1, search_mode=mode),
                                    FakeProvider(), cache=cache, today=TODAY)
        assert ranked[0].job.job_id == "intern:sg", mode


# --- the optional reranker ----------------------------------------------------------------


def test_rerank_order_reorders_only_the_head():
    assert rerank.rerank_order(["a", "b", "c", "d"], [0.1, 0.9, 0.5], top_n=3) == ["b", "c", "a", "d"]
    assert rerank.rerank_order(["b", "a"], [0.5, 0.5]) == ["a", "b"]  # ties by id
    assert rerank.rerank_order(["a", "b", "c"], [], top_n=0) == ["a", "b", "c"]
    with pytest.raises(ValueError):
        rerank.rerank_order(["a", "b"], [0.1], top_n=2)


def test_rerank_relevance_keeps_the_values_and_moves_the_jobs():
    scores = {"a": 0.5, "b": 0.4, "c": 0.3, "d": 0.2, "z": 0.0}
    seen = []

    def scorer(query_text, texts):
        seen.append((query_text, list(texts)))
        return [{"text a": 0.1, "text b": 0.2, "text c": 0.9}[t] for t in texts]

    out = rerank.rerank_relevance(scores, "q", lambda job_id: f"text {job_id}", scorer, top_n=3)
    assert out == {"c": 0.5, "b": 0.4, "a": 0.3, "d": 0.2, "z": 0.0}
    assert seen == [("q", ["text a", "text b", "text c"])]  # only the top 3, best first
    assert rerank.rerank_relevance(scores, "q", str, None) is scores  # no reranker: unchanged
    assert rerank.rerank_relevance({}, "q", str, scorer) == {}


@pytest.fixture
def _no_torch(monkeypatch):
    """As if the optional `rerank` group were not installed."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.split(".")[0] in ("torch", "sentence_transformers"):
            raise ImportError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(rerank, "_loaded", None)
    monkeypatch.setattr(rerank, "_load_failed", False)


def test_missing_torch_means_no_reranking_and_one_clear_log_line(_no_torch, caplog, cache, monkeypatch):
    with caplog.at_level("WARNING", logger="cityu_jobs.core.rerank"):
        assert rerank.load_reranker() is None
        assert rerank.load_reranker() is None  # asked again: still None, no second attempt
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1 and "reranking is off" in messages[0] and "uv sync --group rerank" in messages[0]

    # The whole pipeline still works with reranking switched on.
    monkeypatch.setattr(config, "RERANK", True)
    for job in _jobs():
        cache.upsert_job(job)
    prefs = Preferences(None, "data analyst intern singapore", 1, 1, search_mode="keyword")
    with_flag = pipeline.recommend(prefs, FakeProvider(), cache=cache, today=TODAY)
    monkeypatch.setattr(config, "RERANK", False)
    without = pipeline.recommend(prefs, FakeProvider(), cache=cache, today=TODAY)
    assert [(r.job.job_id, r.score) for r in with_flag] == [(r.job.job_id, r.score) for r in without]


def test_reranking_is_off_by_default_and_never_loads_a_model(cache, monkeypatch):
    assert config.RERANK is False
    monkeypatch.setattr(pipeline, "load_reranker", lambda: pytest.fail("the reranker must not be loaded when RERANK is off"))
    for job in _jobs():
        cache.upsert_job(job)
    pipeline.recommend(Preferences(None, "data analyst", 1, 1, search_mode="keyword"), FakeProvider(), cache=cache, today=TODAY)


def test_pipeline_reranks_the_top_matches_when_enabled(cache, monkeypatch):
    class FakeReranker:
        def scores(self, query_text, texts):
            return [1.0 if "London" in t else 0.0 for t in texts]  # prefers London, against the place boost

    monkeypatch.setattr(config, "RERANK", True)
    monkeypatch.setattr(pipeline, "load_reranker", lambda: FakeReranker())
    for job in _jobs():
        cache.upsert_job(job)
    prefs = Preferences(None, "data analyst intern singapore", 1, 1, search_mode="keyword")
    ranked = pipeline.recommend(prefs, FakeProvider(), cache=cache, today=TODAY)
    assert ranked[0].job.job_id == "intern:ldn"
    assert ranked[0].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)  # it took the top relevance value
