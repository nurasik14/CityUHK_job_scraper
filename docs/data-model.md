# Data model

What a job looks like inside the tool, and what is stored about it on disk. For
why it's shaped this way, see [how-it-works.md](how-it-works.md) §3–4.

- `Job` dataclass: [`core/models.py`](../src/cityu_jobs/core/models.py)
- AIMS → `Job` conversion: [`aims_to_job()`](../src/cityu_jobs/core/aims_scraper.py)
- ITC STEM → `Job` conversion: [`parse_job_list()`](../src/cityu_jobs/core/itc_stem_scraper.py)
- Cache file: [`core/cache.py`](../src/cityu_jobs/core/cache.py) → `.data/cache.json`

## A `Job`

Every source (AIMS and ITC STEM) is converted into the same `Job`. This is the
real AIMS job N26MA004, as produced by `aims_to_job()` from the saved pages and
printed with `job.to_dict()`. Long text is shortened (`…`), and staff names are left
out.

```json
{
  "job_id": "aims:N26MA004",
  "source": "aims_cis",
  "scheme": "Campus Internship Scheme",
  "title": "Apply spherical designs to deep learning - Part I",
  "department_code": "MA",
  "department_name": "Mathematics",
  "job_nature": "Scientific Research",
  "description": "Nested Spherical Designs: Theory, Computation, and Applications\n\nProject Description:\nOne of the most important quadrature rules was studied by Chebyshev in 1874, …\n\nDetail Job Description:\n…",
  "requirements": "Preferred Academic Disciplines: MA",
  "part_time": true,
  "weekly_hours": 16.5,
  "total_hours": 80.0,
  "employment_period": "08-Sep-2026 to 25-Oct-2026",
  "closing_date": "2026-10-25",
  "positions": 1,
  "url": null,
  "raw": { "…": "see below" },
  "content_hash": "cf85af3c4798…ffb88"
}
```

### Fields

| Field | Type | Meaning | Used by |
|---|---|---|---|
| `job_id` | str | Stable unique ID: `aims:<Job No.>` or `itc:<college>:<suffix>` | cache key |
| `source` | str | `aims_cis`, `aims_sh_summer`, `aims_sh_regular` or `itc_stem` | ranking: Student Helper / learning-scheme terms |
| `scheme` | str | Readable scheme name | display, AI prompt |
| `title` | str | Short title (AIMS "Brief Job Description") | display, AI prompt, embedding |
| `department_code` | str or null | e.g. `MA`, `CS` | ranking: department boost |
| `department_name` | str or null | e.g. `Mathematics` | display, AI prompt, embedding |
| `job_nature` | str or null | Category, e.g. `Scientific Research`, `Clerical` | AI prompt, embedding |
| `description` | str | All free text about the work, joined: project title, every text section, learning objectives (minus duplicates) | AI prompt (first 4000 chars), embedding |
| `requirements` | str | `Label: value` lines for disciplines, computer/language/other skills, other requirements | AI prompt, embedding |
| `part_time` | bool or null | `true` = part-time | display |
| `weekly_hours` | float or null | Average hours per week | ranking: hours term (÷ 20, capped at 1) |
| `total_hours` | float or null | Total hours per student | display |
| `employment_period` | str or null | As written in the listing | display |
| `closing_date` | ISO date or null | Last day to apply; jobs past it are not recommended | ranking filter, cache pruning |
| `positions` | int or null | Students required | display |
| `url` | str or null | Public link; always null for AIMS (it needs a login) | display |
| `raw` | dict | Everything the scraper parsed, in the source's own format | content hash; kept so nothing is lost |
| `content_hash` | str | SHA-256 of `raw` with sorted keys | cache invalidation |

`N/A` and blank values in AIMS become `null` or are left out. Hours and counts become
numbers, and dates become ISO strings (`"25-Oct-2026"` → `"2026-10-25"`).
`Job.from_dict()` ignores unknown keys, so an older cache file still loads.

### `raw` for an AIMS job

`raw` is the results-table row plus the parsed detail page, with AIMS's own labels
as keys:

| Key | Contents |
|---|---|
| `summary` | The results-table row: `Job No.`, `Closing Date`, `Brief Job Description`, `Employment Period`, `Part-time/ Full-time`, `No. of Students Required`, `Avg Weekly Work hrs per Student`, `Total Work hrs per Student`, `Recruit. Dept` |
| `scheme` | First heading on the detail page, e.g. `Campus Internship Scheme` |
| `fields` | Label → value pairs from the detail page: `Recruiting Dept/Unit`, `Job no.`, `Contact Person`, `Supervisor`, `Research Project no.`, `Project Title`, `Job Nature`, `Graduate Attributes`, hours, disciplines, skills, `Closing Date`, … |
| `sections` | Free-text sections by heading, e.g. `Project Description`, `Detail Job Description` |
| `learning_objectives` | List of strings |
| `training_plan` | List of `{Type, …}` rows (Pre-internship / Interim / Post-internship) |
| `evaluation` | List of `{Type, Date}` |
| `professional_development` | `{category: {skill: importance}}`, e.g. `{"Attitudes Toward Work": {"Sense of responsibility and commitment": "Very important"}}` |

Note that `raw.fields` includes staff names (`Contact Person`, `Supervisor`). They
stay in your local cache only. They aren't used for ranking and don't belong in docs
or test expectations.

### `raw` for an ITC STEM job

`raw` is one spreadsheet row: `{header text: cell text}`, with every value converted
to a string. Dates become ISO strings, and whole numbers lose their `.0`. Empty cells
are left out, a column without a header is named `column <n>`, and `_sheet` records
which worksheet the row came from. Everything stays JSON-safe, so the content hash and
the cache file always work.

`raw` also has two extra keys added by the parser:

- `_parser_version` (currently 2) is part of what `content_hash` is computed from.
  When the ITC parser changes how it builds a `Job`, the version is bumped. That
  changes every ITC job's hash, so the next run re-rates and re-embeds them once.
  Without it, cached AI results would describe the old text.
- `_honorarium` is set only when the company offers extra pay on top of the scheme
  allowance (answers like "NA", "No" or "0" are dropped). It isn't scored. It's shown
  as "Extra honorarium" in the results' Details.

Other ITC STEM specifics:

- `job_id` is `itc:<college>:<suffix>`, e.g. `itc:cc:…`. The suffix is the reference
  number if the list has a reference column. Otherwise it's
  `sha256(title|company|department)[:12]`, which stays the same as long as those three
  don't change.
- If a row has no department column, `department_code` / `department_name` become
  `CC` / `College of Computing`.
- `url` is the spreadsheet's URL, or the college page for a manually saved file.
  `total_hours` is always null.
- `description` is built in three parts. It starts with `Company: …`, then every
  description-like column, then `About the company: …` (the company overview, cut to
  500 characters). The overview comes last and is kept short, so company boilerplate
  doesn't drown out the job itself. Because the description goes into the embedding
  text and the AI prompt, the overview helps matching (e.g. "AI-powered education").
- `requirements` joins every requirement-like column (as `Header: value` lines when
  there are several).
- `employment_period` is `"start, duration"`, e.g. `"05/10/2026, 59 days"`, or
  whichever of the two is present.
- `weekly_hours` is null, because the list has no hours column. Ranking counts
  unknown hours as 0.5.
- **Swapped dates.** Some Excel date *cells* have day and month swapped: a deadline
  of 2026-06-11 for a job posted on 2026-09-14 was really 6 November. If a date cell
  is earlier than the row's posting date, it's swapped back, but only when that's
  possible (day ≤ 12) and gives a date on or after the posting date. Otherwise it
  becomes null, so the job shows as "closing date unknown" rather than being wrongly
  hidden as closed. Text dates are left as they are, and `raw` keeps the original
  values.

## A cache entry

`.data/cache.json` holds every job seen, keyed by `job_id`, plus when each source was
last fully scraped. Below is the shape for N26MA004 after one scrape, one rating and
one embedding. The timestamps, the reason text and the vector values are
illustrative, and the vector is cut to 3 of its 768 numbers.

```json
{
  "schema_version": 1,
  "sources": {
    "aims_cis": {"last_scraped_at": "2026-10-25T09:12:03+08:00"}
  },
  "jobs": {
    "aims:N26MA004": {
      "first_seen": "2026-10-25T09:10:41+08:00",
      "last_seen": "2026-10-25T09:10:41+08:00",
      "job": { "…": "the Job above, as to_dict()" },
      "content_hash": "cf85af3c4798…ffb88",
      "summary_hash": "3d2e9bd6f5a8…3db1",
      "active": true,
      "level": {
        "value": 4,
        "reason": "Research work applying numerical methods to deep learning.",
        "model": "llama3.2:3b",
        "content_hash": "cf85af3c4798…ffb88"
      },
      "embedding": {
        "model": "nomic-embed-text",
        "content_hash": "cf85af3c4798…ffb88",
        "vector": [0.0132, -0.0481, 0.0275]
      }
    }
  }
}
```

| Key | Meaning |
|---|---|
| `schema_version` | Layout version. A file with a different version is ignored and rebuilt by re-scraping, which is cheaper than migrating it |
| `sources.<source>.last_scraped_at` | Time of the last *complete* scrape of that source. Searches re-scrape only if this is over 6 h old (`STALE_AFTER_HOURS`) |
| `first_seen` / `last_seen` | When the job was first and most recently found |
| `content_hash` | Hash of the job's current `raw` |
| `summary_hash` | Hash of the AIMS results-table row. If it's unchanged, the scraper doesn't reopen the detail page |
| `active` | `false` once a complete scrape of the source no longer lists the job |
| `level` | AI rating: `value` 1–5, or `null` = unrated (retried next run), plus `reason`, the `model` used (for display only), and the `content_hash` it was made from |
| `embedding` | The vector, the `model` that made it, and the `content_hash` it was made from |

**When stored AI results are reused:**

- `level` is reused only if its `content_hash` equals the job's current one. A
  different `model` doesn't matter: switching the LLM doesn't re-rate jobs (see
  architecture.md "AI level rating").
- `embedding` is reused only if its `content_hash` **and** `model` both match.

If they don't match, the stored result is ignored and recomputed on the next run.

**When entries are deleted:** 30 days after the closing date, or 30 days after
`last_seen` if there is no closing date (`CACHE_RETENTION_DAYS`).

**Size:** each embedding is 768 numbers, so a few hundred jobs make a file of a few
MB. The file is written atomically, so a crash never leaves it half-written.
