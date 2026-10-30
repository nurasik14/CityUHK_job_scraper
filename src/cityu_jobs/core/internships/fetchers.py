"""One fetcher per job-board API. Each turns a company's postings into `Posting`s.

All requests go through one httpx.Client (injectable, so tests use a mock transport).
Only public, read-only endpoints are used: the same JSON the companies' own careers pages
load in the browser. Fetchers keep only internships (`INTERN_RE` on the title) in tech
roles (`TECH_RE`) and in the student's regions; detail pages are only opened for those,
which keeps the request count low.
"""

import html
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from cityu_jobs import config
from cityu_jobs.core.internships.regions import in_regions

INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|co-?op|placement|summer analyst|working student)\b", re.I)
TECH_RE = re.compile(
    r"software|engineer|developer|develop|data|machine learning|\bml\b|\bai\b|research|scien|quant|security|"
    r"cloud|architect|product|design|hardware|firmware|fpga|asic|\bsoc\b|\bdft\b|\bit\b|analytics|"
    r"algorithm|backend|back-end|frontend|front-end|full.?stack|mobile|\bios\b|android|devops|\bsre\b|"
    r"infrastructure|platform|systems|robotics|vision|\bnlp\b|\bllm|trading|trader|technolog|technical|"
    r"\btech\b|computer|program|cyber|network|automation|qa\b|test|blockchain|web3|smart contract",
    re.I,
)
# Titles that say outright they are not tech roles, e.g. Amazon's "【2028 Graduates・Non-tech】".
NON_TECH_RE = re.compile(r"non-?tech|business analyst|financial analyst|\bsales\b|marketing|recruit|\bhr\b|legal", re.I)
# TikTok's own job categories (top level or sub-category) that are tech roles.
_TIKTOK_TECH = {"R&D", "Product", "Design", "Data analysis", "IT support"}
# Workday and some boards only say "3 Locations" in the list; the detail page has the real ones.
_VAGUE_LOCATION = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)
# Upper bound on detail pages per company, so one huge board can't stall a refresh.
MAX_DETAILS_PER_COMPANY = 80


@dataclass
class Posting:
    key: str              # unique and stable across runs, e.g. "gh:stripe:4423330009"
    company: str
    title: str
    location: str
    url: str
    tier: str
    description: str = ""
    posted: date | None = None
    deadline: date | None = None
    terms: list[str] = field(default_factory=list)  # e.g. ["Summer 2027"] (Simplify)
    extra: dict = field(default_factory=dict)       # source-specific bits worth keeping


class FetchError(RuntimeError):
    """A company's board could not be read; the scraper notes it and keeps that company's old jobs."""


def html_to_text(markup: str | None) -> str:
    """HTML (sometimes entity-escaped twice, as Greenhouse sends it) -> readable plain text."""
    if not markup:
        return ""
    text = BeautifulSoup(html.unescape(markup), "html.parser").get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[: config.INTERN_MAX_DESCRIPTION_CHARS]


def _date_from_iso(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _date_from_epoch(value, ms: bool = False) -> date | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000 if ms else value, tz=timezone.utc).date()


def wanted(title: str, location: str, regions) -> bool:
    """An internship, in a tech role, in one of the student's regions."""
    return bool(INTERN_RE.search(title)) and bool(TECH_RE.search(title)) and not NON_TECH_RE.search(title) and (
        _VAGUE_LOCATION.match(location or "") is not None or in_regions(location, regions)
    )


def _request(client: httpx.Client, method: str, url: str, **kwargs) -> Any:
    """One JSON request. "Too many requests" (429) / 503 wait and retry twice, honouring Retry-After."""
    for attempt in range(3):
        try:
            response = client.request(method, url, timeout=config.INTERN_TIMEOUT_S, **kwargs)
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__} for {url}") from e
        if response.status_code in (429, 503) and attempt < 2:
            retry_after = response.headers.get("retry-after", "")
            time.sleep(min(float(retry_after), 30) if retry_after.isdigit() else 5 * (attempt + 1))
            continue
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code} for {url}")
        try:
            return response.json()
        except ValueError as e:
            raise FetchError(f"not JSON: {url}") from e
    raise FetchError(f"gave up on {url}")  # pragma: no cover (the loop always returns or raises)


def _get(client: httpx.Client, url: str, **kwargs) -> Any:
    return _request(client, "GET", url, **kwargs)


def _post(client: httpx.Client, url: str, body: dict, **kwargs) -> Any:
    return _request(client, "POST", url, json=body, **kwargs)


def _pause() -> None:
    time.sleep(config.INTERN_DELAY_S)


# --- Public job-board providers -----------------------------------------------------------


def fetch_greenhouse(client: httpx.Client, company: dict, regions) -> list[Posting]:
    slug = company["slug"]
    base = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    listing = _get(client, base)
    matches = [j for j in listing.get("jobs", []) if wanted(j.get("title", ""), (j.get("location") or {}).get("name", ""), regions)]
    postings = []
    for job in matches[:MAX_DETAILS_PER_COMPANY]:
        _pause()
        detail = _get(client, f"{base}/{job['id']}")
        postings.append(
            Posting(
                key=f"gh:{slug}:{job['id']}",
                company=company["name"],
                title=detail.get("title", job.get("title", "")).strip(),
                location=(detail.get("location") or {}).get("name", ""),
                url=detail.get("absolute_url") or job.get("absolute_url", ""),
                tier=company["tier"],
                description=html_to_text(detail.get("content")),
                posted=_date_from_iso(detail.get("first_published") or detail.get("updated_at")),
                deadline=_date_from_iso(detail.get("application_deadline")),
            )
        )
    return postings


def fetch_lever(client: httpx.Client, company: dict, regions) -> list[Posting]:
    slug = company["slug"]
    postings = []
    for job in _get(client, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}):
        categories = job.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location", "")]
        location = "; ".join(loc for loc in locations if loc)
        if not wanted(job.get("text", ""), location, regions):
            continue
        sections = "\n\n".join(
            f"{item.get('text', '')}\n{html_to_text(item.get('content'))}" for item in job.get("lists") or []
        )
        description = "\n\n".join(p for p in [job.get("descriptionPlain", ""), sections, job.get("additionalPlain", "")] if p)
        postings.append(
            Posting(
                key=f"lever:{slug}:{job['id']}",
                company=company["name"],
                title=job.get("text", "").strip(),
                location=location,
                url=job.get("hostedUrl", ""),
                tier=company["tier"],
                description=description.strip()[: config.INTERN_MAX_DESCRIPTION_CHARS],
                posted=_date_from_epoch(job.get("createdAt"), ms=True),
            )
        )
    return postings


def fetch_ashby(client: httpx.Client, company: dict, regions) -> list[Posting]:
    slug = company["slug"]
    data = _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    postings = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        places = [job.get("location", "")] + [s.get("location", "") for s in job.get("secondaryLocations") or []]
        if job.get("isRemote") and not any("remote" in p.lower() for p in places):
            places.append("Remote")
        location = "; ".join(p for p in places if p)
        if not wanted(job.get("title", ""), location, regions):
            continue
        postings.append(
            Posting(
                key=f"ashby:{slug}:{job.get('id') or job.get('jobUrl')}",
                company=company["name"],
                title=job.get("title", "").strip(),
                location=location,
                url=job.get("jobUrl") or job.get("applyUrl", ""),
                tier=company["tier"],
                description=(job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")))[
                    : config.INTERN_MAX_DESCRIPTION_CHARS
                ],
                posted=_date_from_iso(job.get("publishedAt")),
            )
        )
    return postings


def fetch_smartrecruiters(client: httpx.Client, company: dict, regions) -> list[Posting]:
    slug = company["slug"]
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    matches, offset = [], 0
    while True:
        page = _get(client, base, params={"q": "intern", "limit": 100, "offset": offset})
        for job in page.get("content", []):
            loc = job.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(p for p in [loc.get("city"), loc.get("country")] if p)
            if loc.get("remote"):
                location += " (Remote)"
            if wanted(job.get("name", ""), location, regions):
                matches.append((job, location))
        offset += 100
        if offset >= page.get("totalFound", 0) or offset >= 500:
            break
        _pause()
    postings = []
    for job, location in matches[:MAX_DETAILS_PER_COMPANY]:
        _pause()
        detail = _get(client, f"{base}/{job['id']}")
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        description = "\n\n".join(
            html_to_text((sections.get(k) or {}).get("text"))
            for k in ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
        )
        postings.append(
            Posting(
                key=f"sr:{slug}:{job['id']}",
                company=company["name"],
                title=job.get("name", "").strip(),
                location=location,
                url=detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{job['id']}",
                tier=company["tier"],
                description=description.strip()[: config.INTERN_MAX_DESCRIPTION_CHARS],
                posted=_date_from_iso(job.get("releasedDate")),
            )
        )
    return postings


def fetch_workday(client: httpx.Client, company: dict, regions) -> list[Posting]:
    host_part, site = company["site"].split("/", 1)
    tenant = host_part.split(".")[0]
    root = f"https://{host_part}.myworkdayjobs.com"
    api = f"{root}/wday/cxs/{tenant}/{site}"
    candidates, offset, total = [], 0, None
    while total is None or offset < min(total, 400):
        page = _post(client, f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "intern"})
        total = page.get("total", 0) if total is None else total
        for job in page.get("jobPostings", []):
            if wanted(job.get("title", ""), job.get("locationsText", ""), regions):
                candidates.append(job)
        offset += 20
        if not page.get("jobPostings"):
            break
        _pause()
    postings = []
    for job in candidates[:MAX_DETAILS_PER_COMPANY]:
        _pause()
        info = _get(client, f"{api}{job['externalPath']}").get("jobPostingInfo", {})
        location = "; ".join(p for p in [info.get("location", "")] + list(info.get("additionalLocations") or []) if p)
        country = (info.get("country") or {}).get("descriptor", "")
        if country and country not in location:
            location = f"{location} ({country})" if location else country
        if not in_regions(location, regions):
            continue  # the list only said "3 Locations"; none of them is in the student's regions
        postings.append(
            Posting(
                key=f"wd:{tenant}:{info.get('jobReqId') or job['externalPath']}",
                company=company["name"],
                title=info.get("title", job.get("title", "")).strip(),
                location=location,
                url=info.get("externalUrl") or f"{root}/{site}{job['externalPath']}",
                tier=company["tier"],
                description=html_to_text(info.get("jobDescription")),
            )
        )
    return postings


# --- Big tech careers APIs ---------------------------------------------------------------


def fetch_amazon(client: httpx.Client, company: dict, regions) -> list[Posting]:
    postings, offset = [], 0
    while True:
        page = _get(
            client,
            "https://www.amazon.jobs/en/search.json",
            params={"base_query": "intern", "result_limit": 100, "offset": offset, "sort": "recent"},
        )
        for job in page.get("jobs", []):
            location = job.get("normalized_location") or job.get("location", "")
            if not wanted(job.get("title", ""), location, regions):
                continue
            text = "\n\n".join(
                p for p in [
                    html_to_text(job.get("description")),
                    "Basic qualifications:\n" + html_to_text(job.get("basic_qualifications")),
                    "Preferred qualifications:\n" + html_to_text(job.get("preferred_qualifications")),
                ] if p.strip()
            )
            posted = None
            try:
                posted = datetime.strptime(job.get("posted_date", ""), "%B %d, %Y").date()
            except ValueError:
                pass
            postings.append(
                Posting(
                    key=f"amazon:{job.get('id_icims') or job.get('id')}",
                    company="Amazon",
                    title=job.get("title", "").strip(),
                    location=location,
                    url=f"https://www.amazon.jobs{job.get('job_path', '')}",
                    tier=company["tier"],
                    description=text[: config.INTERN_MAX_DESCRIPTION_CHARS],
                    posted=posted,
                )
            )
        offset += 100
        if offset >= min(page.get("hits", 0), 1000) or not page.get("jobs"):
            break
        _pause()
    return postings


def fetch_microsoft(client: httpx.Client, company: dict, regions) -> list[Posting]:
    base = "https://apply.careers.microsoft.com/api/pcsx"
    candidates, start = [], 0
    while True:
        data = _get(client, f"{base}/search", params={"domain": "microsoft.com", "query": "intern", "start": start})["data"]
        positions = data.get("positions") or []
        for job in positions:
            location = "; ".join(job.get("locations") or [])
            if wanted(job.get("name", ""), location, regions):
                candidates.append((job, location))
        start += len(positions)
        if not positions or start >= min(data.get("count", 0), 400):
            break
        _pause()
        _pause()  # Microsoft rate-limits quickly
    postings = []
    for job, location in candidates[:MAX_DETAILS_PER_COMPANY]:
        _pause()
        detail = _get(
            client, f"{base}/position_details", params={"position_id": job["id"], "domain": "microsoft.com", "hl": "en"}
        ).get("data", {})
        postings.append(
            Posting(
                key=f"microsoft:{job.get('displayJobId') or job['id']}",
                company="Microsoft",
                title=job.get("name", "").strip(),
                location=location,
                url=f"https://apply.careers.microsoft.com{job.get('positionUrl', '')}",
                tier=company["tier"],
                description=html_to_text(detail.get("jobDescription")),
                posted=_date_from_epoch(job.get("postedTs")),
                extra={"department": job.get("department")},
            )
        )
    return postings


def _tiktok_location(city: dict | None) -> str:
    names = []
    while city:
        names.append(city.get("en_name") or "")
        city = city.get("parent")
    # City -> state -> country; drop repeats such as "Singapore, Singapore, Singapore".
    return ", ".join(dict.fromkeys(n for n in names if n))


def fetch_tiktok(client: httpx.Client, company: dict, regions) -> list[Posting]:
    url = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
    postings, offset = [], 0
    while True:
        data = _post(client, url, {"keyword": "intern", "limit": 100, "offset": offset}, headers={"website-path": "tiktok"})
        page = (data.get("data") or {}).get("job_post_list") or []
        for job in page:
            location = _tiktok_location(job.get("city_info"))
            category = job.get("job_category") or {}
            names = {category.get("en_name"), (category.get("parent") or {}).get("en_name")}
            if not (names & _TIKTOK_TECH) or not wanted(job.get("title", ""), location, regions):
                continue
            postings.append(
                Posting(
                    key=f"tiktok:{job['id']}",
                    company="TikTok",
                    title=job.get("title", "").strip(),
                    location=location,
                    url=f"https://lifeattiktok.com/search/{job['id']}",
                    tier=company["tier"],
                    description=f"{job.get('description', '')}\n\nRequirements:\n{job.get('requirement', '')}".strip()[
                        : config.INTERN_MAX_DESCRIPTION_CHARS
                    ],
                )
            )
        offset += 100
        if not page or offset >= min((data.get("data") or {}).get("count", 0), 4000):
            break
        _pause()
    return postings


SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


def fetch_simplify(client: httpx.Client, company: dict, regions, big_names: set[str] | None = None) -> list[Posting]:
    from cityu_jobs.core.internships.catalog import BIG_TECH_NAMES

    big_names = big_names or BIG_TECH_NAMES
    postings = []
    for job in _get(client, SIMPLIFY_URL):
        if not job.get("active") or not job.get("is_visible", True):
            continue
        location = "; ".join(job.get("locations") or [])
        title = job.get("title", "")
        # Every entry is an internship (the list is internships only), so no INTERN_RE check.
        if not in_regions(location, regions):
            continue
        terms = [t for t in job.get("terms") or [] if t and t != "N/A"]
        name = job.get("company_name", "").strip()
        lines = [
            f"{job.get('category') or 'Tech'} internship at {name}.",
            f"Terms: {', '.join(terms)}." if terms else "",
            f"Degrees: {', '.join(job.get('degrees') or [])}." if job.get("degrees") else "",
            f"Visa sponsorship: {job['sponsorship']}." if job.get("sponsorship") else "",
        ]
        postings.append(
            Posting(
                key=f"simplify:{job['id']}",
                company=name,
                title=title.strip(),
                location=location,
                url=job.get("url", ""),
                tier="big" if name.lower() in big_names else company["tier"],
                description="\n".join(line for line in lines if line),
                posted=_date_from_epoch(job.get("date_posted")),
                terms=terms,
                extra={"category": job.get("category"), "sponsorship": job.get("sponsorship")},
            )
        )
    return postings


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "amazon": fetch_amazon,
    "microsoft": fetch_microsoft,
    "tiktok": fetch_tiktok,
    "simplify": fetch_simplify,
}
