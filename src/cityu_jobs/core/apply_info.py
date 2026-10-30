"""Who is hiring, how to apply, and every detail the listing has, in one shape for the UI.

Each source writes this differently:
- AIMS (Campus Internship / Student Helper): a CityU department; you apply inside AIMS
  with the job number. The detail page has a contact person, supervisor, skills, etc.
- ITC STEM: a company; the spreadsheet row says how to apply (usually "send your CV to
  <email>"), the deadline and the company website.
- Tech internships: the company's own posting page, where the application form is.

Everything is read from `Job.raw` at display time, so jobs already in the cache get it too.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from cityu_jobs import config
from cityu_jobs.core.itc_stem_scraper import fix_mojibake
from cityu_jobs.core.models import Job

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_EMPTY = {"", "n/a", "na", "nil", "none", "-", "no"}


@dataclass
class ApplyInfo:
    employer: str               # who you would work for
    method: str                 # "aims" | "email" | "website"
    steps: list[str]            # how to apply, in order, as plain sentences
    email: str | None = None    # where to send the application (ITC)
    url: str | None = None      # where to apply online / the listing
    website: str | None = None  # the employer's own site
    contact: str | None = None  # contact person (AIMS)
    deadline: date | None = None
    # Every field of the listing as (label, value), for the "All details" table.
    facts: list[tuple[str, str]] = field(default_factory=list)


def _day(d: date) -> str:
    """2 Dec 2026 (no leading zero, like the rest of the UI)."""
    return f"{d.day} {d:%b %Y}"


def _find(raw: dict, *keywords: str) -> str:
    """First non-empty raw value whose key contains one of `keywords` (case-insensitive)."""
    for keyword in keywords:
        for key, value in raw.items():
            if keyword in key.lower() and isinstance(value, str) and value.strip().lower() not in _EMPTY:
                return value.strip()
    return ""


def clean_label(key: str) -> str:
    """Spreadsheet headers can be whole questions; keep the start, readable."""
    key = re.sub(r"\s+", " ", key).strip()
    key = re.sub(r"\(the internship should be.*?\)", "", key, flags=re.I).strip()
    if key.lower().startswith("will the company provide any monthly honorarium"):
        return "Extra monthly honorarium"
    return key[:80]


def _flatten(value) -> str:
    if isinstance(value, str):
        # Mis-decoded no-break spaces ("based onÂ open-source") from older cached rows.
        return re.sub(r"\u00c2(?=[\s\u00a0])", "", fix_mojibake(value)).strip()
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_flatten(v)}" for k, v in value.items() if _flatten(v))
    if isinstance(value, (list, tuple)):
        return "\n".join(f"• {_flatten(v)}" for v in value if _flatten(v))
    return str(value).strip() if value is not None else ""


def facts(job: Job) -> list[tuple[str, str]]:
    """Every field the scraper kept, labelled, in the listing's own order."""
    raw = job.raw or {}
    items: list[tuple[str, str]] = []
    if job.source.startswith("aims"):
        for key, value in (raw.get("fields") or {}).items():
            items.append((key, _flatten(value)))
        for key, value in (raw.get("sections") or {}).items():
            items.append((key, _flatten(value)))
        for key, label in (
            ("learning_objectives", "Learning objectives"),
            ("training_plan", "Training plan"),
            ("evaluation", "Evaluation"),
            ("professional_development", "Professional development"),
        ):
            items.append((label, _flatten(raw.get(key))))
    elif job.source == "itc_stem":
        items += [(clean_label(k), _flatten(v)) for k, v in raw.items() if not k.startswith("_")]
    else:
        labels = {
            "company": "Company", "title": "Title", "location": "Location", "posted": "Posted",
            "deadline": "Application deadline", "terms": "Terms", "category": "Category",
            "sponsorship": "Visa sponsorship", "department": "Team", "url": "Posting",
        }
        items += [(labels[k], _flatten(raw.get(k))) for k in labels if k in raw]
    seen, out = set(), []
    for label, value in items:
        if value and value.lower() not in _EMPTY and (label, value) not in seen:
            seen.add((label, value))
            out.append((label, value))
    return out


def employer(job: Job) -> str:
    raw = job.raw or {}
    if job.company:
        return job.company
    if job.source == "itc_stem":
        return _find(raw, "company/organisation name", "company", "organisation", "employer") or (job.department_name or "")
    if job.source.startswith("aims"):
        unit = _find(raw.get("fields") or {}, "recruiting dept") or job.department_name or job.department_code or ""
        unit = re.sub(r"^[A-Z0-9^]+\s*--\s*", "", unit)
        return f"CityU · {unit}" if unit else "CityU"
    return job.department_name or ""


def apply_info(job: Job, deadline: date | None = None) -> ApplyInfo:
    """How to apply for `job`. `deadline` may come from the calendar (e.g. read by the AI)."""
    raw = job.raw or {}
    deadline = job.closing_date or deadline
    info = ApplyInfo(employer=employer(job), method="website", steps=[], deadline=deadline, facts=facts(job))

    if job.source.startswith("aims"):
        fields = raw.get("fields") or {}
        number = job.job_id.split(":", 1)[1]
        info.method = "aims"
        info.contact = _find(fields, "contact person") or None
        supervisor = _find(fields, "supervisor")
        info.steps = [
            "Log in to AIMS in your own browser and open the Student Helper / Campus Internship job search page.",
            f"Use its \"apply by job number\" form with job no. {number}"
            + (f", before {_day(deadline)}." if deadline else "."),
        ]
        if info.contact:
            info.steps.append(
                f"Questions about the job: {info.contact}" + (f" (supervisor: {supervisor})" if supervisor and supervisor != info.contact else "") + "."
            )
        return info

    if job.source == "itc_stem":
        procedure = _find(raw, "application procedure", "how to apply", "application method")
        email_cell = _find(raw, "email for submission", "e-mail", "email")
        emails = _EMAIL_RE.findall(email_cell) or _EMAIL_RE.findall(procedure)
        info.email = emails[0] if emails else None
        site = _find(raw, "company website", "website")
        info.website = (_URL_RE.findall(site) or [None])[0] if site else None
        if site and not info.website and "." in site:
            info.website = "https://" + site.lstrip("/")
        links = _URL_RE.findall(procedure)
        info.url = links[0] if links else None
        info.method = "email" if info.email else "website"
        info.steps = [_flatten(procedure)] if procedure else []
        if info.email and not procedure:
            info.steps.append(f"Send your CV (and a short cover letter) to {info.email}.")
        info.steps.append(
            "It is an ITC STEM Internship Scheme job: check the College of Computing page for the "
            "scheme's own registration steps."
        )
        if deadline:
            info.steps.append(f"Deadline: {_day(deadline)}.")
        return info

    info.url = job.url
    info.steps = [f"Apply on {info.employer or 'the company'}'s careers site: open the posting and use its Apply button."]
    if raw.get("sponsorship"):
        info.steps.append(f"Visa sponsorship (from the SimplifyJobs list): {raw['sponsorship']}.")
    if deadline:
        info.steps.append(f"Apply by {_day(deadline)}.")
    else:
        info.steps.append("No deadline is listed: postings like this usually close when filled, so apply early.")
    return info


def itc_page_url() -> str:
    return config.ITC_STEM_SOURCES[0]["page_url"]
