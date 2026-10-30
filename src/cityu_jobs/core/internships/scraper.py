"""Fetch tech internships from outside CityU into the cache, as `Job`s.

Two sources in the cache, by tier (see catalog.py):
- "intern_bigtech": big tech, e.g. Amazon, Microsoft, NVIDIA, TikTok, Stripe.
- "intern_tech": smaller tech / AI / fintech / quant firms, plus the SimplifyJobs list.

Companies are fetched in parallel (a few at a time); one company failing never stops the
others. Its previously cached jobs stay active, so a flaky board doesn't wipe them.
The same posting can appear twice (e.g. on Stripe's board and in the Simplify list); the
direct board wins, matched by URL or by company + title.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from cityu_jobs import config
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.internships.catalog import COMPANIES, TIER_SCHEMES, TIER_SOURCES
from cityu_jobs.core.internships.fetchers import FETCHERS, FetchError, Posting
from cityu_jobs.core.models import Job, compute_content_hash

log = logging.getLogger(__name__)

SOURCES = tuple(TIER_SOURCES.values())  # ("intern_bigtech", "intern_tech")
_PARALLEL = 6
_USER_AGENT = "cityu-jobs/0.2 (self-hosted student job recommender; read-only)"


def _norm_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?|[?#].*$|/$", "", (url or "").lower())


def _norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def to_job(posting: Posting) -> Job:
    raw = {
        "company": posting.company,
        "title": posting.title,
        "location": posting.location,
        "url": posting.url,
        "description": posting.description,
        "posted": posting.posted.isoformat() if posting.posted else None,
        "deadline": posting.deadline.isoformat() if posting.deadline else None,
        "terms": posting.terms,
        **{k: v for k, v in posting.extra.items() if v is not None},
    }
    return Job(
        job_id=f"intern:{posting.key}",
        source=TIER_SOURCES[posting.tier],
        scheme=TIER_SCHEMES[posting.tier],
        title=posting.title,
        department_code="TECH",
        department_name="Tech industry",
        job_nature="Internship",
        description=posting.description,
        closing_date=posting.deadline,
        employment_period=", ".join(posting.terms) or None,
        url=posting.url or None,
        company=posting.company,
        location=posting.location or None,
        raw=raw,
        content_hash=compute_content_hash(raw),
    )


def dedupe(postings: list[Posting]) -> list[Posting]:
    """Drop repeats; direct company boards are listed first, so they win over Simplify."""
    seen_urls, seen_titles, kept = set(), set(), []
    for p in sorted(postings, key=lambda p: p.key.startswith("simplify:")):
        url, title_key = _norm_url(p.url), (_norm_text(p.company), _norm_text(p.title), _norm_text(p.location))
        if (url and url in seen_urls) or title_key in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title_key)
        kept.append(p)
    return kept


def _fetch_one(client: httpx.Client, company: dict, regions) -> tuple[dict, list[Posting] | None, str]:
    try:
        return company, FETCHERS[company["provider"]](client, company, regions), ""
    except (FetchError, httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        log.warning("internships: %s failed: %s", company["name"], e)
        return company, None, str(e)[:160]


def scrape_internships(
    cache: Cache,
    regions,
    client: httpx.Client | None = None,
    companies: list[dict] | None = None,
    progress: Callable[[str], None] = lambda _m: None,
) -> list[str]:
    """Fetch every company's internships into `cache`; returns human-readable notes."""
    companies = companies if companies is not None else COMPANIES
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
    postings, failed, done = [], [], 0
    try:
        with ThreadPoolExecutor(max_workers=_PARALLEL) as pool:
            futures = [pool.submit(_fetch_one, client, c, regions) for c in companies]
            for future in as_completed(futures):  # report progress as each company finishes
                company, found, problem = future.result()
                done += 1
                progress(f"Fetching tech internships: {done}/{len(companies)} companies")
                if found is None:
                    failed.append((company, problem))
                else:
                    postings += found
    finally:
        if own_client:
            client.close()

    kept = dedupe(postings)
    seen: set[str] = set()
    for posting in kept:
        job = to_job(posting)
        cache.upsert_job(job)
        seen.add(job.job_id)

    # Keep the jobs of companies that failed this time: their boards may just be down.
    failed_names = {c["name"] for c, _ in failed}
    for entry in cache.data["jobs"].values():
        job = entry["job"]
        if job["source"] in SOURCES and entry.get("active") and job.get("company") in failed_names:
            seen.add(job["job_id"])
    if any(c["provider"] == "simplify" for c, _ in failed):  # Simplify jobs have many company names
        seen |= {e["job"]["job_id"] for e in cache.data["jobs"].values() if e["job"]["job_id"].startswith("intern:simplify:") and e.get("active")}
    for source in SOURCES:
        cache.mark_scrape_complete(source, seen)

    by_tier = Counter(p.tier for p in kept)
    top = Counter(p.company for p in kept if p.tier == "big").most_common(6)
    notes = [
        f"Tech internships ({', '.join(config.INTERN_REGIONS[r] for r in regions if r in config.INTERN_REGIONS)}): "
        f"{by_tier['big']} big tech, {by_tier['tech']} other tech companies"
        + (f" (big tech: {', '.join(f'{n} {k}' for n, k in top)})" if top else "")
    ]
    if failed:
        notes.append(
            f"Could not read {len(failed)} job board(s), kept their earlier jobs: "
            + "; ".join(f"{c['name']} ({p})" for c, p in failed[:5])
            + (" …" if len(failed) > 5 else "")
        )
    return notes
