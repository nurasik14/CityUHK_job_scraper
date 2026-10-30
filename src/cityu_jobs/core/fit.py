"""How hard is each job for *this* student? Claude compares the profile with each listing.

Output per job (validated by code, cached per listing + profile fingerprint):

    {"difficulty": 1-5,          # 1 = well within reach ... 5 = long shot
     "verdict": "good match",    # strong match | good match | stretch | long shot | not eligible
     "eligible": true,           # false if a hard requirement rules the student out
     "blockers": ["PhD students only"],
     "matched": ["Python", "PyTorch"],   # requirements the profile shows
     "missing": ["C++"],                 # requirements the profile lacks
     "tip": "Lead with your RAG project; mention ..."}

Only the top results get an estimate (config.FIT_TOP_N per group), so the cost stays at a
handful of requests per new search; repeats come from the cache.
"""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from cityu_jobs import config
from cityu_jobs.core.ai.providers import InvalidJSONResponse, LLMProvider
from cityu_jobs.core.analysis import _job_prompt
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job
from cityu_jobs.core.profile import Profile

VERDICTS = ("strong match", "good match", "stretch", "long shot", "not eligible")

SYSTEM_PROMPT = """You are a frank career adviser for a university student. You get the student's
profile (CV, projects, skills) and several job or internship listings, each starting with
"### id: <id>". For every listing, judge how hard it would be for THIS student to get it.

difficulty: 1 = well within reach, 2 = good chance, 3 = competitive but realistic,
4 = a stretch (missing important requirements or experience), 5 = long shot or not eligible.
verdict: one of "strong match", "good match", "stretch", "long shot", "not eligible".
eligible: false only for hard rules the student clearly fails (degree level such as "PhD only",
year of study, graduation date, required language, work permit, must be a student of another
university). Unknown is eligible.
blockers: the hard rules that make it ineligible or very unlikely (empty if none), max 3.
matched: up to 4 requirements the profile clearly shows. missing: up to 4 it lacks.
Write blockers, matched and missing as short phrases (2-6 words), e.g. "PhD students only".
tip: one concrete sentence (max 30 words) on what to emphasise or do to improve the chance.
Be realistic: big tech and quant internships are very competitive; first-year students are
usually not eligible for roles asking for penultimate-year, Master's or PhD students.
Return every listing, in order."""

SCHEMA = {
    "type": "object",
    "properties": {
        "fits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "eligible": {"type": "boolean"},
                    "blockers": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                    "matched": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "missing": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "tip": {"type": "string"},
                },
                "required": ["id", "difficulty", "verdict", "eligible", "tip"],
            },
        }
    },
    "required": ["fits"],
}

_MAX_JOB_CHARS = 2500


def _short(text: str, limit: int = 90) -> str:
    """Cut long phrases at a word boundary, with an ellipsis."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;(") + "…"


def _strings(value, limit: int) -> list[str]:
    return [_short(v) for v in (value or []) if str(v).strip()][:limit] if isinstance(value, list) else []


def _valid(item: dict) -> dict | None:
    difficulty = item.get("difficulty")
    if isinstance(difficulty, str) and difficulty.strip().isdigit():
        difficulty = int(difficulty)
    if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 1 <= difficulty <= 5:
        return None
    verdict = str(item.get("verdict", "")).strip().lower()
    eligible = item.get("eligible") is not False
    if verdict not in VERDICTS:
        by_difficulty = {1: "strong match", 2: "good match", 3: "stretch", 4: "stretch", 5: "long shot"}
        verdict = by_difficulty[difficulty] if eligible else "not eligible"
    return {
        "difficulty": difficulty,
        "verdict": verdict,
        "eligible": eligible and verdict != "not eligible",
        "blockers": _strings(item.get("blockers"), 3),
        "matched": _strings(item.get("matched"), 4),
        "missing": _strings(item.get("missing"), 4),
        "tip": _short(item.get("tip", ""), 300),
    }


def assess_batch(jobs: list[Job], profile_text: str, llm: LLMProvider) -> dict[str, dict]:
    """{job_id: fit} for the jobs the model answered validly (others are simply missing)."""
    ids = {f"j{i}": job for i, job in enumerate(jobs, start=1)}
    listings = "\n\n".join(f"### id: {key}\n{_job_prompt(job, _MAX_JOB_CHARS)}" for key, job in ids.items())
    prompt = f"# Student profile\n{profile_text}\n\n# Listings\n{listings}"
    for _attempt in range(2):
        try:
            answer = llm.complete_json(SYSTEM_PROMPT, prompt, schema=SCHEMA)
        except InvalidJSONResponse:
            continue
        results = {}
        for item in answer.get("fits") or []:
            if isinstance(item, dict) and item.get("id") in ids:
                fit = _valid(item)
                if fit:
                    results[ids[item["id"]].job_id] = fit
        return results
    return {}


def can_assess(llm: LLMProvider) -> bool:
    """Fit needs a strong model reading long prompts: Claude only (small local models guess)."""
    return str(getattr(llm, "llm_model", "")).startswith("claude:")


def assess_fit(
    jobs: Iterable[Job],
    cache: Cache,
    llm: LLMProvider,
    profile: Profile,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, dict]:
    """Make sure every job has a fit estimate for the current profile; returns {job_id: fit}."""
    jobs = list(jobs)
    if profile.is_empty() or not can_assess(llm):
        return {}
    fingerprint, text = profile.fingerprint(), profile.as_text()
    todo = [j for j in jobs if cache.get_fit(j.job_id, j.content_hash, fingerprint) is None]
    batches = [todo[i : i + config.FIT_BATCH_SIZE] for i in range(0, len(todo), config.FIT_BATCH_SIZE)]
    done = 0
    with ThreadPoolExecutor(max_workers=max(int(getattr(llm, "max_parallel", 1) or 1), 1)) as pool:
        futures = {pool.submit(assess_batch, batch, text, llm): batch for batch in batches}
        for future in as_completed(futures):
            for job_id, fit in future.result().items():  # cache writes on this thread only
                job = next(j for j in futures[future] if j.job_id == job_id)
                cache.set_fit(job_id, fit, llm.llm_model, job.content_hash, fingerprint)
            done += len(futures[future])
            if progress:
                progress(done, len(todo))
    out = {}
    for job in jobs:
        fit = cache.get_fit(job.job_id, job.content_hash, fingerprint)
        if fit:
            out[job.job_id] = fit
    return out
