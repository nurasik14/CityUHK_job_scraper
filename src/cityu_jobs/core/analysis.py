"""AI level rating: how advanced / portfolio-worthy is a job, on a 1-5 scale?

The LLM is asked for a fixed JSON shape, `{"level": <1-5>, "reason": "<one sentence>"}`,
so the answer can be checked by code instead of trusted blindly. Anything else counts
as a failure: retry once, then give up and store `None` ("unrated").
"""

from collections.abc import Callable, Iterable

from cityu_jobs.core.ai.providers import InvalidJSONResponse, LLMProvider
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job

SYSTEM_PROMPT = """You rate university student jobs by how advanced the work is.
Use this rubric:
1 = routine or clerical work (data entry, filing, front desk)
2 = general support with light skills (event help, simple office software)
3 = needs subject knowledge or specific tools (lab assistance, tutoring, basic coding)
4 = substantial technical or research work (building software, running experiments, data analysis)
5 = advanced, independent research or engineering (designing methods, publishable work)
Answer with JSON only, exactly: {"level": <integer 1-5>, "reason": "<one sentence>"}"""

# Small local models lose track of long prompts; the first few thousand characters suffice.
_MAX_TEXT_CHARS = 4000


def _job_prompt(job: Job) -> str:
    lines = [
        f"Title: {job.title}",
        f"Scheme: {job.scheme}",
        f"Job nature: {job.job_nature or 'unknown'}",
        f"Department: {job.department_name or job.department_code or 'unknown'}",
        f"Description:\n{job.description[:_MAX_TEXT_CHARS]}",
        f"Requirements:\n{job.requirements or 'none listed'}",
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


def rate_level(job: Job, llm: LLMProvider) -> tuple[int | None, str]:
    """Ask the LLM for the job's level. Returns (level, reason), or (None, why it failed).

    Connection problems (ProviderError) are not retried: they would fail again, so they
    propagate to the caller, which can tell the student to start Ollama.
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


def analyze_jobs(
    jobs: Iterable[Job],
    cache: Cache,
    llm: LLMProvider,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int | None]:
    """Make sure every job has a level for its current content; returns {job_id: level}.

    Only jobs without a usable cached level call the LLM. Earlier failures (`None`) are
    retried, since the model or the listing may have improved. Jobs must already be in
    the cache (the scrapers put them there).
    """
    jobs = list(jobs)
    todo = [j for j in jobs if (cache.get_level(j.job_id, j.content_hash) or {}).get("value") is None]
    for done, job in enumerate(todo, start=1):
        level, reason = rate_level(job, llm)
        cache.set_level(job.job_id, level, reason, llm.llm_model, job.content_hash)
        if progress:
            progress(done, len(todo))
    return {j.job_id: cache.get_level(j.job_id, j.content_hash)["value"] for j in jobs}
