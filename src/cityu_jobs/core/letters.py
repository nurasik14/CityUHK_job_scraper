"""Write a tailored cover letter / application email, and remember which jobs you applied to.

The AI gets the job (listing, employer, how to apply) and the student's profile and writes:

    {"subject": "...", "letter": "...", "email": "...", "answers": [{"question": ..., "answer": ...}]}

- letter: a one-page cover letter (plain text, ready to paste or save as PDF),
- email: a short email to send with the CV (for ITC STEM jobs that say "send your CV to ..."),
- answers: drafts for the questions application forms usually ask ("Why this company?").

Nothing is ever sent or submitted by the app: the student reads, edits and sends it themselves.
Letters are saved as .data/letters/<job>.json so they survive restarts.
"""

import json
import os
import re
from datetime import datetime

from cityu_jobs import config
from cityu_jobs.core.ai.providers import LLMProvider
from cityu_jobs.core.analysis import _job_prompt
from cityu_jobs.core.apply_info import ApplyInfo
from cityu_jobs.core.models import Job
from cityu_jobs.core.profile import Profile

SYSTEM_PROMPT = """You write job applications for a university student. Use ONLY facts from the
student's profile: never invent experience, grades, skills, dates or numbers. If the profile
lacks something the job wants, don't claim it; show the closest real evidence or eagerness to learn.

Write:
- subject: an email subject line, e.g. "Application: <job title> – <name>".
- letter: a cover letter of 230-330 words, plain text, no placeholders except where a fact is
  truly unknown (then write it in [square brackets]). Structure: greeting ("Dear Hiring Team," or
  the contact person if given), why this role and employer (specific, from the listing), 2 short
  paragraphs linking the student's most relevant projects/skills to the job's duties with concrete
  details, availability (dates if the job has them), polite close, the student's name and email.
- email: a 70-120 word email to send with the CV attached, same facts, ends with the name.
- answers: 3 short drafts (40-90 words each) for questions application forms often ask:
  "Why are you interested in this role/company?", "Describe a relevant project.", and one
  question specific to this job.
Tone: confident, warm, concise, no clichés ("I am writing to express my keen interest"),
no exaggeration. English unless the listing is in another language."""

SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "letter": {"type": "string"},
        "email": {"type": "string"},
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["question", "answer"],
            },
            "maxItems": 4,
        },
    },
    "required": ["subject", "letter", "email", "answers"],
}


class LetterError(RuntimeError):
    """The AI answer was not a usable letter."""


def _slug(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", job_id)[:120]


def _path(job_id: str):
    return config.LETTERS_DIR / f"{_slug(job_id)}.json"


def write_letter(job: Job, info: ApplyInfo, profile: Profile, llm: LLMProvider, notes: str = "") -> dict:
    """Generate, save and return the application texts for `job`. Raises LetterError / ProviderError."""
    how = "\n".join(f"- {s}" for s in info.steps)
    prompt = "\n\n".join(
        part for part in [
            f"# Student profile\n{profile.as_text()}",
            f"Student's email: {profile.email}" if profile.email else "",
            f"# Job\nEmployer: {info.employer}\n{_job_prompt(job, 5000)}",
            f"Contact person: {info.contact}" if info.contact else "",
            f"# How to apply\n{how}" if how else "",
            f"# Notes from the student (follow them)\n{notes.strip()}" if notes.strip() else "",
        ] if part
    )
    answer = llm.complete_json(SYSTEM_PROMPT, prompt, schema=SCHEMA)
    letter = str(answer.get("letter", "")).strip()
    if len(letter) < 200:
        raise LetterError("the AI returned an empty or very short letter; try again")
    result = {
        "job_id": job.job_id,
        "title": job.title,
        "employer": info.employer,
        "subject": str(answer.get("subject", "")).strip()[:200] or f"Application: {job.title}",
        "letter": letter,
        "email": str(answer.get("email", "")).strip(),
        "answers": [
            {"question": str(a.get("question", "")).strip(), "answer": str(a.get("answer", "")).strip()}
            for a in answer.get("answers") or [] if isinstance(a, dict) and a.get("answer")
        ][:4],
        "notes": notes.strip(),
        "model": getattr(llm, "llm_model", ""),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    save_letter(result)
    return result


def save_letter(letter: dict) -> None:
    config.LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(letter["job_id"])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(letter, f, ensure_ascii=False, indent=2)


def load_letter(job_id: str) -> dict | None:
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Application tracker -------------------------------------------------------------

STATUSES = {"saved": "Saved", "applied": "Applied", "": "Not tracked"}


def load_tracker() -> dict[str, dict]:
    """{job_id: {"status": "saved" | "applied", "at": ISO time, "title": ...}}"""
    try:
        data = json.loads(config.TRACKER_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_status(job: Job, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    data = load_tracker()
    if status:
        data[job.job_id] = {
            "status": status,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "title": job.title,
        }
    else:
        data.pop(job.job_id, None)
    config.TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.TRACKER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, config.TRACKER_FILE)
