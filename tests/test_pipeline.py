"""T16 (part 3): pipeline.recommend() with FakeProvider, and refresh() with every browser stubbed out.

refresh() normally starts Playwright and opens AIMS / cityu.edu.hk. Here `sync_playwright`,
`aims_session`, `scrape_aims` and `scrape_itc_stem` are replaced, so nothing goes online.
"""

from contextlib import contextmanager
from datetime import date

import pytest

from cityu_jobs.core import pipeline
from cityu_jobs.core.ai.providers import FakeProvider
from cityu_jobs.core.aims_scraper import SCHEMES, SchemeResult
from cityu_jobs.core.auth import AimsSessionExpired
from cityu_jobs.core.ranking import Preferences

from conftest import make_job

TODAY = date(2026, 10, 25)


def _fill(cache, *jobs):
    for job in jobs:
        cache.upsert_job(job)


# --- recommend() ------------------------------------------------------------------


def test_recommend_ranks_open_active_jobs_only(cache):
    ml = make_job("aims:ML", title="Machine learning research", description="deep learning models")
    office = make_job("aims:OF", title="Office helper", description="filing", department_code="AC")
    closed = make_job("aims:OLD", closing_date=date(2026, 1, 1))
    gone = make_job("aims:GONE")
    _fill(cache, ml, office, closed, gone)
    cache.mark_scrape_complete("aims_cis", {"aims:ML", "aims:OF", "aims:OLD"})  # GONE no longer listed

    provider = FakeProvider(level=4)
    ranked = pipeline.recommend(Preferences("CS", "machine learning research", 2, 5), provider, cache=cache, today=TODAY)

    assert [r.job.job_id for r in ranked] == ["aims:ML", "aims:OF"]
    assert provider.llm_calls == 2  # closed and inactive jobs cost no AI calls
    assert ranked[0].level == 4
    assert ranked[0].level_reason == "Fake rating."
    assert cache.path.exists()  # progress was saved


def test_recommend_second_run_uses_cache(cache):
    _fill(cache, make_job("aims:A"), make_job("aims:B"))
    pipeline.recommend(Preferences("CS", "q"), FakeProvider(), cache=cache, today=TODAY)

    provider = FakeProvider()
    pipeline.recommend(Preferences("CS", "another query"), provider, cache=cache, today=TODAY)
    assert provider.llm_calls == 0
    assert provider.embed_calls == 1  # only the query


def test_recommend_empty_query_gives_equal_similarity(cache):
    _fill(cache, make_job("aims:A", department_code="CS"), make_job("aims:B", department_code="MA"))
    provider = FakeProvider()
    ranked = pipeline.recommend(Preferences("CS", "   "), provider, cache=cache, today=TODAY)
    assert all(r.breakdown["similarity"] == 0 for r in ranked)
    assert ranked[0].job.job_id == "aims:A"  # department decides


def test_recommend_reports_progress(cache):
    _fill(cache, make_job("aims:A"))
    messages = []
    pipeline.recommend(Preferences("CS", "q"), FakeProvider(), cache=cache, progress=messages.append, today=TODAY)
    assert any(m.startswith("Rating job levels with fake-llm") for m in messages)
    assert any(m.startswith("Embedding jobs") for m in messages)
    assert messages[-1] == "Ranking..."


# --- refresh() --------------------------------------------------------------------


class _FakeBrowser:
    def new_context(self):
        return "itc-context"

    def close(self):
        pass


class _FakePlaywright:
    class chromium:
        @staticmethod
        def launch(headless=True):
            assert headless, "refresh must never open a visible browser"
            return _FakeBrowser()


@pytest.fixture
def offline(monkeypatch):
    """Stub every browser entry point; returns a dict recording what was called."""
    calls = {"aims_session": [], "itc": 0, "scrape_aims": []}

    @contextmanager
    def fake_sync_playwright():
        yield _FakePlaywright()

    @contextmanager
    def logged_in(playwright, interactive=True):
        calls["aims_session"].append(interactive)
        yield "aims-context"

    def fake_scrape_aims(context, cache, keys, progress=None):
        calls["scrape_aims"].append(list(keys))
        for key in keys:
            cache.mark_scrape_complete(f"aims_{key}", set())
        return [SchemeResult(k, listed=1) for k in keys]

    def fake_itc(context, cache):
        calls["itc"] += 1
        cache.mark_scrape_complete("itc_stem", set())
        return ["ITC: 0 jobs"]

    monkeypatch.setattr(pipeline, "sync_playwright", fake_sync_playwright)
    monkeypatch.setattr(pipeline, "aims_session", logged_in)
    monkeypatch.setattr(pipeline, "scrape_aims", fake_scrape_aims)
    monkeypatch.setattr(pipeline, "scrape_itc_stem", fake_itc)
    return calls


def test_refresh_scrapes_stale_sources_non_interactively(cache, offline):
    report = pipeline.refresh(cache=cache)
    assert offline["aims_session"] == [False]  # never the headed login
    assert offline["scrape_aims"] == [list(SCHEMES)]
    assert sorted(report.scraped) == sorted([f"aims_{k}" for k in SCHEMES] + ["itc_stem"])
    assert not report.login_needed
    assert cache.path.exists()


def test_refresh_skips_fresh_sources(cache, offline):
    pipeline.refresh(cache=cache)
    report = pipeline.refresh(cache=cache)
    assert report.scraped == []
    assert sorted(report.skipped) == sorted([f"aims_{k}" for k in SCHEMES] + ["itc_stem"])
    assert len(offline["aims_session"]) == 1 and offline["itc"] == 1


def test_refresh_force_rescrapes(cache, offline):
    pipeline.refresh(cache=cache)
    pipeline.refresh(cache=cache, force=True)
    assert len(offline["aims_session"]) == 2 and offline["itc"] == 2


def test_refresh_expired_session_sets_login_needed_and_keeps_cached_jobs(cache, offline, monkeypatch):
    _fill(cache, make_job("aims:OLD1"))

    @contextmanager
    def expired(playwright, interactive=True):
        assert interactive is False
        raise AimsSessionExpired("expired")
        yield  # pragma: no cover

    monkeypatch.setattr(pipeline, "aims_session", expired)
    report = pipeline.refresh(cache=cache)

    assert report.login_needed
    assert any("AIMS login needed" in n for n in report.notes)
    assert not any(s.startswith("aims_") for s in report.scraped)
    assert "itc_stem" in report.scraped  # ITC still runs
    assert [j.job_id for j in cache.active_jobs()] == ["aims:OLD1"]


def test_refresh_itc_failure_not_reported_as_scraped(cache, offline, monkeypatch):
    monkeypatch.setattr(pipeline, "scrape_itc_stem", lambda context, cache: ["download needs a CityU login"])
    report = pipeline.refresh(sources=("itc",), cache=cache)
    assert report.scraped == []
    assert "download needs a CityU login" in report.notes


def test_refresh_prunes_old_jobs(cache, offline):
    _fill(cache, make_job("aims:ANCIENT", closing_date=date(2000, 1, 1)))
    report = pipeline.refresh(sources=(), cache=cache)
    assert cache.get_job("aims:ANCIENT") is None
    assert any("Removed 1" in n for n in report.notes)
