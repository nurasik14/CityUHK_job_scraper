"""What is the student missing for the jobs they want? A summary across many listings.

Two steps, so the AI never has to read 90 full listings at once:

1. collect() (plain code, no AI): over the jobs you're targeting (your top results that
   have a "difficulty for you" estimate), count the skills each listing asks for (read by
   the AI when it rated the job level), check with a keyword search whether your profile
   mentions each one, and gather what the per-job estimates listed as missing and as
   blockers.
2. summarize() (one Claude call): reads that evidence plus your profile and groups it
   into a few weaknesses, ordered by how many jobs they cost you, each with concrete fixes;
   plus strengths, CV wording fixes, things only time fixes (year of study) and a short plan.

The report is saved in .data/gaps.json per scope (all / CityU / big tech / other tech)
together with a key made from the jobs and the profile, so the page can say when the
report is out of date.
"""

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from cityu_jobs import config
from cityu_jobs.core.ai.providers import InvalidJSONResponse, LLMProvider
from cityu_jobs.core.models import Job
from cityu_jobs.core.profile import Profile

# --- Step 1: evidence, by code -----------------------------------------------------

# Same skill, different words. Keys and values are lower-case.
_CANONICAL = {
    "ml": "machine learning", "dl": "deep learning", "llms": "llm", "large language models": "llm",
    "sklearn": "scikit-learn", "js": "javascript", "ts": "typescript", "k8s": "kubernetes",
    "huggingface": "hugging face", "postgres": "postgresql", "golang": "go", "c/c++": "c++",
    "natural language processing": "nlp", "react.js": "react", "reactjs": "react", "node": "node.js",
    "nodejs": "node.js", "aws cloud": "aws", "stats": "statistics",
}
# Other words that show a skill in a CV (keyword check only; the AI makes the real call).
_ALSO_SHOWN_BY = {
    "machine learning": ["ml", "pytorch", "scikit-learn", "sklearn", "tensorflow"],
    "deep learning": ["pytorch", "tensorflow", "neural network", "lstm", "dqn"],
    "llm": ["llms", "large language model", "rag", "claude api", "ollama"],
    "rag": ["retrieval-augmented", "retrieval augmented"],
    "scikit-learn": ["sklearn"],
    "hugging face": ["huggingface"],
    "javascript": ["typescript", "node.js"],
    "nlp": ["natural language processing", "multilingual", "rag"],
    "computer vision": ["opencv", "mediapipe", "pose estimation"],
    "data analysis": ["pandas", "numpy"],
    "statistics": ["probability & statistics", "statistical"],
    "rest apis": ["fastapi", "rest api"],
    "sql": ["postgresql", "mysql", "sqlite", "mariadb"],
    "databases": ["postgresql", "mysql", "sqlite", "sql"],
    "linux": ["fedora", "ubuntu", "bash"],
    "reinforcement learning": ["dqn", "rl"],
    "apis": ["fastapi", "rest api", "api"],
    "backend": ["fastapi", "node.js", "flask", "django"],
    "full-stack": ["full-stack", "full stack"],
}


def canonical(skill: str) -> str:
    key = " ".join(skill.lower().strip(" .;:").split())
    return _CANONICAL.get(key, key)


def _mentions(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9+#])", text) is not None


def in_profile(skill: str, profile_text: str) -> bool:
    """Does the profile mention `skill` (or a variant, e.g. "LLM/RAG" -> "llm" or "rag")?"""
    text = profile_text.lower()
    parts = [canonical(p) for p in re.split(r"\s*(?:/|,|&|\band\b|\bor\b)\s*", skill) if p.strip()]
    for part in parts or [canonical(skill)]:
        variants = {part, *(k for k, v in _CANONICAL.items() if v == part), *_ALSO_SHOWN_BY.get(part, [])}
        if any(_mentions(text, v) for v in variants if v):
            return True
    return False


@dataclass
class Demand:
    skill: str               # display name, e.g. "PyTorch"
    count: int               # how many of the analysed jobs ask for it
    in_cv: bool              # keyword check against the profile
    jobs: list[str] = field(default_factory=list)  # job ids (first few)


@dataclass
class Evidence:
    jobs: list[Job]                         # the analysed jobs, numbered J1.. in this order
    employers: dict[str, str]               # job_id -> employer
    demand: list[Demand]                    # most-asked skills first
    verdicts: Counter                       # "not eligible" -> 31, ...
    missing: list[tuple[int, list[str]]]    # (job number, what the estimate said is missing)
    blockers: list[tuple[int, list[str]]]   # (job number, hard blockers)
    has_fit: bool                           # False: only skill demand (no per-job estimates)

    def key(self, profile: Profile) -> str:
        """Changes when the analysed jobs or the profile change -> the report is out of date."""
        ids = ",".join(sorted(j.job_id for j in self.jobs))
        return hashlib.sha256(f"{profile.fingerprint()}|{ids}".encode()).hexdigest()[:16]


def collect(
    rows: Sequence[tuple[Job, dict | None, dict | None]], profile: Profile, employer=lambda j: j.company or ""
) -> Evidence:
    """rows: (job, cached level dict or None, fit dict or None), best first."""
    text = profile.as_text()
    counts: Counter = Counter()
    names: dict[str, Counter] = {}
    examples: dict[str, list[str]] = {}
    missing, blockers, verdicts = [], [], Counter()
    for number, (job, level, fit) in enumerate(rows, start=1):
        seen = set()
        for skill in (level or {}).get("skills") or []:
            key = canonical(skill)
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            names.setdefault(key, Counter())[skill.strip()] += 1
            examples.setdefault(key, []).append(job.job_id)
        if fit:
            verdicts[fit.get("verdict", "")] += 1
            if fit.get("missing"):
                missing.append((number, list(fit["missing"])))
            if fit.get("blockers"):
                blockers.append((number, list(fit["blockers"])))
    demand = [
        Demand(names[key].most_common(1)[0][0], n, in_profile(key, text), examples[key][:6])
        for key, n in counts.most_common(25)
    ]
    return Evidence(
        jobs=[r[0] for r in rows], employers={r[0].job_id: employer(r[0]) for r in rows},
        demand=demand, verdicts=verdicts, missing=missing, blockers=blockers,
        has_fit=any(r[2] for r in rows),
    )


def evidence_text(ev: Evidence) -> str:
    """The evidence as compact text for the AI (job numbers J1.. let it cite jobs)."""
    lines = [f"Jobs analysed: {len(ev.jobs)}"]
    if ev.verdicts:
        lines.append("Per-job verdicts: " + ", ".join(f"{v} {n}" for v, n in ev.verdicts.most_common()))
    lines.append("\n## Skills most asked for (number of jobs; keyword check of the profile)")
    for d in ev.demand:
        lines.append(f"- {d.skill}: {d.count} jobs ({'found in profile' if d.in_cv else 'NOT found in profile'})")
    lines.append("\n## Jobs (number: title · employer)")
    for i, job in enumerate(ev.jobs, start=1):
        lines.append(f"J{i}: {job.title} · {ev.employers.get(job.job_id) or job.scheme}")
    if ev.missing:
        lines.append("\n## What each job's assessment said the student is missing")
        lines += [f"J{n}: {'; '.join(items)}" for n, items in ev.missing]
    if ev.blockers:
        lines.append("\n## Hard blockers found")
        lines += [f"J{n}: {'; '.join(items)}" for n, items in ev.blockers]
    return "\n".join(lines)


# --- Step 2: the summary, by Claude ---------------------------------------------------

SYSTEM_PROMPT = """You are a frank, practical career coach for a university student. You get the
student's profile (CV, projects, skills) and evidence collected from the job listings they are
targeting: how often each skill is asked for, what each job's assessment said is missing, and
hard blockers. Jobs are numbered J1, J2, ...

Summarise the student's WEAKNESSES for these jobs, grouping related items (e.g. "C++", "low-level
performance" -> one systems-programming weakness). Order by impact: how many jobs it costs x how
much it matters. For each weakness:
- area: short name (max 6 words); kind: "skill" (not there), "experience" (no real-world use),
  "evidence" (they likely have it but the CV doesn't show it), or "eligibility" (degree, year,
  graduation date, language, location);
- severity: "high" (blocks many target jobs), "medium", "low";
- jobs_affected: how many of the analysed jobs it affects (an estimate from the evidence);
- jobs: up to 4 job numbers as "J3" that show it;
- why: one or two sentences, specific to this student;
- fix: 1-3 concrete actions (a project to build, a course module, what to add to the CV),
  sized for a first/second-year student; effort: "days", "weeks" or "months".
The keyword check can be wrong (a skill may be in the CV in other words): use the profile text.
Also give: headline (one sentence, the big picture), strengths (up to 4 things the CV clearly
shows that these jobs want, with the evidence), cv_fixes (up to 4 edits to the CV wording that
would surface skills the student already has), later (eligibility rules only time fixes, with
what to target meanwhile), plan (2-4 steps for the next 4-8 weeks, most valuable first).
Never invent experience the profile doesn't show. Max 7 weaknesses."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "weaknesses": {
            "type": "array",
            "maxItems": 7,
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string"},
                    "kind": {"type": "string", "enum": ["skill", "experience", "evidence", "eligibility"]},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "jobs_affected": {"type": "integer", "minimum": 0},
                    "jobs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "why": {"type": "string"},
                    "fix": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                    "effort": {"type": "string", "enum": ["days", "weeks", "months"]},
                },
                "required": ["area", "kind", "severity", "jobs_affected", "why", "fix", "effort"],
            },
        },
        "strengths": {
            "type": "array", "maxItems": 4,
            "items": {"type": "object", "properties": {"skill": {"type": "string"}, "evidence": {"type": "string"}},
                      "required": ["skill", "evidence"]},
        },
        "cv_fixes": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "later": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "plan": {
            "type": "array", "maxItems": 4,
            "items": {"type": "object", "properties": {"when": {"type": "string"}, "do": {"type": "string"}},
                      "required": ["when", "do"]},
        },
    },
    "required": ["headline", "weaknesses", "strengths", "cv_fixes", "later", "plan"],
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class GapsError(RuntimeError):
    """No usable report (nothing to analyse, or the AI answer was unusable)."""


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _job_ref(ev: Evidence, job: Job) -> dict:
    """What the page needs to link a job: id, a tidy short title, employer."""
    title = " ".join(job.title.split())
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].rstrip(" -–(（,") + "…"
    return {"id": job.job_id, "title": title, "employer": ev.employers.get(job.job_id, "")}


def _validate(answer: dict, ev: Evidence) -> dict:
    """Keep only well-formed parts; map "J3" to real job ids; clamp counts."""
    total = len(ev.jobs)
    weaknesses = []
    for item in answer.get("weaknesses") or []:
        if not isinstance(item, dict) or not _text(item.get("area"), 80) or not item.get("fix"):
            continue
        jobs = []
        for ref in item.get("jobs") or []:
            m = re.fullmatch(r"\s*J?(\d+)\s*", str(ref))
            if m and 1 <= int(m.group(1)) <= total:
                job = ev.jobs[int(m.group(1)) - 1]
                if job.job_id not in [j["id"] for j in jobs]:
                    jobs.append(_job_ref(ev, job))
        try:
            affected = max(0, min(int(item.get("jobs_affected") or 0), total))
        except (TypeError, ValueError):
            affected = 0
        weaknesses.append({
            "area": _text(item["area"], 80),
            "kind": item.get("kind") if item.get("kind") in ("skill", "experience", "evidence", "eligibility") else "skill",
            "severity": item.get("severity") if item.get("severity") in SEVERITY_ORDER else "medium",
            "jobs_affected": affected,
            "jobs": jobs[:4],
            "why": _text(item.get("why"), 400),
            "fix": [_text(f, 240) for f in item["fix"] if _text(f, 240)][:3],
            "effort": item.get("effort") if item.get("effort") in ("days", "weeks", "months") else "weeks",
        })
    if not weaknesses:
        raise GapsError("the AI returned no usable weaknesses; try again")
    weaknesses.sort(key=lambda w: (SEVERITY_ORDER[w["severity"]], -w["jobs_affected"]))
    # The AI also cites jobs inside its sentences ("apply to J8 and J21"): keep a lookup
    # so the page can turn those into links with the job's title.
    refs = {}
    for number in re.findall(r"\bJ(\d+)\b", json.dumps(answer)):
        if 1 <= int(number) <= total:
            refs[f"J{number}"] = _job_ref(ev, ev.jobs[int(number) - 1])
    return {
        "refs": refs,
        "headline": _text(answer.get("headline"), 300),
        "weaknesses": weaknesses,
        "strengths": [
            {"skill": _text(s.get("skill"), 60), "evidence": _text(s.get("evidence"), 240)}
            for s in answer.get("strengths") or [] if isinstance(s, dict) and s.get("skill")
        ][:4],
        "cv_fixes": [_text(s, 240) for s in answer.get("cv_fixes") or [] if _text(s, 240)][:4],
        "later": [_text(s, 240) for s in answer.get("later") or [] if _text(s, 240)][:3],
        "plan": [
            {"when": _text(p.get("when"), 40), "do": _text(p.get("do"), 300)}
            for p in answer.get("plan") or [] if isinstance(p, dict) and p.get("do")
        ][:4],
    }


def summarize(ev: Evidence, profile: Profile, llm: LLMProvider) -> dict:
    """One AI call -> validated report. Raises GapsError (or ProviderError from the provider)."""
    if not ev.jobs:
        raise GapsError("no jobs to analyse yet: run a search first")
    prompt = f"# Student profile\n{profile.as_text()}\n\n# Evidence from {len(ev.jobs)} target jobs\n{evidence_text(ev)}"
    for _attempt in range(2):
        try:
            answer = llm.complete_json(SYSTEM_PROMPT, prompt, schema=SCHEMA)
        except InvalidJSONResponse:
            continue
        report = _validate(answer, ev)
        report.update({
            "jobs_analysed": len(ev.jobs),
            "model": getattr(llm, "llm_model", ""),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        return report
    raise GapsError("the AI answer was not valid JSON twice; try again")


# --- Storage -------------------------------------------------------------------------


def load_reports() -> dict[str, dict]:
    try:
        data = json.loads(config.GAPS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_report(scope: str, key: str, report: dict) -> None:
    data = load_reports()
    data[scope] = {**report, "key": key}
    config.GAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.GAPS_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # it describes the student
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.GAPS_FILE)
