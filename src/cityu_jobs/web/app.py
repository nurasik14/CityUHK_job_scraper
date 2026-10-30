"""FastAPI routes. Thin: read the form, call the core (through TaskRunner), render a template.

Run with: uv run uvicorn cityu_jobs.web.app:app --reload   (then open http://127.0.0.1:8000)
uvicorn listens on 127.0.0.1 by default, so only this computer can reach the app.
"""

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import json
import shutil
import subprocess

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cityu_jobs import config
from cityu_jobs.core.apply_info import employer
from cityu_jobs.core.departments import department_name, load_departments
from cityu_jobs.core.letters import load_tracker
from cityu_jobs.core.ranking import Preferences, max_possible_score
from cityu_jobs.core.settings import Settings
from cityu_jobs.web import extra_routes
from cityu_jobs.web.tasks import TaskRunner

WEB_DIR = Path(__file__).parent

# How each source is labelled on the results page.
SOURCE_LABELS = {
    "aims_cis": "Campus Internship",
    "aims_sh_summer": "Student Helper (Summer)",
    "aims_sh_regular": "Student Helper (Regular)",
    "itc_stem": "ITC STEM",
    "intern_bigtech": "Big tech internship",
    "intern_tech": "Tech internship",
}
# Result filters (?show=...): label and the sources each one keeps. "all" keeps everything.
RESULT_GROUPS = {
    "all": ("All", None),
    "cityu": ("CityU jobs", {"aims_cis", "aims_sh_summer", "aims_sh_regular", "itc_stem"}),
    "bigtech": ("Big tech", {"intern_bigtech"}),
    "tech": ("Other tech", {"intern_tech"}),
}
# Score terms in display order, with short labels.
TERM_LABELS = {
    "similarity": "relevance",  # "match" is the overall 0-100 score
    "department": "dept",
    "hours": "hours",
    "student_helper": "student helper",
    "level": "level",
    "learning_scheme": "internship",
}
# The legend groups the terms by the student's priorities: what they asked for, earning, learning.
TERM_GROUPS = [
    ("Relevance", ["similarity", "department"]),
    ("Earn", ["hours", "student_helper"]),
    ("Learn", ["level", "learning_scheme"]),
]


async def _form_lists(request: Request) -> dict[str, list[str]]:
    """Parse a plain HTML form post (avoids the python-multipart dependency); every value as a list."""
    body = (await request.body()).decode("utf-8")
    return parse_qs(body, keep_blank_values=True)


async def _form(request: Request) -> dict[str, str]:
    """Like _form_lists, keeping the last value of each field."""
    return {k: v[-1] for k, v in (await _form_lists(request)).items()}


def claude_status() -> tuple[bool, str]:
    """(ready, message) for the Settings page: is the `claude` CLI installed and logged in?"""
    if not shutil.which("claude"):
        return False, "Claude Code (`claude`) is not installed."
    try:
        done = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=10)
        status = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False, "Could not check the Claude Code login (`claude auth status`)."
    if not status.get("loggedIn"):
        return False, "Claude Code is not logged in: run `claude` in a terminal and log in."
    if status.get("authMethod") not in ("claude.ai", None):
        return True, f"Logged in with {status.get('authMethod')} (not a Claude plan: this may be billed per use)."
    return True, f"Logged in with your Claude plan ({status.get('email') or 'claude.ai'})."


def _check_same_origin(request: Request) -> None:
    """Refuse form posts sent by other websites open in the student's browser (CSRF).

    Browsers send an Origin header with cross-site POSTs; ours must come from this app.
    """
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _priority(value: str | None) -> int:
    try:
        return min(max(int(value or 3), 1), 5)
    except ValueError:
        return 3


def create_app(runner: TaskRunner | None = None, claude_status_fn=None) -> FastAPI:
    """Build the app. Tests pass a TaskRunner with a fake search function and a fake Claude check."""
    app = FastAPI(title="CityU Job Recommender")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    # "2 Dec 2026": the same style as the calendar script.
    templates.env.filters["day"] = lambda d: f"{d.day} {d.strftime('%b %Y')}" if d else ""
    # "claude:sonnet" -> "Claude Sonnet"; local models keep their Ollama name.
    templates.env.filters["model_name"] = lambda m: (
        "Claude " + m.split(":", 1)[1].capitalize() if str(m).startswith("claude:") else str(m)
    )
    templates.env.globals.update(
        source_labels=SOURCE_LABELS,
        term_labels=TERM_LABELS,
        term_groups=TERM_GROUPS,
        result_groups=RESULT_GROUPS,
        employer_of=employer,
        verdict_labels={"strong match": "Strong match", "good match": "Good match", "stretch": "Stretch",
                        "long shot": "Long shot", "not eligible": "Not eligible"},
        stale_hours=config.STALE_AFTER_HOURS,
    )
    app.state.runner = runner = runner or TaskRunner()

    def render(request: Request, name: str, **context) -> HTMLResponse:
        context.setdefault("task", runner.snapshot())
        return templates.TemplateResponse(request, name, context)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        prefs = runner.state.prefs or Preferences(config.DEFAULT_DEPARTMENT, "", earn=3, learn=3)
        return render(request, "index.html", departments=load_departments(), prefs=prefs)

    @app.post("/search")
    async def search(request: Request):
        _check_same_origin(request)
        form = await _form(request)
        prefs = Preferences(
            department_code=form.get("department") or None,
            query=form.get("query", "").strip(),
            earn=_priority(form.get("earn")),
            learn=_priority(form.get("learn")),
        )
        runner.start_search(prefs, Settings.load(), force_refresh=form.get("refresh") == "on")
        return RedirectResponse("/results", status_code=303)

    @app.get("/results", response_class=HTMLResponse)
    def results(request: Request, show: str = "all", eligible: bool = False):
        state = runner.state
        prefs = state.prefs
        show = show if show in RESULT_GROUPS else "all"
        keep = RESULT_GROUPS[show][1]
        # Rank numbers stay the overall ones, so "#7" means the same job under every filter.
        numbered = [
            (i, r) for i, r in enumerate(state.results, start=1)
            if (keep is None or r.job.source in keep) and not (eligible and r.fit and not r.fit.get("eligible", True))
        ]
        counts = {
            key: sum(1 for r in state.results if sources is None or r.job.source in sources)
            for key, (_label, sources) in RESULT_GROUPS.items()
        }
        shown = numbered[: config.RESULTS_LIMIT]
        calendar = [
            {
                "id": f"job-{rank}",
                "rank": rank,
                "title": r.job.title,
                "org": r.job.company or r.job.department_name or r.job.department_code or "",
                "source": r.job.source,
                **(r.timeline.to_dict() if r.timeline else {}),
            }
            for rank, r in shown
        ]
        return render(
            request,
            "results.html",
            prefs=prefs,
            department=department_name(prefs.department_code) if prefs else None,
            report=state.report,
            results=shown,
            total=len(state.results),
            show=show,
            eligible=eligible,
            has_fit=any(r.fit for r in state.results),
            hidden_ineligible=sum(1 for r in state.results if r.fit and not r.fit.get("eligible", True)),
            tracker=load_tracker(),
            counts=counts,
            calendar=calendar,
            today=date.today(),
            # The 0-100 match is score / the best possible score for *these* priorities.
            max_score=max_possible_score(prefs) if prefs else 1.0,
        )

    extra_routes.register(app, render, runner, _check_same_origin)

    @app.get("/api/status")
    def status():
        return JSONResponse(runner.snapshot())

    @app.post("/login")
    def aims_login(request: Request):
        """Opens a visible browser *on this computer* for the student to log in to AIMS."""
        _check_same_origin(request)
        runner.start_login()
        return RedirectResponse("/results" if runner.state.prefs else "/", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: bool = False):
        settings = Settings.load()
        installed, problem = [], ""
        try:  # list the installed models as a hint; the app works without this
            response = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=2)
            installed = sorted(m["name"] for m in response.json().get("models", []))
        except (httpx.HTTPError, ValueError, KeyError):
            problem = f"Could not reach Ollama at {settings.ollama_url}. Is it running (`ollama serve`)?"
        claude_ready, claude_message = (claude_status_fn or claude_status)()
        return render(
            request,
            "settings.html",
            settings=settings,
            installed=installed,
            problem=problem,
            saved=saved,
            claude_ready=claude_ready,
            claude_message=claude_message,
            claude_models=config.CLAUDE_MODELS,
            regions=config.INTERN_REGIONS,
        )

    @app.post("/settings")
    async def save_settings(request: Request):
        _check_same_origin(request)
        lists = await _form_lists(request)
        form = {k: v[-1] for k, v in lists.items()}
        defaults = Settings()
        provider = form.get("ai_provider", "")
        claude_model = form.get("claude_model", "")
        regions = [r for r in lists.get("intern_regions", []) if r in config.INTERN_REGIONS]
        Settings(
            ai_provider=provider if provider in config.AI_PROVIDERS else defaults.ai_provider,
            claude_model=claude_model if claude_model in config.CLAUDE_MODELS else defaults.claude_model,
            ollama_url=form.get("ollama_url", "").strip() or defaults.ollama_url,
            llm_model=form.get("llm_model", "").strip() or defaults.llm_model,
            embed_model=form.get("embed_model", "").strip() or defaults.embed_model,
            # The regions fieldset is always on the form; ticking none means "use the defaults".
            intern_regions=regions or defaults.intern_regions,
        ).save()
        return RedirectResponse("/settings?saved=1", status_code=303)

    return app


app = create_app()
