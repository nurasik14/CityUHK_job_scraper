"""T19: the FastAPI web UI, driven by TestClient with a fake search and a fake login.

The real TaskRunner is used (it runs the work in a background thread), but its search
and login functions are replaced, so nothing scrapes, calls Ollama or opens a window.
"""

import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from cityu_jobs import config
from cityu_jobs.core.pipeline import RefreshReport
from cityu_jobs.core.ranking import Preferences, RankedJob
from cityu_jobs.core.settings import Settings
from cityu_jobs.web import app as web_app
from cityu_jobs.web.tasks import TaskRunner

from conftest import make_job

ORIGIN = {"origin": "http://testserver"}
EVIL = {"origin": "http://evil.example"}


class FakeSearch:
    """Records each call and returns one ranked job; can be made to block or fail."""

    def __init__(self, login_needed=False, error=None):
        self.calls = []
        self.login_needed = login_needed
        self.error = error
        self.release = threading.Event()
        self.release.set()

    def __call__(self, prefs, provider, force_refresh=False, progress=lambda m: None):
        self.calls.append({"prefs": prefs, "provider": provider, "force_refresh": force_refresh})
        progress("Rating job levels: 1/2")
        self.release.wait(5)
        if self.error:
            raise self.error
        job = make_job("aims:N26CS001", title="ML research intern", weekly_hours=16.5)
        ranked = RankedJob(job, 0.72, {"similarity": 0.45, "department": 0.1, "level": 0.17}, 0.8, 4, "Builds models.")
        report = RefreshReport(scraped=["itc_stem"], notes=["ITC: 1 job"], login_needed=self.login_needed)
        return report, [ranked]


@pytest.fixture(autouse=True)
def tmp_settings(tmp_path, monkeypatch):
    """Keep the settings file out of the real .data/ directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")


def _client(search=None, login=None) -> tuple[TestClient, TaskRunner]:
    runner = TaskRunner(search_fn=search or FakeSearch(), login_fn=login or (lambda: None))
    return TestClient(web_app.create_app(runner)), runner


def _wait(runner: TaskRunner) -> None:
    deadline = time.time() + 5
    while runner.busy and time.time() < deadline:
        time.sleep(0.01)
    assert not runner.busy, "background task did not finish"


def _search(client, **form):
    data = {"department": "CS", "query": "machine learning", "earn": "2", "learn": "5", **form}
    return client.post("/search", data=data, headers=ORIGIN, follow_redirects=False)


# --- Search form --------------------------------------------------------------------


def test_index_renders_with_cs_preselected():
    client, _ = _client()
    page = client.get("/")
    assert page.status_code == 200
    assert '<option value="CS" selected>CS - Computer Science</option>' in page.text
    assert page.text.count("<option") == 211  # 210 departments + the divider
    assert 'name="query"' in page.text and 'name="earn"' in page.text and 'name="learn"' in page.text


def test_stylesheet_linked_and_served():
    client, _ = _client()
    page = client.get("/")
    assert "/static/style.css" in page.text
    css = client.get("/static/style.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


# --- Search -> status -> results -------------------------------------------------


def test_submit_then_results():
    search = FakeSearch()
    client, runner = _client(search)

    response = _search(client, refresh="on")
    assert response.status_code == 303
    assert response.headers["location"] == "/results"
    _wait(runner)

    call = search.calls[0]
    assert call["prefs"] == Preferences("CS", "machine learning", earn=2, learn=5)
    assert call["force_refresh"] is True
    assert call["provider"].llm_model == config.DEFAULT_LLM_MODEL

    page = client.get("/results")
    assert page.status_code == 200
    assert "ML research intern" in page.text
    assert "0.72" in page.text
    assert "Builds models." in page.text
    assert "aims:N26CS001" in page.text
    assert "16.5 h/week" in page.text
    assert "ITC: 1 job" in page.text
    assert "Computer Science" in page.text  # department name next to the prefs

    status = client.get("/api/status").json()
    assert status["status"] == "done" and status["kind"] == "search"
    assert status["login_needed"] is False


def test_form_values_are_cleaned():
    search = FakeSearch()
    client, runner = _client(search)
    _search(client, department="", query="  data  ", earn="9", learn="abc")
    _wait(runner)
    prefs = search.calls[0]["prefs"]
    assert prefs == Preferences(None, "data", earn=5, learn=3)
    assert search.calls[0]["force_refresh"] is False


def test_progress_shown_while_running_and_second_search_refused():
    search = FakeSearch()
    search.release.clear()  # block inside the search
    client, runner = _client(search)
    _search(client)
    try:
        deadline = time.time() + 5
        while not search.calls and time.time() < deadline:
            time.sleep(0.01)
        status = client.get("/api/status").json()
        assert status["status"] == "running"
        assert status["message"] == "Rating job levels: 1/2"
        page = client.get("/results")
        assert "Searching" in page.text and "Rating job levels: 1/2" in page.text
        assert "/api/status" in page.text  # the polling script

        _search(client, query="something else")  # busy: ignored
        assert runner.state.prefs.query == "machine learning"
        assert len(search.calls) == 1
    finally:
        search.release.set()
    _wait(runner)


def test_login_needed_banner_shown():
    client, runner = _client(FakeSearch(login_needed=True))
    _search(client)
    _wait(runner)
    assert client.get("/api/status").json()["login_needed"] is True
    page = client.get("/results")
    assert "AIMS login needed" in page.text
    assert 'action="/login"' in page.text


def test_search_error_shown():
    client, runner = _client(FakeSearch(error=RuntimeError("Ollama is not running")))
    _search(client)
    _wait(runner)
    status = client.get("/api/status").json()
    assert status["status"] == "error"
    assert "Ollama is not running" in status["error"]
    assert "Search failed" in client.get("/results").text


def test_results_before_any_search():
    client, _ = _client()
    assert client.get("/results").status_code == 200


def test_user_text_is_html_escaped():
    client, runner = _client()
    _search(client, query="<script>alert(1)</script>")
    _wait(runner)
    page = client.get("/results").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# --- Login --------------------------------------------------------------------------


def test_login_button_runs_login_and_clears_flag():
    logins = []
    client, runner = _client(FakeSearch(login_needed=True), login=lambda: logins.append(1))
    _search(client)
    _wait(runner)

    response = client.post("/login", headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/results"
    _wait(runner)
    assert logins == [1]
    status = client.get("/api/status").json()
    assert status["kind"] == "login" and status["status"] == "done"
    assert status["login_needed"] is False
    assert runner.state.results  # previous results kept during a login


def test_login_from_start_page_redirects_home():
    client, runner = _client()
    response = client.post("/login", headers=ORIGIN, follow_redirects=False)
    assert response.headers["location"] == "/"
    _wait(runner)


# --- CSRF ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/search", "/login", "/settings"])
def test_cross_site_posts_refused(path):
    logins, search = [], FakeSearch()
    client, runner = _client(search, login=lambda: logins.append(1))
    response = client.post(path, data={"query": "x", "ollama_url": "http://evil"}, headers=EVIL, follow_redirects=False)
    assert response.status_code == 403
    time.sleep(0.05)
    assert search.calls == [] and logins == []
    assert not config.SETTINGS_FILE.exists()


def test_post_without_origin_header_allowed():
    client, runner = _client()
    assert client.post("/search", data={"query": "x"}, follow_redirects=False).status_code == 303
    _wait(runner)


# --- Settings -----------------------------------------------------------------------


def test_settings_page_lists_installed_models(monkeypatch):
    monkeypatch.setattr(
        web_app.httpx, "get",
        lambda url, timeout: httpx.Response(200, json={"models": [{"name": "nomic-embed-text"}, {"name": "llama3.2:3b"}]}),
    )
    client, _ = _client()
    page = client.get("/settings")
    assert page.status_code == 200
    assert "llama3.2:3b" in page.text and "nomic-embed-text" in page.text
    assert "api_key" not in page.text.lower() and "api key" not in page.text.lower()  # owner decision: Ollama only


def test_settings_page_when_ollama_down(monkeypatch):
    def down(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(web_app.httpx, "get", down)
    client, _ = _client()
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Could not reach Ollama" in page.text


def test_settings_saved_and_blanks_use_defaults(monkeypatch):
    monkeypatch.setattr(web_app.httpx, "get", lambda url, timeout: httpx.Response(200, json={"models": []}))
    search = FakeSearch()
    client, runner = _client(search)
    response = client.post(
        "/settings",
        data={"ollama_url": " http://127.0.0.1:9999 ", "llm_model": "qwen2.5:7b", "embed_model": " "},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"

    saved = Settings.load()
    assert saved.ollama_url == "http://127.0.0.1:9999"
    assert saved.llm_model == "qwen2.5:7b"
    assert saved.embed_model == config.DEFAULT_EMBED_MODEL

    # The next search uses the saved settings.
    _search(client)
    _wait(runner)
    provider = search.calls[0]["provider"]
    assert (provider.base_url, provider.llm_model) == ("http://127.0.0.1:9999", "qwen2.5:7b")


def test_corrupt_settings_file_falls_back_to_defaults():
    config.SETTINGS_FILE.write_text("{not json")
    assert Settings.load() == Settings()
