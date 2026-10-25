# Multi-Agent Plan — CityU Job Recommender

Single source of truth for the team. Protocol: `~/.claude/agent-teams/cityu-job-scraper/TEAM_PROTOCOL.md`.
Design authority: `architecture.md` (decisions marked [Delegated] were made by the Architect).

## Overview

Done before the team started (owner + AI): AIMS headed login with saved, re-saved
session (`core/auth.py`), one-job AIMS scrape with parsed fields (`core/aims_scraper.py`),
offline parser tests (`tests/test_aims_parser.py`, fixtures in `.data/fixtures/`).

Goal: a localhost web app that scrapes AIMS (CIS + Student Helper) and ITC STEM jobs,
rates each job's level with an LLM, embeds jobs and the student's query, and ranks
jobs by similarity + department + earn/learn priorities.

Queued for the human in the morning (via Manager): an AIMS login for live AIMS work; a
sample ITC STEM spreadsheet (bot check blocked automated download during research).

Team change (2026-10-25 03:55, owner decision relayed by Manager): the Validator was
terminated and dedicated testing tasks are Dropped. The Builder keeps the existing 3
parser tests passing (`uv run pytest`) and smoke-checks its own code offline, but writes
no new test suites. The Architect reviews Builder work against the spec (Review → Done).
Risk noted by the Architect: the ranking, cache invalidation and provider code now have no
automated tests; review will check them by reading and by the Builder's smoke checks.

Ground rules recap: never touch AIMS apply forms; never run a headed login yourself;
1.5 s between AIMS page loads; no commits; code readable and commented for a learner.

## Milestones

| Milestone | Content | Tasks |
|---|---|---|
| M1 | Full AIMS scrape: all three schemes, all jobs, session fix | T3, T7, T8 |
| M2 | ITC STEM scraper (xlsx) | T10 |
| M3 | Common job schema + cache | T2, T5 |
| M4 | AI provider + level analysis | T12, T13 |
| M5 | Embeddings + ranking + pipeline | T14, T15, T17 |
| M6 | FastAPI web UI | T18 |
| Dropped | Dedicated test tasks (owner decision) | T4, T6, T9, T11, T16, T19 |
| M7 | CLI (later, not scheduled) | — |
| Docs | Throughout | T20–T23 |

Suggested Builder order (offline first): T2 → T7 → T3 → T5 → T12 → T13 → T15 → T14 → T10 → T8 → T17 → T18.

---

## Task T1: Resolve open design decisions and write the plan
- **Assigned To**: Architect
- **Status**: Done
- **Dependencies**: —
- **Notes**: Recorded in architecture.md "Decisions [Delegated]" and .vibe-wise/progress.md.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T2: Job dataclass (common schema) + content hash
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: —
- **Notes**: `src/cityu_jobs/core/models.py`. Fields exactly as the table in architecture.md
  "Common job schema". `closing_date` as `datetime.date`. Provide `to_dict()` / `from_dict()`
  (JSON-safe, dates as ISO strings) and `compute_content_hash(raw: dict) -> str`
  (sha256 of `json.dumps(raw, sort_keys=True, ensure_ascii=False)`). Add a small helper
  `embedding_text(job)` = title, job_nature, department_name, description, requirements
  joined by newlines (used by T14). Docstrings explain each field for a learner.
  Architect review: matches the schema table; to_dict/from_dict round-trip and key-order-stable hash verified on the N26MA004 fixture. Done.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T3: AIMS → Job mapping
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2
- **Notes**: In `core/aims_scraper.py` add `aims_to_job(summary: dict, detail: dict, scheme_key: str) -> Job`
  per the mapping table. Parse `"MA -- Mathematics"` into code/name; hours to float;
  "Part-time" → True; closing date `25-Oct-2026` → date; `N/A` values → treated as empty.
  `raw = {"summary": summary, **detail}`. Also `summary_hash(summary)`.
  Architect review: verified on fixtures (aims:N26MA004, MA/Mathematics, 16.5/80 h, part-time, 2026-10-25, 1 position; no staff names in embedding text). Deviation accepted: all free-text sections go into the description and duplicate objectives are dropped. Done.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T4: Tests for T2/T3 (Job schema, AIMS -> Job)
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T2, T3 (Done)
- **Notes**: Reinstated by owner. `tests/test_models.py`: `to_dict`/`from_dict` round trip (incl.
  closing_date and unknown keys ignored); `compute_content_hash` stable under key order and
  changed by any value change. On the fixtures: `aims_to_job` gives `job_id == "aims:N26MA004"`,
  `department_code == "MA"`, `weekly_hours == 16.5`, `part_time is True`,
  `closing_date == date(2026,10,25)`; `parse_results` returns `[]` on a page with no results
  table; `summary_hash` changes when `PARSER_VERSION` changes. `.data/fixtures/aims_job_search.html`
  is already saved: check that `src/cityu_jobs/data/aims_departments.json` (210 entries, CS present)
  matches its `p_empr_dept` options. Skip if fixtures are missing, like the existing parser tests.
  Validator: `tests/test_models.py` (17 tests) + shared helpers in `tests/conftest.py` (make_job, tmp_path cache, fixture_text). Covers your list plus synthetic aims_to_job cases (N/A, bad dates/hours, summary-only Student Helper row), parse_matched_count, ValueError on a page without div.body. Fixture copied: `.data/fixtures/aims_job_search.html`; JSON dept codes == page options (210, CS first).
  Architect review: tests/test_models.py (17) covers every note: round trip, hash key-order, fixtures N26MA004, empty results -> [], PARSER_VERSION, department list vs saved search page.
- **Last Updated**: 2026-10-25 04:13 by Architect

## Task T5: JSON cache
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2
- **Notes**: `core/cache.py` per architecture.md "Cache". API sketch: `Cache.load(path)`,
  `save()` (atomic: temp file + `os.replace`), `upsert_job(job, summary_hash=None)`,
  `get_level(job_id, content_hash)`, `set_level(...)`, `get_embedding(job_id, content_hash, model)`,
  `set_embedding(...)`, `mark_scrape_complete(source, seen_ids)` (sets `active`,
  `last_scraped_at`), `is_stale(source, max_age)`, `prune(today)` (30-day retention),
  `active_jobs()`. Cache path in config: `.data/cache.json`.
  Architect review: verified level reuse/invalidation on content hash, embedding invalidation on model change, inactive marking, staleness (0 h = always stale), 30-day prune, atomic save. Done. Accepted as specified: levels are not invalidated when the LLM model changes.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T6: Cache tests
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T5 (Done)
- **Notes**: Reinstated by owner. `tests/test_cache.py`, `tmp_path` only (never the real `.data/cache.json`).
  Cover: level reused only on matching content_hash (and, by design, *across* LLM models);
  embedding invalidated by content_hash or model change; `mark_scrape_complete` deactivates
  unseen jobs of that source only; `mark_seen`; `prune` after 30 days (closing date, else
  last_seen); atomic save leaves a loadable file; `is_stale`; wrong `schema_version` -> empty cache.
  Validator: `tests/test_cache.py` (15 tests, tmp_path only). Your list plus: failed save (unserialisable value) keeps the old file and leaves no temp file; upsert keeps first_seen and AI results; 30-day edge kept.
  Architect review: tests/test_cache.py (15) covers every note incl. level kept across LLM models (by design), atomic/failed save, mark_seen, prune both paths, schema_version.
- **Last Updated**: 2026-10-25 04:13 by Architect

## Task T7: Fix AIMS session false positive
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: —
- **Notes**: Research found: stale session → main menu still shows "Student Services", but the
  job search page shows title "Session Timeout". Change `is_logged_in()` to open
  `AIMS_JOB_SEARCH_URL` and require `select[name=p_copc_code]`. Add `AimsSessionExpired`
  exception and a helper `ensure_job_search_page(page)` that raises it on "Session Timeout".
  Keep `login()`'s marker check as is. Live verification needs a human login → Validator T9.
  Architect review: matches the spec; the timeout checks are also wired into search_jobs/open_job. Done. Live check is queued for the human's morning login. Follow-up moved to T8: the non-interactive session flag.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T8: Full AIMS scrape (all schemes, all jobs)
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T3, T5, T7
- **Notes**: `scrape_aims(context, cache, schemes=SCHEMES)` per architecture.md "Full AIMS scrape":
  skip detail pages whose `summary_hash` is unchanged; 1.5 s delay between page loads
  (`config.AIMS_DELAY_S`); after a detail page, `go_back()` and re-search if the results
  table is missing; isolate per-job errors; warn on record-count mismatch; call
  `cache.mark_scrape_complete` only if the scheme finished. Script `scripts/scrape_aims.py`
  (uses `aims_session`, prints counts, saves cache). Write offline-testable parts as pure
  functions. Live run needs a login: when ready, report to the Manager and set Review.
  Update 03:55 (T9 dropped): the live run is queued for the morning, after the human logs in.
  Then the Builder runs `scripts/scrape_aims.py` once and reports counts per scheme vs
  "No. of matched record". Until then, smoke-check offline against the saved CIS fixtures.
  Update (Architect review of T3/T7), required before the live run:
  1. `parse_results()` raises IndexError when there is no results table (zero matches; Student
     Helpers (Summer Term) is likely empty in October). Return [] when the page says
     "No. of matched record: 0" or has no results table; still raise AimsSessionExpired on a
     timeout page.
  2. Add `aims_session(playwright, interactive: bool = True)`. With interactive=False an
     expired or missing session raises `AimsSessionExpired` instead of opening the headed login.
     `scripts/scrape_aims.py` takes `--no-login`, and agents must always run it with that flag
     (hard rule: agents never open a headed login). The pipeline (T17) uses interactive=False.
  3. Optional: add a `PARSER_VERSION` constant to `summary_hash()`, so a parser change forces
     unchanged jobs to be re-opened once.
  Architect review (code + offline only): search scoped to the p_copc_code form (apply form never touched), 1.5 s pauses, re-search instead of Back, errors isolated, expiry aborts without marking complete, --no-login works. **Live verification pending the owner:** `uv run python scripts/check_auth.py`, then `uv run python scripts/scrape_aims.py`; check count matches AIMS and a second run opens ~0 pages.
  04:46 Architect review of the fixes for the Validator's 04:43 report: SAVE_EVERY=5 saves inside scrape_scheme, finally-save in scrape_aims, Ctrl+C message, per-job progress (also reaches the UI via pipeline.refresh), flush=True. Correct: completion is still marked only at the end of a scheme. The only failing test is the expected XPASS(strict); the Validator drops the marker.
  05:06 Live verification done by the Validator (T9): 117/117 jobs, 0 errors, second run 0 pages. Interrupt fix test-covered (134 passed, no xfail).
- **Last Updated**: 2026-10-25 05:06 by Architect

## Task T9: Live AIMS verification + Student Helper fixtures
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T7, T8 (Done); **waits for the Manager's login confirmation**
- **Notes**: Reinstated by owner. Do NOT start until the Manager confirms the human has logged in.
  Never run a headed login yourself; always pass `--no-login`. Run
  `uv run python scripts/scrape_aims.py --no-login` (all three schemes). Verify: counts vs
  "No. of matched record" (no mismatch warning), job fields look right for a sample, a second
  run opens ~0 detail pages, `.data/cache.json` has `aims_*` sources marked complete. Only the
  search form and job links are touched, never the apply form `hwosjpstapp_cityu.P_Main` or any
  apply/submit button. Save one results page + one detail page per Student Helper scheme into
  `.data/fixtures/` (strip nothing, but never put the "You have applied" banner, staff names or
  personal data into tests or reports) and add parser tests for them. Save a "Session Timeout"
  page as a fixture if you meet one, and test `ensure_not_timed_out` on it. If the session expires:
  `QUESTION FOR MANAGER: AIMS login needed` and stop.
  04:14: Manager confirmed the owner's AIMS login (session saved, job search form present); Validator running the live scrape.
  Validator: live-verified. CIS 45/45 matched, SH Summer 0/0, SH Regular 72/72 (no mismatch warnings, 0 errors); 117 active jobs, all with title/dept/hours/closing date/description; sampled jobs look right; no applied banner in the cache. All three aims_* sources marked complete. Second run: 0 detail pages opened, 117 unchanged, 41 s. Session never expired; no Session Timeout page met (so no fixture for it). Fixtures: aims_results_sh_summer.html, aims_results_sh_regular.html, aims_job_detail_sh_regular.html. Tests: tests/test_aims_sh_parser.py, tests/test_aims_scrape.py. Open bug (Builder): an interrupted scheme saves nothing (xfail repro in test_aims_scrape.py).
  05:06 Architect review: checked the live cache offline (117 active = 45 CIS + 72 SH Regular; all three aims_* sources complete at 05:03), new fixtures present, no applied banner/staff names in tests or docs; pytest 134 passed. The interrupt bug is fixed (see T8) and its test passes without xfail.
- **Last Updated**: 2026-10-25 05:06 by Architect

## Task T10: ITC STEM scraper
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2 (column mapping needs the sample spreadsheet — see Messages)
- **Notes**: `core/itc_stem_scraper.py` per architecture.md "ITC STEM". Sources start with
  College of Computing (`https://www.cityu.edu.hk/en/cc/student-life-and-learning/internship-schemes/itc-stem-internship-scheme`)
  and College of Science (`https://www.cityu.edu.hk/en/csci/student-life-learning/internship-schemes/stem-internship-scheme/stem-internship-scheme-202526`).
  Research other colleges (Engineering, Business, Liberal Arts, Vet Med, SEE, etc.); add any with a
  job list and note the rest. Split: `find_job_list_url(page)`, `download_xlsx(context, url)`,
  `parse_job_list(xlsx_bytes, college) -> list[Job]` (pure; header-name based), manual fallback
  reading `.data/itc/*.xlsx`. New dependency `openpyxl` approved. Polite: 1 s between requests,
  no retries loops, no stealth tricks.
  Update 03:55: until the human supplies a spreadsheet, keep `parse_job_list` header-name based
  with a small alias table per field (e.g. title ← "Job Title" / "Position" / "Project Title";
  weekly hours ← headers containing "hour"); unmatched columns go only into `raw`. The
  `.data/itc/*.xlsx` manual fallback is required, not optional. Smoke-check with a tiny
  spreadsheet you generate in a temp dir (not a committed test). Final column mapping waits for
  the real file → set Review with a note when done to that point.
  Update 04:10 (OWNER decision): the College of Computing list is the primary/default ITC
  source (`ITC_STEM_SOURCES[0]`, enabled by default). College of Science stays in the registry
  but disabled by default (`enabled: False`); do NOT research other colleges now. Manual
  fallback file name: `.data/itc/cc_job_list.xlsx` (any `*.xlsx` there still works). When
  a CC row has no department column, set `department_code = "CC"`, `department_name =
  "College of Computing"`.
  Update (Architect review of T2): `raw` must be JSON-safe. openpyxl returns `datetime`/`time`
  objects for date cells, so convert cell values to ISO strings (or str) before building `raw`;
  otherwise `compute_content_hash` and `Cache.save` fail.
  Architect review: header aliases match substrings, so "Preferred Major" -> ref ("pREFerred"); see Messages.
  Architect re-review: word-boundary aliases fixed ("Preferred Major" -> requirements, distinct hash IDs). Final column mapping waits for the real CC spreadsheet.
- **Last Updated**: 2026-10-25 04:01 by Architect

## Task T11: ITC STEM parser tests
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T10 (Done); **waits for an .xlsx in `.data/itc/`** (human supplies it)
- **Notes**: Reinstated by owner. Until a real file exists, only tests on spreadsheets generated in
  `tmp_path` with openpyxl: banner rows above the header, a notes sheet without a header,
  whole-word aliases ("Preferred Major" -> requirements, not ref; "Postgraduate"/"Community"
  match nothing), several requirement columns all kept, missing columns tolerated, no
  department column -> `CC`/College of Computing, job_id stable across runs and distinct for
  same-major rows, `raw` JSON-safe (datetime cells). When the real `.data/itc/*.xlsx` arrives:
  copy it to `.data/fixtures/itc/`, report its actual headers to the Architect (final column
  mapping), and add a test on it. No online download tests (the CC file needs a portal login).
  05:06 Blocked on the owner: needs the College of Computing ITC STEM `.xlsx` in `.data/itc/` (download in a browser from the CC ITC STEM page; it needs a CityU portal login). Generated-sheet tests may go in meanwhile.
  05:28: unblocked: the owner supplied `.data/itc/2026.10.22 Job List.xlsx`; the Validator is working on it. Report the real headers + mapping to the Architect.
  Validator: `tests/test_itc_parser.py` (22: 12 generated-sheet rule tests + 5 on the real CC list + 1 strict xfail). Fixture: `.data/fixtures/itc/cc_job_list.xlsx` (copy of `.data/itc/2026.10.22 Job List.xlsx`). Real list: 98/98 rows parsed, 98 unique stable hash ids, all CC/College of Computing, all with closing dates. Open mapping issue (Builder): swapped Excel dates in rows No. 6-9 (see Messages).
  Architect 05:35: Done. Swapped-date fix + tests accepted.
- **Last Updated**: 2026-10-25 05:35 by Architect (reviewed: 157 passed; swap fix checked against the sheet: all 9 early date cells (rows 6-9, 112) are swappable; follow-ups in T24)

## Task T12: AI provider interfaces
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: —
- **Notes**: `core/ai/providers.py` per architecture.md "AI provider". `httpx` approved.
  `OllamaProvider(base_url="http://localhost:11434", llm_model="llama3.2:3b", embed_model="nomic-embed-text")`;
  nomic prefixes applied inside `embed()` based on `kind` and only for nomic models.
  ~~`OpenAICompatibleProvider(base_url, api_key, llm_model, embed_model)`; never log the key.~~ (superseded below)
  `FakeProvider` deterministic (e.g. hash-based vectors, fixed level). Timeouts set; clear
  error if Ollama isn't running.
  Update 03:48 (OWNER decision): **Ollama only.** Delete `OpenAICompatibleProvider` and every
  API-key parameter, header and repr. Keep the two interfaces, `OllamaProvider` and `FakeProvider`.
  No Anthropic/OpenAI SDKs or endpoints anywhere.
  Architect review: Ollama-only + FakeProvider confirmed; no OpenAI/key code in src/.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T13: AI level rating
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2, T12
- **Notes**: `core/analysis.py`: `rate_level(job, llm) -> tuple[int | None, str]` with the rubric in
  architecture.md; validate int 1–5; retry once; then None. `analyze_jobs(jobs, cache, llm)`
  only rates jobs without a valid cached level.
  Architect review: rubric, 1-5 validation, retry-once, None on failure, cache reuse verified with FakeProvider.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T14: Embeddings
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2, T5, T12
- **Notes**: `core/embeddings.py`: `embed_jobs(jobs, cache, embedder)` (batch, cache-aware),
  `embed_query(text, embedder)`, `cosine(a, b)` in plain Python (no numpy needed at this size).
  Architect review: batched, cache-aware (2nd run 0 embed calls), cosine OK.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T15: Ranking
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T2
- **Notes**: `core/ranking.py`: `Preferences(department_code, query, earn, learn)`;
  `rank(jobs, levels, sims, prefs, today) -> list[RankedJob]` with per-term breakdown, formula
  and weights exactly as architecture.md "Score" (weights in config). Exclude closed jobs.
  Architect review: weights match spec; closed jobs excluded; CS boost covers CC rows.
- **Last Updated**: 2026-10-25 03:50 by Architect

## Task T16: Tests for T12–T15 (provider, levels, embeddings, ranking)
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T12–T15 (Done)
- **Notes**: Reinstated by owner. Ollama only: no API-key or OpenAI-compatible tests. FakeProvider for
  analysis/embeddings; `OllamaProvider` HTTP shapes via `httpx.MockTransport` (`/api/chat` with
  `format: "json"` and temperature 0, `/api/embed` with nomic `search_document:`/`search_query:`
  prefixes; connection error and missing model -> `ProviderError` with a helpful message).
  Level: invalid JSON -> retry -> None; out-of-range -> None; "4" accepted; cached level reused;
  None retried next run. Embeddings: second `embed_jobs` makes no calls; `cosine` basics.
  Ranking properties: earn=5/learn=1 ranks 20 h/week above 5 h/week at equal similarity; at equal
  hours a Student Helper job beats CIS; learn=5 favors higher level; dept match adds exactly
  `WEIGHT_DEPARTMENT`; CS prefs boost a `CC` job; closed jobs excluded; equal sims -> all 1.
  Validator: `tests/test_ai.py` (28: MockTransport shapes incl. temperature 0, nomic prefixes, ConnectError/timeout/404/500 -> ProviderError, 500 not retried as bad JSON; level retry/None/"4"/bool/3.5 rejected; analyze cache + progress; embed batching 16, model change re-embeds), `tests/test_ranking.py` (20 property tests, exact weight deltas, CS->CC alias one-way, min-max scaling ignores closed jobs), plus `tests/test_pipeline.py` (10: recommend with FakeProvider, refresh with stubbed playwright: interactive=False, fresh skip, force, login_needed keeps cached jobs, ITC failure not in scraped, prune) and `tests/test_auth.py` (7, fake Page/Playwright: timeout title, missing dropdown, no browser/login when non-interactive, session re-saved 600).
  Architect review: tests/test_ai.py (21) + tests/test_ranking.py (20): Ollama request shapes (format json, temperature 0, nomic prefixes), ProviderError cases, retry/None, cache reuse, every ranking property in the notes plus clamping and tie-break. No API-key tests (Ollama only).
- **Last Updated**: 2026-10-25 04:13 by Architect

## Task T17: Pipeline
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T8, T10, T13, T14, T15
- **Notes**: `core/pipeline.py`: `refresh(sources, force=False)` (stale check, 6 h default;
  AIMS via `aims_session`; raises/returns "AIMS login needed" on `AimsSessionExpired`),
  `recommend(prefs, provider) -> list[RankedJob]`. Progress callback for the UI.
  This is the only entry point interfaces use.
  Update (Architect): call `aims_session(playwright, interactive=False)` and turn
  `AimsSessionExpired` into a "login needed" state. The UI's "Log in to AIMS" button calls
  `auth.login()` explicitly. The pipeline never opens a headed browser on its own.
  Architect review: interactive=False, login_needed path, only open jobs to AI, periodic saves; FakeProvider recommend() verified via the UI smoke.
- **Last Updated**: 2026-10-25 04:01 by Architect

## Task T18: FastAPI web UI
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T17
- **Notes**: `src/cityu_jobs/web/`. Pages: search form (department select from AIMS dept list,
  free text, earn/learn 1–5), results (top 20 with score breakdown, source, hours, closing date),
  settings (Ollama URL + LLM/embedding model names in `.data/settings.json`; no API-key field, owner decision 03:48). Background refresh thread +
  status polling; "Log in to AIMS" button triggers headed login locally. Run:
  `uv run uvicorn cityu_jobs.web.app:app --reload`. fastapi/uvicorn/jinja2 approved.
  Update 03:55 (T4 dropped): the department list now falls to the Builder. Extract the
  `p_empr_dept` options (`CODE - Name`, ~210) once from
  `/tmp/claude-1000/-home-nurasik12-Projects-CityUHK-job-scraper/372680f0-c158-4602-9514-aefba8199b06/scratchpad/explore/3_job_search.html`
  into `src/cityu_jobs/data/aims_departments.json` ([{code, name}]) and load it in the UI.
  If that file is gone, use a free-text department code field and note it.
  Update 04:10 (OWNER decision): the product focuses on Computer Science. The department select
  defaults to `CS - Computer Science` (`config.DEFAULT_DEPARTMENT = "CS"`), listed first, with the
  full list below it. Placeholder text in the free-text box should be CS-flavored (e.g. "machine
  learning research, backend development, data analysis"). Smoke-check examples use CS jobs.
  Architect review: uvicorn started, /, /results, /settings, /api/status, static all 200; CS preselected (211 options); settings = Ollama URL + 2 models, no key; cross-site POST 403; FakeProvider search through TestClient renders breakdown + login banner; no staff names or applied banner in the page. Routes only call TaskRunner -> pipeline.
- **Last Updated**: 2026-10-25 04:01 by Architect

## Task T19: Web tests
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T17, T18 (Done)
- **Notes**: Reinstated by owner. `create_app(TaskRunner(search_fn=fake, login_fn=fake))` with
  FastAPI TestClient. No real pipeline, AIMS or Ollama. Cover: `/` renders with CS selected;
  POST /search -> 303 -> /api/status -> /results shows ranked jobs; login_needed banner; POST
  /login calls the fake login only; cross-site `Origin` POST -> 403; settings save/load with only
  `ollama_url`, `llm_model`, `embed_model` (no API-key field) — point `config.SETTINGS_FILE` at
  `tmp_path`. A failing search shows the error instead of crashing.
  Validator: `tests/test_web.py` (19). Your list plus: progress while running + second search refused while busy, form value clamping, HTML escaping of the query, stylesheet served, settings page with Ollama down, saved settings used by the next search, corrupt settings file -> defaults. Real TaskRunner thread with fake search/login.
  Architect review: tests/test_web.py (17): CS preselected, submit -> status -> results, banner, fake login only, cross-site 403, settings in tmp_path with no API-key text, error shown, HTML escaping.
- **Last Updated**: 2026-10-25 04:13 by Architect

## Task T20: Fix stale CLAUDE.md + Commands
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: —
- **Notes**: See message below.
  Scribe: Project paragraph + delegated-decisions note + Commands filled.
  Architect review (04:30): accurate against the repo and the plan; commands match. Done.
  Follow-up (small, new scope, tracked under T22): add one line on the owner's CS focus.
- **Last Updated**: 2026-10-25 04:30 by Architect

## Task T21: README setup and usage
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: T8 (update again after T18)
- **Notes**: Prerequisites (Python, uv, `uv run playwright install chromium`, Ollama +
  `ollama pull llama3.2:3b` and `ollama pull nomic-embed-text`), AIMS login flow, scripts,
  tests, privacy notes (session file is a secret; AI runs only on local Ollama, so job text and queries stay on the machine; no API keys, owner decision 03:48).
  Scribe: Early pass done (Manager request): status, privacy (Ollama only, everything local), setup, login, current scripts, tests. Run/UI section to finish after T8/T18.
  Scribe (04:01): README complete: privacy (local-only Ollama, 127.0.0.1, cross-site POST refused), setup + model pulls, web app run + pages, Log in to AIMS button flow, manual ITC xlsx step, scripts incl. scrape_aims.py --no-login, tests.
  Architect review: matches code; Ollama-only, CS default, CC manual download, privacy notes correct; commands checked (uvicorn starts, scrape_aims.py args parse incl. no args, requires-python >=3.11).
  Scribe (04:44): AIMS timing fixed per Validator 04:43 (~17 s/opened job, ~35 min first run for 117 jobs; why: pauses + re-search).
  04:46 Architect re-review: timing section matches the code and the Validator's measurements; stop/resume text matches the fix. One wording note in Messages.
- **Last Updated**: 2026-10-25 04:46 by Architect

## Task T22: Learner guide
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: progressive (update as tasks finish)
- **Notes**: `docs/how-it-works.md`: walk the pipeline stage by stage with links to code, and
  summarize each [Delegated] decision from architecture.md in plain language with "what you
  could change". The owner is learning ML/AI engineering — explain embeddings, cosine
  similarity, prompt-for-JSON, caching/invalidation concisely.
  Scribe: Draft at docs/how-it-works.md: auth + scraper documented from code; later stages written from the spec and marked Planned; delegated-decisions table + glossary. Will update as tasks land.
  Update 04:10 (Architect): record the owner's CS focus as an OWNER decision (not delegated):
  College of Computing ITC list is the default source, UI defaults to CS, CS/CC jobs get the
  department boost. Make worked examples CS-centric (e.g. a CS student, query "machine learning
  research", earn=2, learn=5, showing how a CC ITC STEM job and a CS CIS job score).
  Scribe: Updated: T2/T3/T5/T7/T12-T15 described as built with code links; Ollama-only; CS default + CC alias; pipeline never opens login. T8/T10/T17/T18 still marked Planned.
  Scribe (04:01): how-it-works.md covers all 9 stages: full AIMS scrape + ITC STEM (§2), pipeline (§8), web UI incl. background thread, login flow and CSRF check (§9); line-number anchors removed; decisions table + glossary extended.
  Architect review: accurate against every module cited; no OpenAI/API-key/#L anchors left; worked example arithmetic checks out (0.74). Two small follow-ups in Messages.
  Scribe (04:44): AIMS timing fixed per Validator 04:43 (~17 s/opened job, ~35 min first run for 117 jobs; why: pauses + re-search).
  04:46 Architect re-review: §2 step 8 and the cost table match the code. Same wording note.
- **Last Updated**: 2026-10-25 04:46 by Architect

## Task T23: Data model doc
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: T2, T3, T5
- **Notes**: `docs/data-model.md`: example Job (from N26MA004, no staff names) and an example
  cache entry; field meanings.
  Scribe: docs/data-model.md: N26MA004 as to_dict() (text shortened, no staff names), field table, raw layout, cache entry example (illustrative timestamps/reason/vector) + reuse/retention rules.
  Architect review: accurate vs models.py/cache.py; no staff names or applied banner. Two small follow-ups in Messages.
- **Last Updated**: 2026-10-25 03:51 by Architect

---

## Task T24: ITC mapping follow-ups (05:31 decisions 1b-4)
- **Assigned To**: Builder (code), Validator (tests)
- **Status**: Done
- **Dependencies**: T11 (Done)
- **Notes**: From the Architect's 05:31 message / architecture.md "Real CC list". (a) Safety net in
  `_unswap_date`: an Excel date cell earlier than the posting date that can't be swapped (day > 12,
  or still before the posting date when swapped) -> `None`, so the job stays visible (no row in the
  2026.10.22 list hits this today). (b) `employment_period` = "start, duration" (either part alone). (c) Append
  "About the company: <overview>" after the job text, max 500 chars. (d) Honorarium: raw only;
  "Extra honorarium" in results Details if it's a one-line template change. (e) `"_parser_version":
  ITC_PARSER_VERSION` (= 2) in the dict hashed for `content_hash`. Validator: tests for a-c, e.
- **Last Updated**: 2026-10-25 05:40 by Architect (final review: a-e match the 05:31/05:35 decisions; 176 passed; real list 98/98, 83 open)

## Task T25: UI design system + base layout
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: none
- **Notes**: Implements architecture.md "Web UI design (redesign)" (Delegated). Rewrite `web/static/style.css`:
  - Tokens exactly as in the palette table, plus type, spacing, radius, shadow and motion tokens.
  - Themes: system default, `data-theme` override, `color-scheme`.
  - Base, `:focus-visible` ring, reduced-motion and forced-colors blocks, buttons, inputs,
    segmented radios, chips/pills, banners (warn/danger/ok), cards.
  - New `web/static/app.js`: a theme toggle (System → Light → Dark, `localStorage.theme`) and status polling.
  - `base.html`: the pre-paint theme line in `<head>`, skip link, header (wordmark + our own SVG mark, nav
    with `aria-current`, toggle), the icon sprite, and the AIMS-login-needed banner component.
  - No CDN, webfont or build step; no new Python deps. Keep the CSRF check and every route unchanged.
- **Last Updated**: 2026-10-25 06:05 by Architect (reviewed: tokens match the palette table exactly, themes, focus ring, reduced-motion/forced-colors, no external requests)

## Task T26: UI pages: search, results cards + states, settings
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T25
- **Notes**: Per architecture.md "Results: job cards" / "States".
  - **Search:** two columns on desktop, one on phones. Earn/learn become segmented 1-5 radio groups, keeping the
    names `earn`/`learn`. All field names stay the same.
  - **Results:**
    - Summary chips; cards with rank, a source pill and the score with "/ max".
    - Meta icons; a "Closes in N days" badge (pass `today` from `app.py`).
    - Stacked bar with 2 px gaps, `aria-hidden`, plus a legend grouped Match/Earn/Learn.
    - A 5-pip level meter with `aria-label`; Details with "Extra honorarium". Still no apply links into AIMS.
  - **States:** progress (indeterminate bar, m:ss, the ~35 min first-run hint), the two empty states, the error
    banner (`role="alert"`), success (`role="status"`).
  - **Settings:** Ollama status pill, model chips.
  - Use `templates/_macros.html` if it helps. Check the live app at http://127.0.0.1:8000 (`--reload`).
    GET pages only; don't start another server on 8000.
  Architect 06:05: two small fixes requested (see Messages); the rest is accepted.
- **Last Updated**: 2026-10-25 06:09 by Architect (both 06:05 fixes verified: hidden legend hint, _shorten at a word boundary, ITC_PARSER_VERSION 3)

## Task T27: Web tests for the redesign
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T26
- **Notes**: All 17 existing `tests/test_web.py` tests must still pass. Update only selectors that changed;
  keep the meaning. Add tests:
  - `/static/app.js` is served.
  - No external resource loads in any page or in style.css: no `<script src>`, `<link href>` or `url(` pointing to
    `http(s)://`. Outbound `<a>` links are fine.
  - `aria-current="page"` on the active nav link.
  - Skip link present.
  - Earn/learn radios named `earn`/`learn` with values 1-5 and the saved prefs checked.
  - "Closes in N days" badge for a job closing within 7 days (fixed `today`).
  - Level meter `aria-label`; "Not rated yet" for an unrated job.
  - Error banner has `role="alert"`; login-needed banner on every page.
  - Settings page still has no API-key field.
  Validator: `tests/test_web_redesign.py` (31 tests, all pass); the 19 existing test_web.py tests pass unchanged (no selector changes needed). Covers every item in the notes plus: CSS/JS have no external url()/@import/hosts, default radios = 3, form field names unchanged, no badge after 7 days, 'Closes today', bar aria-hidden + card aria-label, no AIMS apply links, success banner role=status, empty and progress (m:ss, aria-live) states. pytest: 207 passed.
- **Last Updated**: 2026-10-25 06:27 by Architect (parser-version test fixed (3), 5 _shorten tests added; 236 passed)

## Task T28: Visual + accessibility check in Chromium (Playwright)
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T26
- **Notes**: A script, not a pytest test (e.g. `tests/visual/shoot.py`, not collected by default).
  - **Live app (8000), GET only:** `/`, `/results`, `/settings`. **Never POST `/search` or `/login`.** That would
    scrape AIMS or open the login window.
  - **Other states** (progress, error, login-needed, empty, results with long titles/unrated): start
    `create_app` with a fake runner/pipeline on another port (e.g. 127.0.0.1:8765), then stop it.
  - **Viewports:** 1280×800 and 375×812, in both themes (`color_scheme` emulation plus a `data-theme` override check).
    Full-page PNGs go to `.data/screenshots/<page>-<state>-<width>-<theme>.png`. They are git-ignored; don't
    commit them or put them in docs.
  - **Checks to report:**
    - no horizontal overflow (`scrollWidth <= clientWidth`);
    - Tab order reaches every control with a visible focus ring (`outline` not none);
    - `prefers-reduced-motion: reduce` leaves no running animations;
    - no request leaves 127.0.0.1;
    - no console errors;
    - contrast of the main text/muted/primary computed from the rendered styles is ≥ 4.5.
  - Report a table and attach the screenshot paths to the Architect.
  Validator: `tests/visual/shoot.py` (not collected by pytest). 12 pages/states x 1280/375 x light/dark + 2 reduced-motion runs = 50 shots in `.data/screenshots/` (+ report.json). Live app GET only; other states from a fake runner on 127.0.0.1:8765. All checks pass (see Messages).
- **Last Updated**: 2026-10-25 06:09 by Architect (baseline accepted: 50 runs, 0 overflow/focus gaps/external requests; re-run as part of T31)

## Task T29: Docs for the redesign
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: T26
- **Notes**:
  - README "Running the web app": new search controls (1-5 buttons), the theme toggle, and how to read a job
    card (score bar groups Match/Earn/Learn, level pips).
  - `docs/how-it-works.md`: a short "The interface" section linking to architecture.md "Web UI design". Explain
    the tokens, why there is no CDN/webfont (privacy), and the accessibility choices in learner terms.
  - Also the pending T24 note (05:40) if not done.
  Scribe: T24 note already done (05:40). Spec read; waiting for T26.
  Scribe (06:05): README "Running the web app" (1-5 buttons, theme toggle, "Reading a job card" sketch + Match/Earn/Learn, level pips, badge, Details); how-it-works §10 "The interface" (tokens, themes, no CDN/webfont = privacy, progressive enhancement, a11y) + decisions row + glossary.
  Scribe (06:28): Architect 06:27 item 4 done.
- **Last Updated**: 2026-10-25 06:30 by Architect (final review together with T32: wording matches the final UI)

## Task T30: UI polish round (Builder)
- **Assigned To**: Builder
- **Status**: Done
- **Dependencies**: T26 (Done)
- **Notes**: Implement architecture.md "Web UI polish round" items 1-8 exactly. Summary:
  1. **Match score 0-100 + SVG ring.** Add a pure `max_possible_score(prefs)` in `core/ranking.py` from the config weights.
     It's used for display instead of MAX_SCORE. Rename the similarity label to "relevance" (key unchanged).
  2. **Card breakdown.** One slim bar per card on the 0-100 scale, plus visually hidden points text. A "Why this score"
     `<details>` lists the non-zero terms as points and "Raw score X of Y possible for your priorities". The legend
     appears once, in the results header.
  3. **Hierarchy.** #1 featured ("Top match" badge, tint, 72 px ring, 180-char teaser); #2-3 standard; #4+ quiet. Drop the
     left accent border.
  4. **Search hero.** Headline, gradient + SVG accent (hidden below 40rem). Example chips shown by app.js (JS-only). Segments
     labelled Not important / Low / Medium / High / Top priority (drop the hidden hint and scale-ends).
  5. **Motion.** Hover lift (hover: hover), staggered fade-up (`--i`, capped at 10), 3 skeleton cards on the progress page;
     everything off under reduced motion.
  6. **Sticky summary bar** on /results, `--header-h` token, compact on phones.
  7. **Urgency chips** 0-3 danger / 4-7 warn / 8-14 neutral; titles `overflow-wrap: break-word; hyphens: auto;
     text-wrap: balance`.
  8. **Touch targets:** `.brand` min-width 44 px; skip link min-height 44 px.
  Constraints unchanged: field names, routes, CSRF, `/api/status`, no CDN, no new deps, live app on :8000 GET only.
  New colours only via `color-mix()` of existing tokens; ask me before adding a raw colour.
- **Last Updated**: 2026-10-25 06:30 by Architect (final review: corrected max_possible_score (only the larger source bonus), 320 px reflow OK, fixed skip link; 237 passed)

## Task T31: Tests + visual re-run for the polish round
- **Assigned To**: Validator
- **Status**: Done
- **Dependencies**: T30
- **Notes**:
  - Update the web tests for T30 and keep their meaning. The raw score is still in the page (in "Why this score").
  - Add tests:
    - `max_possible_score` for (1,1) = 0.55, (5,5) = 1.11, (3,1) = 0.69; the match number = round(100·score/max);
    - zero terms not listed;
    - the legend once per page;
    - "Top match" only on #1;
    - urgency classes at 2, 5, 10 and 20 days;
    - example chips are `type="button"` (never submit);
    - segment accessible names ("1 Not important" … "5 Top priority");
    - the skeletons are `aria-hidden`.
  - Re-run `tests/visual/shoot.py` (same matrix + the reduced-motion runs). Add checks:
    - hero text contrast ≥ 4.5;
    - the sticky bar stays visible after scrolling and causes no horizontal overflow at 375;
    - no running animations under reduced motion;
    - a long-title hyphenation shot at 375;
    - brand and skip link ≥ 44 px.
  - Also finish T27: fix `test_t24e_parser_version_in_content_hash` (version is 3) and add the `_shorten` tests.
  Validator: the 2 failures were outdated assertions, not regressions (ITC_PARSER_VERSION 2->3 is intended; 8-14 days now gets a neutral chip). Tests: tests/test_web_polish.py (16), urgency/no-chip tests in test_web_redesign.py, _shorten tests in test_itc_parser.py; pytest 236 passed. Visual: shoot.py re-run, 50 runs + new checks (hero contrast, sticky bar, 44 px brand/skip, title wrapping, long-title shot, focus hidden under sticky bars). One real bug sent to the Builder: keyboard focus hidden under the sticky header + summary bar.
  Validator (06:27): the Builder's fixes are verified. Focus is no longer hidden under the sticky bars (real Shift+Tab at 1280/375 and the automated check on every results/login-needed run: 0); 'Not important' fits (1 line at 1280, 'Not / important' at 375, no spill). The final full visual run is 50/50 clean; pytest 236 passed. Done-ready.
- **Last Updated**: 2026-10-25 06:30 by Architect (74/74 visual runs incl. 320 px; tests for 1.03 / 0.93 / 0.88)

## Task T32: Docs for the polish round
- **Assigned To**: Scribe
- **Status**: Done
- **Dependencies**: T30
- **Notes**:
  - README: how to read a card. The match score 0-100 is "relative to the best possible job for your priorities", with
    "Why this score", the Top match badge, urgency colours, example chips and the priority words.
  - `docs/how-it-works.md`: explain the `max_possible_score` normalisation in learner terms, and that the raw score is still shown.
  - Update any T29 wording that changes ("match" term → "relevance"; scale-ends removed).
  Scribe (06:18): README card guide rewritten (0-100 match vs best possible for your priorities, Top match, bar + key once, Why this score with raw score, urgency colours, example chips, priority words, sticky bar, skeletons); how-it-works §7 max_possible_score normalisation (worked example 0.74/0.97 = 76) + §10 polish round, a11y and relevance rename; decisions row.
  Scribe (06:28): Architect 06:27 item 4 done.
- **Last Updated**: 2026-10-25 06:30 by Architect (0.93 / 80 match worked example, 1.11 described as the weight sum, 'priorities' wording)

## Task T33: 320 px cosmetic nits
- **Assigned To**: Builder (CSS), Validator (re-check 320 px only)
- **Status**: Done
- **Dependencies**: T30 (Done)
- **Notes**: From the Validator's 06:30 report (`.data/screenshots/results-320-light.png`). Decided by Architect:
  1. Below 22.5rem, the featured card uses the 56 px ring (same as other cards), so the title column isn't squeezed to ~90 px.
  2. Below 22.5rem, hide the query chip in the sticky summary bar. Earn/Learn + "Change" stay, and the query is on the search page.
  CSS only. Validator: re-run the 320 px part of `shoot.py`; no new tests needed. Mark Review; I'll close it.
- **Last Updated**: 2026-10-25 06:33 by Architect (CSS checked; Validator 24/24 at 320 px; 237 passed)

## Messages

### Architect -> All (03:50)
Plan is ready. Design decisions are in architecture.md ("Decisions [Delegated]"); treat it as
the spec and ask me here when something is ambiguous rather than guessing. Builder: start with
T2 and T7, then follow the suggested order — offline-testable tasks first. Validator: T4 can
start from the spec now. Scribe: T20 now, T22 progressively. Live AIMS work (T8 verification,
T9) and ITC STEM column mapping (T10/T11) are blocked on the human — I've asked the Manager.

### Architect -> Scribe (03:50)
CLAUDE.md is stale: the Project paragraph says "No code exists yet". Please update it to say
auth + AIMS scraping exist and point to MULTI_AGENT_PLAN.md, and fill the Commands section:
`uv sync`, `uv run playwright install chromium`, `uv run pytest`,
`uv run python scripts/check_auth.py`, `uv run python scripts/scrape_aims_test.py`.
Keep the "owner is learning" rule but note design decisions are currently delegated to the
Architect (see architecture.md [Delegated]).

### Architect -> Builder (03:50)
Questions before you start (answer here; defaults apply if you don't):
1. T8: if `go_back()` after a detail page re-POSTs the search form (browser resubmit prompt in
   headless = silent), do you see duplicate requests in the network log? If yes, prefer
   re-running `search_jobs` over `go_back()`.
2. T12: any reason to prefer the official `ollama` Python package over raw `httpx`? Default:
   httpx (one dependency for both providers).
3. T2: `job_id` for ITC STEM depends on whether the spreadsheet has a reference number — keep
   the `itc:<college>:<...>` prefix and leave the suffix pluggable until we see the file.

### Architect -> Validator (03:50)
Please keep fixtures free of the owner's personal data: the detail fixture contains the
"You have applied" banner — fine to keep (git-ignored), but don't copy it into any test
expectation or doc. If any live check hits a page you don't recognize on AIMS, stop and report
rather than clicking around.

### Builder -> Architect (04:00)
Answers (Manager confirmed defaults): 1. AIMS session is expired so I can't inspect the network
log; T8 will re-run `search_jobs` after each detail page instead of `go_back()` (one extra
page load per opened job, but no form-resubmit ambiguity; summary_hash skipping keeps it small).
2. httpx, no `ollama` package. 3. ITC id kept pluggable: `itc:<college>:<suffix>`, suffix =
reference column if found, else short hash of title+dept. Starting T2 → T7 → T3 → T5 → T12...

### Architect -> All (03:55)
Team change (owner decision, relayed by Manager): the Validator is terminated and the dedicated
test tasks T4, T6, T9, T11, T16, T19 are Dropped. My earlier message to the Validator is void.
- Builder: keep the existing 3 parser tests passing (`uv run pytest`) and smoke-check your own
  code offline (a short script or REPL run in a temp dir, e.g. rank a few hand-made Jobs, round-trip
  the cache, FakeProvider through analysis/embeddings). Don't add new test suites. When a task is
  done, set it to Review and write one line on how you smoke-checked it; I review against
  architecture.md and move it to Done or back to In Progress with notes.
- Builder: your 04:00 answers are accepted as the decisions (re-search instead of `go_back()`,
  httpx, pluggable ITC id). I updated architecture.md for the re-search change.
- T8 live run and the ITC STEM spreadsheet are queued for the human in the morning. T10: keep
  header-name parsing with an alias table and the required `.data/itc/*.xlsx` fallback until then.
- T18 now includes extracting the AIMS department list (was part of T4).
- Scribe: unchanged. T20 looks done in CLAUDE.md; set it to Review and I'll check it. Please
  update the `uv run pytest` line later if anything about the tests changes.

### Scribe -> All (04:05)
T20 ready for review (CLAUDE.md). T22 draft at docs/how-it-works.md; stages not yet built are marked
"Planned" and follow architecture.md. I'll revise each section as its task reaches Done.
T21 waits on T8, T23 on T2/T3/T5. Per Manager: Validator is terminated, so docs will only
mention the existing parser tests.

### Scribe -> Builder (04:05)
Optional clarity refinements for existing code (low priority, fold in when you touch the file):
1. `auth.login()` prints "waiting up to 5 minutes" as a literal; derive it from
   `config.LOGIN_TIMEOUT_MS` so they can't drift.
2. `aims_scraper.parse_results()` raises a bare `IndexError` when no results table exists
   (zero matches, or a "Session Timeout" page); `parse_job_detail()` raises `AttributeError`
   if `div.body` is missing. A clear `ValueError("no AIMS results table found")` — or
   `AimsSessionExpired` once T7 adds it — would make failures readable.
3. `scripts/scrape_aims_test.py` indexes `rows[0]` without an empty-results check.
4. `_label()` and `_parse_label_value_table()` would benefit from a one-line example in the
   docstring (e.g. `<th>Closing Date:</th><td>25-Oct-2026</td>` -> `{"Closing Date": "25-Oct-2026"}`).
5. For new modules (T2+): please give public functions docstrings that state units/ranges
   (hours per week, 0–1 scaled terms) — I link to them from docs/how-it-works.md.


### Builder -> Architect (04:25)
T2, T7, T3, T5 are in Review. Offline smoke checks passed; `uv run pytest` is still green (3 passed).
- T2 `core/models.py`: Job + to_dict/from_dict (from_dict ignores unknown keys), compute_content_hash, embedding_text.
- T7 `core/auth.py`: AimsSessionExpired, ensure_not_timed_out(page) (title "Session Timeout"),
  ensure_job_search_page(page) (also requires select[name=p_copc_code]); is_logged_in() now opens
  AIMS_JOB_SEARCH_URL. search_jobs/open_job call these checks, so a timeout mid-run raises.
  I checked it offline with set_content pages. A live check still needs a login.
- T3 `aims_scraper.aims_to_job/summary_hash`: on the N26MA004 fixture it gives id aims:N26MA004, MA/Mathematics,
  16.5 h, part_time True, closing date 2026-10-25, positions 1. Where I deviated from the spec: description
  = Project Title + *all* free-text sections (Student Helper section names are unknown) + learning
  objectives, minus any objective whose text is already in a section (the fixture repeats it).
  Requirements are written as "Label: value" lines, with N/A values left out.
- T5 `core/cache.py`: the API as you sketched it, plus mark_seen(job_id) (for rows skipped because their
  summary is unchanged), get_job, get_summary_hash and last_scraped_at. A schema_version mismatch starts an
  empty cache. New config values: CACHE_FILE, CACHE_RETENTION_DAYS=30, STALE_AFTER_HOURS=6, AIMS_DELAY_S=1.5.
Got the owner's decision (College of Computing first, default dept CS). Moving on to T12.

### Builder -> Scribe (04:25)
For docs: new `core/models.py` (Job schema; the field docstrings can feed docs/data-model.md),
`core/cache.py` (module docstring has the file layout and the invalidation rule), and
`aims_to_job()` in aims_scraper.py. Login check: `scripts/check_auth.py` now confirms the *job
search page* opens (the old main-menu check could pass on a stale session). New config values in
config.py: CACHE_FILE (.data/cache.json), STALE_AFTER_HOURS=6, CACHE_RETENTION_DAYS=30, AIMS_DELAY_S=1.5.

### Architect -> Builder (04:30)
OWNER decision (not delegated): the product focuses on Computer Science, and the College of
Computing ITC STEM list is the primary/default source. Concretely: in `config.ITC_STEM_SOURCES`
put CC first with `enabled: True`, keep College of Science but `enabled: False`, and don't research
other colleges. Add `config.DEFAULT_DEPARTMENT = "CS"` and
`config.DEPARTMENT_BOOST_ALIASES = {"CS": {"CS", "CC"}}` (CS students also get the boost on College
of Computing ITC rows that only name the college; other departments match their own code only). In
T15, `dept = 1` if the job's code is in the alias set for the user's department. In T18 default the
select to `CS - Computer Science`. Use CS jobs in your smoke checks. T10/T18 notes are updated.

### Architect -> Scribe (04:30)
T20: reviewed and Done. CLAUDE.md is accurate.
T22 (docs/how-it-works.md) review: well written and correct, and the worked-example arithmetic checks
out (0.74). Please change these:
1. Remove the line-number anchors (`auth.py#L26` etc.). They have already drifted (`login()` is now at
   line 35). Link the file and name the function instead.
2. Add the owner's CS focus as an **owner decision** (not delegated), in a short "Owner decisions"
   note above the delegated table: the product focuses on CS; the College of Computing ITC STEM list is
   the default source; the UI defaults to CS. In the delegated table, replace the row "Fetch all ITC STEM
   lists; department only boosts" with "CS boost also covers CC (College of Computing) rows | CC ITC rows
   may only name the college | exact CS match only".
3. §2 ITC STEM: say the College of Computing list is the primary source and others are optional/off.
4. §7: `dept` = 1 if the job's code matches your department, and for CS that includes CC. Make the
   worked example CS-centric: a CS student, query "machine learning research", earn=2, learn=5,
   comparing a CC ITC STEM job and a CS CIS job (pick plausible numbers and show the arithmetic).
5. §1: T7 has landed (the job-search-page check, `AimsSessionExpired`). Describe it as built, once I've
   moved T7 to Done.
6. Also add one line on the CS focus to CLAUDE.md's Project paragraph and to README when you do T21.
Also from the Builder's 04:25 message: T23 can now start (`core/models.py`, `core/cache.py`, `aims_to_job()`
exist). Use N26MA004 without staff names, and keep the "applied" banner out.

### Builder -> Architect (04:50)
T12, T13, T14, T15 are in Review. `httpx` added to pyproject/uv.lock. Ollama models pulled. pytest is green.
- T12 `core/ai/providers.py`: Protocols LLMProvider/EmbeddingProvider; OllamaProvider, OpenAICompatibleProvider
  (key only in a header, repr masks it), FakeProvider (queued responses, where a str = invalid JSON; bag-of-words
  hashed vectors, so word overlap gives similarity). Errors: ProviderError ("Ollama is not running at ...",
  "run `ollama pull X`"), InvalidJSONResponse (subclass, retryable). httpx.Client can be injected (MockTransport).
  Config: OLLAMA_URL, DEFAULT_LLM_MODEL, DEFAULT_EMBED_MODEL, LLM_TIMEOUT_S=120, EMBED_TIMEOUT_S=60.
- T13 `core/analysis.py`: rate_level retries once on invalid JSON or an out-of-range level, accepts "4" as 4 but
  rejects booleans; ProviderError (connection) is not retried and propagates. analyze_jobs(jobs, cache, llm,
  progress) -> {job_id: level}; a cached `None` gets retried on the next run. Description is cut to 4000 chars.
- T14 `core/embeddings.py`: embed_jobs (batches of 16, cache-aware), embed_query (kind="query"), cosine, similarities().
- T15 `core/ranking.py`: Preferences, RankedJob(job, score, breakdown, similarity, level), rank(), is_closed().
  Weights and FULL_WEEKLY_HOURS=20, RESULTS_LIMIT=20, DEFAULT_DEPARTMENT="CS" and DEPARTMENT_BOOST_ALIASES are in config.
  A spec gap I filled: if every similarity is equal (or there is one job), the scaled sim is 1 for all of them, and a
  job without an embedding gets 0. A job is "closed" only once closing_date < today (still open on its closing day).
Live Ollama check: N26MA004 got level 4, a synthetic CS backend job 4, an office helper 1 (about 7 s per job on this machine);
the CS query is closest to the CS job (cosine 0.63 vs 0.50/0.55). Next: T10 (CC first), then T8, T17, T18.

### Builder -> Scribe (04:50)
New modules for how-it-works.md: `core/ai/providers.py` (Ollama / OpenAI-compatible / Fake; nomic
"search_document:"/"search_query:" prefixes added inside embed()), `core/analysis.py` (SYSTEM_PROMPT holds the
rubric; retry once then "unrated"), `core/embeddings.py` (cosine in plain Python), `core/ranking.py` (the module
docstring has the formula; weights are in config.py; CS also matches CC through DEPARTMENT_BOOST_ALIASES).
A level rating takes about 7 s per job with llama3.2:3b on CPU, so the first analysis of ~100 jobs takes minutes; after that the cache makes it fast.

### Architect -> Builder (03:46)
Review done: T2, T3, T5 and T7 are Done. I smoke-checked them on the fixtures and in a temp cache
file, and everything matches the spec. Your description-dedup deviation in T3 is accepted.
Three follow-ups, added to the task notes:
- T8 (must, before the live run): (1) `parse_results()` throws IndexError on a zero-match search,
  and Student Helpers (Summer Term) is likely empty now, so return []. (2) Add
  `aims_session(..., interactive=False)` that raises `AimsSessionExpired` instead of opening the
  headed login. Give `scripts/scrape_aims.py` a `--no-login` flag and always use it yourself (hard
  rule: agents never open a login window). (3) Optional: a PARSER_VERSION in `summary_hash()`.
- T10: openpyxl returns datetime objects for date cells; make `raw` JSON-safe (ISO strings).
- T17: the pipeline uses interactive=False; only the UI's "Log in to AIMS" button calls `login()`.
Also, please answer the Scribe's 04:05 notes as you go: the derived "5 minutes" message, a clear
error instead of IndexError/AttributeError (point 2 overlaps with T8 (1)), and docstrings with units.
Note on clocks: use `date '+%H:%M'` for message times. Earlier headers (04:00–04:30) ran ahead
of the system clock, which says 03:46 now.

### Architect -> Scribe (03:46)
T2, T3, T5 and T7 are Done, so docs can describe them as built: the job-search-page login check
with `AimsSessionExpired`, `core/models.py`, `core/cache.py` and `aims_to_job()`. That completes
T22 note 5, and T23 can go ahead. New design point to mention in §1: the pipeline never opens a
login window itself; the student clicks "Log in to AIMS" (architecture.md, AIMS session).

### Architect -> Builder (03:48)
OWNER decision (not delegated): the AI provider is **Ollama only**. In `core/ai/providers.py`, delete
`OpenAICompatibleProvider` and all API-key handling (parameters, Authorization header, repr). Keep
the `LLMProvider`/`EmbeddingProvider` interfaces, `OllamaProvider` and `FakeProvider`. Remove any
key-related config. T18 settings: only the Ollama URL and the two model names, with no key field.
`httpx` stays approved. Re-run `uv run pytest` and your Ollama/Fake smoke check, then set T12 to Review.

### Architect -> Scribe (03:48)
OWNER decision: **Ollama only**, with no API keys and no hosted AI providers. In docs/how-it-works.md, drop
OpenAI's `response_format` (keep Ollama `format: "json"`). In the delegated-decisions table, change the
"Plain httpx for both AI providers" row to "Plain httpx for the Ollama API | you see the raw HTTP |
the official `ollama` package". Add "Ollama only" to the Owner decisions note. In README (T21), state
that everything runs locally and nothing is sent to a hosted AI service. Same for CLAUDE.md if it
mentions API keys.

### Scribe -> Architect (03:49)
Owner decision relayed by Manager: Ollama is the only AI provider (no API keys, nothing sent to
external AI services). README.md and docs/how-it-works.md now say so. Still stale in files you own:
architecture.md lines ~38 (diagram "OpenAI-compatible"), ~66, ~125, ~226-227
(`OpenAICompatibleProvider`), ~265-266 (settings page with base URL + key, `.data/settings.json`),
~277 (trust boundary "With an API key..."); plan T12 notes (`OpenAICompatibleProvider(...)`) and
T18 notes (settings: "key in `.data/settings.json`"). Please also tell the Builder whether
`OpenAICompatibleProvider` should be removed from `core/ai/providers.py` and what the T18 settings
page keeps (Ollama URL + model names, I assume).


### Builder -> Architect (05:15)
**Finding (T10):** the CC job list is not behind a bot check. The page loads fine in headless Chromium, but
`.../cc/portal/studentlan/2026.10.22 Job List.xlsx` answers **302 to `/cc/portal/_spauth/_login.aspx`**
(a SharePoint portal login; the page itself says "Students are required to login to see the list"). I made one page
load and one request and did not follow the login. So automatic download can't work without the student
logging in. **Question:** should we add a headed "Log in to the CC portal" step later (same pattern as AIMS: the student
logs in themselves and we keep the cookies; it probably goes through the same CityU SSO), or keep manual download as the
supported path? Default for now: manual fallback, with the online try kept as a cheap first attempt.
T10 is in Review (up to the real-file mapping). `core/itc_stem_scraper.py`: find_job_list_url, download_xlsx (no redirects
followed; raises ItcDownloadBlocked on a login redirect, a non-zip body or an HTTP error), manual_files (`<college>_*.xlsx`; for the
primary source any other .xlsx in .data/itc/ too, newest first), parse_job_list(bytes, source, url) (alias table, finds the
header row among the top 15 rows, skips sheets without one, company goes into description as "Company: X", no dept column
-> CC/College of Computing, suffix = ref column else sha256(title|company|dept)[:12], duplicate rows get "-N"),
fetch_source and scrape_itc_stem(context|None, cache) -> notes for the UI (marks inactive only when every enabled college
succeeded). Config: ITC_STEM_SOURCES (CC enabled, CSci disabled), ITC_DIR, ITC_DELAY_S. openpyxl added.
Smoke checks (generated xlsx in the scratchpad): banner row, notes sheet, duplicate rows, "40 hours/week", dd/mm/yyyy dates; the manual
fallback runs end to end; the live online path reports "download needs a CityU login" plus the save-it-here instruction.
**T12 change (owner):** OpenAICompatibleProvider and all API-key code are removed; only OllamaProvider and FakeProvider are left.
No API-key setting will go in T18. Next: T8, then T17, then T18.

### Builder -> Scribe (05:15)
ITC STEM for the README/how-it-works: the College of Computing spreadsheet needs a CityU portal login, so for now
the student downloads it in their own browser and saves it to `.data/itc/cc_job_list.xlsx` (any .xlsx there
also works for CC). The app tries the online download first and otherwise says exactly where to save the file.
Columns are matched by header names (alias table `_FIELD_ALIASES` in itc_stem_scraper.py).
Owner decision: Ollama is the only provider, so the README should drop the API-key option. Job text never leaves the machine.

### Scribe -> Architect (03:52)
T23 ready for review: docs/data-model.md. T21 (README) has an early pass incl. the local-only
privacy section; I'll finish run/UI sections after T8/T18. T22 updated for everything Done/Review.

### Architect -> Builder (03:50)
Reviewed T10, T12-T15 (pytest 3 passed; FakeProvider smoke check end to end). T12-T15 Done.
T10 back to In Progress, one fix:
1. `_map_columns` matches keywords as substrings. Smoke test: header "Preferred Major" became
   `ref` (contains "ref"), so job_id = `itc:cc:ComputerScience` (two jobs with the same major
   collide) and requirements came out empty. Same risk: "unit" in "Community/Opportunity",
   "mode" in "accommodation", "post" in "Postgraduate", "area", "field". Match on word
   boundaries (e.g. `re.search(rf"\b{re.escape(k)}", text)`; keep stems like "responsibilit"
   working by anchoring only the start). Re-run a temp-dir smoke with a "Preferred Major"
   column, then set Review.
Notes for T17 (not blockers): pass only open jobs to `analyze_jobs`/`embed_jobs` (closed
jobs currently get rated, wasted LLM calls); `weekly_hours=None` -> 0.5 on the hours term is
fine, I'll record it in architecture.md.

### Builder -> Architect (05:35)
T8 is in Review, code-complete. I haven't run it live (session expired). In `aims_scraper.py`: parse_matched_count, parse_results
returns [] when there is no results table (a scheme with no jobs), SchemeResult, needs_detail, scrape_scheme (re-runs the search
instead of go_back, and only when a detail page was opened; a 1.5 s pause before every page load; errors are isolated per job, but a
failed job still counts as "seen" so it isn't deactivated; AimsSessionExpired propagates and nothing gets marked complete; warns
on a matched-count mismatch), scrape_aims(context, cache, scheme_keys, progress) saves the cache after each scheme.
Script: `uv run python scripts/scrape_aims.py [cis sh_summer sh_regular]`.
Offline smoke check (fetchers patched to serve the CIS fixtures): first run 45 listed/45 matched, 44 opened, 1 injected error isolated;
second run 44 unchanged and 1 retried (1 search + 1 open). Cost: on the first run, about 2 page loads per job (about 3 s each), so
about 2.5 min for 45 CIS jobs; later runs are near zero.
**For the Manager/human:** the live check = `uv run python scripts/check_auth.py` (human login), then `uv run python scripts/scrape_aims.py`.
Next: T17 pipeline.

### Builder -> Scribe (05:35)
New command for CLAUDE.md/README: `uv run python scripts/scrape_aims.py [cis] [sh_summer] [sh_regular]` scrapes all
AIMS schemes into `.data/cache.json` (needs a valid session; read-only; a few minutes the first time, and later runs only open
new or changed jobs).

### Architect -> Scribe (03:51)
T23 reviewed: Done. It matches `models.py` and `cache.py`, keeps staff names out and uses
illustrative values where it should. Small follow-ups (no re-review needed): (1) drop the
`#L230` anchor on `aims_to_job()` (line numbers drift); (2) "AIMS now, ITC STEM later":
ITC STEM is built now (T10), so add a short "`raw` for an ITC STEM job" note: header text ->
cell text (as strings), plus `_sheet`; `job_id` suffix = reference column, else
sha256(title|company|dept)[:12]; no department column -> `CC` / College of Computing.
Your 03:49 API-key list: architecture.md was already updated at 03:48 (line 66 is marked
"Superseded" on purpose, as the history of the owner's original design). I struck the old T12
spec line and fixed project-map/progress. T12 is Done with Ollama + FakeProvider only;
T18 settings keep only the Ollama URL and the two model names. New fact for docs: the CC
`.xlsx` needs a CityU portal login, so manual download to `.data/itc/cc_job_list.xlsx` is
the supported path (architecture.md "ITC STEM").

### Architect -> Builder (03:51)
Re your 05:15 T10 question: keep manual download as the supported path (Decided by
Architect, delegated; recorded in architecture.md "ITC STEM"). Keep the single online attempt
that never follows the redirect. No CC portal login step for now. Revisit with the owner.
The word-boundary fix from my previous message still applies.


### Builder -> Architect (03:52)
(Clock fixed: I now use `date`. My 04:00–05:35 headers were wrong, and those messages came before this one.)
Follow-ups done; pytest 3 passed.
- T8: (1) parse_results returns [] already (in my 05:35 message). (2) `ensure_session/aims_session(playwright, interactive=True)`: with
  False it raises AimsSessionExpired and never opens a window. `scripts/scrape_aims.py --no-login` turns that into "AIMS login needed:
  ... Run: uv run python scripts/check_auth.py". I checked it with SESSION_FILE pointed at a missing file: it exits cleanly with no browser
  (side effect: it wrote an empty .data/cache.json). (3) PARSER_VERSION = 1 is mixed into summary_hash.
- T10 (Review again): keywords now match whole words with an optional plural "s", and a trailing `*` marks a stem (`responsibilit*`,
  `vacanc*`, `hour*`). Checked: "Preferred Major" goes to requirements; Postgraduate/Community/Accommodation match nothing; Ref. No., Dept.,
  Working Hours/Week, No. of Vacancies and Mode of Employment map correctly. Also new: `requirements` and `description` collect *all* matching
  columns (as "Header: value" lines when there are several), so "Preferred Major" + "Skills Required" are both kept. Your temp-dir smoke check with two
  same-major jobs now gives distinct hash IDs. raw is JSON-safe: every value goes through _cell_text, which turns datetimes into ISO strings.
- Scribe notes: the login message is derived from LOGIN_TIMEOUT_MS; parse_job_detail raises ValueError when div.body is missing;
  scrape_aims_test.py checks for empty results; _label/_parse_label_value_table have examples in their docstrings.
- T17 notes noted: only open jobs go to analyze/embed, and interactive=False. Starting T17 now.

### Builder -> Scribe (03:52)
Done from your 04:05 list: 1 (the minutes are derived), 2 (parse_results -> [] on zero matches; a timeout page raises AimsSessionExpired
first; a missing div.body -> ValueError), 3, 4, 5 (the new modules state units: hours per week, 0–1 terms). Also:
`scripts/scrape_aims.py --no-login` stops with "AIMS login needed" instead of opening a window.

### Builder -> Architect (03:54)
T17 is in Review. `core/pipeline.py`: `refresh(sources=("aims","itc"), force, cache, progress, stale_after) -> RefreshReport(scraped, skipped,
notes, login_needed)`. It checks staleness per AIMS scheme, uses `aims_session(interactive=False)` (AimsSessionExpired -> login_needed + a note,
cached AIMS jobs are still used), fetches ITC with a plain hidden browser (no AIMS cookies), prunes, and saves. `recommend(prefs, provider, cache,
progress, today) -> list[RankedJob]` sends only open active jobs to analyze/embed, saves the cache every 10 ratings and after each stage, and with an
empty query gives every job the same similarity. `search(prefs, provider, force_refresh, progress)` runs both on one cache: this is what the UI calls.
Progress is a `Callable[[str], None]` that receives status lines.
Smoke check (scratchpad cache; manual CC xlsx; AIMS session missing): login_needed=True with a clear note; ITC got 3 jobs with the note "download needs a
CityU login"; the second refresh skipped itc_stem (fresh). recommend with Ollama, CS + "machine learning research", earn 2 / learn 5: N26MA004
0.721 (level 4), ML Intern (CC, ITC) 0.698 (level 4), Backend intern 0.330 (level 3). About 7 s per rating.
**Question (spec gap):** the cached level is reused when content_hash matches, *even if the student switches LLM model*. (I hit this in the smoke
check: the Fake ratings were reused by Ollama.) Should a level also store and require the same `model`, like embeddings do? That means a model switch
re-rates everything, which costs minutes. Default for now: keep the spec (hash only); it's a one-line change in `Cache.get_level` if you want it.
Starting T18.

### Builder -> Scribe (03:54)
`core/pipeline.py` is the only entry point the interfaces use: `search()` = `refresh()` (only stale sources; 6 h) + `recommend()`. With a
missing or expired AIMS session it never opens a window: it shows the cached AIMS jobs and sets `login_needed`.

### Builder -> Architect (03:58)
T18 is in Review. Run: `uv run uvicorn cityu_jobs.web.app:app --reload`, then open http://127.0.0.1:8000. fastapi/uvicorn/jinja2 are added.
- `web/app.py` (create_app(runner) factory + `app`): GET / (department select with CS first, then a divider and the other 209 AIMS departments;
  a CS-flavoured placeholder; earn/learn 1–5 sliders; a "Refresh now" checkbox = force), POST /search -> 303 /results, GET /results (polls /api/status
  every 1.5 s while running; top RESULTS_LIMIT with a stacked score bar + per-term values, level + AI reason, details), GET /api/status,
  POST /login (headed `auth.login()` in the background thread; only from the button), GET/POST /settings (Ollama URL + 2 model names, no
  key; lists the installed Ollama models as a hint).
- `web/tasks.py` TaskRunner: one background thread, one task at a time (search or login never overlap), progress lines from the pipeline.
- New core helpers (the CLI will need them too): `core/settings.py` (Settings -> .data/settings.json, `.provider()`), `core/departments.py`
  (loads `src/cityu_jobs/data/aims_departments.json`, 210 entries, extracted from the saved search page), `RankedJob.level_reason`
  (filled in by pipeline.recommend).
- Choices to note: (1) form bodies are parsed with `urllib.parse.parse_qs`, so the `python-multipart` dependency isn't needed. (2) POSTs with a
  foreign `Origin` header get a 403 (CSRF: otherwise another website could trigger the login window or change the Ollama URL). (3) The
  settings page warns that changing the LLM model doesn't re-rate jobs. That matches the current spec; my level/model question (03:58) is still open, and if you
  change it, I'll update that hint and `Cache.get_level`. Seen your 03:51 reply on T10: manual download it is, already built that way.
Smoke checks: TestClient with a fake search (form -> status -> results, login_needed banner, cross-site 403, login via a stub, settings
saved); visual check in Chromium via the server with the fake runner (Ollama ratings on the scratchpad cache). I fixed one CSS clash (the level bar
segment was hidden). I didn't run a real search from the UI because it would touch AIMS with the expired session. pytest is green.
All my tasks (T2, T3, T5, T7, T8, T10, T12–T15, T17, T18) are now Review or Done. Waiting for review feedback or new tasks.

### Builder -> Scribe (03:58)
T18 is ready for the README: run `uv run uvicorn cityu_jobs.web.app:app --reload` and open http://127.0.0.1:8000. Pages: Search (CS is the default department;
free text; earn/learn sliders; "Refresh now" ignores the 6 h freshness), Results (progress while running; the top 20 with a coloured score bar:
match / dept / hours / student helper / level / internship; the AI's level reason; details), Settings (Ollama URL + LLM + embedding model; no API key).
"Log in to AIMS" opens a browser window on the student's machine; the app only keeps the cookies. The first search can take minutes (≈7 s per job
rating); later searches use the cache.

### Architect -> Builder (04:01)
Reviewed T8, T10, T17, T18: all Done (pytest 3 passed). Smoke checks: uvicorn pages all 200, CS
preselected, no key field, cross-site POST 403; a FakeProvider search via TestClient on the
N26MA004 fixture rendered the score breakdown and login banner, and leaked no staff names or the
applied banner. T10 re-smoke: "Preferred Major" -> requirements, distinct IDs.
Your level/model question: keep the spec (content_hash only; the settings hint stays).
Recorded in architecture.md "AI level rating" as delegated, to revisit with the owner.
Small follow-ups, non-blocking, do when convenient:
1. `TaskRunner.start_search`: set `state.prefs` (and clear old results) *before* starting the
   thread. Right now /results, right after the redirect, can briefly show the previous
   search's prefs next to its results.
2. `pipeline.refresh`: ITC is appended to `report.scraped` even when every source failed.
   Only add it when `scrape_itc_stem` marked it complete (e.g. `last_scraped_at` changed), so
   the UI note is honest. (With no manual file, each search retries the CC page once; that
   is polite enough, keep it.)
T8 live verification stays with the owner (in the T8 notes).

### Scribe -> Architect (04:01)
T21 and T22 are in Review (README.md, docs/how-it-works.md). T23 follow-ups done (no #L anchor; ITC STEM
`raw`/job_id/CC-default note in docs/data-model.md). CLAUDE.md Commands now also list
`scripts/scrape_aims.py --no-login` and the uvicorn command. Docs state current behavior for the
Builder's open question (03:54): a cached level is kept when the LLM model changes. If you decide
otherwise, tell me and I'll update README "Running the web app" and the guide's decisions table.


### Builder -> Architect (04:02)
Did both 04:01 follow-ups. (1) `TaskRunner.start_search` now sets `state.prefs` and clears
`report`/`results` under the lock *before* the thread starts (new optional `before` hook in
`_start`; a login still keeps old results). (2) `pipeline.refresh` adds `itc_stem` to
`report.scraped` only if `cache.last_scraped_at("itc_stem")` changed, i.e. every enabled college
was fetched; the notes are still shown either way. pytest 3 passed; offline smoke: /search then
/results shows the new prefs with no stale results; refresh with a failing/succeeding fake ITC scrape
gives scraped=[] / ["itc_stem"]. Ack on level/model: keeping content_hash only.

### Scribe -> Architect (04:06)
Docs now match your 04:01 decision (level invalidated only by content_hash; model stored for display;
settings page warns): README "Running the web app", how-it-works §5 + decisions table, data-model
level rows. T21/T22 remain in Review.

### Architect -> Scribe (04:03)
T21 (README) and T22 (how-it-works) reviewed: both Done. I checked them against the code. The
uvicorn command starts and serves every page, `scrape_aims.py` parses `[]`/`cis`/`--no-login`,
the Python version matches pyproject, and every function you cite exists and does what you say.
The docs are Ollama-only and CS-focused. Two small follow-ups (no re-review needed):
1. how-it-works decisions table, "A cached level is kept when you switch LLM model": it is no
   longer an open question. Decided by Architect (delegated), to revisit with the owner (see
   architecture.md "AI level rating"). Change "(open question to the Architect)" accordingly.
2. §7 worked example: optional, but my earlier note asked for a CS-centric one, e.g. a CS
   "machine learning research" CIS job, earn = 2, learn = 5. Recompute if you change it:
   earn 2 -> 0.25, learn 5 -> 1.0.

### Architect -> Validator (04:09)
Welcome back. The owner reinstated T4, T6, T9, T11, T16 and T19, all Pending and assigned to you,
with notes updated to what was built (Ollama only, so no API-key/OpenAI tests). Order: T4, T6,
T16, T19 (all offline; use `tmp_path`, never the real `.data/` files). T9 waits for the
Manager's login confirmation, and even then you use `--no-login` and never touch the apply
form. T11 waits for an `.xlsx` in `.data/itc/`; until then, generated spreadsheets only.
`uv run pytest` shows 82 passed right now (your new tests are already landing?); if some of these tasks are already covered, set them to Review with the file names. Set each task to Review when it's done and I'll review it.
Don't change `src/`: send bugs to the Builder (and me), with a failing test.

### Validator -> Architect (04:12)
T4, T6, T16 and T19 are in Review. `uv run pytest`: 119 passed (3 old + 116 new), 1.1 s, stable over 5 runs. Everything is offline: tmp_path caches, FakeProvider, httpx.MockTransport, stubbed Playwright, settings file redirected; I checked that the real `.data/cache.json` was not touched. Files: tests/conftest.py, test_models.py, test_cache.py, test_ai.py, test_ranking.py, test_pipeline.py, test_auth.py, test_web.py. I also added pipeline and auth tests (not in the notes) because refresh/login_needed and the interactive=False rule had no coverage. No src bugs found: every spec property held on the first run. My only failure was a wrong test assumption (the stylesheet link is absolute via url_for), which I fixed in the test. Fixture `.data/fixtures/aims_job_search.html` is copied (T4 housekeeping). The one remaining warning is Starlette's httpx TestClient deprecation, harmless for now. Next: T9 when the Manager confirms the login, and T11 when an xlsx is in .data/itc/.

### Architect -> Validator (04:13)
T4, T6, T16, T19 reviewed: all Done (119 passed). Every plan note is covered, and the bonus
test_pipeline.py / test_auth.py cover T17 and T7 as well. Isolation checked: config paths are
monkeypatched to tmp_path, Playwright/AIMS/Ollama are faked, and the real `.data/cache.json`
was untouched by the run. The one warning (Starlette's TestClient wants `httpx2`) comes from a
library: leave it, no new dependency. Next: T9 after the Manager's login confirmation
(`--no-login` only), and T11 when an `.xlsx` appears in `.data/itc/` (generated-sheet tests can
go in now).


### Validator -> Builder, Architect (04:43)
T9 live run findings (session is fine; nothing expired; only the search form and job links used).
The first run (all schemes, started 04:13) wasn't hung: CIS finished at 04:26 (45 jobs, 45 matched), sh_summer at 04:26 (0 jobs, matched 0), and then it was still working through sh_regular (72 jobs). The Manager saw an empty log and an idle event loop. I stopped it with SIGINT at 04:40; the traceback shows it was inside `_polite_pause()` in scrape_scheme's per-job loop. The session was re-saved cleanly by aims_session's finally block. Re-running sh_regular alone now.
**Bug (Builder): an interrupted scheme loses all its work.** `scrape_scheme` only saves via `scrape_aims` after a whole scheme, and `scripts/scrape_aims.py` doesn't save on KeyboardInterrupt, so ~14 min of sh_regular detail pages were thrown away. Repro: `uv run pytest tests/test_aims_scrape.py::test_interrupted_scheme_keeps_jobs_already_opened --runxfail` (fake fetchers; KeyboardInterrupt on job 3 -> the saved file has neither job 1 nor 2). Suggested fix: `cache.save()` every N opened jobs inside scrape_scheme (safe: mark_scrape_complete still runs only at the end, and saved jobs are skipped next run by summary_hash), and/or try/finally save in scrape_aims. When fixed, remove nothing: the test is `xfail(strict=True)`, so it will flip to XPASS -> failing, and I'll drop the marker.
**Usability (Builder, low):** (1) progress is one line per scheme, so a 20-min scheme looks frozen, both in the CLI and in the UI status. A per-job line ("Student Helpers (Regular Term): 12/72") via `progress` would fix both. (2) Output to a pipe/file is block-buffered: use `print(..., flush=True)` in scrape_aims.py (or `python -u`).
**Timing (Scribe/Architect, docs):** measured ~17 s per opened job (CIS: 45 jobs in ~13 min), because each job costs 2 pauses + a re-search (goto + submit = 2 page loads) + the detail page. The docs' "about 2.5 min for 45 CIS jobs" is off by ~5x; a first full run is ~35 min (117 jobs). Later runs only open changed rows.
Fixtures saved: aims_results_sh_summer.html (0 jobs), aims_results_sh_regular.html (72), aims_job_detail_sh_regular.html (P26FO006). New tests: tests/test_aims_sh_parser.py (5: Student Helper results have no Part-time column -> part_time None; staff names never in embedding text) and tests/test_aims_scrape.py (7: re-search count, second run opens only changed rows, inactive marking, error isolation, expiry not marked complete, + the xfail). pytest: 130 passed, 1 xfailed.

### Scribe -> All (04:44)
Docs fixed for the measured AIMS timing (Validator 04:43): README "first search is slow" (~35 min AIMS
first run, why, tip to run scripts/scrape_aims.py once, don't-interrupt warning) and how-it-works §2
cost table. README/CLAUDE.md test lines now say "all offline" instead of "only parser tests".
T21/T22 back in Review.

### Scribe -> Builder (04:44)
1. `scripts/scrape_aims.py` docstring says "Takes a few minutes the first time": measured ~35 min
   (117 jobs, ~17 s each). Please update. The results.html "several minutes" note could say the first
   AIMS scrape takes ~30+ min.
2. When you fix the interrupted-scheme bug (Validator 04:43), tell me: README currently warns that
   stopping mid-scheme loses that scheme's progress, and I'll remove the warning.


### Builder -> Validator, Architect (04:44)
Fixed the 04:43 report (no live AIMS run on my side; your run was left alone).
(1) Interrupt keeps work: `scrape_scheme` saves the cache every `SAVE_EVERY = 5` opened jobs, and
`scrape_aims` now saves in a `finally` (session expiry, errors, Ctrl+C). `mark_scrape_complete` still
runs only at the end of a scheme. `scripts/scrape_aims.py` exits on Ctrl+C with "Interrupted. Jobs
scraped so far are saved in ...". **Validator: please drop the xfail marker** on
`tests/test_aims_scrape.py::test_interrupted_scheme_keeps_jobs_already_opened`; it is XPASS(strict)
now, which is the only failure (130 passed, 1 failed).
(2) Per-job progress: `scrape_scheme(..., progress=None)` emits "Student Helpers (Regular Term): 12/72"
for each row it opens (unchanged rows are skipped silently); `scrape_aims` passes its `progress`
through, so the CLI and the UI status (via pipeline.refresh) both show it.
(3) The script prints with `flush=True` (`functools.partial(print, flush=True)` as the progress callback).
Its docstring now says the first run takes about 35 min (~17 s per job). Scribe should get the same timing fix for the docs.

### Scribe -> Builder, Validator (04:46)
Saw the interrupted-scheme fix land (SAVE_EVERY + finally save; scrape_aims.py Ctrl+C message). README
now says "you can stop and resume" instead of the warning, and the guide's §2 lists the periodic save.
Validator: `test_interrupted_scheme_keeps_jobs_already_opened` is now XPASS(strict) -> 1 failed;
the marker can be dropped as you planned.

### Architect -> Builder (04:46)
Reviewed your 04:44 fixes: good, no changes needed. The periodic save can't deactivate jobs
(mark_scrape_complete is still end-of-scheme only), the finally-save covers expiry/errors/Ctrl+C,
and per-job progress reaches the UI through pipeline.refresh. Leftover from the Scribe's 04:44
note: `web/templates/results.html` hint still says the first search "can take several minutes".
Make it say the first AIMS scrape takes about 35 min plus ~7 s per job for rating.

### Architect -> Validator (04:46)
Builder's fix reviewed and accepted: please drop the xfail marker on
`test_interrupted_scheme_keeps_jobs_already_opened` (it is XPASS(strict) = the only failure). When
your sh_regular re-run finishes, report its real duration and counts, so the docs can quote a
measured total.

### Architect -> Scribe (04:46)
T21/T22 re-reviewed: Done. One wording fix, no re-review needed: the 35-min total and "the first
full run opened 117 jobs" are presented as measured, but the first run was stopped during
sh_regular. Only CIS (45 jobs ≈ 13 min) was measured end to end. Say "estimated ~35 min (117 jobs
× ~17 s, measured on CIS)" until the Validator reports the sh_regular re-run, then put in the real number.

### Validator -> Architect (05:04)
T9 is in Review. The sh_regular re-run finished at 05:02 (72/72, 0 errors, 21 min), and a second full run opened 0 detail pages (117 unchanged, 41 s). Details are in the T9 notes. pytest: 130 passed, 1 xfailed (the interrupt bug I reported to the Builder at 04:4x). Only T11 is left; it waits for an xlsx in .data/itc/.

### Validator -> Architect (05:05)
The Builder's interrupt fix is verified offline (no live run). I removed the xfail marker from test_interrupted_scheme_keeps_jobs_already_opened; it now passes as a normal test. Also added to tests/test_aims_scrape.py: exact per-job progress lines ("Scraping AIMS: <scheme>", then "<scheme>: i/n" for each opened job; an unchanged second run emits only the scheme line); scrape_scheme's own periodic save (SAVE_EVERY patched to 2, interrupted on job 3 -> jobs 1-2 on disk, even without scrape_aims' finally save); and an interrupted run resuming without re-opening saved jobs. The pipeline passes `progress` through to scrape_aims, so the UI gets the same lines; scrape_aims.py prints with flush=True. pytest: 134 passed, 0 failed, 0 xfail. Nothing open on my side except T11 (waits for an ITC xlsx).

### Architect -> All (05:06)
T9 Done (live: CIS 45/45, SH Summer 0/0, SH Regular 72/72, 0 errors; second run 0 pages in
41 s). The interrupt fix is accepted with tests (134 passed). T8 is fully verified. The only
open task is T11, Blocked until the owner saves the CC ITC STEM `.xlsx` in `.data/itc/`.
Scribe: measured numbers for the docs: CIS ≈ 13 min, SH Regular 72 jobs ≈ 21 min, so the
first full run is ≈ 34 min; a second full run ≈ 41 s. Replace the "estimated" wording with these.

### Validator -> Builder, Architect (05:29)
T11 is in Review: the real College of Computing ITC list (2026.10.22) is checked offline. **Actual headers** (row 1 is a banner, row 2 is the header) and how `_map_columns` maps them now:
No. -> (none; row number, so correctly not a ref) | Date (posting date) -> none | Company/Organisation Name -> company | Company /Organisation Overview -> none | Company Website -> none | Job Title -> title | Job Description(the internship should be Information & Technology related) -> description | Job Requirements -> requirements | Working location -> none | Proposed Internship Start Date -> period | Proposed Internship Duration -> **none** | No. of Vacancies for this position -> positions | Will the company provide any monthly honorarium ON TOP of the allowance...? -> none | Application Procedures -> none | Email for Submission of Application -> none | Application Deadline -> closing_date | Remarks-other specific requirements (if any) -> requirements.
Result: 98/98 rows parsed; 98 unique ids (hash suffix, stable across runs); dept CC/College of Computing for all; every row has a closing date and positions; no hours column, so weekly_hours = None for all (0.5 on the hours term, as designed); ~70 of 98 titles are clearly CS/IT. Unmapped columns stay in raw.
**Bug (Builder), affects recommendations:** sheet rows No. 6-9 (incl. "AI / Machine Learning Research Intern", "Game Developer Intern (JavaScript)", "Software Engineering Intern - Biomechanics...") hold their deadline/start as Excel *date cells* with day and month swapped: posted 2026-09-14, deadline 2026-06-11, start 2026-05-10 (really 6 Nov / 5 Oct; the 84 text date cells in this sheet are all day-first, e.g. "27/11/2026"). One more start date is swapped (No. 112: 2026-02-11, posted 2026-10-22). Taken literally, these CS jobs count as closed and are never shown. Repro: `uv run pytest tests/test_itc_parser.py::test_real_swapped_excel_dates_are_corrected --runxfail`. Suggested fix: in parse_job_list, if a datetime cell is earlier than the row's "Date" (posting) column and its day <= 12, swap day and month. Architect: please approve that heuristic or pick another (e.g. ignore date cells earlier than the posting date -> closing_date None, so the job stays visible).
**Mapping suggestions (Builder, low; Architect decides):** (1) "Proposed Internship Duration" ("90 days") is dropped from the Job: make employment_period "21/09/2026, 90 days" (start + duration). (2) "Company /Organisation Overview" could go into description ("About the company: ...") to improve embeddings. (3) The honorarium column is the only "earn money" signal in ITC rows; it's only in raw for now (a design question, not a bug).
pytest: 155 passed, 1 xfailed (the swapped-date repro).

### Architect -> Builder, Validator (05:31)
Decisions on the Validator's 05:29 T11 report (Decided by Architect, delegated; full text in
architecture.md "ITC STEM", "Real CC list"):
1. **Swapped dates: approved, with a safety net.** Only for Excel *date cells* (datetime values)
   earlier than the row's posting "Date": if day ≤ 12 and the swapped date is on/after the posting
   date, swap; otherwise set that field to `None` (job stays visible). Text dates are left
   alone. If the posting Date is missing, leave the cell as is. Add a `posted` alias ("date",
   whole word, only after closing/period aliases) or read it by header so it doesn't take another column.
2. **Duration -> employment_period:** "start, duration" (e.g. "21/09/2026, 90 days"); either part alone if the other is missing.
3. **Company overview -> description:** append "About the company: <overview>" *after* the job
   description (and the existing "Company: X" line), cut to 500 chars.
4. **Honorarium: not scored.** Keep it in raw, and show it in the results "Details" if that's a one-line
   template change (label "Extra honorarium"). weekly_hours stays None for ITC rows.
**Cache catch:** ITC `content_hash` is computed from `raw`, and (2)/(3) change `description` but
not `raw`. So the 98 cached ITC jobs would keep their old embeddings and levels. Fix: include
`"_parser_version": ITC_PARSER_VERSION` (= 2) in the dict hashed for `content_hash` (same idea as
AIMS's PARSER_VERSION in summary_hash). The next run then re-embeds and re-rates the 98 ITC jobs once. Validator: tests for 1-3 plus that a parser-version bump
changes content_hash; then I'll review T11.


### Builder -> Validator, Architect (05:31)
Fixed the 05:29 swapped-date bug in `itc_stem_scraper.py` (Manager asked for it now; I used the
Validator's heuristic, Architect may still change it). A bare "Date" header (exact match only, via a
new `_EXACT_HEADERS`, so "Start Date" is not affected) maps to a new `posted` field.
`_unswap_date(value, posted)`: an Excel *date cell* earlier than the posting date, with day <= 12,
is day/month-swapped, but only if the swapped date is on/after the posting date. Text cells and rows
without a posting date are untouched; `raw` (and so content_hash) keeps the original values.
Applied to closing_date and employment_period. Real sheet: rows 6-8 now close 2026-11-06 and start
2026-10-05; No. 112 starts 2026-11-02. Row 9's deadline becomes 2026-10-05, so it is still closed.
15/98 remain closed. All are text dates or date cells that are plausible as stored (e.g. 2026-10-01, posted 09-17).
**Validator: please drop the xfail marker** on
`tests/test_itc_parser.py::test_real_swapped_excel_dates_are_corrected`; it is XPASS(strict) now
(155 passed, 1 failed = that test; with --runxfail all 22 ITC tests pass).
Not done yet, waiting for the Architect: the 3 mapping suggestions (duration in
employment_period, company overview in description, honorarium column).

### Validator -> Architect (05:32)
Swapped-date fix verified against the sheet (offline). Rows No. 6-8: deadline 2026-06-11 -> 2026-11-06, start -> 2026-10-05; No. 9: deadline -> 2026-10-05 (correctly closed today); No. 112 start -> 2026-11-02; day-first text cells (e.g. No. 5, 27/11/2026) unchanged. I removed the xfail marker and added test_real_swap_fix_covers_all_affected_rows_and_leaves_text_dates. pytest: 157 passed, 0 xfail. T11 stays in Review.

### Architect -> Builder, Validator (05:35)
T11 is **Done**. The swapped-date fix is accepted: I re-checked the sheet, and all 9 early date cells
(rows 6-9, 112) are swappable, so the output is right. Our 05:31 messages crossed. The
remaining parts of my 05:31 decisions are now **T24** (Builder code, Validator tests):
(a) the `None` safety net for date cells that can't be swapped, (b) the duration in employment_period, (c) "About the company"
in the description, (d) the honorarium only in raw / Details, (e) ITC_PARSER_VERSION in the content_hash dict.
Without (e), the 98 cached ITC jobs keep their old embeddings after (b) and (c).

### Builder -> Validator, Architect (05:36)
T24 is in Review (`src/cityu_jobs/core/itc_stem_scraper.py`, plus one line in `results.html`).
(a) `_unswap_date`: an Excel date cell earlier than the posting date that can't be swapped (day > 12,
or still too early after swapping) -> `None`. (b) A new `duration` field (alias "duration", taken
out of `period`): `employment_period` = "start, duration" or either part alone, e.g. "21/09/2026, 90 days".
(c) An `overview` field (aliases "overview", "about the company", "company profile", "company background",
tried before `company`): "About the company: ..." comes after the job text, cut at `OVERVIEW_MAX_CHARS = 500`.
(d) A `honorarium` field: not scored; stored as `raw["_honorarium"]` only when it offers something
(`_offers_honorarium` drops "NA", "No.", "0", "... will not provide ..."). Details shows "Extra
honorarium: ..." (3 of 98 real rows: "/monthly...", "minimum wage from 60th day", "8000").
(e) `ITC_PARSER_VERSION = 2`, stored as `raw["_parser_version"]`. It sits in raw itself (like `_sheet`)
rather than only in the hashed dict, so `content_hash == compute_content_hash(raw)` still holds (your
`test_raw_is_json_safe_with_date_cells` relies on it). The next run re-rates and re-embeds the 98 ITC jobs once.
Real sheet: 98 jobs, 98 unique ids, all 98 have an overview.
**Validator:** 156 passed, 1 failed. `test_real_swap_fix_covers_all_affected_rows_and_leaves_text_dates`
expects `employment_period == "2026-10-05"`, but per (b) it is now `"2026-10-05, <duration>"` (e.g.
"2026-10-05, 59 days"). Please update it (e.g. `.startswith("2026-10-05")`), and add tests for a-c and e.

### Validator -> Architect (05:38)
T24 tests written; pytest is all green: **176 passed**. In tests/test_itc_parser.py:
- Updated test_real_swap_fix_covers_all_affected_rows_and_leaves_text_dates for "start, duration": rows No. 6-8 = "2026-10-05, <their duration>"; No. 112 starts "2026-11-02, ".
- (a) unswappable early date cell -> None (day 13; and 2 Mar -> 3 Feb is still before posting); a swappable one is swapped (10 Sep -> 9 Oct); dates on/after posting, text cells and rows without a posting date are untouched.
- (b) period = "start, duration", either part alone, or None; a swapped start cell is fixed first.
- (c) "About the company: ..." comes after the job text, capped at OVERVIEW_MAX_CHARS = 500; absent when there's no overview.
- (d) honorarium: _offers_honorarium on No/NA/N/A/0/No./blank/"will not provide" -> not offered; amounts -> raw["_honorarium"]; never in description/requirements; equal scores with and without it; "Extra honorarium: ..." shown on /results.
- (e) raw["_parser_version"] == ITC_PARSER_VERSION == 2; bumping it changes content_hash but keeps job_id.
No bugs found in T24. Please review.

### Architect -> Scribe (05:40)
T24 is Done. Please add its user-visible changes to the docs where they fit:
- `docs/data-model.md` (ITC rows):
  - `employment_period` is now "start, duration" (e.g. "05/10/2026, 59 days").
  - `description` ends with "About the company: …" (max 500 chars) after the job text.
  - `raw._parser_version` (= 2) is part of `content_hash`, so a parser change re-embeds and re-rates ITC jobs once.
  - `raw._honorarium` is set only when the company offers extra pay. It isn't scored and is shown as "Extra honorarium" in Details.
  - Excel date cells with day and month swapped are corrected. If a date cell can't be corrected, it becomes empty, so the job stays visible.
- `docs/how-it-works.md`:
  - One line under ITC STEM / the decisions table: the honorarium is not an earn signal, and ITC weekly hours stay unknown (0.5).
  - The company overview feeds the embedding text.
  - Mark these as Architect (delegated) decisions.

### Architect -> All (05:40)
T24 final review: Done. (a) The safety net returns None for date cells that can't be swapped. (b) Duration is in the
period on 98/98 rows. (c) "About the company" is on 98/98, capped at 500. (d) The honorarium is raw-only, filtered for "NA/No/0"
(3 rows), and shown in Details. (e) `_parser_version` is in the hashed raw. pytest: 176 passed. **All tasks T1-T24 are
Done; no open tasks.** The only item left is the Scribe's doc note above.

### Scribe -> Architect (05:40)
T24 documented: docs/data-model.md (ITC raw `_parser_version`/`_honorarium`, description order with
"About the company" ≤500, "start, duration" period, weekly_hours null -> 0.5, swapped-date rule) and
docs/how-it-works.md (§2 ITC STEM "Fixing the real list" + ITC_PARSER_VERSION; three Architect-delegated
rows in the decisions table).

### Architect -> All (05:56)
The owner asked for a UI/UX redesign and delegated the visual decisions. The design is in architecture.md,
"Web UI design (redesign, 2026-10-25) [Delegated]". It sets tokens, themes, layout, job cards, states and
accessibility. Tasks: **T25** (Builder: tokens, base layout, theme toggle) → **T26** (Builder:
pages and states) → then in parallel **T27** (Validator: web tests), **T28** (Validator: Playwright
screenshots + checks) and **T29** (Scribe: docs).
Hard rules:
- Server-rendered Jinja, thin over the pipeline; no Node, CDN, webfont or tracker; no new Python deps.
- CSRF check, form field names, `/api/status`, Ollama-only settings: all unchanged.
- The app runs live with `--reload` on :8000 (tmux window `web-app`). Look at it with **GET only**. Never POST
  /search or /login from a script: that scrapes AIMS or opens the login window. Don't start another
  server on 8000.
- No apply links into AIMS.
The palette contrast is pre-checked (numbers in the design). Use the token values exactly, or ask me before
changing one.

### Builder -> Validator, Architect (05:59)
T25 is in Review. `static/style.css` was rewritten in 7 sections: tokens (the full palette table, status
warn/danger/ok, type, spacing, radius, shadow and motion), themes (system default, `data-theme` override, `color-scheme`), base, layout, components,
pages, and a11y modes (reduced motion, forced colours). The new `static/app.js` has the theme toggle (System -> Light -> Dark,
`localStorage.theme`) and polling of `[data-status-url]` at 1.5 s with m:ss; it stays hidden without JS. `base.html`
has the pre-paint theme line (the only inline script), a skip link to `#main`, a header with our own
SVG mark and an inline SVG favicon, nav with `aria-current`, the 10-icon sprite (8 from the spec + chevron + theme) and the
AIMS-login-needed warn banner. The new `templates/_macros.html` has icon, mmss, banner and level_meter. Routes, CSRF and field
names are unchanged; no new deps, CDN or font. pytest 176 passed. Starting T26 (pages) now.

### Builder -> Validator, Architect (06:03)
T26 is in Review. Field names, routes, CSRF and /api/status are unchanged; pytest 176 passed.
- **Search:** two columns from 64rem (the form, plus an aside with "How ranking works" and the AIMS session card), one column below.
  Earn/learn are segmented 1-5 radios (`fieldset`/`legend`, ids `earn-1`..`learn-5`, amber/green when checked, 44 px targets).
- **Results:** title + "Change search", query/department/earn/learn chips, a "Scrape notes" disclosure. Cards have the rank, h2,
  a source pill, the score with "/ 1.11", meta icons (dept, h/week, closes), and a "Closes in N days" warn badge when <= 7 days
  (`today` is passed from app.py). The stacked bar is `aria-hidden` with 2 px gaps, plus a `<dl>` legend per Match/Earn/Learn
  group (`TERM_GROUPS` in app.py; zero terms are shown muted). There is a 5-pip level meter (`role="img"`,
  aria-label "Level N of 5") or "Not rated yet". The top 3 get an accent border, and each card has an aria-label summary.
  Details show "Extra honorarium" and "Open the listing", or "Apply in AIMS with job no. X" (no AIMS links).
- **States:** progress (`data-status-url`, indeterminate bar, striped under reduced motion, m:ss, the ~35 min / ~7 s per job hint);
  "No search yet" (SVG + "Start a search") and "No open jobs" empty states; error = danger banner `role="alert"` + "Try again";
  login done / "Saved." = ok banner `role="status"`.
- **Settings:** an "Ollama connected · N models" / "Ollama not reachable" pill, model chips; the URL input is now `type="url"`.
- **Checked:** with a preview on port 8765 (fake runner, real ITC jobs; port 8000 not touched) at 1100 px light and 360 px dark:
  no horizontal scroll (on phones the brand text is visually hidden so the header stays one row), polling updates m:ss, and
  the toggle cycles and saves the theme. The live app on 8000: /, /results, /settings, style.css and app.js all 200.
- **Validator (T27) ideas:** 10 radios with the saved value `checked`; `data-status-url` on the progress page; the badge at 3 days
  and not at 8; `role="alert"` on the error; the pips' aria-label; no `aims.cityu` apply links.

### Scribe -> Architect (06:05)
T29 is in Review: README.md "Running the web app" + "Reading a job card", docs/how-it-works.md §10 "The interface".

### Scribe -> Builder (06:05)
Small a11y suggestion from writing T29 (not blocking): on the search page the `.scale-ends` text
("1 · don't care" / "5 · very important") is `aria-hidden`, so screen-reader users hear only "1".."5"
inside "Earn money" with no meaning for the ends. Consider giving the fieldset
`aria-describedby` pointing to a visually-hidden "1 = don't care, 5 = very important" (or drop aria-hidden).

### Architect -> Builder (06:05)
Review of T25/T26. **T25 is Done.** The tokens match the palette table exactly, and the theme toggle, the pre-paint line, the
skip link, the focus ring, and the reduced-motion and forced-colors blocks are as designed.

How I checked: live app (pages only, nothing submitted) at 1280 and 375 px, both themes, plus a fake-data preview on 127.0.0.1:8766
with real ITC jobs. No horizontal scroll and no console errors; no request left 127.0.0.1; Tab order skip link → brand → nav →
toggle → fields, each with a 3 px outline. The cards read well: the bar to scale, the Match/Earn/Learn legend, the
pips, "Closes in 3 days", "Not rated yet".

**T26: two small fixes, then Done:**
1. **Radio meaning for screen readers (a11y).** The scale ends are `aria-hidden`, so a screen reader hears "Earn money, 3,
   radio" without knowing what 1 and 5 mean. Add `<span class="visually-hidden">(1 = don't care, 5 = very important)</span>`
   inside each `<legend>`. Keep the visible scale-ends as they are.
2. **Truncated overview.** The 500-char cap cuts mid-word ("…identifies the 20 best-fit programm"). In
   `itc_stem_scraper.py`, cut at the last space within 500 and append "…". This changes the description, so bump
   `ITC_PARSER_VERSION` to 3 so the cache refreshes.
Optional, no re-review needed: replace the two inline `style="margin:0"` with a class.
Fine as is: `type="url"` on the Ollama URL, and the 4 px top-3 border shifting the rank by 3 px.

### Architect -> Validator (06:05)
FYI for T27/T28:
- The `.data/screenshots/*.png` files are from 05:58, *before* T26 landed (06:03). Please re-shoot after the Builder's two
  T26 fixes. My GET-only and preview scripts are in the scratchpad if useful (`shoot_ui.py`, `preview_ui.py`).
- The favicon in `base.html` is a `data:` SVG containing `xmlns='http://www.w3.org/2000/svg'`. Your "no external resource"
  test should ignore `data:` URIs and the SVG namespace, and check only `src=`/`href=` on script/link and CSS `url(`.
- Add to T27: each earn/learn `<legend>` has the visually hidden "(1 = don't care, 5 = very important)".

### Builder -> Scribe (06:05)
Done, thanks. The scale ends are no longer `aria-hidden`: each Earn/Learn fieldset has
`aria-describedby="earn-scale"` / `"learn-scale"`, pointing to a visible `<p class="scale-ends">` with the text
"1 = don't care, 5 = very important". It looks the same as before; only the wording changed from "1 · don't care". pytest 207 passed.
If the T29 docs quote the old wording, please update it.

### Validator -> Architect (06:06)
T27 and T28 are in Review. pytest: **207 passed** (the 19 old web tests are unchanged and green, plus 31 new ones in tests/test_web_redesign.py).
T28 (`uv run python tests/visual/shoot.py`; live app on 8000 was sent GET requests only; no POSTs, no AIMS):
| check | result |
|---|---|
| pages/states | search, results, settings (live) + empty, progress, error, no-jobs, results (long titles, unrated, closing in 3 days, ITC, SH), login-needed (results + search), settings-saved, login-done |
| viewports x themes | 1280x800 and 375x812, light and dark: 48 runs + 2 reduced-motion = 50 |
| horizontal overflow | 0 px everywhere (incl. a 57-char unbreakable title at 375) |
| keyboard | Tab reaches every visible control (6-14 per page); 0 without a visible focus ring |
| contrast (tokens + rendered body) | min 6.42 light, 7.23 dark (text/muted/primary on bg and surface, all >= 4.5) |
| reduced motion | 0 running animations on progress and results |
| network | 0 requests leave 127.0.0.1; 0 console errors |
| theme override | system light + localStorage theme=dark -> html[data-theme=dark], bg rgb(18,17,23), color-scheme dark |
| touch targets (375, live /, /settings) | radios, buttons, nav, toggle, checkbox label all >= 44x44 |
Screenshots: `.data/screenshots/<page>-<state>-<width>-<theme>.png`, e.g. results-375-light.png, results-1280-dark.png, search-live-375-light.png, progress-375-dark.png, error-1280-light.png, loginneeded-375-dark.png; machine report in `.data/screenshots/report.json`. I looked at the key shots: cards, pills, badge, pips, legend groups, empty/progress/error states all render as specified in both themes.
No blocking UI bugs. Two small nits for the Builder (optional, below).

### Validator -> Builder (06:06)
Redesign checks out: 207 tests pass, and the visual check found 0 overflow, 0 focus-ring gaps, 0 console errors and 0 external requests. Two optional nits:
1. On phones the brand link is only the 28x44 px mark (the wordmark is hidden), under the 44x44 target. A min-width: 44px on `.brand` would fix it. See `.data/screenshots/search-live-375-light.png`.
2. The skip link is 41 px tall when focused; a min-height of 44px would fix it.

### Builder -> Architect, Validator, Scribe (06:06)
T26 is back in Review with your 06:05 fixes.
1. Each Earn/Learn `<legend>` now has `<span class="visually-hidden">(1 = don't care, 5 = very important)</span>`, and the
   visible scale ends are back to "1 · don't care / 5 · very important" with `aria-hidden`. Note: our messages crossed again.
   At 06:05 I had done the Scribe's version (`aria-describedby` -> the visible ends). I removed it so the scale isn't announced twice.
   Scribe: the visible wording is "1 · don't care" again.
2. A new `_shorten(text, limit)` in `itc_stem_scraper.py` cuts at the last space within the limit, trims trailing punctuation and adds
   "…" (55 of 98 real overviews are cut, max 499 chars, e.g. "…identifies the 20 best-fit…"). `ITC_PARSER_VERSION = 3`.
Optional: both inline `style="margin:0"` are now a `.flush` utility class.
**Validator:** pytest 206 passed, 1 failed. `test_t24e_parser_version_in_content_hash` asserts `ITC_PARSER_VERSION == 2`;
it is 3 by the Architect's decision. Please drop the `== 2` (or make it 3); the bump-changes-hash part still passes.
A `_shorten` test would be welcome: a word boundary, "…" only when cut, and a single long word.

### Architect -> Builder (06:09)
**T26 is Done** (06:05 fixes verified). The new task is **T30, a UI polish round**. The owner (via the Manager) asked for more personality.
The spec is in architecture.md, "Web UI polish round (T30-T32) [Delegated]". Please follow it closely. The main points:
- **Match score.** The 0-100 number is relative to `max_possible_score(prefs)`. Put that function in the **core**
  (`ranking.py`), not the template. Example: earn = 1, learn = 5 gives max 0.83, so a perfect job reads 100, not 75.
- **Rename.** The similarity *label* becomes "relevance", so "match" means only the overall score.
- **Card details.** Zero terms are gone from cards, and the legend appears once. The raw score stays visible inside "Why this score".
- **Progressive enhancement.** Example chips are JS-only (`hidden` until app.js runs) and `type="button"`. Without JS the page
  is the T26 page plus the hero.
- **Animation and touch targets.** The stagger uses an inline `--i`. Cards must be fully visible when animations are off
  (reduced motion, or the fade never runs). Include the two 44 px nits (brand, skip link).
- **Colours.** New colours only as `color-mix()` of existing tokens; I've checked that the badge and hero keep AA contrast.
Live app on :8000 with `--reload`: GET only. Preview fake data on another port (e.g. 8766).

### Architect -> Validator (06:09)
**T28 is Done** (baseline accepted, thanks). **T27** stays in Review until `test_t24e_parser_version_in_content_hash` passes
with version 3 and the `_shorten` tests are in. Then I'll mark it Done. Your next task is **T31** (tests + visual re-run),
after the Builder's T30.

### Architect -> Scribe (06:09)
**T32** (after T30): document the polish round. Hold T29 as it is; I'll review T29 and T32 together once T30 lands, so the
docs describe the final UI once.

### Builder -> Architect, Validator, Scribe (06:16)
T30 is in Review (items 1-8). No new raw colours (only `color-mix()` of tokens); routes, CSRF, field names and /api/status are unchanged.
1. `max_possible_score(prefs)` is in `core/ranking.py` (earn 1 / learn 5 -> 0.83; 5/5 -> 1.11; 1/1 -> 0.55). `app.py` passes it as
   `max_score` to results (MAX_SCORE removed). The ring macro `match_ring(pct, size)` is in `_macros.html` (`pathLength=100`, SVG `aria-hidden`,
   the text "95 match"). The label is "relevance"; the groups are Relevance/Earn/Learn (the search-page aside now says "Relevance:" too).
2. A 6 px bar on the 0-100 scale with segment titles ("Relevance: 51 points") plus a visually hidden "Relevance 51, dept 12, ... points".
   "Why this score" lists the non-zero terms and "Raw score 0.79 of 0.83 possible for your priorities". The legend appears once, under the h1.
3. Featured #1: "Top match" badge, tint + border, 72 px ring, a 180-char teaser. The teaser is the first paragraph that isn't
   ITC's "Company:"/"About the company:" one. #2-3 standard; #4+ quiet. The left accent is gone.
4. Hero + SVG arcs/dot grid (hidden below 40rem). Example chips: `hidden` until app.js, `type="button"`, 44 px, `aria-label="Add example: …"`,
   append ", text" unless already present (case-insensitive), focus stays on the chip. Segments are "1 Not important" … "5 Top priority".
   The hidden hint and the scale-ends are gone. On phones the word is 11 px so it doesn't break mid-word (Chromium here doesn't hyphenate).
5. Hover lift inside `(hover: hover)`. fade-up uses `animation-fill-mode: backwards`, so a card is at full opacity whenever the animation
   isn't running, and the hover transform isn't overridden. `--i` is capped at 10. 3 `aria-hidden` skeleton cards with a shimmer (static under reduced motion, checked).
6. Sticky `.summary-bar` at `top: calc(var(--header-h) + 1px)` (the header's 1 px border), only when there are results. 92% surface + blur
   with an `@supports` fallback. Phones: the query (no icon) + "Earn n · Learn n" + "Change".
7. `badge-soon` now has `data-urgency="danger|warn|neutral"` (0-3 / 4-7 / 8-14 days; constants at the top of the block) and a clock icon.
   Titles use break-word/hyphens/balance.
8. `.brand` min-width 44 px; skip link 44 px inline-flex.
**Checked in Chromium** on a preview at :8767 (fake runner, real ITC jobs) at 1280 light and 375 dark: no horizontal scroll, the sticky bar at
57 px, chips append and keep focus, cards at opacity 1 after the stagger, reduced motion = static skeletons and striped progress.
On :8000 (GET only), /, /results, /settings and the assets are all 200.
**Validator: 205 passed, 2 failed, both tests to update:**
- `test_no_badge_after_7_days` asserts no "Closes in" at 8 days. Per item 7, 8-14 days now shows a neutral chip
  (`data-urgency="neutral"`); only > 14 days has no chip.
- `test_t24e_parser_version_in_content_hash` still asserts `== 2` (it is 3 since the 06:05 fix, from my earlier message).
The card's aria-label is unchanged ("Rank N: title, score X of Y", now Y = max_possible_score), so your regex still matches.
**Scribe:** the user-visible changes are the 0-100 match score and the rename from "match" to "relevance" for the term.

### Scribe -> Architect (06:18)
T32 is in Review (README.md "Running the web app" / "Reading a job card"; docs/how-it-works.md §7 "From raw
score to a 0-100 match", §10 polish round + a11y updates). T29 wording updated in the same pass ("relevance";
scale-ends gone; priority words). Ready for your joint T29+T32 review.

### Validator -> Builder (06:22)
T31 results. The 2 pytest failures were my outdated assertions (ITC_PARSER_VERSION 3; the neutral 8-14 day chip), so your T30 has no regression there. Fixed; pytest **236 passed**.
**Bug (a11y, WCAG 2.4.11 Focus Not Obscured):** on /results, moving up with Shift+Tab scrolls focused controls *under* the sticky site header + summary bar, fully hidden (focused "Details" at y 0-44 px, bars cover y 0-119). It affects card "Details" / "Why this score", "Scrape notes" and the banner's "Log in to AIMS", at both 1280 and 375. Evidence: `.data/screenshots/results-focus-hidden-1280-light.png` and `-375-light.png` (card 2's "Details" is focused but invisible). Repro: `uv run python tests/visual/shoot.py`, "focus hidden" column / report.json `focus_obscured`. Suggested fix: `html { scroll-padding-top: calc(var(--header-h) + <summary-bar height> + var(--space-2)); }` (or a `--summary-h` token), and/or `scroll-margin-top` on focusable elements inside `.card`.
**Nit (readability):** the segment word "Not important" hyphenates over 3 lines ("Not / impor- / tant") in the narrow 1-5 segments, at 1280 (the two groups sit side by side) and at 375, where the word is 11 px. See `.data/screenshots/search-live-1280-light.png` / `search-live-375-light.png`. Options: shorter words ("None", "Low", "Medium", "High", "Top"), or stack the groups and keep `hyphens: manual` on `.seg-word`. Your call / the Architect's.

### Validator -> Architect (06:22)
T31 is in Review. pytest 236 passed (16 new in tests/test_web_polish.py: max_possible_score (1,1)=0.55, (5,5)=1.11, (3,1)=0.69; match = round(100*score/max) e.g. 0.60/0.90 -> 67 and 0.90 -> 100; zero terms not listed; screen-reader points text; legend once; "Top match" + teaser only on #1; stagger capped at 10; example chips type="button" + hidden without JS; segment names "1 Not important" ... "5 Top priority"; 3 aria-hidden skeletons. Plus urgency 2/3 danger, 5/7 warn, 8/10/14 neutral, 15/20 none, and 5 `_shorten` tests).
Visual re-run (`tests/visual/shoot.py`, live app GET only): 50 runs (12 states x 1280/375 x light/dark + 2 reduced-motion). 0 overflow (also after scrolling with the sticky bar), Tab reaches all controls with a visible ring, contrast min 6.42 light / 7.23 dark, **hero text min 5.48** (light) / 6.64 (dark), sticky bar visible after scroll, brand 44x44 at 375 (117x44 desktop), skip link 150x44, titles wrap (hyphens auto, break-word, no overflow), 0 running animations under reduced motion, 0 console errors, 0 external requests, theme override OK. Long-title shots: `.data/screenshots/results-longtitle-375-light.png` / `-dark.png`.
**One failing check: focus hidden under the sticky bars** (bug sent to the Builder with screenshots). One readability nit (the "Not important" hyphenation) also sent to the Builder.

### Builder -> Validator, Architect (06:24)
Fixed both 06:22 items (CSS only). Please re-check with `tests/visual/shoot.py`.
- **Focus not obscured:** the new token `--summary-h: 62px` (also the bar's `min-height`). `html { scroll-padding-top: header + 1px + space-2 }`
  everywhere, and `html:has(.summary-bar.is-sticky) { … + var(--summary-h) … }` on results. Re-running `shoot.py`, `focus_obscured`
  is now 0 for `results` (1280/375, light/dark, reduced motion). It still lists "Log in to AIMS" for `loginneeded`, but I believe that is a
  false positive in FOCUS_OBSCURED_JS: that button is in the banner at the very top, so after `scrollIntoView` the page is at scroll 0 and
  the summary bar sits in its normal place *below* the button, not over it. `cover` is the bar's bottom edge either way, so `r.top < cover`
  is true. Suggest counting a bar only when it overlaps the element, e.g.
  `bars.filter(b => { const br = b.getBoundingClientRect(); return br.top < r.bottom && br.bottom > r.top; })`.
- **"Not important":** `.seg-word { hyphens: manual }`, and the Earn/Learn groups are stacked at all widths, so each segment is wide
  enough on desktop. At 375 the word is 11 px and fits (49 px in a 53 px segment). No mid-word breaks.
pytest 236 passed.

### Architect -> Builder, Validator, Scribe (06:27)
Review of T30-T32. The polish works and reads well. I checked it on a fake-data preview (:8768, real ITC jobs) at 1280/375/360/320 px
and in both themes:
- the featured #1, the rings, the urgency chip, "Why this score", the legend once, the sticky bar at 62 px and one row on phones,
  the hero, the example chips and the priority words;
- cards reach opacity 1 by 1.6 s (shots taken earlier only catch the fade);
- **the focus fix works.** With an overlap-based check, no focused control is hidden under the bars at 1280/375/360.
  I agree with the Builder that the `loginneeded` "Log in to AIMS" hit was a false positive.
**T27 is Done.** Three follow-ups before T30-T32 are Done:
1. **Builder, core (my spec error, found by the Scribe's docs):** `max_possible_score` counts both the Student Helper and
   the learning-scheme bonus, but a job is one *or* the other (by source). So 100 is unreachable when earn and learn are both > 1.
   Use `0.45 + 0.10 + 0.20·e + 0.20·l + max(0.08·e, 0.08·l)` (e, l = (p−1)/4). This updates the docstring, and the
   architecture.md amendment is in. Results: (1,1) = 0.55, (3,1) = 0.69, (1,5) = 0.83 unchanged; (5,5) = 1.03; (5,3) = 0.93.
2. **Builder, CSS (WCAG 1.4.10 Reflow, 320 CSS px):** at 320 px the page scrolls 7 px sideways. The header row (mark + 3 nav
   links + toggle) is 327 px wide. Below about 22.5rem, tighten `.site-nav a` padding-inline to `--space-1` and the header gap, keeping the
   44 px heights. Nit: make `.skip-link` `position: fixed`, so it's visible when focused from a scrolled page (Shift+Tab back to the top).
3. **Validator (T31):** update the (5,5) test to 1.03 and add (5,3) = 0.93. Add a 320 px width to the overflow check. Use the
   overlap test in FOCUS_OBSCURED_JS (`br.top < r.bottom && br.bottom > r.top`). Then confirm the re-run.
4. **Scribe (T32):**
   - In how-it-works §7, the worked example becomes 0.55 + 0.20 + 0.10 + max(0.08, 0.04) = 0.93, so the 0.74 job shows
     **80 match**. Replace the "no job can have all of these, 100 is a ceiling" paragraph with: the formula takes the better
     of the two bonuses because a job is a Student Helper job or an internship, so a perfect job reads 100.
   - Check that the "1.11" mentions are still right (global maximum, unchanged).
   - README "relative to your own sliders" → "priorities".
   T29 is reviewed together with T32; both close then.
Nit, not needed: hours `#b45309` and student helper `#c2410c` are close in the bar. The exact numbers are in "Why this score",
so it's fine as is.

### Validator -> Architect, Builder (06:27)
Re-check of my 06:22 findings after the Builder's fix: both are fixed. T31 is in Review and **Done-ready**.
- Focus not obscured: `scroll-padding-top` (header + `--summary-h`) works. A real Shift+Tab through /results and the login-needed page at 1280 and 375 leaves 0 controls under the sticky header/summary bar. I also corrected my automated check (it now flags only a real on-screen overlap with a bar; before, the AIMS banner above the bar at scroll 0 counted as a false positive).
- "Not important": `hyphens: manual` keeps it on 1 line at 1280 and as "Not / important" at 375 (no hyphen, no overflow). See `.data/screenshots/search-live-375-dark.png`.
- Full `tests/visual/shoot.py` run: 50/50 runs clean (overflow, Tab reach + focus ring, focus hidden, contrast min 6.42 / hero 5.48, sticky bar, 44 px brand/skip, title wrap, reduced motion 0 animations, 0 console errors, 0 external requests, theme override). Screenshots refreshed in `.data/screenshots/` (52 PNGs + report.json). pytest: 236 passed.

### Scribe -> Architect (06:28)
06:27 item 4 done; T29/T32 in Review. how-it-works §7: corrected formula with max(0.08·e, 0.08·l), "a job is SH or
internship, so a perfect job reads 100", worked example 0.93 -> 80 match, raw-score example "0.74 of 0.93". 1.11 mentions
now say "sum of all weights" (no job can reach it); README examples (0.71 of 0.83 at earn 1/learn 5) are unchanged
by the fix. README "own sliders" -> "own priorities". Note: `core/ranking.py` still has the old formula until the
Builder lands item 1; the docs describe the amended one.


### Builder -> Validator, Architect (06:29)
Did items 1 and 2 of the Architect's 06:27 review.
1. `max_possible_score` = 0.45 + 0.10 + 0.20·e + 0.20·l + max(0.08·e, 0.08·l), with the docstring explaining why only one source bonus is reachable.
   Check: (1,1)=0.55, (3,1)=0.69, (1,5)=0.83, (5,5)=1.03, (5,3)=0.93.
2. Below 22.5rem: header gap `--space-1`, nav gap 0, `.site-nav a` padding-inline `--space-1`; all header targets stay 44 px tall.
   Checked on the live app at 320 px (GET only): scrollWidth == clientWidth on /, /results and /settings. The live app has no results, so
   the sticky bar at 320 is for your 320 px overflow check. Skip link: `position: fixed`. Focused after scrolling 500 px, it is at top 8 px, 44 px tall.
**Validator:** pytest 232 passed, 4 failed, all in `tests/test_web_polish.py` and all from the old formula, as item 3 expects:
`test_max_possible_score[5-5-1.11]` (now 1.03), `[2-5-0.9]` (now 0.88 = .45+.10+.05+.20+max(.02,.08)),
`test_match_number_is_score_over_max_possible` (ring-num 67 was computed with the old max) and `test_points_text_for_screen_readers` (same cause).
Please update those, add (5,3)=0.93 and a 320 px width to the overflow check, then re-run.

### Builder -> Validator (06:29)
Update: your test changes landed while I was writing. pytest is now **237 passed** with the new formula, so ignore the "4 failed" list above.

### Validator -> Architect, Builder (06:30)
Item 3 is done, and the re-run with the Builder's fixes is confirmed.
- Tests: max_possible_score (5,5) = 1.03, new (5,3) = 0.93, (2,5) = 0.88; match/points tests updated for the 0.88 max (0.60 -> 68; 0.88 -> 100). pytest **237 passed**.
- shoot.py: 320x640 added to the matrix; FOCUS_OBSCURED_JS already uses the overlap test (since 06:27).
- Full run: **74/74 clean** (1280: 26, 375: 24, 320: 24). Overflow 0 at 320 on every page (WCAG 1.4.10 reflow OK); 0 focus hidden; Tab reach and focus ring, contrast (min 6.42, hero 5.48), 44 px brand/skip link, title fit, sticky bar, reduced motion, 0 console errors, 0 external requests; theme override OK. The skip link is now position: fixed. Screenshots refreshed (incl. *-320-*.png).
Two cosmetic nits at 320 only (no overflow; optional, Builder/Architect decide), see `.data/screenshots/results-320-light.png`:
1. The featured #1 card's 72 px ring leaves the title column about 90 px wide, so normal words split mid-word without a hyphen ("Undergrad|uate", "Benchmark|s"). Suggestion: use the 56 px ring (or move the ring under the title) below 22.5rem.
2. The summary bar's query chip collapses to just "“." at 320 px. Suggestion: hide the query chip below 22.5rem (it's on the search page anyway), or let the chips row wrap.
T31 is Done-ready from my side.

### Architect -> All (06:30)
Final review: **T29, T30, T31 and T32 are Done.** I checked:
- the corrected `max_possible_score` (only the larger source bonus), 237 passed;
- the live app at 320 and 375 px (GET only), 0 sideways scroll on /, /results and /settings;
- the Validator's 74/74 visual runs;
- the docs: the worked example is 0.93 / 80 match, and 1.11 is described as the weight sum.
The Validator's two 320 px nits are **T33** (Builder, CSS only): a 56 px ring on the featured card, and the query chip hidden below 22.5rem.
Thanks all. The redesign + polish round is complete apart from T33.

### Builder -> Validator, Architect (06:31)
T33 is in Review (CSS only, inside the existing `@media (max-width: 22.5rem)` block in style.css):
1. `.job.featured .ring { --ring: 56px !important; }`. `!important` is needed to beat the inline `--ring: 72px` from the macro.
2. `.summary-bar .chip-query { display: none; }`, so the bar shows "Earn n · Learn n" + "Change".
Checked at 320 px on a fake-data preview (:8767, now stopped): no horizontal scroll, featured ring 56 px (title column 113 px), the sticky bar
62 px on one row with no overflow. pytest 237 passed. Validator: please re-run the 320 px part of `shoot.py`.

### Validator -> Architect, Builder (06:32)
T33 re-check (320 px only) is confirmed. Run: shoot.py's 320x640 part (live app GET only + fake states), light and dark: **24/24 runs clean** (overflow 0, Tab reach and focus ring, 0 focus hidden, contrast, hero contrast, 44 px brand/skip, title fit, sticky bar, 0 console errors, 0 external requests). Report: `.data/screenshots/report-320.json`; screenshots `*-320-*.png` refreshed.
1. Featured ring at 320: 56 px (all four cards 56 px); the featured title column grew from ~90 px to 128 px, so "Benchmarks", "Interaction" etc. now fit. Only the 13-letter "Undergraduate" still breaks ("Undergradu|ate"). That's the overflow-wrap fallback for a word wider than the column at that size, and acceptable (no overflow).
2. Query chip hidden at 320: the summary bar shows only "Earn 2 · Learn 5" + "Change".
See `.data/screenshots/results-320-light.png`. T33 is Review/Done-ready from my side.

### Architect -> All (06:33)
**T33 is Done.** All tasks T1-T33 are Done; there are no open tasks. pytest: 237 passed.
