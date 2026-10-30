"""Tech internships from company job boards (core/internships/), with a mock HTTP transport."""

import json

import httpx
import pytest

from cityu_jobs import config
from cityu_jobs.core.internships import fetchers
from cityu_jobs.core.internships.fetchers import Posting, fetch_greenhouse, fetch_lever, fetch_simplify, html_to_text, wanted
from cityu_jobs.core.internships.regions import classify, in_regions
from cityu_jobs.core.internships.scraper import dedupe, scrape_internships, to_job

REGIONS = ["hk", "china", "apac", "remote"]


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr(config, "INTERN_DELAY_S", 0)


def client_for(routes: dict) -> httpx.Client:
    """A client whose requests are answered from `routes` ({url-prefix: json or status code})."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                return httpx.Response(answer, text="nope") if isinstance(answer, int) else httpx.Response(200, json=answer)
        return httpx.Response(404, text=f"no route for {url}")
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Regions and filters ---------------------------------------------------------------


@pytest.mark.parametrize(
    "location, regions",
    [
        ("Hong Kong SAR", {"hk"}),
        ("Hong Kong; Singapore; Taiwan", {"hk", "apac"}),
        ("Shanghai, China", {"china"}),
        ("APAC - Remote", {"apac"}),
        ("Remote", {"remote"}),
        ("Remote in USA", {"americas"}),
        ("San Francisco, CA", {"americas"}),
        ("London, UK", {"europe"}),
        ("Indiana", {"americas"}),  # not India
        ("", set()),
    ],
)
def test_classify(location, regions):
    assert classify(location) == regions


def test_in_regions_keeps_unknown_locations():
    assert in_regions("", REGIONS) and not in_regions("Austin, TX", REGIONS) and in_regions("Austin, TX", ["americas"])


@pytest.mark.parametrize(
    "title, ok",
    [
        ("Software Engineer Intern", True),
        ("Machine Learning Internship 2027", True),
        ("Senior Manager, Internal Audit", False),        # "internal" is not "intern"
        ("Director, International Tax", False),
        ("Financial Analyst Intern", False),
        ("【2028 Graduates・Non-tech】Supply Chain Manager Intern", False),
        ("Marketing Intern", False),
    ],
)
def test_wanted_titles(title, ok):
    assert wanted(title, "Hong Kong", REGIONS) is ok


def test_html_to_text_unescapes_greenhouse_content():
    assert html_to_text("&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;") == "Build & ship\nPython"


# --- Fetchers ------------------------------------------------------------------------------


COMPANY = {"name": "Acme", "slug": "acme", "tier": "tech"}


def test_greenhouse_opens_details_only_for_matches():
    base = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    client = client_for({
        f"{base}/1": {"title": "Data Science Intern", "location": {"name": "Hong Kong"}, "absolute_url": "https://x/1",
                      "content": "&lt;p&gt;Models&lt;/p&gt;", "first_published": "2026-10-01T10:00:00-04:00",
                      "application_deadline": "2026-12-01"},
        f"{base}/2": 500,  # must never be requested: the job isn't wanted
        base: {"jobs": [
            {"id": 1, "title": "Data Science Intern", "location": {"name": "Hong Kong"}},
            {"id": 2, "title": "Data Science Intern", "location": {"name": "Austin, TX"}},
            {"id": 3, "title": "Staff Engineer", "location": {"name": "Hong Kong"}},
        ]},
    })
    [posting] = fetch_greenhouse(client, COMPANY, REGIONS)
    assert posting.key == "gh:acme:1" and posting.description == "Models"
    assert str(posting.deadline) == "2026-12-01" and str(posting.posted) == "2026-10-01"


def test_lever_uses_all_locations():
    client = client_for({"https://api.lever.co/v0/postings/acme": [
        {"id": "a", "text": "Backend Intern", "hostedUrl": "https://jobs.lever.co/acme/a", "createdAt": 1790000000000,
         "categories": {"location": "London", "allLocations": ["London", "Singapore"]}, "descriptionPlain": "APIs",
         "lists": [{"text": "You will", "content": "<li>Write Go</li>"}]},
    ]})
    [posting] = fetch_lever(client, COMPANY, REGIONS)
    assert posting.location == "London; Singapore" and "Write Go" in posting.description


def test_simplify_tiers_big_tech_and_skips_inactive():
    client = client_for({fetchers.SIMPLIFY_URL: [
        {"id": "1", "company_name": "Google", "title": "SWE Intern", "locations": ["Singapore"], "active": True,
         "is_visible": True, "terms": ["Summer 2027"], "url": "https://g/1", "category": "Software"},
        {"id": "2", "company_name": "Tiny", "title": "ML Intern", "locations": ["Remote"], "active": True, "terms": ["N/A"]},
        {"id": "3", "company_name": "Old", "title": "ML Intern", "locations": ["Hong Kong"], "active": False},
        {"id": "4", "company_name": "Far", "title": "ML Intern", "locations": ["NYC"], "active": True},
    ]})
    postings = {p.company: p for p in fetch_simplify(client, COMPANY, REGIONS)}
    assert set(postings) == {"Google", "Tiny"}
    assert postings["Google"].tier == "big" and postings["Google"].terms == ["Summer 2027"]
    assert postings["Tiny"].tier == "tech" and postings["Tiny"].terms == []


# --- Scraper -------------------------------------------------------------------------------


def _posting(key, company="Acme", title="ML Intern", url="https://acme/jobs/1", tier="tech", **kw):
    return Posting(key=key, company=company, title=title, location="Hong Kong", url=url, tier=tier, **kw)


def test_dedupe_prefers_company_board_over_simplify():
    kept = dedupe([_posting("simplify:9", url="https://ACME/jobs/1/?utm=x"), _posting("gh:acme:1")])
    assert [p.key for p in kept] == ["gh:acme:1"]


def test_to_job_maps_fields():
    job = to_job(_posting("gh:acme:1", tier="big", terms=["Summer 2027"], description="Train models"))
    assert job.job_id == "intern:gh:acme:1" and job.source == "intern_bigtech" and job.scheme == "Big tech internship"
    assert job.company == "Acme" and job.department_code == "TECH" and job.employment_period == "Summer 2027"
    assert job.content_hash  # edits to the posting change the hash


def test_scrape_keeps_jobs_of_failed_company(cache, monkeypatch):
    ok = {"name": "Good", "provider": "greenhouse", "slug": "good", "tier": "tech"}
    bad = {"name": "Flaky", "provider": "lever", "slug": "flaky", "tier": "tech"}
    old = to_job(_posting("lever:flaky:1", company="Flaky", url="https://flaky/1"))
    gone = to_job(_posting("gh:good:old", company="Good", url="https://good/old"))
    cache.upsert_job(old)
    cache.upsert_job(gone)

    monkeypatch.setitem(fetchers.FETCHERS, "greenhouse", lambda client, c, r: [_posting("gh:good:1", company="Good", url="https://good/1")])

    def broken(client, c, r):
        raise fetchers.FetchError("HTTP 503")

    monkeypatch.setitem(fetchers.FETCHERS, "lever", broken)
    notes = scrape_internships(cache, REGIONS, client=client_for({}), companies=[ok, bad])

    active = {j.job_id for j in cache.active_jobs()}
    assert active == {"intern:gh:good:1", old.job_id}  # Good's vanished job is inactive, Flaky's is kept
    assert any("Flaky" in n and "kept their earlier jobs" in n for n in notes)
    assert not cache.is_stale("intern_tech", __import__("datetime").timedelta(hours=1))
