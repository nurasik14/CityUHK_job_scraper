"""AIMS session checks with fake Playwright objects: no browser, no network, no login window.

Covers the T7 fix (a stale session can still show the main menu, so the job search page is
checked) and T8's interactive=False rule (agents and the web app never open a login window).
"""

import pytest

from cityu_jobs import config
from cityu_jobs.core import auth
from cityu_jobs.core.auth import AimsSessionExpired


class FakePage:
    """Just enough of a Playwright Page: a title and whether the scheme dropdown exists."""

    def __init__(self, title="Job Search", has_dropdown=True):
        self._title, self._has_dropdown = title, has_dropdown
        self.visited = []

    def title(self):
        return self._title

    def locator(self, selector):
        assert selector == "select[name=p_copc_code]"
        count = 1 if self._has_dropdown else 0
        return type("L", (), {"count": lambda self: count})()

    def goto(self, url):
        self.visited.append(url)

    def close(self):
        pass


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.saved_to = None

    def new_page(self):
        return self.page

    def storage_state(self, path):
        self.saved_to = path
        path.write_text("{}")


class FakePlaywright:
    def __init__(self, page):
        self.launches = []
        page_ = page

        class Browser:
            closed = False

            def new_context(self, storage_state=None):
                return FakeContext(page_)

            def close(self):
                Browser.closed = True

        outer = self

        class Chromium:
            def launch(self, headless=True):
                outer.launches.append(headless)
                return Browser()

        self.chromium = Chromium()
        self.Browser = Browser


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "aims_session.json")
    return config.SESSION_FILE


def test_timeout_page_raises():
    with pytest.raises(AimsSessionExpired):
        auth.ensure_not_timed_out(FakePage(title="Session Timeout"))
    auth.ensure_not_timed_out(FakePage(title="Job Search"))  # no error


def test_job_search_page_needs_dropdown():
    with pytest.raises(AimsSessionExpired):
        auth.ensure_job_search_page(FakePage(has_dropdown=False))  # e.g. the "User Login" page
    auth.ensure_job_search_page(FakePage())


def test_is_logged_in_checks_job_search_page_not_main_menu():
    page = FakePage()
    assert auth.is_logged_in(FakeContext(page))
    assert page.visited == [config.AIMS_JOB_SEARCH_URL]
    assert not auth.is_logged_in(FakeContext(FakePage(title="Session Timeout")))


def test_non_interactive_without_session_file_raises_and_opens_no_browser(session_file, monkeypatch):
    monkeypatch.setattr(auth, "login", lambda pw: pytest.fail("must not open the login window"))
    playwright = FakePlaywright(FakePage())
    with pytest.raises(AimsSessionExpired):
        auth.ensure_session(playwright, interactive=False)
    assert playwright.launches == []


def test_non_interactive_expired_session_raises_and_deletes_file(session_file, monkeypatch):
    monkeypatch.setattr(auth, "login", lambda pw: pytest.fail("must not open the login window"))
    session_file.write_text("{}")
    playwright = FakePlaywright(FakePage(title="Session Timeout"))
    with pytest.raises(AimsSessionExpired):
        with auth.aims_session(playwright, interactive=False):
            pass
    assert playwright.launches == [True]  # only the hidden check browser
    assert not session_file.exists()


def test_valid_session_is_resaved_on_exit(session_file):
    session_file.write_text("old")
    playwright = FakePlaywright(FakePage())
    with auth.aims_session(playwright, interactive=False) as context:
        assert isinstance(context, FakeContext)
    assert session_file.read_text() == "{}"  # re-saved with the latest cookies
    assert session_file.stat().st_mode & 0o777 == 0o600
    assert playwright.Browser.closed


def test_interactive_calls_login_when_missing(session_file, monkeypatch):
    logins = []

    def fake_login(pw):
        logins.append(pw)
        session_file.write_text("{}")

    monkeypatch.setattr(auth, "login", fake_login)
    playwright = FakePlaywright(FakePage())
    browser, context = auth.ensure_session(playwright, interactive=True)
    assert logins == [playwright]
