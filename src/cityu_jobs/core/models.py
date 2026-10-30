"""The common job schema shared by every source (AIMS and ITC STEM).

Each scraper turns its own page or spreadsheet format into `Job` objects, so the later
stages (cache, AI analysis, embeddings, ranking) only ever deal with one shape.
Everything a scraper found is also kept in `Job.raw`, so nothing is lost by mapping.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date


@dataclass
class Job:
    """One job listing, from any source.

    Fields:
        job_id: Unique, stable ID across runs, prefixed by source family:
            `aims:<Job No.>` (e.g. `aims:N26MA004`) or `itc:<college>:<suffix>`.
            This is the cache key.
        source: Where the job came from: `aims_cis`, `aims_sh_summer`, `aims_sh_regular`
            or `itc_stem`. Used for "is this a Student Helper / internship job" in ranking.
        scheme: Human-readable scheme name, e.g. "Campus Internship Scheme".
        title: Short job title (AIMS "Brief Job Description").
        department_code: Department code such as "MA" or "CS"; matched against the
            student's department for the ranking boost.
        department_name: Full department name, e.g. "Mathematics".
        job_nature: Category such as "Scientific Research" or "Clerical".
        description: All free text describing the work, joined into one string.
        requirements: Disciplines, skills and other requirements, joined into one string.
        part_time: True for part-time, False for full-time, None if unknown.
        weekly_hours: Average work hours per week (the "earn money" signal).
        total_hours: Total work hours per student over the whole job.
        employment_period: Period as written in the listing, e.g. "08-Sep-2026 to 25-Oct-2026".
        closing_date: Last day to apply; jobs past this date are not recommended.
        positions: Number of students required.
        url: Public link to the listing, if there is one (AIMS pages need a login, so None).
        company: Employer name for jobs outside CityU (internship boards), e.g. "Stripe".
        location: Where the job is, as the listing writes it, e.g. "Hong Kong; Singapore".
        start_date, end_date: When the work starts / ends, if the source gives exact dates.
            Most sources don't; core/dates.py also derives them from `employment_period`.
        raw: Everything the scraper parsed, in the source's own format.
        content_hash: Fingerprint of `raw`. If it changes, the listing was edited and
            the cached AI level and embedding must be recomputed.
    """

    job_id: str
    source: str
    scheme: str
    title: str
    department_code: str | None = None
    department_name: str | None = None
    job_nature: str | None = None
    description: str = ""
    requirements: str = ""
    part_time: bool | None = None
    weekly_hours: float | None = None
    total_hours: float | None = None
    employment_period: str | None = None
    closing_date: date | None = None
    positions: int | None = None
    url: str | None = None
    company: str | None = None
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    raw: dict = field(default_factory=dict)
    content_hash: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict: dates become ISO strings like "2026-10-25"."""
        data = asdict(self)
        for name in _DATE_FIELDS:
            if getattr(self, name) is not None:
                data[name] = getattr(self, name).isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        """Inverse of `to_dict()`. Unknown keys are ignored so old cache files still load."""
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for name in _DATE_FIELDS:
            if values.get(name):
                values[name] = date.fromisoformat(values[name])
        return cls(**values)


_DATE_FIELDS = ("closing_date", "start_date", "end_date")


def compute_content_hash(raw: dict) -> str:
    """sha256 of the raw listing, with keys sorted so dict order never changes the hash."""
    text = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_text(job: Job) -> str:
    """The text that represents a job for semantic search (see core/embeddings.py)."""
    parts = [job.title, job.company, job.job_nature, job.department_name, job.description, job.requirements]
    return "\n".join(p for p in parts if p)
