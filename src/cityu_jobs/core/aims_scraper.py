"""Scrape Campus Internship Scheme / Student Helper jobs from AIMS.

Fetching (needs a live, logged-in page) is kept separate from parsing (pure HTML -> dict),
so the parsers can be tested offline against saved pages.

Safety: the job search page also has an "apply by job number" form (hwosjpstapp_cityu).
This module only ever uses the search form and links inside the results table.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag
from playwright.sync_api import BrowserContext, Page

from cityu_jobs import config
from cityu_jobs.core.auth import AimsSessionExpired, ensure_job_search_page, ensure_not_timed_out
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job, compute_content_hash

log = logging.getLogger(__name__)

SCHEMES = {
    "cis": "Campus Internship Scheme",
    "sh_summer": "Student Helpers (Summer Term)",
    "sh_regular": "Student Helpers (Regular Term)",
}

_RESULTS_TABLE = "table:has(a[href*='p_rowid'])"

# Bump when parse_job_detail()/aims_to_job() change, so every job's detail page is re-opened
# and re-parsed once instead of keeping data from the old parser (see summary_hash()).
PARSER_VERSION = 1
# Save the cache every this many opened detail pages (~17 s each), so Ctrl+C or a crash
# loses at most a few minutes of work.
SAVE_EVERY = 5


# --- Fetching -----------------------------------------------------------------

def search_jobs(page: Page, scheme: str) -> list[dict]:
    """Run the job search for one scheme (a SCHEMES value) and return the result rows."""
    page.goto(config.AIMS_JOB_SEARCH_URL)
    ensure_job_search_page(page)  # raises AimsSessionExpired on a "Session Timeout" page
    search_form = page.locator("form:has(select[name=p_copc_code])")
    search_form.locator("select[name=p_copc_code]").select_option(label=scheme)
    search_form.locator("input[type=submit]").click()
    page.wait_for_load_state()
    ensure_not_timed_out(page)
    return parse_results(page.content())


def open_job(page: Page, job_no: str) -> str:
    """From the results page, open one job's description and return its HTML."""
    row = page.locator(_RESULTS_TABLE).last.locator("tr").filter(has_text=job_no)
    # The link submits a form via JavaScript; wait for that navigation, not the current page.
    with page.expect_navigation():
        row.locator("a[href*='p_rowid']").click()
    ensure_not_timed_out(page)
    return page.content()


# --- Parsing ------------------------------------------------------------------

def _text(cell: Tag) -> str:
    """Cell text with line breaks kept and runs of spaces collapsed."""
    lines = (" ".join(line.split()) for line in cell.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def _label(cell: Tag) -> str:
    """Header cell text without the trailing colon: <th>Closing Date:</th> -> "Closing Date"."""
    return _text(cell).rstrip(":").strip()


def parse_matched_count(html: str) -> int | None:
    """The "No. of matched record: 45" number above the results table, if present."""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    match = re.search(r"matched\s+records?\s*:\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_results(html: str) -> list[dict]:
    """Results table -> one dict per job, keyed by the table's column headers.

    A search with no matching jobs has no results table and gives [].
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = [t for t in soup.find_all("table") if t.select_one("a[href*='p_rowid']")]
    if not tables:
        return []
    table = tables[-1]
    header, *rows = table.find_all("tr", recursive=False) or table.find_all("tr")
    columns = [" ".join(_text(c).split()) for c in header.find_all(["th", "td"])]
    jobs = []
    for tr in rows:
        cells = tr.find_all(["th", "td"])
        if len(cells) != len(columns):
            continue
        # First column is just the row number.
        jobs.append({col: _text(cell) for col, cell in zip(columns[1:], cells[1:])})
    return jobs


def _parse_label_value_table(table: Tag, fields: dict) -> bool:
    """Collect <th>label</th><td>value</td> pairs, including a label row followed by a value row.

    Example: <tr><th>Closing Date:</th><td>25-Oct-2026</td></tr> -> fields["Closing Date"] = "25-Oct-2026".
    Returns True if the table had at least one pair (i.e. it was a label/value table).
    """
    found = False
    rows = table.find_all("tr")
    for i, tr in enumerate(rows):
        for th in tr.find_all("th"):
            td = th.find_next_sibling("td")
            if td is None and i + 1 < len(rows) and len(tr.find_all("th")) == 1:
                td = rows[i + 1].find("td")
            if td is not None and _label(th):
                fields[_label(th)] = _text(td)
                found = True
    return found


def _parse_training_plan(table: Tag) -> list[dict]:
    header, *rows = table.find_all("tr")
    columns = [_label(c) for c in header.find_all(["th", "td"])]
    return [
        {col: _text(c) for col, c in zip(columns[1:], tr.find_all("td")[1:])}
        for tr in rows
        if tr.find_all("td")
    ]


def _parse_evaluation(table: Tag) -> list[dict]:
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) >= 3:
            rows.append({"Type": _text(cells[1]), "Date": _text(cells[2])})
    return rows


def _parse_professional_development(table: Tag) -> dict:
    """Grid of skills x importance levels with an X in one column -> {category: {skill: level}}."""
    header, *rows = table.find_all("tr")
    levels = [" ".join(_text(c).split()) for c in header.find_all("th")]
    result: dict[str, dict[str, str]] = {}
    category = "General"
    for tr in rows:
        th = tr.find("th")
        if th is not None and _text(th):
            category = _text(th)
            continue
        cells = tr.find_all("td")
        if not cells or not _text(cells[0]):
            continue
        marked = [levels[i] for i, c in enumerate(cells) if i > 0 and _text(c).upper() == "X"]
        result.setdefault(category, {})[_text(cells[0])] = marked[0] if marked else ""
    return result


def parse_job_detail(html: str) -> dict:
    """Job description page -> dict of fields, text sections and tables.

    Keys are AIMS's own labels for now; a shared format with ITC STEM comes later.
    The page body is a flat run of headings (<p>/<b>) each followed by a table.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", class_="body")
    if body is None:
        raise ValueError("not an AIMS job description page (no <div class='body'>)")
    job: dict = {
        "scheme": None,
        "fields": {},
        "sections": {},
        "learning_objectives": [],
        "training_plan": [],
        "evaluation": [],
        "professional_development": {},
    }
    heading = None
    for el in body.find_all(recursive=False):
        if el.name in ("p", "b") and _text(el):
            heading = _text(el)
            if job["scheme"] is None:
                job["scheme"] = heading  # first heading names the scheme
            continue
        if el.name != "table":
            continue

        if heading == "Intended Learning Objectives/Outcomes":
            for tr in el.find_all("tr"):
                cells = tr.find_all("td")
                if len(cells) >= 2 and _text(cells[1]):
                    job["learning_objectives"].append(_text(cells[1]))
        elif heading == "Training Plan":
            job["training_plan"] = _parse_training_plan(el)
        elif heading == "Evaluation":
            job["evaluation"] = _parse_evaluation(el)
        elif heading == "Professional Development" and not job["professional_development"]:
            job["professional_development"] = _parse_professional_development(el)
        elif not _parse_label_value_table(el, job["fields"]) and heading and _text(el):
            job["sections"][heading] = _text(el)  # free-text section, e.g. Project Description
    return job


# --- Mapping to the common Job schema -----------------------------------------

# Labelled requirement fields, in the order they are joined into Job.requirements.
_REQUIREMENT_FIELDS = [
    "Preferred Academic Disciplines",
    "Computer Skills",
    "Language Skills",
    "Other Skills",
    "Other Requirements",
]


def _clean(value: str | None) -> str | None:
    """Empty strings and AIMS's "N/A" placeholder both mean "no value"."""
    if value is None:
        return None
    value = value.strip()
    return None if value.upper() in ("", "N/A", "NA", "-") else value


def _float(value: str | None) -> float | None:
    try:
        return float(_clean(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: str | None) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _date(value: str | None) -> date | None:
    """AIMS dates look like "25-Oct-2026"."""
    try:
        return datetime.strptime(_clean(value), "%d-%b-%Y").date()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _split_department(value: str | None) -> tuple[str | None, str | None]:
    """ "MA -- Mathematics" -> ("MA", "Mathematics"); a bare "MA" -> ("MA", None)."""
    value = _clean(value)
    if value is None:
        return None, None
    code, sep, name = value.partition("--")
    return code.strip() or None, (name.strip() or None) if sep else None


def summary_hash(summary: dict) -> str:
    """Fingerprint of one results-table row (+ PARSER_VERSION). Unchanged -> no need to reopen the job."""
    return compute_content_hash({"_parser_version": PARSER_VERSION, **summary})


def aims_to_job(summary: dict, detail: dict, scheme_key: str) -> Job:
    """Combine a results-table row and its parsed detail page into a common `Job`.

    `scheme_key` is a SCHEMES key ("cis", "sh_summer", "sh_regular"). Detail-page values
    are preferred; the results row fills gaps (its column names differ slightly).
    """
    fields = detail.get("fields", {})

    def pick(detail_label: str, summary_label: str) -> str | None:
        return _clean(fields.get(detail_label)) or _clean(summary.get(summary_label))

    code, name = _split_department(fields.get("Recruiting Dept/Unit"))
    code = code or _clean(summary.get("Recruit. Dept"))

    # Description: project title, every free-text section, then the learning objectives.
    # Student Helper pages may name their sections differently, so all sections are used.
    description_parts = [_clean(fields.get("Project Title"))]
    description_parts += [f"{heading}:\n{text}" for heading, text in detail.get("sections", {}).items()]
    sections_text = "\n".join(detail.get("sections", {}).values())
    # Staff often paste the job description as the objective too; don't repeat it.
    description_parts += [o for o in detail.get("learning_objectives", []) if o not in sections_text]

    requirement_parts = [
        f"{label}: {_clean(fields.get(label))}" for label in _REQUIREMENT_FIELDS if _clean(fields.get(label))
    ]

    part_time_text = pick("Part-time/Full-time", "Part-time/ Full-time")
    raw = {"summary": summary, **detail}
    job_no = pick("Job no.", "Job No.")
    return Job(
        job_id=f"aims:{job_no}",
        source=f"aims_{scheme_key}",
        scheme=detail.get("scheme") or SCHEMES[scheme_key],
        title=pick("Brief Job Description", "Brief Job Description") or "",
        department_code=code,
        department_name=name,
        job_nature=_clean(fields.get("Job Nature")),
        description="\n\n".join(p for p in description_parts if p),
        requirements="\n".join(requirement_parts),
        part_time=None if part_time_text is None else part_time_text.lower().startswith("part"),
        weekly_hours=_float(pick("Average Weekly Work Hours per Student", "Avg Weekly Work hrs per Student")),
        total_hours=_float(pick("Estimated Total Work Hours per Student", "Total Work hrs per Student")),
        employment_period=pick("Employment Period", "Employment Period"),
        closing_date=_date(pick("Closing Date", "Closing Date")),
        positions=_int(pick("No. of Students required", "No. of Students Required")),
        url=None,  # AIMS pages need a login, so there is no shareable link
        raw=raw,
        content_hash=compute_content_hash(raw),
    )


# --- Full scrape --------------------------------------------------------------

@dataclass
class SchemeResult:
    """What happened while scraping one scheme, for logs and the UI."""

    scheme_key: str
    listed: int = 0           # rows in the results table
    matched: int | None = None  # AIMS's own "No. of matched record" count
    opened: int = 0           # detail pages opened (new or changed rows)
    unchanged: int = 0        # rows skipped because their summary hash was unchanged
    errors: list[str] = field(default_factory=list)


def needs_detail(cache: Cache, summary: dict) -> bool:
    """Open the detail page only for new jobs or jobs whose results row changed."""
    job_id = f"aims:{summary.get('Job No.')}"
    return cache.get_job(job_id) is None or cache.get_summary_hash(job_id) != summary_hash(summary)


def _polite_pause() -> None:
    time.sleep(config.AIMS_DELAY_S)


def scrape_scheme(
    page: Page, cache: Cache, scheme_key: str, progress: Callable[[str], None] | None = None
) -> SchemeResult:
    """Scrape every job of one scheme into the cache; marks the scheme complete at the end.

    To get back from a detail page we re-run the search instead of the browser's Back
    button: Back could silently re-submit the search form. It costs one extra page load
    per opened job, and unchanged jobs are skipped, so later runs open very few pages.
    AimsSessionExpired is not caught: it aborts the run, and nothing is marked complete.
    The cache is saved every SAVE_EVERY opened jobs, so an interrupted run keeps its work
    (the next run skips those jobs by their summary hash).
    """
    result = SchemeResult(scheme_key)
    _polite_pause()
    rows = search_jobs(page, SCHEMES[scheme_key])
    result.listed = len(rows)
    result.matched = parse_matched_count(page.content())
    if result.matched is not None and result.matched != len(rows):
        log.warning(
            "%s: AIMS reports %d matched jobs but %d rows were parsed (paging not handled?)",
            scheme_key, result.matched, len(rows),
        )

    seen: set[str] = set()
    on_results_page = True
    for index, summary in enumerate(rows, start=1):
        job_no = summary.get("Job No.")
        if not job_no:
            continue
        job_id = f"aims:{job_no}"
        seen.add(job_id)  # listed = still open, even if its detail page fails below
        if not needs_detail(cache, summary):
            cache.mark_seen(job_id)
            result.unchanged += 1
            continue
        if progress:
            progress(f"{SCHEMES[scheme_key]}: {index}/{len(rows)}")
        try:
            if not on_results_page:
                _polite_pause()
                search_jobs(page, SCHEMES[scheme_key])
            _polite_pause()
            on_results_page = False
            detail = parse_job_detail(open_job(page, job_no))
            cache.upsert_job(aims_to_job(summary, detail, scheme_key), summary_hash(summary))
            result.opened += 1
            if result.opened % SAVE_EVERY == 0:
                cache.save()
        except AimsSessionExpired:
            raise
        except Exception as e:  # one broken job must not stop the whole scrape
            log.exception("%s: failed to scrape job %s", scheme_key, job_no)
            result.errors.append(f"{job_no}: {type(e).__name__}: {e}")

    cache.mark_scrape_complete(f"aims_{scheme_key}", seen)
    return result


def scrape_aims(
    context: BrowserContext,
    cache: Cache,
    scheme_keys: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[SchemeResult]:
    """Scrape the given schemes (default: all) one after another, saving the cache after each.

    The cache is also saved if the run stops early (session expired, error, Ctrl+C).
    """
    results = []
    page = context.new_page()
    try:
        for key in scheme_keys or list(SCHEMES):
            if progress:
                progress(f"Scraping AIMS: {SCHEMES[key]}")
            results.append(scrape_scheme(page, cache, key, progress))
            cache.save()  # keep finished schemes even if a later one fails
    finally:
        cache.save()
        page.close()
    return results
