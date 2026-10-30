"""When does a job happen? Start, end and application deadline, for the calendar view.

Sources write dates differently, so this module turns whatever is available into one
`Timeline`, trying the most precise source first:

1. exact `Job.start_date` / `Job.end_date` (set by scrapers that get real dates),
2. `Job.employment_period` text:
   - AIMS:     "08-Sep-2026 to 25-Oct-2026"
   - ITC STEM: "21/09/2026, 90 days" or "2026-09-21, 90 days" (start date + duration),
3. dates the AI read out of the description (Claude only; see core/analysis.py),
4. a season in the title or the listing's terms, e.g. "Summer 2027" -> June to August.
   These are guesses, so the timeline is marked `approximate` and drawn dashed.

The deadline is always `Job.closing_date` (or an AI-read deadline if the listing has none).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from cityu_jobs.core.models import Job

# Season -> (start month, start day, end month, end day). Winter spans the new year's start.
_SEASONS = {
    "spring": ((1, 15), (5, 15)),
    "summer": ((6, 1), (8, 31)),
    "fall": ((9, 1), (12, 15)),
    "autumn": ((9, 1), (12, 15)),
    "winter": ((1, 5), (4, 30)),
}
_SEASON_RE = re.compile(r"\b(spring|summer|fall|autumn|winter)\s*(?:'|20)?(\d{2})\b", re.I)
_RANGE_RE = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})\s*to\s*(\d{1,2}-[A-Za-z]{3}-\d{4})")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(day|week|month)s?", re.I)


@dataclass
class Timeline:
    start: date | None = None
    end: date | None = None
    deadline: date | None = None
    approximate: bool = False  # start/end guessed from a season, not written in the listing
    label: str = ""            # how the period was written, e.g. "Summer 2027"

    @property
    def days(self) -> int | None:
        if self.start and self.end:
            return (self.end - self.start).days + 1
        return None

    def to_dict(self) -> dict:
        """JSON-safe, for the calendar script."""
        def iso(d):
            return d.isoformat() if d else None
        return {
            "start": iso(self.start),
            "end": iso(self.end),
            "deadline": iso(self.deadline),
            "approx": self.approximate,
            "label": self.label,
            "days": self.days,
        }


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _duration(text: str) -> timedelta | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    n, unit = float(match.group(1)), match.group(2).lower()
    days = n * {"day": 1, "week": 7, "month": 30.4}[unit]
    return timedelta(days=max(round(days) - 1, 0))  # "90 days" from the 1st ends on day 90


def parse_period(text: str | None) -> tuple[date | None, date | None]:
    """(start, end) from an employment-period string; (None, None) if it has no dates."""
    if not text:
        return None, None
    match = _RANGE_RE.search(text)
    if match:
        return _parse_date(match.group(1)), _parse_date(match.group(2))
    first = text.split(",")[0]
    start = _parse_date(first)
    if start is None:
        return None, None
    span = _duration(text[len(first):])
    return start, (start + span if span else None)


def season_range(text: str) -> tuple[date, date, str] | None:
    """("Summer 2027" anywhere in `text`) -> (1 Jun 2027, 31 Aug 2027, "Summer 2027")."""
    match = _SEASON_RE.search(text or "")
    if not match:
        return None
    season, yy = match.group(1).lower(), int(match.group(2)[-2:])
    year = 2000 + yy
    (sm, sd), (em, ed) = _SEASONS[season]
    return date(year, sm, sd), date(year, em, ed), f"{season.capitalize()} {year}"


def _ai_date(value) -> date | None:
    return _parse_date(value) if isinstance(value, str) else None


def job_timeline(job: Job, ai_dates: dict | None = None) -> Timeline:
    """Best-known start, end and deadline for `job`. `ai_dates` is the AI's read of the text."""
    ai = ai_dates or {}
    timeline = Timeline(deadline=job.closing_date or _ai_date(ai.get("deadline")))
    timeline.label = job.employment_period or ""

    start, end = job.start_date, job.end_date
    if not start:
        start, parsed_end = parse_period(job.employment_period)
        end = end or parsed_end
    if not start and _ai_date(ai.get("start")):
        start, end = _ai_date(ai.get("start")), end or _ai_date(ai.get("end"))
        if start and not end and isinstance(ai.get("weeks"), (int, float)) and ai["weeks"] > 0:
            end = start + timedelta(days=round(ai["weeks"] * 7) - 1)
    if start and end and end < start:
        end = None  # a typo in the listing; show the start only
    if start:
        timeline.start, timeline.end = start, end
        return timeline

    terms = " ".join(job.raw.get("terms", []) if isinstance(job.raw.get("terms"), list) else [])
    guess = season_range(f"{job.title} {terms}")
    if guess:
        timeline.start, timeline.end, timeline.label = guess
        timeline.approximate = True
    return timeline
