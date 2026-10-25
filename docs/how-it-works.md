# How it works

A walk through the recommender, one stage at a time, for someone learning ML/AI
engineering. The authoritative design is [architecture.md](../architecture.md);
this guide explains it in plain language and points at the code.

> **Build status (2026-10-25).** Every stage below is built and checked offline.
> The full AIMS scrape still has to be tried with a real login. A CLI is planned later.
> Task numbers (T8, T10…) refer to [MULTI_AGENT_PLAN.md](../MULTI_AGENT_PLAN.md).

## The big picture

```
 student: department, "what I want", earn 1–5, learn 1–5
      │
      ▼
 9–10. Web UI (localhost) ─────── web/app.py, web/tasks.py, templates/, static/
      │
      ▼
 8. Pipeline: search() = refresh() + recommend()   core/pipeline.py
      │
      ├─ refresh():
      │   1. Log in to AIMS (saved session) ──── core/auth.py
      │   2. Scrape AIMS + ITC STEM ──────────── core/aims_scraper.py, core/itc_stem_scraper.py
      │   3. Normalize into one Job shape ────── core/models.py
      │   4. Store in the cache ──────────────── core/cache.py
      │
      └─ recommend():
          5. AI rates each job's level ──────── core/ai/providers.py, core/analysis.py
          6. Embed jobs and the query ───────── core/embeddings.py
          7. Score and rank ──────────────────── core/ranking.py
      │
      ▼
 top 20 jobs, each with "why it ranked here"
```

The numbers match the sections below. Stages 1–7 are the core. The web UI (and the
later CLI) only call the pipeline. They hold no scraping or ranking logic, so both
interfaces always behave the same.

**Everything runs on your machine.** The AI models run locally in
[Ollama](https://ollama.com). No job text, query or credential is sent to any external
AI service, and there are no API keys (owner decision).

---

## 1. Logging in to AIMS

Code: [`core/auth.py`](../src/cityu_jobs/core/auth.py)

Campus Internship Scheme (CIS) and Student Helper jobs are behind the AIMS login.
The tool never sees your password:

1. [`login()`](../src/cityu_jobs/core/auth.py) opens a **visible** Chromium window
   on the AIMS login page. You log in through CityU single sign-on yourself, including
   any two-factor step.
2. When the browser reaches the AIMS main menu, the tool saves the browser's
   *storage state* (its cookies) to `.data/aims_session.json`, with permissions set so
   only your user can read it.
3. Later runs start a **hidden** browser that loads those cookies
   ([`load_session()`](../src/cityu_jobs/core/auth.py)), so AIMS sees you as already
   logged in.

**Why the session file is a secret.** Anyone holding those cookies is logged in as
you until they expire. The file is git-ignored and should never be printed or shared.

**Rotating cookies.** AIMS issues a new `SESSID` cookie on every page load, so the
file saved at login goes stale as soon as you browse.
[`aims_session()`](../src/cityu_jobs/core/auth.py) is a context manager that keeps
one hidden browser open for the whole scrape and re-saves the cookies when it exits.
That way the next run starts with the newest cookie.

**How "am I logged in?" is checked.** This took two attempts, and the reason is
worth knowing:

- A logged-out AIMS shows a "User Login" page at the *same URL* as the main menu, so
  the URL alone proves nothing. The first check therefore looked for text that only
  appears after login ("Student Services").
- With a stale session, though, the main menu can still show that text while every
  other page says "Session Timeout". That's a *false positive*: the check says yes,
  reality says no.
- [`is_logged_in()`](../src/cityu_jobs/core/auth.py) now opens the page we actually
  need, the job search page, and requires its scheme dropdown to be there. If a page
  shows "Session Timeout" mid-run, `AimsSessionExpired` is raised
  ([`ensure_not_timed_out()`](../src/cityu_jobs/core/auth.py)).

The lesson: test for the thing you need, not for something that usually comes with it.

**Who opens the login window.** Only you do, by clicking "Log in to AIMS" in the web
UI (§9). The pipeline asks for the session with `aims_session(playwright,
interactive=False)`. If the session is missing or expired, that raises
`AimsSessionExpired` instead of opening a window, and the pipeline reports "AIMS login
needed" (§8). A program that pops up login windows by itself is surprising, and it
can't work at all when nobody is at the screen.

## 2. Scraping

Code: [`core/aims_scraper.py`](../src/cityu_jobs/core/aims_scraper.py),
[`core/itc_stem_scraper.py`](../src/cityu_jobs/core/itc_stem_scraper.py)

### Fetching vs parsing

Both scrapers split **fetching** (needs a live browser) from **parsing** (pure: page
or file in, data out):

| | Fetching | Parsing |
|---|---|---|
| AIMS | `search_jobs()` picks a scheme and submits the search; `open_job()` clicks a job's link | `parse_results()`: results table → one dict per row; `parse_job_detail()`: detail page → fields, text sections, tables |
| ITC STEM | `find_job_list_url()` finds the `.xlsx` link; `download_xlsx()` downloads it | `parse_job_list()`: spreadsheet bytes → `Job`s |

The split matters because fetching needs a login (or a file), while parsing can be
tested offline against saved pages ([`tests/test_aims_parser.py`](../tests/test_aims_parser.py)).
When a layout changes, you save the new page and fix the parser without touching the
live site.

### The full AIMS scrape

`scrape_aims()` goes through all three schemes (CIS, Student Helpers Summer, Student
Helpers Regular). For each scheme, `scrape_scheme()`:

1. Runs the search and parses the results table. A scheme with no jobs gives an
   empty list rather than an error.
2. For each row, `needs_detail()` compares the row's **summary hash** with the
   cached one (§4). Only new or changed jobs get their detail page opened.
3. After each detail page, it re-runs the search instead of pressing "back". That
   avoids the browser silently re-submitting a form.
4. Waits 1.5 s before every page load and never runs anything in parallel. This is
   politeness towards CityU's server.
5. Isolates errors. A job that fails to parse is logged and skipped, not fatal. It
   still counts as "seen", so it isn't wrongly marked inactive.
6. Warns if the page's "No. of matched record" differs from the rows parsed. That
   would mean results paginate, which hasn't been seen yet.
7. Marks the scheme complete in the cache only if the whole scheme finished. A
   session timeout (`AimsSessionExpired`) stops the run without marking anything.
8. Saves the cache every few opened jobs (`SAVE_EVERY`), and again when the run
   stops for any reason. An interrupted run keeps its work. This is safe because
   "complete" is still marked only at the end, and the saved jobs are skipped next
   time by their summary hash.

**How long it takes (measured live, 2026-10-25).** Each opened job costs about
**17 seconds**:

| Step | Why |
|---|---|
| 1.5 s pause, then load the job's detail page | politeness pause before every page load |
| 1.5 s pause, then load the search page again | re-search instead of "back" (step 3) |
| submit the search form and wait for the results | another full AIMS page load |

That's 3 AIMS page loads, plus the pauses, for every job, and AIMS pages are slow to
respond. The first full run opened 117 jobs (CIS 45, Student Helpers Summer 0,
Student Helpers Regular 72) and took about **35 minutes**. CIS alone was about 13 min.

Later runs are fast. Thanks to the summary hash, only new or changed rows are
opened, so a typical refresh is a few searches plus a handful of jobs. The 6-hour
freshness rule (§8) means most searches don't scrape AIMS at all.

This slowness is deliberate. The pauses and the one-job-at-a-time loop keep load on
CityU's server low, and re-searching is the safe way back to the results. The
trade-off is a long first run, done once. Two ways to make it faster: `go_back()`
instead of re-searching saves about 2 page loads per job, but risks a silent form
re-submit; shorter pauses would put more load on AIMS. Neither is worth it for a
one-time cost.

**Safety.** The job search page also has an "apply by job number" form. The scraper
only uses the search form and the links in the results table, and never anything
that applies or submits an application.

**Parser version.** `summary_hash()` mixes in a `PARSER_VERSION` number. If the
parser is improved, bumping that number changes every summary hash, so each job is
re-opened once and re-parsed with the new code. This is another kind of cache
invalidation: the *code* that produced the data changed, not the data.

### ITC STEM

ITC STEM jobs are published as an Excel job list per college. The owner chose the
College of Computing list as the main source. College of Science is in
`config.ITC_STEM_SOURCES` but off by default.

- **Getting the file.** `fetch_source()` tries once online: it opens the college
  page, finds the `.xlsx` link, and downloads it without following redirects. The CC
  file redirects to a CityU portal login, so this usually fails with
  `ItcDownloadBlocked`. The tool never tries to log in there or get around a bot
  check. The supported path is manual: you download the file in your own browser and
  save it as `.data/itc/cc_job_list.xlsx` (see the README).
- **Header-name matching.** The column layout isn't fixed, so `parse_job_list()`
  doesn't use column positions. It maps each header to a field through an alias
  table (`_FIELD_ALIASES`), e.g. "Job Title" / "Position" → title, and "Working
  Hours/Week" → weekly hours. Keywords must match **whole words**: an early version
  matched substrings, so "Preferred Major" contained "ref" and was taken as the
  reference-number column. That made job IDs collide. Unrecognised columns still go
  into `raw`.
- **Stable IDs.** `job_id` is `itc:cc:<reference no.>`, or, if the list has no
  reference column, a short hash of title + company + department. That way the same
  job gets the same ID on every run, which the cache relies on.
- **Defaults.** A row without a department column gets `CC` / College of Computing.
  Since the CS department boost also matches CC (§7), CS students see these jobs
  boosted.
- **Fixing the real list.** Once the real CC spreadsheet arrived, a few mapping rules
  were added (details in [data-model.md](data-model.md)):
  - The period is shown as "start, duration".
  - The company overview is added to the end of the description, capped at 500
    characters, so it feeds the embedding.
  - Day/month-swapped Excel dates are fixed. A date that can't be fixed becomes
    "unknown", so the job isn't hidden by mistake.
  - The honorarium is shown but not scored.
- **Parser version.** Like `PARSER_VERSION` for AIMS, `ITC_PARSER_VERSION` is mixed
  into each ITC job's content hash. Changing the parser bumps it, and the next run
  re-rates and re-embeds every ITC job once, so cached AI results never describe old
  text. Here it's the content hash itself that changes, so the AI work is redone, not
  just the scrape.

## 3. One shape for every job

Code: [`core/models.py`](../src/cityu_jobs/core/models.py),
[`aims_to_job()`](../src/cityu_jobs/core/aims_scraper.py)

AIMS and ITC STEM describe jobs differently. Every later stage would need two code
paths unless both are converted into one `Job` dataclass first: title, department,
hours, closing date, description, requirements, and so on. The field meanings are in
the `Job` docstring and in [data-model.md](data-model.md), with a real example.
Everything scraped is also kept in `job.raw`, so nothing is lost if a field turns out
to matter later.

`aims_to_job()` does the AIMS conversion. For example, it splits `"MA -- Mathematics"`
into code `MA` and name `Mathematics`, turns `"16.5"` into the number 16.5, turns
`"25-Oct-2026"` into a real date, and treats `N/A` as empty.

This step is a standard data-engineering move: **normalize at the boundary**, so the
ML parts only ever see one clean format.

## 4. Caching and cache invalidation

Code: [`core/cache.py`](../src/cityu_jobs/core/cache.py)

Rating a job with an LLM (about 7 s per job with `llama3.2:3b` on a laptop CPU) and
embedding it are the slow steps. A job that hasn't changed should never go through
them twice. Results are stored in `.data/cache.json`, keyed by job ID (e.g.
`aims:N26MA004`).

The hard part of any cache is knowing when a stored answer is **no longer valid**.
Three fingerprints handle that:

- **Content hash** ([`compute_content_hash()`](../src/cityu_jobs/core/models.py)). A
  SHA-256 hash of the job's scraped data, with keys sorted so dict order never changes
  it. If the employer edits the listing, the hash changes. The stored level and
  embedding remember the hash they were made from, so they stop matching and are
  recomputed. Nothing has to be deleted by hand. A hash is a cheap way to ask "is this
  exactly the same as before?" without storing and comparing the full text.
- **Embedding model name.** Vectors from different embedding models live in
  different spaces. Comparing a `nomic-embed-text` vector with one from another model
  is meaningless, like comparing GPS coordinates with postcodes. `get_embedding()`
  returns a stored vector only if both the content hash *and* the model match.
- **Summary hash.** A hash of the job's row in the AIMS results table. If the row is
  unchanged, the scraper skips opening the detail page. This saves AIMS requests, not
  AI calls.

Housekeeping: after a *complete* scrape of a source, its jobs that weren't seen are
marked inactive (`mark_scrape_complete()`). A half-finished scrape must not do this,
or it would deactivate every job it didn't reach. Entries are deleted 30 days after
closing (`prune()`). The file is written atomically (to a temp file, then renamed), so
a crash mid-write leaves the old file intact rather than half a file.

## 5. AI level rating: prompting for JSON

Code: [`core/ai/providers.py`](../src/cityu_jobs/core/ai/providers.py),
[`core/analysis.py`](../src/cityu_jobs/core/analysis.py)

A local LLM (`llama3.2:3b` in Ollama by default) reads each job's title, scheme,
description and requirements and rates its level from 1 to 5 against a fixed rubric
(`SYSTEM_PROMPT` in `analysis.py`): 1 routine/clerical … 5 advanced, independent
research or engineering. It must answer in a fixed shape:

```json
{"level": 4, "reason": "Builds numerical methods for a research project."}
```

Why the strictness:

- **Structured output.** Free text would need fragile parsing. The request sets
  Ollama's `format: "json"`, which constrains the model to emit valid JSON.
- **Validate anyway.** Valid JSON can still be wrong (`"level": 7`, `"level": true`).
  `_valid_level()` accepts only an integer from 1 to 5, and tolerates `"4"` because
  small models do that. `rate_level()` retries once, then records `None`
  ("unrated"). Never trust model output without checking it.
- **Errors are sorted by kind.** A bad answer is retried. A connection error (Ollama
  isn't running) isn't, because it would just fail again. It goes up to the caller,
  so the UI can say "start Ollama".
- **Bounded input.** Descriptions are cut to 4000 characters, because small models
  lose track of long prompts.

The AI only does the fuzzy part (judging difficulty). Hard facts like hours and
department come straight from the listing, because there's no reason to let a model
guess what the page already states.

`analyze_jobs()` only calls the model for jobs without a valid cached level. An
earlier "unrated" result is tried again on the next run.

**Switching the LLM model doesn't re-rate anything.** A level is invalidated only by
the job's content hash, not by the model. The model name is stored next to the level
for display only. Compare embeddings (§6), which must match the model. The reasoning
is a cost/benefit trade-off. A level is a coarse 1–5 label where models mostly agree,
so a mix of old and new ratings is a small error. Re-rating every job is a large cost
(about 7 s each). Vectors from two models, on the other hand, can't be compared at
all, so there's no cheap compromise there. The Settings page warns that old ratings
stay until listings change.

**Talking to Ollama.** `OllamaProvider` uses plain HTTP (`httpx`) against
`http://localhost:11434`: `POST /api/chat` for ratings and `POST /api/embed` for
embeddings. Keeping it to raw HTTP shows you exactly what goes over the wire.
`FakeProvider` returns deterministic answers, so the stages can be tried without
Ollama.

## 6. Embeddings and cosine similarity

Code: [`core/embeddings.py`](../src/cityu_jobs/core/embeddings.py)

An **embedding model** turns text into a list of numbers (a vector, 768 numbers for
`nomic-embed-text`), placed so that texts with similar *meaning* end up close
together. "Build websites" and "frontend developer" share no words but get nearby
vectors.

Each job is embedded once and cached. The embedded text is title, job nature,
department, description and requirements joined together (`embedding_text()` in
`models.py`). `embed_jobs()` sends them in batches of 16 to cut HTTP round trips. Your
free-text query is embedded on every search (`embed_query()`). The two are compared
with **cosine similarity**:

```
cosine(a, b) = (a · b) / (|a| × |b|)
```

This is the cosine of the angle between the vectors: 1 means the same direction (very
similar), 0 means unrelated. It ignores vector length and looks only at direction.
Small example: `a = [1, 0]`, `b = [0.6, 0.8]` → `(1·0.6 + 0·0.8) / (1 × 1) = 0.6`.
The code in `cosine()` is just this formula in plain Python. With a few hundred jobs
it takes milliseconds, so no numpy or vector database is needed.

In a live check, a CS-flavoured query scored 0.63 against a CS backend job and
0.50–0.55 against unrelated jobs. Raw similarities sit in a narrow band like this,
which is why ranking rescales them (§7).

**Query vs document prefixes.** `nomic-embed-text` was trained to see
`search_query: ` before questions and `search_document: ` before the texts being
searched. Adding them improves matching. The provider adds them inside `embed()`
based on `kind`, and only for nomic models.

This is **semantic retrieval**: finding relevant items by meaning. It's the
"retrieval" half of RAG, but here nothing is generated afterwards. The ranked list is
the answer.

## 7. Ranking: a weighted sum

Code: [`core/ranking.py`](../src/cityu_jobs/core/ranking.py); weights in
[`config.py`](../src/cityu_jobs/config.py)

Similarity alone ignores your priorities, so the final score combines several
signals, each first scaled to the range 0–1:

```
score = 0.45·sim + 0.10·dept
      + earn  · (0.20·hours + 0.08·is_student_helper)
      + learn · (0.20·level + 0.08·is_internship_or_itc_stem)
```

- `sim`: cosine similarity, min-max scaled across the open jobs (best match = 1,
  worst = 0), so the narrow raw band uses the full weight. If all similarities are
  equal, every job gets 1, and a job without an embedding gets 0.
- `dept`: 1 if the job is in your department. It's a boost, not a filter. The default
  department is CS, and CS also matches CC (College of Computing), because ITC STEM
  rows may only name the college (`DEPARTMENT_BOOST_ALIASES`).
- `hours`: weekly hours ÷ 20, capped at 1. Unknown hours count as 0.5.
- `level`: the AI level mapped from 1–5 onto 0–1. Unrated counts as 0.5.
- `earn`, `learn`: your 1–5 priorities mapped onto 0–1. A priority of 1 switches its
  bracket off completely.

Closed jobs (closing date before today) are excluded. A job is still shown on its
closing day.

**Worked example.** A CIS job with scaled similarity 0.8 in your department, 16.5
h/week and AI level 4, with earn = 5 and learn = 3:

```
0.45·0.8 + 0.10·1 + 1.0·(0.20·0.825 + 0) + 0.5·(0.20·0.75 + 0.08·1)
= 0.36 + 0.10 + 0.165 + 0.115 = 0.74
```

The weight *ratios* keep the owner's ordering: within "earn", hours matter more than
being a Student Helper job; within "learn", level matters more than job type.
Similarity is the largest single term, so results still match what you asked for.
Each `RankedJob` keeps a `breakdown` of every term, so the UI can show why a job
ranked where it did. Ties are broken by job ID, so the order is always the same.

**From raw score to a 0–100 match.** A raw score like 0.74 is hard to read on its
own. Out of what? Adding up all six weights gives 1.11, but no job can score that,
and your priorities shrink the reachable maximum further. With earn = 1 the earn
bracket is switched off, so even a perfect job reaches only 0.83. Dividing by 1.11
would make a perfect job look like 75.

So the UI divides by the best score possible *for your priorities*:

```
max_possible_score = 0.45 + 0.10 + 0.20·earn + 0.20·learn + max(0.08·earn, 0.08·learn)
match              = round(100 × score / max_possible_score)
```

This is the score of a perfect job: similarity 1, your department, 20+ h/week and
level 5. The `max(...)` is there because the two 0.08 bonuses can't both be earned. A
job is either a Student Helper job (the earn bonus) or an internship / ITC STEM job
(the learn bonus), never both, so the formula takes the better of the two. That
makes the maximum reachable: a perfect job reads **100**. Counting both bonuses, as
the first version did, would put 100 out of reach whenever both priorities are above
1.

In the worked example above, earn = 5 and learn = 3 give
`0.55 + 0.20 + 0.10 + max(0.08, 0.04) = 0.93`, so the 0.74 job shows **80 match**.

Two learner points:

- **This is normalisation, not re-ranking.** Every job is divided by the same number,
  so the order doesn't change. Only the readability does. It does mean that match
  numbers from searches with different priorities can't be compared.
- **Keep the raw number visible.** The 0–100 view is friendlier, but rounding hides
  detail. "Why this score" shows each term as points and the raw score ("Raw score
  0.74 of 0.93 possible for your priorities"). Anyone can then check the arithmetic,
  which matters for an explainable ranking.

`max_possible_score()` lives in `core/ranking.py`, built from the same config
weights, so the UI and a future CLI can't disagree about it. The template only
divides.

These weights are hand-picked, not learned. With feedback data (which jobs students
clicked or applied to), they could be fitted instead. That is the step from a scoring
formula to a *learning-to-rank* model.

## 8. The pipeline: one entry point

Code: [`core/pipeline.py`](../src/cityu_jobs/core/pipeline.py)

The pipeline is the only thing interfaces call. `search(prefs, provider)` loads the
cache once and runs two steps on it:

**`refresh()`: get fresh data.**

- Each source (each AIMS scheme, and ITC STEM) is re-scraped only if its last
  *complete* scrape is older than 6 hours (`STALE_AFTER_HOURS`), or if you ticked
  "Refresh now" (`force=True`). Otherwise it's skipped.
- AIMS runs inside `aims_session(interactive=False)`. If the session is missing or
  expired, the report gets `login_needed = True` and a note. The cached AIMS jobs are
  still used, so you get results either way.
- ITC STEM is fetched with a separate hidden browser that has **no** AIMS cookies, on
  the principle of least privilege: a public page doesn't need your session.
- Old entries are pruned, the cache is saved, and a `RefreshReport` (scraped,
  skipped, notes, login_needed) goes back to the UI.

**`recommend()`: rank what is cached.**

1. It takes the active jobs whose closing date hasn't passed. Only these are rated
   and embedded, so closed listings cost no AI calls.
2. `analyze_jobs()` rates the ones without a cached level. The cache is saved every
   10 ratings, so stopping halfway keeps most of the work.
3. `embed_jobs()` embeds the ones without a cached vector.
4. It embeds your query and computes similarities. With an empty query, every job
   gets the same similarity and only your priorities decide.
5. `rank()` scores and sorts, and each result gets the AI's reason for its level.

If Ollama isn't running, `recommend()` raises `ProviderError`, and the UI tells you
to start it.

**Progress.** Both steps take a `progress` callback, a function that receives status
lines like "Rating job levels with llama3.2:3b: 12/45". The pipeline doesn't know
who is listening: the web UI shows the lines in the browser, and a CLI could print
them. This is how the core stays independent of any interface.

## 9. The web UI

Code: [`web/app.py`](../src/cityu_jobs/web/app.py),
[`web/tasks.py`](../src/cityu_jobs/web/tasks.py), `web/templates/`, `web/static/`

A FastAPI app with server-rendered Jinja2 templates, run with
`uv run uvicorn cityu_jobs.web.app:app --reload` on `127.0.0.1:8000`.

| Route | What it does |
|---|---|
| `GET /` | Search form: department (CS first, then all ~210 AIMS departments), free text with example chips, earn/learn priority buttons (Not important … Top priority), "Refresh now" |
| `POST /search` | Starts a search in the background, then redirects to `/results` |
| `GET /results` | Shows progress (with skeleton cards) while running, then the top 20 as cards with a 0–100 match ring, "Why this score" and the AI's reasons |
| `GET /api/status` | JSON progress, polled by the results page every 1.5 s |
| `POST /login` | Opens the headed AIMS login window on your machine (only from the button) |
| `GET/POST /settings` | Ollama URL and the two model names, saved in `.data/settings.json`; lists the installed models |

**Why a background thread.** A search can take minutes, and Playwright's sync API
must not run on the web server's event loop. So `POST /search` only *starts* a task
in `TaskRunner` and returns at once. The page then polls `/api/status` until the task
finishes. `TaskRunner` runs one task at a time, so a login and a scrape can never use
the AIMS session at once.

**The login button flow.**

1. You search. If AIMS needs a login, the results page shows the "AIMS login needed"
   banner with a **Log in to AIMS** button, plus whatever results are possible.
2. Clicking it sends `POST /login`. The task runner opens a visible browser on your
   machine (`auth.login()`), and you log in there.
3. The window closes and the cookies are saved. Your next search scrapes AIMS
   normally.

**Thin interface.** The routes read the form, call the core through the task runner,
and render a template. The helpers the CLI will also need live in the core:
`core/settings.py` (Ollama settings) and `core/departments.py` (the AIMS department
list, extracted once from a saved search page).

**Safety details.**

- uvicorn listens on `127.0.0.1` only, so the app can't be reached from other
  machines.
- POST requests carrying another site's `Origin` header get a 403. Without this
  check, any web page you visit could send a hidden form to `localhost:8000` to open
  the login window or point the app at a different Ollama server. This attack is
  called cross-site request forgery (CSRF).
- There is no API key anywhere: Ollama is the only provider.

## 10. The interface

Code: [`web/static/style.css`](../src/cityu_jobs/web/static/style.css),
[`web/static/app.js`](../src/cityu_jobs/web/static/app.js),
[`web/templates/`](../src/cityu_jobs/web/templates/). The full spec is in
[architecture.md, "Web UI design"](../architecture.md#web-ui-design-redesign-2026-10-25-delegated).
These are Architect (delegated) decisions.

**Colour carries meaning.** The look is a "quiet study desk": a warm paper background,
white cards and one indigo accent. The two priorities have their own colour families:
**amber = earn** and **green = learn**. Relevance (how well the job fits your
description) is indigo, and department is slate. You'll see the same colours on the
priority buttons, in each card's bar and in the key at the top of the results, so the
breakdown reads at a glance. The word "match" is kept for the overall 0–100 score
(§7), which is why the similarity term is labelled "relevance".

**Polish round (T30): hierarchy and motion.**

- **Visual hierarchy.** The #1 result is a featured card, with a "Top match" badge, a
  tint, a bigger ring and a teaser. #2–3 are standard cards, and #4 onwards are
  quieter (no shadow, smaller title). The eye goes to the best results first.
- **Less on each card.** Each card has one slim bar. The exact numbers are inside
  "Why this score", and the colour key appears once per page rather than once per
  card.
- **Urgency colours.** The closing chip is red for 0–3 days, amber for 4–7 and grey
  for 8–14. It always carries text ("Closes in 5 days") and a clock icon, never colour
  alone.
- **The hero.** The search page opens with a headline and a soft gradient with a
  geometric SVG accent, hidden on phones. The new colours are mixed from existing
  tokens with `color-mix()`, so the palette and its contrast checks still hold.
- **Motion with a purpose.** Cards fade up one after another; the delay comes from a
  `--i` index capped at 10. Cards lift slightly on hover, but only on devices that can
  hover. The progress page shows placeholder "skeleton" cards. Under "reduce motion"
  all of this is off. Cards are fully visible even if an animation never runs.
- **Sticky summary bar.** Your query and priorities stay visible while you scroll
  the results.

**Design tokens.** Every colour, font size, spacing step, corner radius, shadow and
animation speed is defined once, as a CSS custom property on `:root`, e.g.
`--primary: #4338ca` or `--space-4: 1rem`. Components only ever say
`var(--primary)`, never a raw colour. Two things follow from that:

- **Dark mode is just a second set of values.** The dark theme redefines the same
  tokens. It applies when your system prefers dark, or when you pick Dark on the
  toggle (`data-theme="dark"` on `<html>`). A one-line script at the top of the page
  applies your saved choice *before* the page is drawn, so the light page never
  flashes first.
- **Consistency for free.** Changing one token changes every component that uses it.
  The contrast of every text/background pair was checked once, on the tokens.

**Nothing is loaded from the internet.** There's no CDN, webfont, framework or
tracker. Every file comes from `web/static/`, and text uses your system's own fonts
(`system-ui`). Loading a font or script from a CDN would tell that server that you
opened the app, from which IP and when. That would break the "everything stays
local" promise (README, Privacy). It also means the app works offline and needs no
build step.

**It works without JavaScript.** The pages are server-rendered HTML forms.
`app.js` only adds the theme toggle, the live progress updates and the example query
chips. Without it you can still search and reload to see results; the chips are
simply hidden, since they'd do nothing. This approach is called *progressive
enhancement*. The chips are `type="button"`, so clicking one never submits the form
by accident.

**Accessibility (WCAG 2.2 AA), in plain terms:**

- **Contrast.** Text is at least 4.5:1 against its background (most is far higher),
  and controls and graphics at least 3:1, in both themes. Low contrast is the most
  common reason text is hard to read.
- **Never colour alone.** Source pills and urgency chips have text labels. The score
  ring and bar are `aria-hidden`, because a screen reader can't describe a coloured
  shape. Next to them is real text that carries the same information: "86 match",
  and a visually hidden "Relevance 46, department 12, level 18, internship 10
  points". Each card also has an `aria-label` summary, and the level meter says
  "Level 4 of 5".
- **Keyboard first.** A "skip to content" link comes first, so you can jump past the
  header. Every control shows a clear focus ring when reached with Tab, and that ring
  is never removed. The priorities are real radio buttons inside a `fieldset` with a
  `legend`, so arrow keys work without any custom code. Each option is labelled with
  a word as well as a number ("1 Not important" … "5 Top priority"), so a screen
  reader announces something like "Earn money, 4 High, radio button, 4 of 5". The
  meaning is in the label itself, not in a separate hint that could be missed. Using
  the native element is almost always better than rebuilding it in JavaScript.
- **Big enough targets.** Buttons, radios, chips, nav links, the logo link, the
  skip link and the toggle are at least 44×44 px, which matters on phones.
- **Live regions.** The progress message is `aria-live="polite"`, so screen readers
  announce new stages. Errors use `role="alert"` (announced immediately) and
  confirmations use `role="status"`.
- **User settings win.** With "reduce motion" turned on in the OS, there are no
  animations: no fade-up, hover lift or shimmer, and the progress bar becomes static
  stripes. In Windows high-contrast mode
  (`forced-colors`), borders and bar segments stay visible.
- **Phones.** The layout is mobile-first: one column on small screens, two columns
  from 64rem, and no sideways scrolling at 360 px.

---

## Decisions made for you (and what you could change)

Most of these were delegated to the Architect agent. The rows marked *owner* are the
owner's own calls. Each is a sensible default, not a fixed rule.

| Decision | Why | What you could change |
|---|---|---|
| *Owner:* Ollama is the only AI provider, with no API keys | Everything stays local and free; no job text leaves the machine | Add a hosted provider later (it would need a privacy notice) |
| Match score = score ÷ best possible for *your* priorities, shown 0–100; the raw score stays in "Why this score" | A perfect job reads 100 whatever your priorities | Divide by the sum of all weights, 1.11 (comparable across searches, but a perfect job never reads 100) |
| Redesign: "quiet study desk" look, amber = earn, green = learn, no CDN or webfont | Colour encodes the two priorities; nothing loaded from outside keeps the app local | Another palette (change the tokens in `style.css`); CityU maroon was rejected so it doesn't look official |
| *Owner:* CS is the default department; CS also matches CC | The product focuses on Computer Science | Change `DEFAULT_DEPARTMENT` / `DEPARTMENT_BOOST_ALIASES` in `config.py` |
| Re-scrape a source only if its last scrape is over 6 h old | Even an incremental AIMS scrape costs several searches, and puts load on CityU | Set `STALE_AFTER_HOURS = 0` to always scrape |
| Build offline-testable parts first | Live AIMS work needs a human login | Finish the full AIMS scrape first |
| One module per pipeline stage | Readable, and agents can work in parallel | Merge small modules |
| One `Job` dataclass for both sources | Later stages need only one code path | Separate classes per source |
| JSON cache file, not a database | Small (a few MB) and readable by humans | SQLite if it gets slow |
| Skip unchanged AIMS detail pages (summary hash) | Fewer requests to AIMS | Always open every detail page |
| Re-run the search after each detail page instead of going back | Avoids silent form re-submission | `go_back()` (about 2 fewer page loads per job; the first run would be much shorter) |
| *Owner:* College of Computing is the main ITC STEM list | The product focuses on CS | Enable College of Science in `ITC_STEM_SOURCES` |
| Manual ITC STEM download (no portal login) | The CC file needs a CityU portal login; the tool never logs in on its own | Add a headed portal login, like the AIMS one |
| ITC company overview appended to the description (max 500 chars) | The company's field helps matching; last and short so boilerplate doesn't dominate | Leave it out, or embed it separately |
| ITC honorarium is not an earn signal; ITC weekly hours stay unknown (0.5) | Every ITC intern already gets the scheme allowance; the answers are free text | Parse amounts into a score term, or count ITC jobs as full-time on hours (open question for the owner) |
| Swapped Excel dates fixed; unfixable ones become unknown | A wrongly "closed" job would vanish from results | Always treat early date cells as unknown |
| A cached level is kept when you switch LLM model (only the content hash invalidates it) | Re-rating everything takes minutes; 1–5 levels mostly agree across models | Also require the same `model`, as embeddings do (one line in `Cache.get_level`) |
| Fetch all ITC STEM lists; department only boosts | Only a handful of files; no mapping table to maintain | Pick lists by department (needs a dept → college table) |
| Plain `httpx` to call Ollama, not the `ollama` package | One small dependency; you see the raw HTTP | Use the official `ollama` package |
| `llama3.2:3b` + `nomic-embed-text` by default | Run on a student laptop | Larger Ollama models for better ratings |
| Unrated job counts as level 3 | Neutral: neither boosted nor punished | Hide unrated jobs, or rank them last |
| Weights 0.45 / 0.10 / 0.20 / 0.08 | Similarity first; the owner's ordering kept | Tune in `config.py`, or learn them from feedback |

## Glossary

- **Storage state.** Playwright's saved cookies and local storage for a browser
  context.
- **False positive.** A check that says "yes" when the true answer is "no".
- **Hash (SHA-256).** A short fingerprint of data. Any change to the input gives a
  completely different fingerprint.
- **Embedding.** A vector representation of text where closeness means similar
  meaning.
- **Cosine similarity.** A similarity score between two vectors based on the angle
  between them.
- **Min-max scaling.** `(x - min) / (max - min)`, which stretches values onto 0–1.
- **Cache invalidation.** Deciding when a stored result is out of date and has to be
  recomputed.
- **CSRF (cross-site request forgery).** Another website making your browser send a
  request to a site you're logged into (here, the local app).
- **Design token.** A named value (colour, size, spacing) defined once and reused
  everywhere, here as CSS custom properties.
- **Progressive enhancement.** Build the page to work in plain HTML, then add
  JavaScript as an optional extra.
- **Least privilege.** Give each part only the access it needs, e.g. no AIMS cookies
  for public pages.
- **Atomic write.** Write to a temporary file, then rename it over the old one, so
  readers see either the old file or the new one, never half of one.
