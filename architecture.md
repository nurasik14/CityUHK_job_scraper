# Architecture

Status (2026-10-25): design confirmed by the owner; AIMS auth and a one-job AIMS scrape
are implemented and verified. The owner then delegated the remaining design decisions to
the agent team. Decisions marked **[Delegated]** were made by the Architect agent, not the
owner; each lists why and the main alternative, so the owner can review them later.

## Purpose

A self-hosted tool that CityU students run on their own machines. It scrapes CityU
job listings (Campus Internship Scheme, Student Helper Scheme and ITC STEM jobs)
and recommends jobs based on what the student says they want and how they rank
earning money against learning and building a portfolio.

## Why self-hosted

Campus Internship Scheme and Student Helper listings sit behind an AIMS login.
Because each student runs the app locally and logs in with their own account, no
CityU credentials ever pass through a central server.

## Components

```
            ┌───────────────┐   ┌───────────────┐
            │ Web UI        │   │ CLI (later)   │   thin interfaces
            │ (localhost)   │   │               │
            └──────┬────────┘   └──────┬────────┘
                   └─────────┬─────────┘
                             ▼
                   ┌───────────────────┐
                   │ Core (pipeline)   │
                   │ scrape → analyze  │
                   │ → embed → rank    │
                   └─┬───────┬───────┬─┘
                     ▼       ▼       ▼
              Scrapers   AI provider  Cache file
            (Playwright) (local       (.data/cache.json:
                          Ollama      jobs, levels,
                          only)       embeddings)
```

## Main flow

1. The user selects their department.
2. The user enters free text describing the job they want, plus two priorities
   rated 1–5: earn money, and learn/build a portfolio.
3. On submit, jobs are scraped on demand **unless the last successful scrape of that
   source is less than 6 hours old** (configurable) **[Delegated]**. Why: an AIMS
   scrape of every scheme takes minutes and may need a login; re-scraping on every
   query adds load on CityU for little gain. Alternative: always scrape (owner's
   original wording); set the threshold to 0 to get that behavior.
4. For each job, the cache is checked by job ID. A job whose content changed or isn't
   cached yet goes to the AI, which rates its level, and is embedded.
5. The user's query is embedded and compared with the job embeddings.
6. Jobs are ranked and shown. Closed jobs (closing date in the past) are excluded.

## Confirmed by the owner

- Self-hosted; localhost web UI first, CLI later, both thin over a shared core.
- AIMS: headed login by the student, session cookies saved locally; one hidden browser
  per scrape run, session re-saved on exit (AIMS rotates `SESSID` on every page load).
- Scrape AIMS (CIS, Student Helper) then ITC STEM; parse fields; cache by job code.
- Retrieval: embeddings (replaced multi-label classification).
- Ranking: earn favors hours (stronger) then Student Helper (weaker); learn favors
  AI level then internship/ITC STEM; department is a boost, not a filter.
- AI provider: Ollama or the user's own API key; user picks the model.
  **Superseded 2026-10-25 by the owner: Ollama only** (see below).
- Accepted AI proposals: weighted-sum score; AI level integer 1–5 in JSON; cache stores
  content hash + embedding model name; vectors in the cache file (no vector DB);
  FastAPI + server-rendered templates; no container in v1.

## Owner decisions after delegation (2026-10-25, relayed by the Manager)

These came from the owner directly and are **not** delegated decisions:
- **AI provider: Ollama only.** No Claude/Anthropic or OpenAI-compatible provider and no
  API-key handling. `FakeProvider` stays for offline smoke checks.
- **Product focus: Computer Science** (the owner's field). The UI department defaults to
  `CS - Computer Science`; examples and smoke checks are CS-centric.
- **ITC STEM: the College of Computing job list is the primary/default source.** Other
  colleges (College of Science already in the registry) are optional, disabled by default,
  and not researched for now.
- Earlier, also from the owner: the Validator role was ended and dedicated test tasks were
  dropped; only the existing parser tests are kept.

Follow-on detail [Delegated]: for a CS student the department boost applies to jobs whose
department code is `CS` **or** `CC` (College of Computing), because ITC STEM rows from the
College of Computing list may only carry the college, not the department. Stored as
`config.DEPARTMENT_BOOST_ALIASES = {"CS": {"CS", "CC"}}`; other departments match only their
own code. Alternative: exact `CS` match only — would never boost CC-level ITC rows.

## Findings from research (2026-10-25)

- **AIMS job search** (`hwosjpstenq_cityu.P_Main`): one scheme per search
  (Campus Internship Scheme / Student Helpers (Summer Term) / Student Helpers (Regular
  Term)); filters for job nature (26 options), employing department (~210 options,
  `CODE - Name`), closing date. Results: one table (CIS had 45 rows on one page; paging
  not seen yet). Detail page is opened by a JavaScript form submit with `p_rowid`.
- **The same page has an apply-by-job-number form (`hwosjpstapp_cityu.P_Main`).**
  Never touch it.
- **Session check false positive:** with a stale session the main menu can still show
  "Student Services" while the job search page shows a "Session Timeout" page. The
  login check must test the job search page.
- **ITC STEM job lists are Excel files** (`.xlsx`) linked from each college's ITC STEM
  page, with dated filenames (e.g. College of Computing `.../2026.10.22 Job List.xlsx`,
  College of Science `.../Job list_20260723.xlsx`). The School of Energy and
  Environment page has no list. The site is behind a bot check (Incapsula): plain HTTP
  clients get a JavaScript challenge; a real browser loads the pages. Column layout of
  the spreadsheets is not known yet.
- **College of Computing list needs a portal login** (Builder, 2026-10-25): the CC page
  itself loads in headless Chromium, but the `.xlsx` link answers 302 to a SharePoint
  login (`/cc/portal/_spauth/_login.aspx`; "Students are required to login to see the
  list"). So for CC the blocker is a login, not the bot check.

## Decisions [Delegated]

### Build order
Milestones M1–M7 in `MULTI_AGENT_PLAN.md`. Offline-testable parts (job schema, cache,
AI provider, ranking) are built first, because live AIMS work needs a human login and
ITC STEM parsing needs a sample spreadsheet. Alternative: strictly finish M1 first —
rejected because it would idle the team while waiting for a login.

### File layout
```
src/cityu_jobs/
  config.py            URLs, paths, defaults (delays, staleness, models, weights)
  core/
    models.py          Job dataclass (common schema) + content hash
    auth.py            AIMS session (exists)
    aims_scraper.py    AIMS fetch + parse (exists) + aims_to_job()
    itc_stem_scraper.py  ITC STEM sources, xlsx download + parse -> Job
    cache.py           JSON cache: load/save, invalidation, retention
    ai/providers.py    LLM + embedding interfaces; Ollama + Fake (tests)
    analysis.py        AI level rating
    embeddings.py      job/query text, embedding, cosine
    ranking.py         score formula
    pipeline.py        refresh -> analyze -> embed -> rank (what interfaces call)
  web/                 FastAPI app, templates/, static/
scripts/               manual live checks (check_auth.py, scrape_aims_test.py, ...)
tests/                 offline tests; fixtures in git-ignored .data/fixtures/
```
Why: one module per pipeline stage keeps each piece readable for a learner and lets
the Builder/Validator work on them in parallel.

### Common job schema (`core/models.py`)
A `Job` dataclass shared by both sources:

| Field | Type | AIMS source | ITC STEM source |
|---|---|---|---|
| `job_id` | str | `aims:<Job No.>` e.g. `aims:N26MA004` | `itc:<college>:<ref no., else hash of title+dept>` |
| `source` | str | `aims_cis` / `aims_sh_summer` / `aims_sh_regular` | `itc_stem` |
| `scheme` | str | scheme heading | "ITC STEM Internship" |
| `title` | str | Brief Job Description | column TBD |
| `department_code` / `department_name` | str \| None | Recruiting Dept/Unit (`MA -- Mathematics`) | column TBD; college code as fallback |
| `job_nature` | str \| None | Job Nature | TBD |
| `description` | str | Project Title + Project Description + Detail Job Description + learning objectives | TBD |
| `requirements` | str | Preferred Disciplines + Computer/Language/Other Skills + Other Requirements | TBD |
| `part_time` | bool \| None | Part-time/Full-time | TBD |
| `weekly_hours` / `total_hours` | float \| None | listing | TBD |
| `employment_period` | str \| None | listing | TBD |
| `closing_date` | date \| None | Closing Date | TBD |
| `positions` | int \| None | No. of Students required | TBD |
| `url` | str \| None | None (AIMS needs login) | the spreadsheet URL |
| `raw` | dict | parsed AIMS dict | the spreadsheet row |
| `content_hash` | str | sha256 of normalized `raw` | same |

Why: ranking needs a few comparable fields (hours, dept, type, text); everything else
stays in `raw` so nothing scraped is lost. Alternative: separate classes per source —
rejected because every later stage would need two code paths.

### Cache (`.data/cache.json`)
```json
{"schema_version": 1,
 "sources": {"aims_cis": {"last_scraped_at": "..."}},
 "jobs": {"aims:N26MA004": {
   "job": {...}, "content_hash": "...", "summary_hash": "...",
   "active": true, "first_seen": "...", "last_seen": "...",
   "level": {"value": 4, "reason": "...", "model": "llama3.2:3b", "content_hash": "..."},
   "embedding": {"model": "nomic-embed-text", "content_hash": "...", "vector": [...]}}}}
```
- Level is reused only if its `content_hash` matches; embedding only if both
  `content_hash` and `model` match.
- `summary_hash` (hash of the AIMS results-table row) lets the AIMS scraper **skip
  opening the detail page** for a job whose row hasn't changed — fewer AIMS requests.
- Jobs missing from the latest successful scrape of their source become
  `active: false`. Entries are deleted 30 days after their closing date (or 30 days
  after `last_seen` if no closing date).
- Writes are atomic (write temp file, then rename) so a crash can't corrupt the cache.
Why JSON: a few hundred jobs × 768 floats is a few MB; human-readable for a learner.
Alternative: SQLite — revisit if the file gets slow.

### AIMS session
- Keep `.data/aims_session.json`, re-saved on exit (already implemented).
- Fix the false positive: `is_logged_in()` must open the job search page and require
  the scheme dropdown (`select[name=p_copc_code]`) to be present.
- If a page shows "Session Timeout" mid-run, raise `AimsSessionExpired`; the pipeline
  reports "AIMS login needed" to the interface, which offers a "Log in to AIMS" button
  (headed login on the student's own machine).
- `aims_session(playwright, interactive=True)`: only manual scripts run by the student use
  the default, which opens the login window when needed. The pipeline, and any agent running
  a live script, pass `interactive=False` and get `AimsSessionExpired` instead. Why: a login
  window must only appear when the student asked for it, never from a background thread
  or an unattended run. (Added 2026-10-25 by the Architect during review [Delegated].)
- An AIMS search with zero matches returns an empty list, not an error.

### Full AIMS scrape
- For each scheme: search → parse results → for each row whose `summary_hash` changed
  or isn't cached, open the detail page, parse, then re-run the search to get back to
  the results. (Updated 2026-10-25 by Builder + Architect: re-searching costs one extra
  page load per opened job, but avoids the ambiguity of `go_back()` re-posting a form;
  `summary_hash` skipping keeps the number of opened jobs small after the first run.)
- Wait 1.5 s between AIMS page loads; never parallel. One failed job is logged and
  skipped, not fatal.
- If the page's "No. of matched record" count differs from the rows parsed, log a
  warning (signals paging we haven't handled).

### ITC STEM
- `config.ITC_STEM_SOURCES`: list of `{college, name, page_url, enabled}`. The College of
  Computing list is first and enabled by default (owner decision); College of Science is
  present but disabled; other colleges are not researched for now.
- Load the college page in Playwright, find the first `a[href$=".xlsx"]` link, download
  it with the same browser context (`context.request.get`), parse with `openpyxl`
  using header names, not column positions.
- No stealth plugins or challenge workarounds. If the download is blocked, fall back to
  a spreadsheet the student saves into `.data/itc/` manually, and say so in the UI.
- **Manual download is the supported path for CC** (Decided by Architect, delegated,
  after the portal-login finding). The scraper still makes one cheap online attempt,
  never follows the login redirect, and otherwise reads `.data/itc/cc_job_list.xlsx`
  (any `.xlsx` there also works). Alternative, deferred: a headed "Log in to the CC
  portal" step like AIMS (student logs in, only cookies kept). Deferred because it
  adds a second secret session file and a second login flow for one spreadsheet that
  changes rarely. Revisit with the owner.
- **Real CC list (2026.10.22), mapping decisions** (Decided by Architect, delegated, after the
  Validator's T11 report; one sheet, banner row then header row, 98 jobs, no hours column):
  - *Swapped Excel dates.* Some date **cells** (not text) have day and month swapped (deadline
    2026-06-11 for a job posted 2026-09-14). Fix only Excel date cells earlier than the row's
    posting "Date": swap day/month if the day is 12 or less and the result is on/after the posting
    date. If the date is still before the posting date, use `None`, so a closing date becomes
    unknown and the job stays visible rather than wrongly closed. Text dates are already
    day-first (`%d/%m/%Y`). Alternative: always `None` for such cells; rejected because it loses
    the real deadline when the swap is obvious.
  - *Duration* joins the start date in `employment_period` ("21/09/2026, 90 days").
  - *Company overview* is appended to `description` as "About the company: ...", **after** the
    job text and cut to 500 characters. The company's field ("AI-powered education") helps
    matching, and keeping it last and short stops boilerplate from dominating short job texts
    or the LLM's 4000-character window.
  - *Honorarium* stays in `raw` only (shown in the details, not scored). Every ITC STEM intern
    already gets the scheme allowance (the header says "ON TOP of the allowance"), and the
    answers are free text, so a score term would need a parser and a new weight. ITC rows keep
    `weekly_hours = None` (0.5 on the hours term). Revisit with the owner: count ITC rows as
    full-time (paid allowance) on the hours term?
- Header matching uses word starts, not substrings (review finding: "Preferred
  Major" contains "ref" and was read as the reference column).
- Department selection: all *enabled* ITC lists are fetched (by default only College of
  Computing); the user's department is used as a ranking boost.

### AI provider (`core/ai/providers.py`)
Two small interfaces: `LLMProvider.complete_json(system, user) -> dict` and
`EmbeddingProvider.embed(texts, kind="document"|"query") -> list[list[float]]`, each
with a `model` name. **Owner decision (2026-10-25): Ollama only.** No Claude/Anthropic,
OpenAI or other hosted provider, and no API keys anywhere in the app. Implementations,
using plain `httpx`:
- `OllamaProvider`: `POST /api/chat` with `format: "json"`, and `POST /api/embed`.
- `FakeProvider`: deterministic, for offline smoke checks.
The two interfaces stay, so a provider could be added later without touching the
other stages; there's no plan to add one.
Defaults: LLM `llama3.2:3b`, embeddings `nomic-embed-text` (768-d; needs the
`search_document: ` / `search_query: ` prefixes). Why: both are small enough for a
student laptop. Alternative: larger models for better level ratings, user-selectable.

### AI level rating (`core/analysis.py`)
Temperature 0; the model returns `{"level": 1-5, "reason": "<one sentence>"}` using a
fixed rubric: 1 routine/clerical, 2 general support with light skills, 3 needs subject
knowledge or tools, 4 substantial technical or research work, 5 advanced, independent
research or engineering. Invalid output → retry once → else `level = None`
(treated as 3 in ranking and shown as "unrated").
- **A cached level is reused across LLM model changes** (Decided by Architect,
  delegated, on the Builder's question in T17). Reuse requires only a matching
  `content_hash`, as CLAUDE.md states; the `model` is stored for display. Why: a switch
  would otherwise re-rate every job (about 7 s each with llama3.2:3b), and levels are
  coarse 1-5 labels where models mostly agree. Embeddings differ: vectors from two
  models can't be compared at all, so they must match the model. Cost: after a switch,
  old and new ratings are mixed until listings change. The settings page says so.
  Alternative: also require the same model (one line in `Cache.get_level`). Revisit
  with the owner.

### Score (`core/ranking.py`)
All inputs are scaled to 0–1:
```
sim      = cosine similarity, min-max scaled over the candidate jobs
dept     = 1 if the job's department code is in DEPARTMENT_BOOST_ALIASES for the user's
           department (CS → {CS, CC}; others → just their own code), else 0
hours    = min(weekly_hours / 20, 1)          (unknown → 0.5)
is_sh    = 1 for Student Helper jobs
level    = (level - 1) / 4                    (unrated → 0.5)
is_learn = 1 for Campus Internship Scheme or ITC STEM
earn, learn = (priority - 1) / 4              (1–5 sliders → 0–1)

score = 0.45·sim + 0.10·dept
      + earn  · (0.20·hours + 0.08·is_sh)
      + learn · (0.20·level + 0.08·is_learn)
```
Why: similarity stays the largest single term so results match what the student asked
for; within each priority the owner's ordering (hours > Student Helper, level > type)
is kept by weight ratios. Weights live in `config.py`. Alternative: learned weights —
no training data yet. Show the top 20 with a per-term breakdown so the student can see
why a job ranked where it did.

### Web UI
FastAPI + Jinja2 + uvicorn. The scrape runs in a background thread (Playwright's sync
API must not run on the event loop); the browser polls a status endpoint and then shows
results. Settings page: Ollama URL and the two model names (LLM, embeddings), stored in
`.data/settings.json` (git-ignored). It has no API-key field and stores no secrets.

### Web UI design (redesign, 2026-10-25) [Delegated]
The owner asked for a modern redesign and delegated the visual decisions; I made them as the Architect
(decided by Architect, delegated). Tasks are T25-T29.

**Constraints (owner):**
- Same architecture: FastAPI + Jinja server-rendered, routes thin over `core/pipeline.py`.
- No Node/build step, no external CDN, font or tracker. Everything is served from `web/static/`.
- CSRF Origin check, the Ollama-only Settings page, form field names (`department`, `query`,
  `earn`, `learn`, `refresh`) and `/api/status` JSON are unchanged.

**Identity: "quiet study desk".**
- Warm paper background, white cards, one indigo accent.
- Two colour families that encode the student's two priorities: **amber = earn**, **green = learn**.
  Match is the indigo accent; department is slate.
- Alternatives considered: CityU maroon (rejected: the app should not look like an official CityU page)
  and a dark-first "dashboard" look (rejected: it's a reading-heavy page of job text).

**Design tokens.**
- All are CSS custom properties on `:root` in `static/style.css`. Components use only tokens, never raw colours.

Palette:

| token | light | dark | use |
|---|---|---|---|
| `--bg` | `#f7f6f3` | `#121117` | page |
| `--surface` | `#ffffff` | `#1b1a21` | cards, header |
| `--surface-2` | `#f0eee9` | `#24232b` | inputs, chips, bar track |
| `--border` | `#dedad2` | `#34323d` | decorative hairlines (cards) |
| `--border-strong` | `#7a7684` | `#807d8b` | input/radio borders (≥ 3:1) |
| `--text` | `#1c1b1f` | `#ecebf0` | body text |
| `--text-muted` | `#5b5864` | `#a9a6b3` | hints, meta (≥ 4.5:1 on bg and surface) |
| `--primary` | `#4338ca` | `#a5b4fc` | links, primary button, match term |
| `--on-primary` | `#ffffff` | `#17161c` | text on primary |
| `--focus` | `#4338ca` | `#c7d2fe` | focus ring |
| `--t-match` | `#4338ca` | `#a5b4fc` | score: match |
| `--t-dept` | `#64748b` | `#94a3b8` | score: department |
| `--t-hours` | `#b45309` | `#fbbf24` | score: hours (earn) |
| `--t-sh` | `#c2410c` | `#fb923c` | score: student helper (earn) |
| `--t-level` | `#15803d` | `#4ade80` | score: level (learn) |
| `--t-scheme` | `#4d7c0f` | `#bef264` | score: internship (learn) |

Contrast checked by the Architect (05:56):
- text 15.8 (light) / 14.6 (dark) or better; muted ≥ 6.0; primary ≥ 6.8;
- all six score colours ≥ 4.1 on bg, surface and surface-2 in both themes;
- on-primary 7.9 / 9.0; border-strong ≥ 3.0.

Status colours:
- `--warn-*` (amber), `--danger-*` (red) and `--ok-*` (green), each with a `-bg`, a `-border` and a `-text` token.
- The text tokens must reach 4.5:1 on their own `-bg`.

Type:
- `font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Helvetica Neue", Arial, sans-serif`.
- Mono is `ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace`.
- No webfont.
- Scale ratio 1.25: `--fs-xs .8125rem`, `--fs-sm .875rem`, `--fs-base 1rem`, `--fs-lg 1.25rem`, `--fs-xl 1.5625rem`,
  `--fs-2xl clamp(1.75rem, 1.2rem + 2vw, 2.25rem)` (page h1).
- Line height 1.55 for body, 1.2 for headings.
- Weights 400/600/700.
- `font-variant-numeric: tabular-nums` for scores and times.
- Text measure max 70ch.

Spacing, radius and elevation:
- Spacing (4 px base): `--space-1 .25rem` … `--space-8 4rem` (4, 8, 12, 16, 24, 32, 48, 64 px).
- Radius: `--radius-sm 6px` (inputs, chips), `--radius-md 12px` (cards, banners), `--radius-pill 999px`.
- Elevation:
  - `--shadow-1` (cards): `0 1px 2px rgb(0 0 0 / .06), 0 1px 3px rgb(0 0 0 / .08)`.
  - `--shadow-2`: hover/raised.
  - `--shadow-3`: sticky header when scrolled; optional.
  - In dark mode, shadows are replaced by a 1 px `--border` plus the lighter `--surface`.
- Motion: `--dur-fast 120ms`, `--dur 200ms`, `--ease cubic-bezier(.2,.7,.2,1)`.

**Themes.**
- The default follows `prefers-color-scheme`.
- A header toggle cycles System → Light → Dark. It sets `data-theme="light|dark"` on `<html>` and is
  remembered in `localStorage` (`theme`).
- A tiny inline script at the top of `<head>` applies it before paint, so there is no flash.
- Dark tokens are defined twice:
  - under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }`;
  - under `:root[data-theme="dark"]`.
- `color-scheme: light dark` is set so form controls and scrollbars match.
- Without JS the app works and follows the system theme.

**Layout and responsiveness.**
- Mobile-first. Breakpoints at `40rem` and `64rem`. Container max `72rem`, 16 px side gutter on phones.
- No horizontal scroll at 360 px width.
- Header: wordmark "CityU Jobs" (text + small inline SVG mark, not the CityU logo), nav with
  `aria-current="page"`, and the theme toggle.
  - On phones the header stays one row: nav as compact links, no hamburger.
- Search page:
  - Desktop: two columns. The form card (wide) sits on the left; on the right, "How ranking works" (3 bullets
    with the colour families) and the AIMS session card.
  - Phone: one column.
- Earn/learn priorities become **segmented 1-5 radio groups** (`fieldset` + `legend`, same `earn`/
  `learn` names and values) instead of range sliders. They have clear labels at both ends, ≥ 44 px targets,
  and work with arrow keys natively. Earn uses the amber family, learn the green.
- Touch targets ≥ 44×44 px for buttons, radios, nav links and the toggle.

**Results: job cards.**
- Summary bar on top: "Top N of M open jobs" plus chips for query/department/earn/learn, a "Change search"
  link, and a scrape-notes disclosure.
- Each card has:
  - **Head:** rank number, title (h2), and a source pill (text label + tinted background; colour is never
    the only cue). The score sits at the right: big tabular number "0.73" with "/ max".
  - **Meta row** with small inline icons: department, hours/week, closing date. A "Closes in N days"
    badge (warn colour) when ≤ 7 days; `app.py` passes `today` to the template.
  - **Score breakdown:** one stacked horizontal bar to scale (`max_score`).
    - Segments are in the order match, dept, hours, SH, level, internship, separated by 2 px gaps in `--surface`.
    - Below it is a legend list (`<dl>` or `<ul>`): colour dot + label + value. It carries the
      information, so the bar can be `aria-hidden`; the card gets an `aria-label` summary.
    - The terms are grouped visually as "Match", "Earn", "Learn".
  - **Level:** a 5-step meter (filled pips, `aria-label="Level 4 of 5"`) plus the AI's one-line reason.
    Unrated jobs show "Not rated yet" muted.
  - **Details** (`<details>`) with a custom chevron: description, requirements, period, "Extra honorarium",
    then a link to the listing or "Apply in AIMS with job no. X" (still no apply links into AIMS).
- Cards: `--surface`, `--radius-md`, `--shadow-1`, hover `--shadow-2` (no movement under reduced motion).
  The top 3 get a subtle `--primary` left accent.

**States** (each a designed component, not just text):
- **Loading/progress:**
  - A card with an indeterminate progress bar (it becomes a static striped bar under reduced
    motion), the stage message (`aria-live="polite"`), elapsed time as m:ss, and a hint that the first
    AIMS scrape takes ~35 min, rating ~7 s/job, and that it is safe to leave the page.
  - Polling stays at 1.5 s on `/api/status`.
- **Empty:**
  - "No search yet": inline SVG + one sentence + primary "Start a search" button.
  - "No open jobs": explanation + next step (log in / refresh).
- **Error:** a danger banner with `role="alert"`, the message, and a "Try again" link.
- **AIMS login needed:** a warn banner at the top of every page, with an icon, a title "AIMS login needed",
  one-line explanation and the "Log in to AIMS" button. On phones it stacks.
- **Success:** e.g. "Saved." or login done, as an ok banner with `role="status"`.
- **Settings:** the Ollama status as a pill ("Ollama connected · 3 models" / "Ollama not reachable"),
  installed models as chips, and inputs with hints.

**Accessibility (WCAG 2.2 AA):**
- Contrast: text ≥ 4.5:1 (large ≥ 3:1), UI and graphics ≥ 3:1, in both themes.
- Skip link to `<main>`; landmarks `header`/`nav`/`main`; one h1 per page.
- `:focus-visible` ring: 3 px `--focus` outline, 2 px offset, never removed.
- Labels on every control; errors use text, not colour alone.
- `@media (prefers-reduced-motion: reduce)`: no animations or transitions, no smooth scroll.
- `@media (forced-colors: active)`: borders stay visible, and bar segments get a `CanvasText` outline.

**Files and code rules:**
- `static/style.css` (tokens, base, components, utilities; plain CSS, sectioned, nesting not required).
- `static/app.js`: theme toggle and progress polling, about 60 lines, no framework. It replaces the inline
  `oninput`/polling scripts. The only inline script is the pre-paint theme line.
- `static/icons.svg` or an inline `<svg><symbol>` sprite in `base.html`: about 8 simple stroke icons drawn by us
  (search, settings, clock, calendar, building, sparkle, alert, check).
- No new Python dependencies. Optional template macros go in `templates/_macros.html`.

### Web UI polish round (T30-T32, 2026-10-25) [Delegated]
The owner (via the Manager) found T25/T26 clean and accessible, but conservative. These decisions build on the
"Web UI design" tokens above. They don't replace them. All constraints still hold: no build step, no CDN or webfont,
WCAG AA, light and dark, 375 px phones, tests green. Decided by Architect (delegated).

**1. Match score 0-100 with a ring.**
- The headline number per card is a **match score**: `round(100 × score / max_possible_score(prefs))`.
- `max_possible_score` is the best score *for this student's priorities*:
  - `0.45 + 0.10 + earn_w·(0.20 + 0.08) + learn_w·(0.20 + 0.08)`, with `w = (p − 1)/4`.
  - **Amended 06:27:** a job is a Student Helper job *or* a learning scheme (CIS / ITC STEM), never both, so the two
    bonuses can't both be earned. Corrected:
    `0.45 + 0.10 + 0.20·earn_w + 0.20·learn_w + max(0.08·earn_w, 0.08·learn_w)`. 100 is now reachable. For (5,5) the
    maximum is 1.03, not 1.11; for (1,1) and (3,1) it is unchanged. The Scribe's T32 docs pointed this out.
  - So 100 means "as good as a job can be for what you asked". Dividing by the global 1.11 instead would cap a
    student with earn = 1 at ~50 even for a perfect match.
- It is computed in the core: a new pure `max_possible_score(prefs)` in `core/ranking.py`, built from the config weights.
  The template only divides. It replaces `MAX_SCORE` in `app.py` for display.
- **Ring:** an inline SVG circle, `pathLength="100"`, `stroke-dasharray="<n> 100"`, arc `--primary` on a
  `--surface-2` track. The number sits in the centre with "match" below. Sizes: 56 px on cards, 72 px on the featured card.
  The SVG is `aria-hidden`; the text "73 match" is real text.
- To avoid two different "match"es, the similarity term is renamed **"relevance"** (label only; the code key stays
  `similarity`). The groups are **Relevance** (relevance, department), **Earn** and **Learn**.

**2. The breakdown is shown on demand, and the legend once.**
- Each card keeps one slim stacked bar (6 px), to the same 0-100 scale, so the segments add up to the match number.
- The bar is `aria-hidden`. Next to it is visually hidden text: "Relevance 37, department 9, level 14, internship 6 points".
  Segments have a `title` for mouse hover.
- A "Why this score" `<details>` (44 px summary) lists only the **non-zero** terms as points (rounded; the
  sum may be off by 1, and that's fine), plus "Raw score 0.72 of 0.80 possible for your priorities" (keeps the raw number visible).
- **The legend appears once**, in the results header ("How to read the cards": the colour dots for the three groups).
  Per-card legends and zero terms are removed.

**3. Hierarchy.**
- **#1 is featured:**
  - "Top match" badge (sparkle icon, `--primary` background, `--on-primary` text);
  - a subtle tinted background `linear-gradient(135deg, color-mix(in srgb, var(--primary) 8%, var(--surface)), var(--surface) 60%)`;
  - a border `color-mix(… primary 35% …)`;
  - the 72 px ring, and the first ~180 characters of the description visible as a teaser.
- **#2-3:** standard cards (shadow-1).
- **#4 and below:** quieter. Border only, no shadow; h2 at `--fs-base`; the rank number muted.
- The T26 left accent border on the top 3 is removed.

**4. Search hero.**
- A hero band at the top of `/`: headline **"Find the CityU job that fits you"** (h1) plus the existing lede.
- Background:
  - a soft gradient from `color-mix(in srgb, var(--primary) 10%, var(--bg))` to `var(--bg)`;
  - one inline SVG geometric accent (concentric arcs + a dot grid in `--primary` at ~12-18% opacity), `aria-hidden`,
    placed to the right on desktop and hidden below 40rem.
  - Text contrast on the hero must still be ≥ 4.5:1 (the Validator measures it).
- **Example query chips** under the textarea: "machine learning research", "backend development", "data analysis",
  "web frontend", "IT support".
  - They are `type="button"` chips with `hidden`; `app.js` shows them. Without JS they don't appear.
  - Click: if the textarea is empty, set the text; otherwise append ", <text>" unless it's already there. Focus stays on the chip.
  - ≥ 44 px tall. `aria-label="Add example: …"`.
- **Priority controls:** still the segmented 1-5 radios (keyboard and screen-reader friendly). Each segment shows the number and a
  word:
  - 1 **Not important**, 2 **Low**, 3 **Medium**, 4 **High**, 5 **Top priority**;
  - `--fs-xs`, may wrap to two lines;
  - the accessible name becomes "1 Not important", etc. The visually hidden "(1 = …)" hint and the scale-ends row go.

**5. Motion** (tokens `--dur`, `--ease`):
- Card hover lift `translateY(-2px)` + `--shadow-2`, only inside `@media (hover: hover)`.
- Results fade up in a stagger: `animation: fade-up 320ms var(--ease) both; animation-delay: calc(var(--i) * 40ms)`, with
  `--i` = the card index set inline and capped at 10. The base state must be visible (opacity 1) when animations are off.
- **Skeleton cards while searching:** the progress page shows 3 `aria-hidden` skeleton cards (ring, title and meta
  placeholders) with a shimmer under the progress card.
- Under `prefers-reduced-motion: reduce`: no lift, no fade, no shimmer (the existing global block covers it).
  The skeletons stay as static placeholders.

**6. Sticky summary bar** on `/results` (when there are results):
- Sticks just below the site header, `top: var(--header-h)` with a new token `--header-h: 56px`.
- Background: `--surface` at ~92% with `backdrop-filter: blur(8px)`, falling back to solid `--surface`.
- One row of compact chips: query (max-width with ellipsis, full text in `title`), department code, Earn n/5, Learn n/5,
  then "Change search" (button-styled link, ≥ 44 px).
- On phones: query + "Earn n · Learn n" + "Change"; the department is hidden below 40rem. Never a horizontal page scroll.
- The h1 "Top N of M open jobs" and the legend stay above it in the normal flow.

**7. Urgency and wrapping.**
- The closing chip is coloured by urgency, and the text always says the number of days, so colour is never the only cue:
  - 0-3 days → danger ("Closes today" / "Closes in 2 days");
  - 4-7 → warn;
  - 8-14 → neutral chip (`--surface-2`, `--text-muted`);
  - more than 14 → only the date in the meta row.
  - Clock icon. The thresholds are template constants.
- Titles: `overflow-wrap: break-word; hyphens: auto; text-wrap: balance;` instead of `overflow-wrap: anywhere`, so
  English words hyphenate properly (`lang="en"` is already set).

**8. Touch targets** (Validator nits): `.brand { min-width: var(--target) }`; `.skip-link { min-height: var(--target);
display: inline-flex; align-items: center }`.

**Not doing:** charts per card, a JS framework, client-side re-sorting or filters (out of scope; the owner hasn't asked for them).

### Dependencies approved
`openpyxl`, `httpx`, `fastapi`, `uvicorn`, `jinja2` (+ `pytest` dev). Nothing else
heavy without Architect approval.

## Data and trust boundaries

- AIMS credentials are typed only into the browser window on the user's machine.
  Session cookies are stored locally.
- AI runs only on the local Ollama server (owner decision), so job text and the student's
  query never leave the machine. The only network traffic is to CityU (AIMS, ITC STEM
  pages) and to localhost Ollama.
- Unknown: whether CityU's rules allow automated scraping of AIMS — owner's call.

## Open questions

- ITC STEM spreadsheet columns (needs a sample file).
- Whether AIMS results ever paginate; Student Helper result columns (needs a login).
- Which other colleges publish ITC STEM job lists.
