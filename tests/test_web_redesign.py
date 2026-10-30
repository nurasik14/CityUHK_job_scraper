"""T27: web tests for the UI redesign (T25/T26): static assets, no external loads, nav,
skip link, earn/learn radios, closing-soon badge, level meter, banners, settings.

Uses the helpers from test_web.py: a real TaskRunner with fake search/login functions.
"""

import re
from urllib.parse import urlparse
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from cityu_jobs.core.pipeline import RefreshReport
from cityu_jobs.core.ranking import Preferences, RankedJob
from cityu_jobs.web import app as web_app
from cityu_jobs.web.tasks import TaskRunner

from conftest import make_job
from test_web import FakeSearch, _client, _search, _wait, tmp_settings  # noqa: F401 (tmp_settings is an autouse fixture)

PAGES = ["/", "/results", "/settings"]
TODAY = date(2026, 10, 25)


class FixedDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    """The settings page asks Ollama for its models; answer "not running" without a network call."""
    def down(url, timeout):
        raise web_app.httpx.ConnectError("refused")

    monkeypatch.setattr(web_app.httpx, "get", down)


def _results_client(*ranked: RankedJob, login_needed=False) -> TestClient:
    runner = TaskRunner()
    runner.state.prefs = Preferences("CS", "machine learning", earn=2, learn=5)
    runner.state.status, runner.state.kind = "done", "search"
    runner.state.results = list(ranked)
    runner.state.report = RefreshReport()
    runner.state.login_needed = login_needed
    return TestClient(web_app.create_app(runner))


def _ranked(job, level=None) -> RankedJob:
    return RankedJob(job, 0.6, {"similarity": 0.3, "level": 0.1}, 0.7, level, "Reason." if level else "")


# --- Assets and privacy -------------------------------------------------------------


def test_app_js_served():
    client, _ = _client()
    page = client.get("/").text
    assert "/static/app.js" in page
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def _external_urls(text: str, attrs: tuple[str, ...]) -> list[str]:
    """Absolute or protocol-relative URLs whose host isn't this app (TestClient's "testserver")."""
    urls = []
    for attr in attrs:
        urls += re.findall(rf'{attr}\s*=?\s*\(?\s*["\']?((?:https?:)?//[^"\')\s>]+)', text)
    return [u for u in urls if urlparse(u if ":" in u.split("//")[0] else "http:" + u).hostname != "testserver"]


@pytest.mark.parametrize("path", PAGES)
def test_no_external_resources_in_pages(path):
    """<script src>, <link href>, <img src> and CSS url() must load from this app only. Outbound <a> links are fine."""
    client, _ = _client()
    html = client.get(path).text
    resource_tags = " ".join(re.findall(r"<(?:script|link|img|source|iframe)\b[^>]*>", html))
    assert _external_urls(resource_tags, ("src", "href")) == []
    assert _external_urls(html, ("url",)) == []
    assert "@import" not in html


def test_no_external_resources_in_css_and_js():
    client, _ = _client()
    css = client.get("/static/style.css").text
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", css)
    assert not re.search(r"@import", css)
    js = client.get("/static/app.js").text
    assert not re.search(r"https?://", js)  # no fetches to other hosts (polling uses /api/status)


# --- Navigation and landmarks -------------------------------------------------------


@pytest.mark.parametrize("path, label", [("/", "Search"), ("/results", "Results"), ("/settings", "Settings")])
def test_active_nav_link_has_aria_current(path, label):
    client, _ = _client()
    html = client.get(path).text
    current = re.findall(r'<a href="([^"]+)" aria-current="page">([^<]+)</a>', html)
    assert current == [(path, label)]


@pytest.mark.parametrize("path", PAGES)
def test_skip_link_targets_main(path):
    client, _ = _client()
    html = client.get(path).text
    assert re.search(r'<a class="skip-link" href="#main">', html)
    assert 'id="main"' in html
    assert html.index("skip-link") < html.index('id="main"')  # first thing to tab to


# --- Search form --------------------------------------------------------------------


def test_earn_learn_radios_with_saved_prefs_checked():
    search = FakeSearch()
    client, runner = _client(search)
    _search(client, earn="2", learn="5")
    _wait(runner)
    html = client.get("/").text
    for name, saved in (("earn", 2), ("learn", 5)):
        radios = re.findall(rf'<input type="radio"[^>]*name="{name}"[^>]*>', html)
        values = [re.search(r'value="(\d)"', r).group(1) for r in radios]
        assert values == ["1", "2", "3", "4", "5"]
        checked = [re.search(r'value="(\d)"', r).group(1) for r in radios if "checked" in r]
        assert checked == [str(saved)]
    assert html.count("<fieldset") >= 2 and html.count("<legend") >= 2


def test_default_prefs_check_3():
    client, _ = _client()
    html = client.get("/").text
    for name in ("earn", "learn"):
        assert re.search(rf'name="{name}" value="3" checked', html)


def test_form_field_names_unchanged():
    client, _ = _client()
    html = client.get("/").text
    for name in ("department", "query", "earn", "learn", "refresh"):
        assert f'name="{name}"' in html


# --- Results cards ------------------------------------------------------------------


@pytest.mark.parametrize("days, text", [(0, "Closes today"), (1, "Closes in 1 day"), (3, "Closes in 3 days"), (7, "Closes in 7 days")])
def test_closes_soon_badge(monkeypatch, days, text):
    monkeypatch.setattr(web_app, "date", FixedDate)
    job = make_job(closing_date=TODAY + timedelta(days=days))
    html = _results_client(_ranked(job)).get("/results").text
    assert text in html
    assert 'class="badge-soon"' in html


@pytest.mark.parametrize("days, urgency", [(2, "danger"), (3, "danger"), (5, "warn"), (7, "warn"), (8, "neutral"), (10, "neutral"), (14, "neutral")])
def test_urgency_chip_levels(monkeypatch, days, urgency):
    """T30: 0-3 days danger, 4-7 warn, 8-14 a neutral chip."""
    monkeypatch.setattr(web_app, "date", FixedDate)
    html = _results_client(_ranked(make_job(closing_date=TODAY + timedelta(days=days)))).get("/results").text
    assert f'class="badge-soon" data-urgency="{urgency}"' in html
    assert f"Closes in {days} days" in html


@pytest.mark.parametrize("days", [15, 20])
def test_no_urgency_chip_after_14_days(monkeypatch, days):
    monkeypatch.setattr(web_app, "date", FixedDate)
    html = _results_client(_ranked(make_job(closing_date=TODAY + timedelta(days=days)))).get("/results").text
    assert "badge-soon" not in html and "Closes in" not in html


def test_level_meter_and_unrated():
    rated = make_job("aims:A", title="Rated job")
    unrated = make_job("aims:B", title="Unrated job")
    html = _results_client(_ranked(rated, level=4), _ranked(unrated, level=None)).get("/results").text
    assert 'aria-label="Level 4 of 5"' in html
    assert html.count('class="on"') >= 4
    assert "Not rated yet" in html


def test_score_bar_hidden_and_card_labelled():
    html = _results_client(_ranked(make_job(title="ML intern"), level=3)).get("/results").text
    assert '<div class="bar" aria-hidden="true">' in html
    assert re.search(r'aria-label="Rank 1: ML intern, score 0\.60 of \d\.\d\d"', html)


def test_no_apply_links_into_aims():
    html = _results_client(_ranked(make_job())).get("/results").text
    assert "hwosjpstapp" not in html and "banweb.cityu.edu.hk" not in html


# --- Banners and states -------------------------------------------------------------


def test_error_banner_has_role_alert():
    client, runner = _client(FakeSearch(error=RuntimeError("Ollama is not running")))
    _search(client)
    _wait(runner)
    html = client.get("/results").text
    assert re.search(r'<div class="banner danger" role="alert">', html)
    assert "Search failed" in html and "Ollama is not running" in html


@pytest.mark.parametrize("path", PAGES)
def test_login_needed_banner_on_every_page(path):
    client = _results_client(_ranked(make_job()), login_needed=True)
    html = client.get(path).text
    assert "AIMS login needed" in html
    assert 'action="/login"' in html and "Log in to AIMS" in html


def test_no_login_banner_when_not_needed():
    client = _results_client(_ranked(make_job()))
    assert "AIMS login needed" not in client.get("/").text


def test_success_banner_has_role_status():
    client, _ = _client()
    html = client.get("/settings?saved=1").text
    assert re.search(r'<div class="banner ok" role="status">', html)


def test_empty_state_before_any_search():
    client, _ = _client()
    html = client.get("/results").text
    assert "Start a search" in html and 'href="/"' in html


def test_progress_state_shows_elapsed_and_polls():
    search = FakeSearch()
    search.release.clear()
    client, runner = _client(search)
    _search(client)
    try:
        html = client.get("/results").text
        assert "aria-live" in html
        assert re.search(r"\b\d+:\d\d\b", html)  # elapsed as m:ss
    finally:
        search.release.set()
    _wait(runner)


# --- Settings -----------------------------------------------------------------------


def test_settings_fields_and_no_secrets():
    client, _ = _client()
    html = client.get("/settings").text
    names = set(re.findall(r'<(?:input|select)[^>]*name="([^"]+)"', html))
    assert names == {"ai_provider", "claude_model", "ollama_url", "llm_model", "embed_model", "intern_regions"}
    assert "api" not in " ".join(names).lower() and "password" not in html.lower()
    assert re.search(r'name="ai_provider" value="claude" checked', html)  # Claude is the default
