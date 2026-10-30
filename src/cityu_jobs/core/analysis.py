"""AI level rating: how advanced / portfolio-worthy is a job, on a 1-5 scale?

The LLM is asked for a fixed JSON shape, `{"level": <1-5>, "reason": "<one sentence>"}`,
so the answer can be checked by code instead of trusted blindly. Anything else counts
as a failure: retry once, then give up and store `None` ("unrated").

Strong models (Claude) rate several jobs per request (`batch_size`), a few requests at a
time (`max_parallel`), and also read out what the listing says about dates and skills:

    {"ratings": [{"id": "j1", "level": 4, "reason": "...", "skills": ["Python", "PyTorch"],
                  "start": "2027-06-01", "end": null, "weeks": 12, "deadline": "2026-11-30"}]}

Dates feed the calendar when the listing has no structured period (core/dates.py).
"""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from cityu_jobs.core.ai.providers import InvalidJSONResponse, LLMProvider
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job

_RUBRIC = """Use this rubric:
1 = routine or clerical work (data entry, filing, front desk)
2 = general support with light skills (event help, simple office software)
3 = needs subject knowledge or specific tools (lab assistance, tutoring, basic coding)
4 = substantial technical or research work (building software, running experiments, data analysis)
5 = advanced, independent research or engineering (designing methods, publishable work)"""

SYSTEM_PROMPT = f"""You rate university student jobs by how advanced the work is.
{_RUBRIC}
Answer with JSON only, exactly: {{"level": <integer 1-5>, "reason": "<one sentence>"}}"""

BATCH_SYSTEM_PROMPT = f"""You rate university student jobs and internships by how advanced the work is.
{_RUBRIC}
You get several jobs, each starting with "### id: <id>". For every job return:
- id: the job's id, copied exactly
- level: integer 1-5 from the rubric
- reason: one sentence (max 25 words) naming the concrete work that sets the level
- skills: up to 4 short skill or tool names the job asks for, e.g. "Python", "PyTorch", "SQL"
- start, end: internship start / end date as YYYY-MM-DD, only if the text states it; else null
- weeks: duration in weeks, only if stated (e.g. "12-week", "3 months" = 13); else null
- deadline: application deadline as YYYY-MM-DD, only if stated; else null
Never guess dates. Return every job, in order."""

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": 5},
                    "reason": {"type": "string"},
                    "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "weeks": {"type": ["number", "null"]},
                    "deadline": {"type": ["string", "null"]},
                },
                "required": ["id", "level", "reason"],
            },
        }
    },
    "required": ["ratings"],
}

# Small local models lose track of long prompts; the first few thousand characters suffice.
_MAX_TEXT_CHARS = 4000
# Per job inside a batch: enough for the duties, requirements and any dates.
_MAX_BATCH_TEXT_CHARS = 3000

Rating = tuple[int | None, str, dict]  # (level, reason, extra: skills + dates)


def _job_prompt(job: Job, max_chars: int = _MAX_TEXT_CHARS) -> str:
    lines = [
        f"Title: {job.title}",
        f"Scheme: {job.scheme}",
    ]
    if job.company:
        lines.append(f"Company: {job.company}")
    if job.location:
        lines.append(f"Location: {job.location}")
    if job.employment_period:
        lines.append(f"Period: {job.employment_period}")
    lines += [
        f"Job nature: {job.job_nature or 'unknown'}",
        f"Department: {job.department_name or job.department_code or 'unknown'}",
        f"Description:\n{job.description[:max_chars]}",
        f"Requirements:\n{(job.requirements or 'none listed')[:1500]}",
    ]
    return "\n".join(lines)


def _valid_level(answer: dict) -> int | None:
    """The level as an int in 1..5, or None if the answer doesn't follow the format."""
    level = answer.get("level")
    if isinstance(level, str) and level.strip().isdigit():
        level = int(level)  # tolerate "4" for 4; small models do this
    if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 5:
        return level
    return None


def _extra(answer: dict) -> dict:
    """Skills and dates from a batch answer, keeping only well-formed values."""
    skills = [str(s).strip()[:30] for s in answer.get("skills") or [] if str(s).strip()][:4]
    dates = {k: answer.get(k) for k in ("start", "end", "deadline") if isinstance(answer.get(k), str)}
    if isinstance(answer.get("weeks"), (int, float)) and not isinstance(answer.get("weeks"), bool):
        dates["weeks"] = answer["weeks"]
    extra = {}
    if skills:
        extra["skills"] = skills
    if dates:
        extra["dates"] = dates
    return extra


def rate_level(job: Job, llm: LLMProvider) -> tuple[int | None, str]:
    """Ask the LLM for the job's level. Returns (level, reason), or (None, why it failed).

    Connection problems (ProviderError) are not retried: they would fail again, so they
    propagate to the caller, which can tell the student to start Ollama / log in to Claude.
    """
    problem = ""
    for _attempt in range(2):  # first try + one retry
        try:
            answer = llm.complete_json(SYSTEM_PROMPT, _job_prompt(job))
        except InvalidJSONResponse as e:
            problem = str(e)
            continue
        level = _valid_level(answer)
        if level is not None:
            return level, str(answer.get("reason", "")).strip()
        problem = f"level out of range or missing: {answer!r}"[:200]
    return None, f"Unrated ({problem})"


def rate_batch(jobs: list[Job], llm: LLMProvider) -> dict[str, Rating]:
    """Rate several jobs in one request; returns {job_id: (level, reason, extra)} for every job.

    Jobs missing from the answer (or with a bad level) come back unrated, so the next
    search retries them. A malformed answer is retried once as a whole.
    """
    ids = {f"j{i}": job for i, job in enumerate(jobs, start=1)}
    prompt = "\n\n".join(f"### id: {key}\n{_job_prompt(job, _MAX_BATCH_TEXT_CHARS)}" for key, job in ids.items())
    problem = "no answer"
    for _attempt in range(2):
        try:
            answer = llm.complete_json(BATCH_SYSTEM_PROMPT, prompt, schema=BATCH_SCHEMA)
        except InvalidJSONResponse as e:
            problem = str(e)
            continue
        results: dict[str, Rating] = {}
        for item in answer.get("ratings") or []:
            if not isinstance(item, dict) or item.get("id") not in ids:
                continue
            level = _valid_level(item)
            if level is not None:
                results[ids[item["id"]].job_id] = (level, str(item.get("reason", "")).strip(), _extra(item))
        for job in jobs:
            results.setdefault(job.job_id, (None, "Unrated (missing from the batch answer)", {}))
        return results
    return {job.job_id: (None, f"Unrated ({problem})"[:200], {}) for job in jobs}


def _needs_rating(cached: dict | None, llm: LLMProvider) -> bool:
    """No usable level yet, or an earlier failure, or (once) a local-model level when Claude is on.

    Owner decision 2026-10-30: switching to Claude should improve the ratings, so levels from
    local models are re-rated once by Claude. Other model switches keep the old level.
    """
    if not cached or cached.get("value") is None:
        return True
    upgrading = llm.llm_model.startswith("claude:") and not str(cached.get("model", "")).startswith("claude:")
    return upgrading


def analyze_jobs(
    jobs: Iterable[Job],
    cache: Cache,
    llm: LLMProvider,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | None]:
    """Make sure every job has a level for its current content; returns {job_id: level}.

    Only jobs without a usable cached level call the LLM (see `_needs_rating`). Earlier
    failures (`None`) are retried, since the model or the listing may have improved. Jobs
    must already be in the cache (the scrapers put them there).
    """
    jobs = list(jobs)
    todo = [j for j in jobs if _needs_rating(cache.get_level(j.job_id, j.content_hash), llm)]
    batch_size = max(int(getattr(llm, "batch_size", 1) or 1), 1)
    parallel = max(int(getattr(llm, "max_parallel", 1) or 1), 1)

    if batch_size == 1 and parallel == 1:
        for done, job in enumerate(todo, start=1):
            level, reason = rate_level(job, llm)
            cache.set_level(job.job_id, level, reason, llm.llm_model, job.content_hash)
            if progress:
                progress(done, len(todo))
    else:
        batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
        done = 0
        # Requests run in threads; the cache is only written here, on the calling thread.
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(rate_batch, batch, llm): batch for batch in batches}
            try:
                for future in as_completed(futures):
                    batch = futures[future]
                    results = future.result()  # a ProviderError stops the whole run
                    for job in batch:
                        level, reason, extra = results[job.job_id]
                        cache.set_level(job.job_id, level, reason, llm.llm_model, job.content_hash, extra=extra)
                    done += len(batch)
                    if progress:
                        progress(done, len(todo))
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

    return {j.job_id: (cache.get_level(j.job_id, j.content_hash) or {}).get("value") for j in jobs}
