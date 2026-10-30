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
from cityu_jobs.core.ai.providers import EmbeddingProvider, LLMProvider, ProviderError
from cityu_jobs.core.profile import Profile
from cityu_jobs.core.aims_scraper import SCHEMES, scrape_aims
from cityu_jobs.core.analysis import analyze_jobs
from cityu_jobs.core.auth import AimsSessionExpired, aims_session
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.dates import job_timeline
from cityu_jobs.core.embeddings import embed_jobs, embed_query, similarities
from cityu_jobs.core.fit import assess_fit, can_assess
from cityu_jobs.core.itc_stem_scraper import manual_files
from cityu_jobs.core.internships.scraper import SOURCES as INTERN_SOURCES
from cityu_jobs.core.internships.scraper import scrape_internships
from cityu_jobs.core.itc_stem_scraper import SOURCE as ITC_SOURCE
from cityu_jobs.core.itc_stem_scraper import scrape_itc_stem
from cityu_jobs.core.ranking import Preferences, RankedJob, is_closed, rank

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
ALL_SOURCES = ("aims", "itc", "intern")
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
    intern_regions: list[str] | None = None,
) -> RefreshReport:
    """Re-scrape the given sources ("aims", "itc", "intern") if stale (or if `force`), then save the cache.

    A source is stale if its last *complete* scrape is older than `stale_after`.
    `intern_regions` defaults to the student's choice on the Settings page.
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
        if force or cache.is_stale(ITC_SOURCE, stale_after) or _new_itc_file(cache):
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

    if "intern" in sources:
        if force or any(cache.is_stale(s, stale_after) for s in INTERN_SOURCES):
            progress("Fetching tech internships...")
            if intern_regions is None:
                from cityu_jobs.core.settings import Settings  # here, so tests can run without a settings file

                intern_regions = Settings.load().intern_regions
            report.notes += scrape_internships(cache, intern_regions, progress=progress)
            report.scraped += list(INTERN_SOURCES)
            cache.save()  # the next step (AI rating) can take a while
        else:
            report.skipped += list(INTERN_SOURCES)

    removed = cache.prune(date.today())
    if removed:
        report.notes.append(f"Removed {removed} jobs that closed over {config.CACHE_RETENTION_DAYS} days ago.")
    cache.save()
    return report


def _new_itc_file(cache: Cache) -> bool:
    """A spreadsheet was saved in .data/itc/ (e.g. uploaded on the ITC page) after the last ITC scrape."""
    last = cache.last_scraped_at(ITC_SOURCE)
    files = [f for s in config.ITC_STEM_SOURCES if s.get("enabled") for f in manual_files(s["college"])]
    return last is not None and any(f.stat().st_mtime > last.timestamp() for f in files)


# Groups whose top results each get a fit estimate, so every results tab has them.
FIT_GROUPS = (
    ("aims_cis", "aims_sh_summer", "aims_sh_regular", "itc_stem"),
    tuple(INTERN_SOURCES[:1]),
    tuple(INTERN_SOURCES[1:]),
)


def fit_candidates(ranked: list[RankedJob], top_n: int = config.FIT_TOP_N) -> list[RankedJob]:
    """Top N overall plus the top N of each group, best first, without repeats."""
    picked = {id(r): r for r in ranked[:top_n]}
    for group in FIT_GROUPS:
        for r in [r for r in ranked if r.job.source in group][:top_n]:
            picked.setdefault(id(r), r)
    return sorted(picked.values(), key=lambda r: ranked.index(r))


def recommend(
    prefs: Preferences,
    provider: AIProvider,
    cache: Cache | None = None,
    progress: Progress = _noop,
    today: date | None = None,
    profile: Profile | None = None,
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
        cached = cache.get_level(r.job.job_id, r.job.content_hash) or {}
        r.level_reason = cached.get("reason", "")
        r.skills = list(cached.get("skills") or [])
        r.timeline = job_timeline(r.job, cached.get("dates"))

    # "Difficulty for you": needs a profile and Claude. A failure here never loses the ranking.
    profile = profile if profile is not None else Profile.load()
    if not profile.is_empty() and can_assess(provider):
        candidates = fit_candidates(ranked)
        try:
            fits = assess_fit(
                [r.job for r in candidates], cache, provider, profile,
                progress=lambda done, total: progress(f"Estimating difficulty for you: {done}/{total}"),
            )
        except ProviderError as e:
            log.warning("fit estimates skipped: %s", e)
            fits = {}
        cache.save()
        for r in candidates:
            r.fit = fits.get(r.job.job_id)
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
