"""FastAPI routes. Thin: read the form, call the core (through TaskRunner), render a template.

Run with: uv run uvicorn cityu_jobs.web.app:app --reload   (then open http://127.0.0.1:8000)
uvicorn listens on 127.0.0.1 by default, so only this computer can reach the app.
"""

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cityu_jobs import config
from cityu_jobs.core.departments import department_name, load_departments
from cityu_jobs.core.ranking import Preferences, max_possible_score
from cityu_jobs.core.settings import Settings
from cityu_jobs.web.tasks import TaskRunner

WEB_DIR = Path(__file__).parent

# How each source is labelled on the results page.
SOURCE_LABELS = {
    "aims_cis": "Campus Internship",
    "aims_sh_summer": "Student Helper (Summer)",
    "aims_sh_regular": "Student Helper (Regular)",
    "itc_stem": "ITC STEM",
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


async def _form(request: Request) -> dict[str, str]:
    """Parse a plain HTML form post (avoids the python-multipart dependency)."""
    body = (await request.body()).decode("utf-8")
    return {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}


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


def create_app(runner: TaskRunner | None = None) -> FastAPI:
    """Build the app. Tests pass a TaskRunner with a fake search function."""
    app = FastAPI(title="CityU Job Recommender")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.globals.update(
        source_labels=SOURCE_LABELS,
        term_labels=TERM_LABELS,
        term_groups=TERM_GROUPS,
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
    def results(request: Request):
        state = runner.state
        prefs = state.prefs
        return render(
            request,
            "results.html",
            prefs=prefs,
            department=department_name(prefs.department_code) if prefs else None,
            report=state.report,
            results=state.results[: config.RESULTS_LIMIT],
            total=len(state.results),
            today=date.today(),
            # The 0-100 match is score / the best possible score for *these* priorities.
            max_score=max_possible_score(prefs) if prefs else 1.0,
        )

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
        return render(request, "settings.html", settings=settings, installed=installed, problem=problem, saved=saved)

    @app.post("/settings")
    async def save_settings(request: Request):
        _check_same_origin(request)
        form = await _form(request)
        defaults = Settings()
        Settings(
            ollama_url=form.get("ollama_url", "").strip() or defaults.ollama_url,
            llm_model=form.get("llm_model", "").strip() or defaults.llm_model,
            embed_model=form.get("embed_model", "").strip() or defaults.embed_model,
        ).save()
        return RedirectResponse("/settings?saved=1", status_code=303)

    return app


app = create_app()
