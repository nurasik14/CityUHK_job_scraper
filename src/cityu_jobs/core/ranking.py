"""Rank jobs by a weighted sum of simple, explainable terms.

    score = 0.45·sim + 0.10·dept
          + earn  · (0.20·hours + 0.08·is_sh)
          + learn · (0.20·level + 0.08·is_learn)

Every term is scaled to 0-1 first, so the weights (in config.py) say directly how much
each one matters. Each RankedJob keeps its per-term breakdown so the UI can show *why*
a job ranked where it did.
"""

from dataclasses import dataclass, field
from datetime import date

from cityu_jobs import config
from cityu_jobs.core.dates import Timeline
from cityu_jobs.core.models import Job

# Internship schemes: CityU's own (CIS, ITC STEM) and tech internships from company boards.
_LEARNING_SOURCES = {"aims_cis", "itc_stem", "intern_bigtech", "intern_tech"}


@dataclass
class Preferences:
    """What the student asked for.

    department_code: e.g. "CS"; jobs from this department get a boost (not a filter).
        See config.DEPARTMENT_BOOST_ALIASES for codes that also count (CS also matches CC).
    query: free text describing the job they want.
    earn, learn: priorities from 1 (don't care) to 5 (very important).
    """

    department_code: str | None
    query: str
    earn: int = 3
    learn: int = 3


@dataclass
class RankedJob:
    job: Job
    score: float
    breakdown: dict[str, float]  # term name -> its contribution to the score
    similarity: float | None     # raw cosine similarity, before scaling
    level: int | None            # AI level, None = unrated
    level_reason: str = ""       # the AI's one-sentence reason (filled in by the pipeline)
    skills: list[str] = field(default_factory=list)  # skills the AI read from the listing (Claude only)
    timeline: Timeline | None = None  # start / end / deadline for the calendar (filled in by the pipeline)
    fit: dict | None = None      # "difficulty for you" from core/fit.py (top results, Claude + profile only)


def _priority(value: int) -> float:
    """Slider 1-5 -> 0-1."""
    return (min(max(value, 1), 5) - 1) / 4


def _min_max(values: dict[str, float]) -> dict[str, float]:
    """Rescale so the best match is 1 and the worst is 0.

    Raw cosine similarities from one model tend to sit in a narrow band (e.g. 0.45-0.70);
    rescaling makes the similarity term use its full weight. If all values are equal,
    similarity can't tell the jobs apart, so every job gets 1.
    """
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high == low:
        return {k: 1.0 for k in values}
    return {k: (v - low) / (high - low) for k, v in values.items()}


def max_possible_score(prefs: Preferences) -> float:
    """The best score any job could get with these priorities.

    The UI shows a 0-100 match as score / this, so 100 means "as good as a job can be for what
    you asked". Dividing by the global maximum instead would cap a student with earn = 1 near 75.
    Every term counts at its maximum except the two source bonuses: a job is a Student Helper
    job *or* an internship (CIS / ITC STEM), never both, so only the larger bonus is reachable.
    """
    earn, learn = _priority(prefs.earn), _priority(prefs.learn)
    return (
        config.WEIGHT_SIMILARITY
        + config.WEIGHT_DEPARTMENT
        + earn * config.WEIGHT_HOURS
        + learn * config.WEIGHT_LEVEL
        + max(earn * config.WEIGHT_STUDENT_HELPER, learn * config.WEIGHT_LEARNING_SCHEME)
    )


def is_closed(job: Job, today: date) -> bool:
    return job.closing_date is not None and job.closing_date < today


def rank(
    jobs: list[Job],
    levels: dict[str, int | None],
    sims: dict[str, float],
    prefs: Preferences,
    today: date,
) -> list[RankedJob]:
    """Score open jobs and return them best first.

    levels: {job_id: AI level 1-5 or None}. sims: {job_id: cosine similarity to the query};
    a job without a similarity counts as the worst match.
    """
    open_jobs = [j for j in jobs if not is_closed(j, today)]
    scaled = _min_max({j.job_id: sims[j.job_id] for j in open_jobs if j.job_id in sims})
    earn, learn = _priority(prefs.earn), _priority(prefs.learn)
    wanted = (prefs.department_code or "").strip().upper()
    # e.g. "CS" -> {"CS", "CC"}; an empty department matches nothing.
    dept_codes = config.DEPARTMENT_BOOST_ALIASES.get(wanted, {wanted}) if wanted else set()

    ranked = []
    for job in open_jobs:
        level = levels.get(job.job_id)
        hours = 0.5 if job.weekly_hours is None else min(job.weekly_hours / config.FULL_WEEKLY_HOURS, 1.0)
        terms = {
            "similarity": config.WEIGHT_SIMILARITY * scaled.get(job.job_id, 0.0),
            "department": config.WEIGHT_DEPARTMENT * float((job.department_code or "").upper() in dept_codes),
            "hours": earn * config.WEIGHT_HOURS * hours,
            "student_helper": earn * config.WEIGHT_STUDENT_HELPER * float(job.source.startswith("aims_sh")),
            "level": learn * config.WEIGHT_LEVEL * (0.5 if level is None else (level - 1) / 4),
            "learning_scheme": learn * config.WEIGHT_LEARNING_SCHEME * float(job.source in _LEARNING_SOURCES),
        }
        ranked.append(RankedJob(job, sum(terms.values()), terms, sims.get(job.job_id), level))

    # Highest score first; job_id breaks ties so the order is always the same.
    ranked.sort(key=lambda r: (-r.score, r.job.job_id))
    return ranked
