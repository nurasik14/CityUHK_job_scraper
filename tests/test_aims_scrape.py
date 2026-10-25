"""scrape_scheme / scrape_aims with the fetchers replaced: no browser, no AIMS.

search_jobs/open_job are patched to return hand-made rows and a minimal detail page, and the
polite pause is switched off, so these run instantly.
"""

import pytest

from cityu_jobs.core import aims_scraper
from cityu_jobs.core.aims_scraper import scrape_aims, scrape_scheme
from cityu_jobs.core.auth import AimsSessionExpired
from cityu_jobs.core.cache import Cache

DETAIL = '<html><body><div class="body"><p>Student Helpers (Regular Term)</p></div></body></html>'


def _row(n: int, hours: str = "10") -> dict:
    return {"Job No.": f"P26XX{n:03d}", "Brief Job Description": f"Job {n}", "Recruit. Dept": "CS",
            "Avg Weekly Work hrs per Student": hours, "Closing Date": "30-Dec-2099"}


class FakePage:
    def content(self):
        return "<p>No. of matched record: 3</p>"

    def close(self):
        pass


class FakeContext:
    def new_page(self):
        return FakePage()


@pytest.fixture
def fake_aims(monkeypatch):
    """Patch the fetchers; returns a dict to control rows and failures and to count calls."""
    state = {"rows": [_row(1), _row(2), _row(3)], "fail": {}, "searches": 0, "opened": []}

    def search_jobs(page, scheme):
        state["searches"] += 1
        return list(state["rows"])

    def open_job(page, job_no):
        if job_no in state["fail"]:
            raise state["fail"][job_no]
        state["opened"].append(job_no)
        return DETAIL

    monkeypatch.setattr(aims_scraper, "search_jobs", search_jobs)
    monkeypatch.setattr(aims_scraper, "open_job", open_job)
    monkeypatch.setattr(aims_scraper, "_polite_pause", lambda: None)
    return state


def test_first_run_opens_every_job_and_marks_complete(cache, fake_aims):
    result = scrape_scheme(FakePage(), cache, "sh_regular")
    assert (result.listed, result.matched, result.opened, result.unchanged) == (3, 3, 3, 0)
    assert fake_aims["searches"] == 3  # 1 search + a re-search before jobs 2 and 3
    assert cache.last_scraped_at("aims_sh_regular") is not None
    assert len(cache.active_jobs()) == 3


def test_second_run_opens_only_changed_rows(cache, fake_aims):
    scrape_scheme(FakePage(), cache, "sh_regular")
    fake_aims["opened"].clear()
    fake_aims["searches"] = 0
    fake_aims["rows"][1] = _row(2, hours="12")  # one listing edited

    result = scrape_scheme(FakePage(), cache, "sh_regular")
    assert fake_aims["opened"] == ["P26XX002"]
    assert (result.opened, result.unchanged) == (1, 2)
    assert fake_aims["searches"] == 1  # still on the results page: no re-search needed


def test_unlisted_job_becomes_inactive(cache, fake_aims):
    scrape_scheme(FakePage(), cache, "sh_regular")
    fake_aims["rows"].pop()
    scrape_scheme(FakePage(), cache, "sh_regular")
    assert {j.job_id for j in cache.active_jobs()} == {"aims:P26XX001", "aims:P26XX002"}


def test_one_broken_job_is_isolated(cache, fake_aims):
    fake_aims["fail"]["P26XX002"] = ValueError("odd page")
    result = scrape_scheme(FakePage(), cache, "sh_regular")
    assert result.opened == 2 and len(result.errors) == 1 and "P26XX002" in result.errors[0]
    assert cache.last_scraped_at("aims_sh_regular") is not None


def test_session_expiry_aborts_without_marking_complete(cache, fake_aims):
    fake_aims["fail"]["P26XX002"] = AimsSessionExpired("timeout")
    with pytest.raises(AimsSessionExpired):
        scrape_scheme(FakePage(), cache, "sh_regular")
    assert cache.last_scraped_at("aims_sh_regular") is None


def test_scrape_aims_saves_after_each_scheme(tmp_path, fake_aims):
    cache = Cache(tmp_path / "cache.json")
    scrape_aims(FakeContext(), cache, ["sh_regular"])
    assert len(Cache.load(cache.path).active_jobs()) == 3


def test_interrupted_scheme_keeps_jobs_already_opened(tmp_path, fake_aims):
    """Live run 2026-10-25: Ctrl+C after ~14 min of Student Helper (Regular) work saved nothing.

    Jobs opened before the interrupt should be in the saved file (without marking the scheme
    complete), so the next run skips them via their summary hash.
    """
    cache = Cache(tmp_path / "cache.json")
    fake_aims["fail"]["P26XX003"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        scrape_aims(FakeContext(), cache, ["sh_regular"])

    saved = Cache.load(cache.path)
    assert {"aims:P26XX001", "aims:P26XX002"} <= set(saved.data["jobs"])
    assert saved.last_scraped_at("aims_sh_regular") is None


def test_progress_lines_per_opened_job(cache, fake_aims):
    lines = []
    scrape_aims(FakeContext(), cache, ["sh_regular"], progress=lines.append)
    assert lines == [
        "Scraping AIMS: Student Helpers (Regular Term)",
        "Student Helpers (Regular Term): 1/3",
        "Student Helpers (Regular Term): 2/3",
        "Student Helpers (Regular Term): 3/3",
    ]

    lines.clear()  # second run: nothing changed, so only the scheme line
    scrape_aims(FakeContext(), cache, ["sh_regular"], progress=lines.append)
    assert lines == ["Scraping AIMS: Student Helpers (Regular Term)"]


def test_scrape_scheme_saves_every_n_opened_jobs(tmp_path, fake_aims, monkeypatch):
    """Without scrape_aims' final save: the periodic save alone keeps the first jobs."""
    monkeypatch.setattr(aims_scraper, "SAVE_EVERY", 2)
    cache = Cache(tmp_path / "cache.json")
    fake_aims["fail"]["P26XX003"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        scrape_scheme(FakePage(), cache, "sh_regular")
    assert set(Cache.load(cache.path).data["jobs"]) == {"aims:P26XX001", "aims:P26XX002"}


def test_interrupted_run_resumes_without_reopening(tmp_path, fake_aims):
    cache = Cache(tmp_path / "cache.json")
    fake_aims["fail"]["P26XX003"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        scrape_aims(FakeContext(), cache, ["sh_regular"])

    del fake_aims["fail"]["P26XX003"]
    fake_aims["opened"].clear()
    result = scrape_aims(FakeContext(), Cache.load(cache.path), ["sh_regular"])[0]
    assert fake_aims["opened"] == ["P26XX003"]
    assert (result.opened, result.unchanged) == (1, 2)
