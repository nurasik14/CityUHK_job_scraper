"""AIMS authentication: the student logs in themselves; we only keep the session cookies."""

from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, TimeoutError

from cityu_jobs import config

# AIMS shows this page title instead of the requested page when the session has expired.
_SESSION_TIMEOUT_TITLE = "Session Timeout"
# The scheme dropdown only exists on the job search page of a live session.
_JOB_SEARCH_MARKER = "select[name=p_copc_code]"


class AimsSessionExpired(RuntimeError):
    """The saved AIMS session no longer works; the student has to log in again."""


def _save_session(context: BrowserContext) -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    context.storage_state(path=config.SESSION_FILE)
    config.SESSION_FILE.chmod(0o600)  # readable only by the current user


def _has_logged_in_marker(page: Page, timeout_ms: float) -> bool:
    marker = page.get_by_text(config.AIMS_LOGGED_IN_MARKER).first
    try:
        marker.wait_for(state="visible", timeout=timeout_ms)
        return True
    except TimeoutError:
        return False


def login(playwright: Playwright) -> None:
    """Open a visible browser, wait for the student to log in, then save the session."""
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.AIMS_LOGIN_URL)
        minutes = config.LOGIN_TIMEOUT_MS / 60_000
        print(f"Log in to AIMS in the browser window (waiting up to {minutes:g} minutes)...")
        page.wait_for_url(config.AIMS_LOGGED_IN_URL_PATTERN, timeout=config.LOGIN_TIMEOUT_MS)
        # The main menu URL can also show the "User Login" page, so confirm the marker too.
        if not _has_logged_in_marker(page, timeout_ms=15_000):
            raise RuntimeError("Reached the AIMS main menu URL but not logged in.")
        _save_session(context)
    finally:
        browser.close()


def load_session(playwright: Playwright) -> tuple[Browser, BrowserContext]:
    """Start a hidden browser that reuses the saved session. Caller closes the browser."""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=config.SESSION_FILE)
    return browser, context


def ensure_not_timed_out(page: Page) -> None:
    """Raise AimsSessionExpired if AIMS answered with its "Session Timeout" page."""
    if _SESSION_TIMEOUT_TITLE.lower() in page.title().lower():
        raise AimsSessionExpired("AIMS session timed out; log in again.")


def ensure_job_search_page(page: Page) -> None:
    """Check that `page` really is the job search page, else raise AimsSessionExpired."""
    ensure_not_timed_out(page)
    if page.locator(_JOB_SEARCH_MARKER).count() == 0:
        raise AimsSessionExpired("AIMS job search page not available; log in again.")


def is_logged_in(context: BrowserContext) -> bool:
    """Open the job search page; the session is valid only if the scheme dropdown is there.

    The main menu is not enough: with a stale session it can still show "Student Services"
    while every other page answers "Session Timeout".
    """
    page = context.new_page()
    try:
        page.goto(config.AIMS_JOB_SEARCH_URL)
        ensure_job_search_page(page)
        return True
    except AimsSessionExpired:
        return False
    finally:
        page.close()


def ensure_session(playwright: Playwright, interactive: bool = True) -> tuple[Browser, BrowserContext]:
    """Return a logged-in hidden browser.

    If the saved session is missing or expired: with `interactive=True` a visible browser
    opens for the student to log in again; with `interactive=False` AimsSessionExpired is
    raised instead (the web app and agents use this, and show a "Log in to AIMS" button).
    """
    if config.SESSION_FILE.exists():
        browser, context = load_session(playwright)
        if is_logged_in(context):
            return browser, context
        browser.close()
        config.SESSION_FILE.unlink()  # expired cookies are useless; start fresh
        print("Saved AIMS session has expired.")

    if not interactive:
        raise AimsSessionExpired("No valid AIMS session; log in to AIMS first.")
    login(playwright)
    browser, context = load_session(playwright)
    if not is_logged_in(context):
        browser.close()
        raise RuntimeError("Login finished but the check page is still not accessible.")
    return browser, context


@contextmanager
def aims_session(playwright: Playwright, interactive: bool = True) -> Iterator[BrowserContext]:
    """One logged-in hidden browser for a whole scrape run, re-saving the session on exit.

    AIMS issues a new SESSID cookie on every page load, so the file saved at login goes
    stale as soon as we browse. Saving on exit lets the next run reuse the latest cookie.
    `interactive` is passed to ensure_session().
    """
    browser, context = ensure_session(playwright, interactive)
    try:
        yield context
    finally:
        _save_session(context)
        browser.close()
