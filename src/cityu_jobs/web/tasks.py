"""Runs the slow work (scraping, AI calls, the AIMS login window) in one background thread.

Playwright's sync API must not run on the web server's event loop, and a search can take
minutes, so the request only *starts* a task and the page polls `snapshot()` for progress.
Only one task runs at a time: a login and a scrape must never share the AIMS session.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from cityu_jobs import config
from cityu_jobs.core import pipeline
from cityu_jobs.core.auth import login
from cityu_jobs.core.pipeline import RefreshReport
from cityu_jobs.core.ranking import Preferences, RankedJob
from cityu_jobs.core.settings import Settings

log = logging.getLogger(__name__)

SearchFn = Callable[..., tuple[RefreshReport, list[RankedJob]]]


def headed_login() -> None:
    """Open a visible browser on this machine for the student to log in to AIMS."""
    with sync_playwright() as playwright:
        login(playwright)


@dataclass
class TaskState:
    kind: str | None = None   # "search" or "login"
    status: str = "idle"      # idle | running | done | error
    message: str = ""         # latest progress line
    error: str = ""
    started_at: float = 0.0
    prefs: Preferences | None = None
    report: RefreshReport | None = None
    results: list[RankedJob] = field(default_factory=list)
    login_needed: bool = False


class TaskRunner:
    def __init__(self, search_fn: SearchFn = pipeline.search, login_fn: Callable[[], None] = headed_login):
        self._search_fn = search_fn
        self._login_fn = login_fn
        self._lock = threading.Lock()
        self.state = TaskState()

    @property
    def busy(self) -> bool:
        return self.state.status == "running"

    def _progress(self, message: str) -> None:
        self.state.message = message

    def _start(self, kind: str, work: Callable[[], None], before: Callable[[], None] | None = None) -> bool:
        """Start `work` in a thread unless a task is already running. Returns False if busy.

        `before` runs under the lock, before the thread starts, so the next page load sees it.
        """
        with self._lock:
            if self.busy:
                return False
            if before:
                before()
            # Keep the previous results visible while a login runs.
            self.state.kind, self.state.status, self.state.error = kind, "running", ""
            self.state.message, self.state.started_at = "Starting...", time.time()

        def run() -> None:
            try:
                work()
                self.state.status = "done"
            except Exception as e:  # shown to the student instead of crashing the thread
                log.exception("%s task failed", kind)
                self.state.status, self.state.error = "error", f"{type(e).__name__}: {e}"

        threading.Thread(target=run, name=f"{kind}-task", daemon=True).start()
        return True

    def start_search(self, prefs: Preferences, settings: Settings, force_refresh: bool = False) -> bool:
        def work() -> None:
            report, results = self._search_fn(
                prefs, settings.provider(), force_refresh=force_refresh, progress=self._progress
            )
            self.state.report, self.state.results = report, results
            self.state.login_needed = report.login_needed
            self.state.message = f"Ranked {len(results)} open jobs."

        def reset() -> None:  # so /results never shows the new prefs next to old results
            self.state.prefs, self.state.report, self.state.results = prefs, None, []

        return self._start("search", work, before=reset)

    def start_login(self) -> bool:
        def work() -> None:
            minutes = config.LOGIN_TIMEOUT_MS / 60_000
            self._progress(f"Log in to AIMS in the browser window that just opened (up to {minutes:g} minutes)...")
            self._login_fn()
            self.state.login_needed = False
            self.state.message = "Logged in to AIMS. Run a search with “Refresh now” to fetch AIMS jobs."

        return self._start("login", work)

    def snapshot(self) -> dict:
        """JSON-safe status for the polling endpoint."""
        s = self.state
        return {
            "kind": s.kind,
            "status": s.status,
            "message": s.message,
            "error": s.error,
            "elapsed_s": round(time.time() - s.started_at) if s.started_at else 0,
            "login_needed": s.login_needed,
        }
