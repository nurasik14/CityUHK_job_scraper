"""The whole flow, in two steps. Interfaces (web UI, later a CLI) only call this module.

1. `refresh()`  — scrape sources whose data is older than STALE_AFTER_HOURS into the cache.
2. `recommend()` — rate levels and embed jobs that need it (cache-aware), embed the query,
   and rank the open jobs.

`search()` runs both. Everything here is synchronous and may take minutes (scraping, local
LLM calls), so the web UI runs it in a background thread and shows `progress` messages.

The pipeline never opens a login window: if the AIMS session is missing or expired it
reports `login_needed` and carries on with whatever is cached. The student then presses
"Log in to AIMS" in the UI, which calls `auth.login()` explicitly.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from playwright.sync_api import sync_playwright

from cityu_jobs import config
from cityu_jobs.core.ai.providers import EmbeddingProvider, LLMProvider
from cityu_jobs.core.aims_scraper import SCHEMES, scrape_aims
from cityu_jobs.core.analysis import analyze_jobs
from cityu_jobs.core.auth import AimsSessionExpired, aims_session
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.embeddings import embed_jobs, embed_query, similarities
from cityu_jobs.core.itc_stem_scraper import SOURCE as ITC_SOURCE
from cityu_jobs.core.itc_stem_scraper import scrape_itc_stem
from cityu_jobs.core.ranking import Preferences, RankedJob, is_closed, rank

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
ALL_SOURCES = ("aims", "itc")
# Save the cache every this many AI results, so a crash or Ctrl+C keeps most of the work.
_SAVE_EVERY = 10


class AIProvider(LLMProvider, EmbeddingProvider, Protocol):
    """One object that can both rate jobs and embed text (OllamaProvider, FakeProvider)."""


@dataclass
class RefreshReport:
    """What `refresh()` did, for the UI."""

    scraped: list[str] = field(default_factory=list)  # sources scraped this time
    skipped: list[str] = field(default_factory=list)  # sources still fresh, not re-scraped
    notes: list[str] = field(default_factory=list)    # human-readable lines (counts, problems)
    login_needed: bool = False                       # AIMS session missing/expired


def _noop(_message: str) -> None:
    pass


def refresh(
    sources: tuple[str, ...] = ALL_SOURCES,
    force: bool = False,
    cache: Cache | None = None,
    progress: Progress = _noop,
    stale_after: timedelta = timedelta(hours=config.STALE_AFTER_HOURS),
) -> RefreshReport:
    """Re-scrape the given sources ("aims", "itc") if stale (or if `force`), then save the cache.

    A source is stale if its last *complete* scrape is older than `stale_after`.
    """
    cache = cache or Cache.load()
    report = RefreshReport()

    if "aims" in sources:
        keys = [k for k in SCHEMES if force or cache.is_stale(f"aims_{k}", stale_after)]
        report.skipped += [f"aims_{k}" for k in SCHEMES if k not in keys]
        if keys:
            progress("Opening AIMS...")
            try:
                with sync_playwright() as playwright, aims_session(playwright, interactive=False) as context:
                    for result in scrape_aims(context, cache, keys, progress=progress):
                        report.scraped.append(f"aims_{result.scheme_key}")
                        report.notes.append(
                            f"{SCHEMES[result.scheme_key]}: {result.listed} jobs "
                            f"({result.opened} new or changed, {len(result.errors)} errors)"
                        )
            except AimsSessionExpired:
                report.login_needed = True
                report.notes.append("AIMS login needed: showing AIMS jobs from the last successful scrape.")

    if "itc" in sources:
        if force or cache.is_stale(ITC_SOURCE, stale_after):
            progress("Fetching ITC STEM job lists...")
            before = cache.last_scraped_at(ITC_SOURCE)
            # Public pages: a plain hidden browser, no AIMS cookies.
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    report.notes += scrape_itc_stem(browser.new_context(), cache)
                finally:
                    browser.close()
            # scrape_itc_stem only marks the source complete if every college was fetched.
            if cache.last_scraped_at(ITC_SOURCE) != before:
                report.scraped.append(ITC_SOURCE)
        else:
            report.skipped.append(ITC_SOURCE)

    removed = cache.prune(date.today())
    if removed:
        report.notes.append(f"Removed {removed} jobs that closed over {config.CACHE_RETENTION_DAYS} days ago.")
    cache.save()
    return report


def recommend(
    prefs: Preferences,
    provider: AIProvider,
    cache: Cache | None = None,
    progress: Progress = _noop,
    today: date | None = None,
) -> list[RankedJob]:
    """Rank the cached, still-open jobs for `prefs`, best first (all of them; the UI shows the top N).

    Only open jobs are rated and embedded, so closed listings cost no AI calls.
    Raises ProviderError if Ollama is not reachable.
    """
    cache = cache or Cache.load()
    today = today or date.today()
    jobs = [j for j in cache.active_jobs() if not is_closed(j, today)]

    def rating_progress(done: int, total: int) -> None:
        progress(f"Rating job levels with {provider.llm_model}: {done}/{total}")
        if done % _SAVE_EVERY == 0:
            cache.save()

    levels = analyze_jobs(jobs, cache, provider, progress=rating_progress)
    cache.save()
    vectors = embed_jobs(
        jobs, cache, provider, progress=lambda done, total: progress(f"Embedding jobs: {done}/{total}")
    )
    cache.save()

    progress("Ranking...")
    sims = similarities(embed_query(prefs.query, provider), vectors) if prefs.query.strip() else {}
    ranked = rank(jobs, levels, sims, prefs, today)
    for r in ranked:
        r.level_reason = (cache.get_level(r.job.job_id, r.job.content_hash) or {}).get("reason", "")
    return ranked


def search(
    prefs: Preferences,
    provider: AIProvider,
    force_refresh: bool = False,
    progress: Progress = _noop,
) -> tuple[RefreshReport, list[RankedJob]]:
    """refresh() then recommend() on one shared cache: what the UI's search button does."""
    cache = Cache.load()
    report = refresh(force=force_refresh, cache=cache, progress=progress)
    return report, recommend(prefs, provider, cache=cache, progress=progress)
