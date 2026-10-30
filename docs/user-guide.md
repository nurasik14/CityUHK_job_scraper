# User guide

Everything the web app does, page by page. For setup see the [README](../README.md); for
the design and the reasons behind it see [architecture.md](../architecture.md) and
[how-it-works.md](how-it-works.md).

- [Search](#search)
- [Results](#results)
- [Reading a job card](#reading-a-job-card)
- [Calendar](#calendar)
- [Job page: how to apply](#job-page-how-to-apply)
- [Profile and difficulty for you](#profile-and-difficulty-for-you)
- [Cover letters](#cover-letters)
- [Your gaps](#your-gaps)
- [ITC STEM job list](#itc-stem-job-list)
- [Logging in to AIMS](#logging-in-to-aims)
- [Settings](#settings)
- [Tech internship sources](#tech-internship-sources)
- [Timing and caching](#timing-and-caching)
- [Command-line scripts](#command-line-scripts)

## Search

- **Department.** Computer Science is the default. It gives jobs from your department a
  boost; nothing is filtered out.
- **What kind of job do you want?** Free text, e.g. "machine learning research, backend
  development". The example chips under the box add a phrase (they need JavaScript).
- **Earn money** and **Learn / build a portfolio**: five levels each, *Not important ·
  Low · Medium · High · Top priority*. On a keyboard, Tab to the group and use the arrow keys.
- **Refresh now** re-fetches every source even if it is fresh. Normally a source is
  re-fetched only when its data is more than 6 hours old.

## Results

While the search runs you see the current stage, the elapsed time and placeholder cards;
it is safe to leave the page and come back. Then the top 30 open jobs appear.

- A summary bar with your search stays at the top while you scroll, with a **Change** link.
- Filter tabs **All · CityU jobs · Big tech · Other tech** keep each job's overall rank,
  so #7 is the same job under every tab.
- **Hide not eligible** (shown once difficulty estimates exist) removes jobs the AI judged
  you can't apply for, e.g. "PhD students only".
- **What am I missing?** opens [Your gaps](#your-gaps) for the current tab.
- **Scrape notes** lists what was fetched, reused from the cache, or skipped.
- The **theme button** in the header cycles System → Light → Dark and is remembered.

## Reading a job card

```
  ◜ 86 ◝   ★ Top match
  ◟match◞  Machine Learning Intern                          [ITC STEM]  Acme AI
           Hong Kong · Starts 1 Dec 2026 · 90 days · Closes 30 Nov 2026
           ●●○○○ Good match for you
           Missing: SQL, cloud deployment
           [Python] [PyTorch] [RAG]
           ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
           Level ●●●●○  "Builds and evaluates ML models for a product team."
           ▸ Why this score  ▸ Details        Show on calendar   How to apply ›
```

- **Match score (0–100), in the ring.** How close the job comes to the *best possible job
  for your priorities*. With "earn" set to Not important, hours don't count, so 100 means
  "fits your description and is advanced". Scores from searches with different priorities
  aren't comparable.
- **Difficulty for you** (dots + verdict): from 1 = well within reach to 5 = long shot,
  with the main blocker or what is missing. See [Profile](#profile-and-difficulty-for-you).
- **Skill tags**: up to four skills the listing asks for, read by the AI.
- **The bar** splits the match score into its parts. *Relevance* (indigo/slate): closeness
  to your description plus the department boost. *Earn* (amber): weekly hours plus a
  Student Helper bonus. *Learn* (green): the AI-rated level plus an internship bonus.
- **Why this score** shows the exact points, e.g. "relevance 46, dept 12, level 18,
  internship 10 = 86", and the raw score.
- **Level** (1–5): the AI's rating of how advanced the work is, with a one-line reason.
  "Not rated yet" counts as level 3.
- **Closing chip**: red for 0–3 days left, amber for 4–7, grey for 8–14. Closed jobs are
  never shown.
- **Saved / Applied** pills show your tracking marks from the job page.

## Calendar

On wide screens the calendar sits beside the results; on phones it is a collapsible panel
above them. Rose dots are application deadlines, green dots are start dates.

**Hover or Tab to a card** and the calendar jumps to that job: deadline (filled rose day),
start (filled green day), end (ringed day), the duration as a band, a strip from today to
the end, and the exact dates ("Apply by 30 Nov 2026 (in 30 days) · Starts 10 Dec 2026 ·
lasts 90 days"). **Show on calendar** pins a job (for touch screens); Escape unpins.

Dates come from the listing (AIMS period, ITC start + duration), from what the AI read in
the description, or, for "Summer 2027"-style postings, from the season. Season dates are
drawn striped and labelled "approx.". Without JavaScript a "Coming up" deadline list is shown.

## Job page: how to apply

Each card's **How to apply** button opens `/job/<id>`:

- **Employer** and location, deadline, start and duration, number of positions.
- **How to apply**, by source:
  - *ITC STEM*: the company's own instructions, an **Email** button that opens your mail
    app with the subject (and, once written, the application email) filled in, the
    company website, and a link to the job's row in the ITC STEM list.
  - *AIMS (Campus Internship / Student Helper)*: the job number to apply with in AIMS and
    the contact person. The app never links to or touches the AIMS apply form.
  - *Tech internships*: the company's posting, where the application form is, plus visa
    sponsorship notes when the SimplifyJobs list has them.
- **All details from the listing**: every field the scraper kept.
- **Track**: mark the job *Saved* or *Applied*.
- **Difficulty for you**, **job level** and **cover letter** (below).

## Profile and difficulty for you

On **Profile** (`/profile`) upload a CV (`.pdf`, `.docx`, `.tex`, `.md` or `.txt`, up to
5 MB), check the extracted text, and add education, skills, projects and experience. Only
the text is stored, never the file. **Your year of study matters most**: many internships
are only for penultimate-year, Master's or PhD students.

After each search, Claude compares your profile with the top 30 results overall and the
top 30 of each tab (about 90 jobs; 1–2 minutes the first time, then cached until the
listing or your profile changes). For each job you get:

- **difficulty** 1–5 and a **verdict**: strong match, good match, stretch, long shot or
  not eligible;
- **blockers** (hard rules such as degree level or graduation date);
- what you **have** and what you're **missing**;
- a one-sentence **tip**.

Other jobs can be estimated from their page with **Estimate it now**. Estimates need
Claude; small local models guess.

## Cover letters

On a job page, optionally add notes ("I can start in December, 3 days a week"), then
**Write my cover letter** (20–40 s). You get:

- a cover letter of about 230–330 words: edit it in place, copy it, or download `.txt`;
- a short email to send with your CV (used by the Email button for ITC jobs);
- drafts for three common application-form questions.

The prompt forbids facts that are not in your profile; where something is unknown it is
left in [square brackets]. Read it before sending. **The app never submits anything**:
AIMS and most company forms don't allow automated applications, and a sent application
can't be taken back.

## Your gaps

**Gaps** (`/gaps`, or **What am I missing?** on the results page) compares what your
target jobs ask for with your CV.

- **Evidence, computed by code** (right column): the skills your top results ask for most,
  how many jobs ask for each, and whether your profile mentions it ("In CV" / "Not in CV",
  a keyword check with a few aliases, e.g. PostgreSQL counts as SQL), plus how many jobs
  are a strong match, stretch, long shot or not eligible.
- **Summary, by Claude** (one request, 30–60 s): up to 7 weaknesses ranked by how many
  jobs they cost you, each labelled *skill to learn*, *needs real experience*, *not shown
  in your CV* or *eligibility*, with why it matters, 1–3 concrete fixes, the effort, and
  example jobs (links). Then: what already works, quick CV wording fixes, what only time
  fixes (year of study), and a plan for the next weeks.

There is one summary per tab (All, CityU, Big tech, Other tech). It is saved, and the page
says when your results or profile have changed since it was made.

## ITC STEM job list

ITC STEM jobs come from the College of Computing's Excel list, which needs a CityU portal
login to download, so you fetch it yourself:

1. Open the [College of Computing ITC STEM page](https://www.cityu.edu.hk/en/cc/student-life-and-learning/internship-schemes/itc-stem-internship-scheme)
   and download the job list (`.xlsx`).
2. Upload it on **Settings → Open / upload the ITC STEM list** (`/itc`), or save it in
   `.data/itc/`. The newest file is used, and the next search re-reads it.

The `/itc` page shows the whole spreadsheet as a filterable table, lets you download any
saved list, and highlights a job's row when you arrive from its job page.

## Logging in to AIMS

Campus Internship and Student Helper jobs need your AIMS login:

1. Click **Log in to AIMS** on the search page or in the "AIMS login needed" banner.
2. A browser window opens at the real CityU login page. Log in normally, including
   two-factor. You have 5 minutes.
3. The window closes and only the session cookies are saved. Search again.

The app never opens the login window on its own. With a missing or expired session it
still ranks the AIMS jobs from the last successful scrape plus everything else, and shows
the banner.

## Settings

- **AI that rates jobs**: Claude with your plan (default; Haiku, Sonnet or Opus) or
  Ollama (free, local). A pill shows whether Claude Code is installed and logged in.
- **Ollama**: URL, the embedding model (always used) and the local LLM used when Ollama
  is selected. Changing the embedding model re-embeds every job on the next search.
- **ITC STEM job list**: open or upload the spreadsheet.
- **Internship regions**: Hong Kong, Mainland China, Singapore & Asia-Pacific and Remote
  are on by default; Europe & UK and North America are opt-in (they usually need a work
  visa). Changes apply at the next internship refresh.

Switching between local LLMs doesn't re-rate jobs; switching to Claude re-rates
locally-rated jobs once.

## Tech internship sources

Fetched when the list is over 6 hours old (about 2–3 minutes), from public, read-only
job-board APIs (the same JSON the companies' careers pages load):

- **Big tech** (19): Amazon, Microsoft, TikTok (own careers APIs); NVIDIA, Adobe, Intel,
  Salesforce (Workday); Stripe, Databricks, Airbnb, Cloudflare, Pinterest, Coinbase, Waymo,
  Anthropic, Palantir, Snowflake, OpenAI, Grab.
- **Other tech** (47): AI, fintech, crypto and quant firms, several hiring in Hong Kong or
  Singapore: Binance, Bybit, OKX, Airwallex, Lalamove, Animoca Brands, AfterShip, Optiver,
  IMC, Jump, Squarepoint, Point72, HRT, Scale AI, Notion and more.
- **[SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships)**: a community
  list of internships at hundreds of companies (mostly North America); its big-tech entries
  (Google, Meta, Apple, …) count as big tech.

Only tech internships in your regions are kept. JobsDB, CTgoodjobs and LinkedIn are not
used: their terms forbid scraping. The company list is
`src/cityu_jobs/core/internships/catalog.py`; adding a company is one line (its Greenhouse,
Lever, Ashby or SmartRecruiters board name). A board that fails is noted under Scrape
notes and its earlier jobs are kept.

## Timing and caching

- **First AIMS scrape: about 35 minutes.** About 17 s per job: the scraper waits 1.5 s
  before every page load and opens one job at a time. Progress is saved as it goes, so a
  stopped run resumes. Tip: run `uv run python scripts/scrape_aims.py` once in a terminal.
- **AI rating:** with Claude, 8 jobs per request and 3 requests in parallel (about 10 s
  per 16 jobs with Sonnet). With Ollama's `llama3.2:3b`, about 7 s per job on a laptop CPU.
- **Difficulty estimates:** about 90 jobs in 1–2 minutes the first time.
- **Later searches take seconds.** Sources are re-fetched only after 6 hours, and only new
  or changed listings are opened, rated and embedded again.

## Command-line scripts

```sh
uv run python scripts/check_auth.py              # log in to AIMS if needed; confirm the session works
uv run python scripts/scrape_aims.py             # scrape all AIMS schemes into .data/cache.json
uv run python scripts/scrape_aims.py cis         # ...or only some: cis, sh_summer, sh_regular
uv run python scripts/scrape_aims.py --no-login  # stop with "AIMS login needed" instead of opening a window
uv run python scripts/scrape_aims_test.py        # scrape ONE Campus Internship job to .data/aims_test_job.json
uv run python scripts/make_demo_data.py DIR      # build a demo data folder (public internships + fictional CityU jobs)
```
