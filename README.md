# CityUHK_job_scraper

A self-hosted job recommender for CityU students. It collects Campus Internship
Scheme, Student Helper and ITC STEM listings, plus **tech internships** from big tech
(Amazon, Microsoft, NVIDIA, TikTok, Stripe, …) and ~50 smaller tech, AI, fintech and
quant companies. Claude (through your Claude plan) rates each job, and the app ranks
them by what you describe and how you weigh **earning money** against **learning /
building a portfolio**. A calendar shows each job's deadline, start and duration.

Each job has its own page with the employer, **how to apply** (email, AIMS job number or
the company's application page) and every field of the listing. Add your CV and projects
on the **Profile** page to see a **difficulty for you** estimate (1 = well within reach,
5 = long shot, with blockers such as "PhD students only") and to have Claude write a
**cover letter**, an application email and drafts for common form questions. The app
never submits anything: you check the text and send it yourself.

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
- **Which AI sees what.** By default, job ratings come from **Claude**, run through the
  Claude Code CLI (`claude -p`) with your **Claude plan** login (Pro/Max), so there is
  no API key in this app. That means **job descriptions are sent to Anthropic**. Your
  search text is not: matching (embeddings) always runs locally in
  [Ollama](https://ollama.com). Pick "Ollama, free and local" on the Settings page to
  keep everything on your machine. If `ANTHROPIC_API_KEY` is set in your shell, the
  app removes it before starting `claude`, so you are never billed per use by accident.
- **The web app is local only.** It listens on `127.0.0.1`, so other computers can't
  reach it. Form submissions from other websites are refused, so a web page you visit
  can't trigger the login window or change your settings.
- **Your profile (CV, projects) is personal data.** It is stored in
  `.data/profile.json` (readable only by you). Only the CV's text is kept, not the
  uploaded file. With Claude selected, the profile is **sent to Anthropic** when the app
  estimates difficulty or writes a letter. Cover letters are saved in `.data/letters/`
  and your Saved/Applied marks in `.data/applications.json`. "Delete my profile" on the
  Profile page removes the profile.
- **Local data.** Scraped jobs, AI ratings and embeddings are cached in
  `.data/cache.json`, and your Ollama settings are in `.data/settings.json`. The whole
  `.data/` folder is git-ignored.
- **Read-only on AIMS.** The scraper only searches and opens job descriptions, with
  a 1.5 s pause between page loads. It never touches the apply form or any
  apply/submit button.
- Whether CityU's rules allow automated access to AIMS is not confirmed. Using it is
  your call.

## Setup

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com) (for embeddings), and, for the default AI,
[Claude Code](https://claude.com/claude-code) logged in with your Claude plan
(run `claude` once and log in; `claude auth status` should say `"authMethod": "claude.ai"`).

```sh
uv sync                                # install Python dependencies into .venv/
uv run playwright install chromium     # one-time browser download for Playwright

ollama pull nomic-embed-text           # embedding model for matching your description (always needed)
ollama pull llama3.2:3b                # only if you pick Ollama instead of Claude for rating
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
3. **Settings page.**
   - **AI that rates jobs:** Claude with your plan (default; Haiku, Sonnet or Opus) or
     Ollama (free, local). A pill shows whether Claude Code is installed and logged in.
   - **Ollama:** URL and the embedding model (always used), plus the local LLM used when
     Ollama is chosen. Chips list the models you have installed.
   - **Internship regions:** Hong Kong, Mainland China, Singapore & rest of Asia-Pacific
     and Remote are on by default; Europe & UK and North America are opt-in (they
     usually need a work visa). Changes apply at the next internship refresh.

### Tech internships (outside CityU)

Fetched on search when the list is over 6 hours old (about 2–3 minutes), from public,
read-only job-board APIs, the same JSON the companies' careers pages load:

- **Big tech:** Amazon, Microsoft, TikTok (own careers APIs), NVIDIA, Adobe, Intel,
  Salesforce (Workday), Stripe, Databricks, Airbnb, Cloudflare, Pinterest, Coinbase,
  Waymo, Anthropic, Palantir, Snowflake, OpenAI, Grab.
- **Other tech companies:** ~45 AI, fintech, crypto and quant firms, several hiring in
  Hong Kong or Singapore (Binance, Bybit, OKX, Airwallex, Lalamove, Animoca Brands,
  AfterShip, Optiver, IMC, Jump, Squarepoint, Point72, HRT, Scale AI, Notion, …).
- **The [SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships) list:**
  community-curated internships at hundreds of companies (mostly North America). Its
  big-tech entries (Google, Meta, Apple, …) count as big tech.

Only internships in tech roles and in your regions are kept. The full company list is in
`src/cityu_jobs/core/internships/catalog.py`; add a company by adding one line (its
Greenhouse / Lever / Ashby / SmartRecruiters board name). A board that fails is noted
under "Scrape notes" and its earlier jobs are kept.

### Calendar

On wide screens the results page has a calendar beside the list (on phones, a
collapsible "Calendar" panel above it). Rose dots are application deadlines, green dots
start dates. **Hover or Tab to a job card** and the calendar jumps to it and shows:

- the deadline (filled rose day), the start (filled green day), the end (ringed day) and
  the duration as a band between them,
- a strip from today to the end of the job, so long internships fit in one glance,
- the exact dates, e.g. "Apply by 30 Nov 2026 (in 31 days) · Starts 21 Sep 2026 · 90 days".

"Show on calendar" on a card pins it (for touch screens); Escape unpins. Dates come from
the listing (AIMS period, ITC start + duration), from what Claude read in the description,
or, for "Summer 2027"-style internships without dates, from the season. Those are
drawn striped and labelled "approx.".

The filter row (**All · CityU jobs · Big tech · Other tech**) keeps each job's overall rank
number, so #7 is the same job under every filter.

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
- **AI rating:** with Claude, 8 jobs per request and 3 requests at a time: about 10 s per
  16 jobs (measured with Sonnet), so a few minutes for every job the first time. Levels
  that a local model rated earlier are re-rated once by Claude. With Ollama's
  `llama3.2:3b`, about 7 s per job on a laptop CPU.
- **After the first run, searches are fast.** AIMS is re-checked only when its data
  is over 6 hours old. Even then, only new or changed listings are opened and rated.
- **You can stop and resume.** Progress is saved every few jobs and when the run
  stops (Ctrl+C, an expired session, an error). The next run skips the jobs already
  opened.
- **Tip:** run `uv run python scripts/scrape_aims.py` once in a terminal (e.g. while
  doing something else). Later searches in the web app then start from a full cache.

**Switching between local LLM models doesn't re-rate jobs** (switching to Claude does, once). A stored level is reused until the
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
2. Upload it on the **ITC STEM list** page (`/itc`, linked from Settings), or save it in
   `.data/itc/` yourself. The newest `.xlsx` there is used, and the next search re-reads
   it automatically.

The `/itc` page also shows the whole spreadsheet as a table you can filter, and lets you
download any saved list. "See it in the ITC STEM list" on an ITC job's page jumps to its
row. If no file is saved, the results page says so, and you still get the AIMS jobs.

### Job page, profile, difficulty and cover letters

- **Job page** (`/job/<id>`, the "How to apply" button on each card): the employer, how
  to apply (ITC: the email address and the company's own steps, with a button that opens
  your mail app with the subject and email filled in; AIMS: the job number to apply with
  in AIMS; internships: the posting's apply page), the deadline and dates, and **all
  details** of the listing. Mark jobs **Saved** or **Applied**; the mark shows on the cards.
- **Profile** (`/profile`): upload a CV (`.pdf`, `.docx`, `.tex`, `.md`, `.txt`), check
  the extracted text, and add education, skills, projects and experience. Your year of
  study matters most: many internships are only for penultimate-year or Master's students.
- **Difficulty for you** (Claude only): after each search, Claude compares your profile
  with the top 30 results overall and the top 30 of each tab (about 90 jobs, ~1-2 min the
  first time; cached afterwards until the listing or your profile changes). Cards show
  the verdict and the main blocker; "Hide not eligible" on the results page filters out
  jobs you can't apply for. Other jobs can be estimated from their page.
- **Cover letter**: on a job page, optionally add notes ("I can start in June 2027"),
  then "Write my cover letter" (~20-40 s). You get a letter (edit, copy or download
  `.txt`), a short email to send with your CV, and drafts for common application-form
  questions. The prompt forbids inventing facts not in your profile, but always read it.
- **Why no auto-apply**: AIMS and most company application forms don't allow automated
  submissions, and a sent application can't be taken back. The app prepares everything;
  you press send.

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

All tests run offline: no AIMS login, no network, no `claude` process, and Ollama
isn't needed (fakes stand in for the browser, the job boards and the AI). A test that
tries to reach the network fails. Tests that use saved AIMS pages in
`.data/fixtures/` skip when those pages are missing. The pages contain personal
data, so they aren't in git.

## Stack

Python, Playwright (browser automation), BeautifulSoup (HTML parsing), openpyxl
(ITC STEM spreadsheets), httpx (job-board APIs, Ollama), the Claude Code CLI (Claude
ratings on your plan), Ollama (embeddings, optional local LLM), and FastAPI + Jinja2 +
uvicorn for the localhost web UI. A CLI is planned later.
