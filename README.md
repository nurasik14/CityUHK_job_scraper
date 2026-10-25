# CityUHK_job_scraper

A self-hosted job recommender for CityU students. It collects Campus Internship
Scheme, Student Helper and ITC STEM listings, uses a local AI model to rate each
job, and ranks them by what you describe and how you weigh **earning money**
against **learning / building a portfolio**.

> **Status (2026-10-25): first version complete, not yet tested live.** The full
> pipeline and the localhost web UI are built and checked offline. The full AIMS
> scrape still has to be tried with a real login. See
> [MULTI_AGENT_PLAN.md](MULTI_AGENT_PLAN.md).

How it works, stage by stage, with the ML concepts explained:
[docs/how-it-works.md](docs/how-it-works.md). What is stored about each job:
[docs/data-model.md](docs/data-model.md). Full design: [architecture.md](architecture.md).

## Privacy: everything stays on your machine

- **Your password never reaches the tool.** You type it only into the real CityU
  login page, in a browser window on your own computer. The tool keeps only the
  session cookies AIMS gives the browser afterwards.
- **The session file is a secret.** `.data/aims_session.json` logs anyone who has it
  into AIMS as you, until it expires. It's readable only by your user, git-ignored,
  and never printed. Don't share or commit it. Delete it to log out of the tool.
- **AI runs locally.** Job ratings and embeddings come from [Ollama](https://ollama.com)
  on `localhost`. No job text, search query or credential is sent to an external AI
  service, and the tool has no API keys.
- **The web app is local only.** It listens on `127.0.0.1`, so other computers can't
  reach it. Form submissions from other websites are refused, so a web page you visit
  can't trigger the login window or change your settings.
- **Local data.** Scraped jobs, AI ratings and embeddings are cached in
  `.data/cache.json`, and your Ollama settings are in `.data/settings.json`. The whole
  `.data/` folder is git-ignored.
- **Read-only on AIMS.** The scraper only searches and opens job descriptions, with
  a 1.5 s pause between page loads. It never touches the apply form or any
  apply/submit button.
- Whether CityU's rules allow automated access to AIMS is not confirmed. Using it is
  your call.

## Setup

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and
[Ollama](https://ollama.com).

```sh
uv sync                                # install Python dependencies into .venv/
uv run playwright install chromium     # one-time browser download for Playwright

ollama pull llama3.2:3b                # LLM that rates each job's level (1-5)
ollama pull nomic-embed-text           # embedding model for matching your description
```

Ollama must be running (`ollama serve`, or the desktop app) at
`http://localhost:11434`. You can choose other installed Ollama models on the
app's Settings page.

## Running the web app

```sh
uv run uvicorn cityu_jobs.web.app:app --reload
```

Open <http://127.0.0.1:8000>.

1. **Search page.**
   - Pick your department (Computer Science is the default). It's a boost, not a filter.
   - Describe the job you want, e.g. "machine learning research, backend development".
     The example chips under the box add a phrase when clicked; they only appear when
     JavaScript is on.
   - For **Earn money** and **Learn / build a portfolio**, pick one of five levels:
     **Not important · Low · Medium · High · Top priority** (1–5). On a keyboard, Tab
     to the group and use the arrow keys.
   - Tick **Refresh now** to re-scrape even if the data is fresh. Normally a source is
     re-scraped only if it's more than 6 hours old.
2. **Results page.** While it works, you see the current stage, the elapsed time and
   placeholder cards. It's safe to leave the page and come back. Then the top 20 open
   jobs appear as cards (see below). A summary bar with your search stays at the top
   while you scroll, with a **Change** link. **Scrape notes** says what was fetched or
   skipped.
3. **Settings page.** Set the Ollama URL and the LLM and embedding model names. A
   pill shows whether Ollama is reachable, and chips list the models you have
   installed.

The **theme button** in the header cycles **System → Light → Dark**. "System"
follows your computer's setting, and the choice is remembered in this browser.

### Reading a job card

```
  ◜ 86 ◝   ★ Top match
  ◟match◞  Machine Learning Intern                         [ITC STEM]
           CC · Closes 06 Nov 2026  (⏱ Closes in 5 days)
           "Build and evaluate ML models for a learning platform…"
           ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
           Level ●●●●○  "Builds and evaluates ML models for a product team."
           ▸ Why this score      ▸ Details
```

The values in this sketch are illustrative, for earn = Not important and learn =
Top priority.

- **Match score (0–100), in the ring.** It shows how close this job comes to the
  *best possible job for your priorities*: 100 would be a perfect match. The number
  is relative to your own priorities. With earn set to Not important, hours don't count,
  so the best possible job is one that fits your description and is advanced, and
  that's what 100 means for you. Scores from searches with different priorities
  aren't comparable.
- **Top match.** The #1 card is highlighted, with a bigger ring and a short teaser of
  the job. #2–3 are standard cards. From #4 the cards are quieter, so the best
  results stand out.
- **The bar** shows the match score split into its parts, in three colour families.
  The key is shown once, at the top of the results:
  - **Relevance** (indigo / slate): how close the job is to your description, plus
    the department boost.
  - **Earn** (amber): hours per week, plus a bonus for Student Helper jobs, scaled by
    your earn priority.
  - **Learn** (green): the AI-rated level, plus a bonus for internships and ITC STEM,
    scaled by your learn priority.
- **Why this score** opens the exact numbers. It lists each part that counted, as
  points (e.g. relevance 46, dept 12, level 18, internship 10 = 86), plus the raw
  score, e.g. "Raw score 0.71 of 0.83 possible for your priorities". Points are
  rounded, so they may add up to one point off.
- **Closing chip.** Red for 0–3 days left, amber for 4–7, grey for 8–14. There is no
  chip further out. Closed jobs are never shown.
- **Level meter**: 1–5 filled dots for the AI's rating, with its one-line reason.
  "Not rated yet" means the AI hasn't rated the job; it then counts as level 3.
- **Details** has the full description, requirements, period and any extra
  honorarium. For AIMS jobs it shows the job number to apply with in AIMS. The tool
  never links to or touches the apply form.

**The first search is slow: plan for about 45 minutes.**

- **AIMS scrape: about 35 minutes the first time.** That's about 17 s for each of the
  ~117 jobs (measured: CIS 45 jobs ≈ 13 min, Student Helpers Regular 72, Summer 0).
  This is deliberate. The scraper waits 1.5 s before every page load, opens one job
  at a time, and re-runs the search after each job instead of pressing "back". So
  each job costs three AIMS page loads.
- **AI rating: about 7 s per job** with `llama3.2:3b` on a laptop CPU.
- **After the first run, searches are fast.** AIMS is re-checked only when its data
  is over 6 hours old. Even then, only new or changed listings are opened and rated.
- **You can stop and resume.** Progress is saved every few jobs and when the run
  stops (Ctrl+C, an expired session, an error). The next run skips the jobs already
  opened.
- **Tip:** run `uv run python scripts/scrape_aims.py` once in a terminal (e.g. while
  doing something else). Later searches in the web app then start from a full cache.

**Switching the LLM model doesn't re-rate jobs.** A stored level is reused until the
listing itself changes. Re-rating everything would take minutes, and 1–5 levels
mostly agree across models. After a switch, old and new ratings are mixed until
listings change; the Settings page warns about this. Switching the *embedding* model
is different: all jobs are re-embedded, because vectors from two models can't be
compared.

### Logging in to AIMS (the "Log in to AIMS" button)

Campus Internship and Student Helper jobs need your AIMS login:

1. Click **Log in to AIMS** on the search page, or in the "AIMS login needed" banner.
2. A browser window opens on your computer at the CityU login page. Log in normally,
   including two-factor. You have 5 minutes.
3. The window closes and the session cookies are saved. Run your search again.

The app **never opens the login window on its own**. If your session is missing or
expired when you search, it doesn't stop. It shows the "AIMS login needed" banner,
ranks the AIMS jobs from the last successful scrape (if any) and the ITC STEM jobs,
and waits for you to click the button. AIMS sessions expire after a while, so expect
to log in again from time to time.

### ITC STEM job list (one manual step)

ITC STEM jobs come from the College of Computing's Excel job list. The download
needs a CityU portal login, which the tool doesn't attempt, so you save the file
yourself:

1. Open the [College of Computing ITC STEM page](https://www.cityu.edu.hk/en/cc/student-life-and-learning/internship-schemes/itc-stem-internship-scheme)
   in your browser and download the job list (`.xlsx`).
2. Save it as `.data/itc/cc_job_list.xlsx`. Any `.xlsx` file in `.data/itc/` also
   works, and the newest is used.

If the file is missing, the results page says so, and you still get the AIMS jobs.

## Command-line scripts

These are for checking things by hand. The web app doesn't need them.

```sh
uv run python scripts/check_auth.py          # log in to AIMS if needed; confirm the session works
uv run python scripts/scrape_aims.py         # scrape all AIMS schemes into .data/cache.json
uv run python scripts/scrape_aims.py cis     # ...or only some: cis, sh_summer, sh_regular
uv run python scripts/scrape_aims.py --no-login   # stop with "AIMS login needed" instead of opening a window
uv run python scripts/scrape_aims_test.py    # scrape ONE Campus Internship job to .data/aims_test_job.json
```

## Tests

```sh
uv run pytest
```

All tests run offline: no AIMS login, no network, and Ollama isn't needed (fakes
stand in for the browser and the AI). Tests that use saved AIMS pages in
`.data/fixtures/` skip when those pages are missing. The pages contain personal
data, so they aren't in git.

## Stack

Python, Playwright (browser automation), BeautifulSoup (HTML parsing), openpyxl
(ITC STEM spreadsheets), httpx + Ollama (local LLM and embeddings), and FastAPI +
Jinja2 + uvicorn for the localhost web UI. A CLI is planned later.
