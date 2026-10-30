# CLAUDE.md

## Project

A self-hosted CityU job recommender: Playwright scrapers, AI job-level analysis,
embedding retrieval and priority-weighted ranking, behind a localhost FastAPI UI
(CLI later). The design is in `architecture.md`; the learner guide is in `docs/`.
The core (`src/cityu_jobs/core/`, entry point `pipeline.py`) and the web UI
(`src/cityu_jobs/web/`) are built by an agent team; `MULTI_AGENT_PLAN.md` holds the
task list and team messages.

## Working in this repo

- The owner is learning (VibeWise learning mode). Normally they make the design
  decisions. For now they have delegated them to the Architect agent: those decisions
  are marked [Delegated] in `architecture.md`, with the reasoning and alternatives, so
  the owner can review them. Don't implement beyond what the plan or the owner approved.
  Learner notes live in `.vibe-wise/`.
- Keep scrape/analyze/embed/rank logic in a shared core. Interfaces (web, CLI)
  stay thin.
- Never handle or store AIMS passwords. Logging in happens in a headed browser,
  and only session cookies are kept locally.
- Cache AI results by job code. Invalidate them when the listing's content hash
  or the embedding model changes.
- Keep `architecture.md` up to date when a design decision changes.
- AI: Claude via the owner's Claude plan (`claude -p`, never an API key) is the default
  LLM since 2026-10-30; embeddings always run in local Ollama. Tests must never start
  `claude` or reach the network (tests/conftest.py blocks real HTTP).
- The student's profile (`.data/profile.json`), letters and tracker are personal data:
  mode 600, never commit, never print them. The app never submits applications.
- `CITYU_JOBS_DATA_DIR=/some/dir` points the app at another data folder (demos, screenshots).

## Commands

- `uv sync`: install dependencies into `.venv/`
- `uv run playwright install chromium`: one-time browser download for Playwright
- `uv run pytest`: offline tests, no login or Ollama needed (fixture-based ones skip if
  `.data/fixtures/` is missing)
- `uv run python scripts/check_auth.py`: log in to AIMS if needed (opens a browser
  window for the student) and confirm the saved session works
- `uv run python scripts/scrape_aims_test.py`: scrape one Campus Internship Scheme job
  into `.data/aims_test_job.json`
- `uv run python scripts/scrape_aims.py --no-login [cis] [sh_summer] [sh_regular]`:
  scrape AIMS schemes into `.data/cache.json`; agents always pass `--no-login`
- `uv run uvicorn cityu_jobs.web.app:app --reload`: run the web app at http://127.0.0.1:8000
