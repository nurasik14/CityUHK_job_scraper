# Retrieval evaluation

This folder measures how well the app's search finds the right internship listings for a
student's query, and how much each improvement of "retrieval v2" changed that.

- **Version 1** (2026-11-07) compared three systems on 42 queries: **keyword** (SQLite FTS5
  with BM25), **vector** (nomic-embed-text on the full listing text) and **hybrid**
  (Reciprocal Rank Fusion of the two). Keyword won clearly; a frozen copy of that
  evaluation is in [`results/before_v1/`](results/before_v1/).
- **Version 2** (2026-11-12) changed the app's search in four ways and evaluates each
  change on **100 queries** with a dev/test split: a compact embedding text (or its
  chunked form), query understanding (place and company boost), the bge-m3 embedding
  model, and an optional cross-encoder reranker.

Everything measured here is the app's own code: `src/cityu_jobs/core/search.py` (keyword
index, fusion, boost), `core/text.py` (what gets embedded), `core/query.py` (query
understanding) and `core/rerank.py`. `retrievers.py` and `systems.py` only wrap it.

Results: [`results/report.md`](results/report.md) (also `summary.json`, `per_query.jsonl`
and `tuning_dev.json`).

## Headline (60 test queries, nDCG@10)

| | system | nDCG@10 | recall@10 | MRR@10 |
|---|---|---|---|---|
| before | v1 keyword (the best of version 1) | 0.641 | 0.214 | 0.881 |
| before | v1 hybrid | 0.465 | 0.171 | 0.838 |
| before | v1 vector (what the app used by default) | 0.303 | 0.105 | 0.726 |
| after | `app_hybrid`: the app's new default | **0.754** | 0.237 | 0.935 |
| after | `app_hybrid_rerank`: with the optional reranker | **0.803** | 0.249 | 0.930 |

- `app_hybrid` − v1 keyword: **+0.114**, 95% CI [+0.059, +0.176]. − v1 hybrid: **+0.290**,
  CI [+0.236, +0.343]. − v1 vector: +0.452, CI [+0.378, +0.520].
- `app_hybrid_rerank` − v1 keyword: +0.162, CI [+0.086, +0.246]; − `app_hybrid`: +0.049.
- The app embeds one compact text per listing (`config.EMBED_CHUNKS = False`). Dev had
  picked pieces; see "Why long listings won with nomic-embed-text" for why that was undone.
- What made the difference: **bge-m3**. With the same compact text, bge-m3 scores 0.642 and
  nomic-embed-text 0.231. Fusing keyword search with bge-m3 gives 0.739; query understanding
  adds +0.015 to that (+0.020 on keyword search alone, +0.030 on vector search alone).
- What did not help: the compact text on its own. With nomic-embed-text it scored *lower*
  than the full text (0.231 vs 0.303, CI of the difference [−0.100, −0.045]). Scoring a
  listing by its best piece was better than one compact text for nomic (+0.056) and, less
  clearly, for bge-m3 (+0.032, CI [−0.004, +0.070]).
- The reranker is **off by default**: on the dev queries, where that was decided, it added
  only +0.004 to hybrid search. On the test queries it added +0.052. Both are in the
  report; the default follows the dev decision and was not changed after seeing test.
- Latency on the author's laptop (i5-12500H, RTX 4050): the hybrid search itself takes
  22 ms (p50); with the bge-m3 query embedding through Ollama 146 ms; with the reranker on
  30 candidates 307 ms. v1 keyword search took 8 ms.

## Why long listings won with nomic-embed-text

Measured on the 5,510 listings and the first 42 queries (one vector per listing, v1);
rerun with `uv run --group eval python eval/length_bias.py`.
Short = under 300 characters (4,447 listings, mostly title + company), long = over 1,000 (1,057).

| | short listings | long listings |
|---|---|---|
| mean cosine to the queries | 0.570 | 0.571 |
| spread of scores within one query (std) | 0.011 | 0.045 |
| best score per query | 0.609 | 0.708 |
| 10th-best score per query | 0.594 | 0.676 |
| cosine between two random listings of the group | 0.963 | 0.792 |

Long listings do not score higher on average. nomic-embed-text gives nearly every short
listing the same vector (two random short listings: cosine 0.963), so within a query their
scores barely differ and the right one cannot stand out; long listings differ from each other,
so a few always stick out. A top 10 takes only the extremes: even the 10th-best long listing
beats the best short one, hence 9.8 of every vector top 10 were long listings.

| embedding | long listings in the vector top 10 | cosine between two short listings |
|---|---|---|
| nomic, full text (v1) | 9.8 | 0.963 |
| nomic, compact text | 9.7 | 0.963 |
| nomic, best of ~500-character pieces | 10.0 | — |
| bge-m3, compact text (the app) | 3.8 | 0.640 |
| bge-m3, best of pieces | 6.2 | — |

The fix was the model: bge-m3 tells short listings apart. Shorter text alone did not help, and
scoring a listing by its best piece brings part of the bias back (a long listing gets ~6
chances to score high). Pieces won on dev for vector search (0.668 vs 0.648) but tied in the
default hybrid mode on test (0.738 vs 0.739) at twice the vectors and search time, so the app
uses the compact text. That last choice was made with the test numbers in view; the report
shows both variants.

## Reproduce the results

```sh
git clone https://github.com/nurasik14/CityUHK_job_scraper.git
cd CityUHK_job_scraper
uv sync --group eval                       # the app's deps + numpy; no browser, no torch
uv run --group eval python -m eval.run     # ~1.5 min; writes eval/results/
uv run --group eval pytest -q tests/eval   # metrics, fusion, split, pooling, labelling tests
```

`eval.run` first checks the sha256 of every file it reads against `data/meta.json` and
stops if one differs. It needs no login, API key, Ollama, torch or GPU: document and query
vectors and the reranker's scores are committed. The quality numbers and bootstrap
intervals come out the same on every run. Latency depends on the machine and its load.

Optional, with the real models:

```sh
ollama pull bge-m3 && ollama pull nomic-embed-text
uv sync --group eval --group rerank        # adds torch (CUDA 12.6 build on Linux) + sentence-transformers, ~6 GB
uv run --group eval --group rerank python -m eval.run --live-embed --live-rerank
```

`--live-embed` times the Ollama query embedding, `--live-rerank` runs and times the real
cross-encoder (and reports how far its scores are from the stored ones). Both only add the
end-to-end latency columns; the metrics always come from the committed vectors and scores.
The timings are saved in `results/live_latency.json` and shown by later runs.

### Version 1 again

The first evaluation can still be reproduced exactly from the current code and data:

```sh
uv run --group eval python -m eval.run --systems v1 \
    --queries eval/results/before_v1/queries_v1.jsonl \
    --qrels eval/results/before_v1/qrels_v1.jsonl --out /tmp/v1
uv run python eval/compare_v1.py /tmp/v1     # "identical to eval/results/before_v1"
```

`compare_v1.py` checks the metrics, the per-category table, the bootstrap intervals and
every per-query row (top 10 and scores). Nothing in `results/before_v1/` is ever rewritten.

## The systems

`systems.py` has the list; `--systems` picks `all` (default), `v1` or names.

| system | what it is |
|---|---|
| `keyword`, `vector`, `hybrid` | version 1, unchanged: BM25 on the full text; nomic-embed-text on the full text; RRF (k = 60, top 100 each, equal weights) |
| `nomic_compact` | nomic-embed-text on the compact text |
| `nomic_chunks` | nomic-embed-text, a listing scores as its best ~500-character piece |
| `bge_compact`, `bge_chunks` | the same two with bge-m3 |
| `keyword_qu`, `bge_chunks_qu` | keyword / vector search plus query understanding |
| `hybrid_bge_compact`, `hybrid_bge_chunks` | keyword fused with bge-m3 (compact / best piece), equal weights, no boost |
| `hybrid_alt_weights` | the same with the vector ranking at half weight (a check on the fusion weights) |
| `keyword_rerank` | v1 keyword, top 30 re-ordered by the cross-encoder |
| `app_keyword`, `app_vector`, `app_hybrid` | the app's three search modes with its settings from `config.py` |
| `app_hybrid_rerank` | `app_hybrid` with the reranker switched on |

### What changed in version 2

1. **Compact embedding text** (`core/text.py`). Version 1 embedded the whole listing. A
   fifth of the listings are long (4,000+ characters, much of it company boilerplate), the
   rest have ~170 characters, and the location was not in the text at all. Now every
   listing is embedded as title, company, location, job type, department and the first
   ~500 characters of its description with boilerplate removed. Boilerplate is found by
   rules, conservatively: fixed patterns (equal-opportunity, accommodation, privacy and
   pay-transparency notices, benefits lists, "About <company>" sections), and any paragraph
   of 80+ characters that one company repeats under three or more different job titles.
   **Chunks** are the alternative: the cleaned description is cut into ~500-character pieces
   with 50 characters of overlap, the header is put on each, and a listing scores as its
   best piece (max cosine). The first piece is the compact text.
2. **Query understanding** (`core/query.py`, `core/places.py`). Places ("HK", "香港",
   "NYC", "bay area", "europe", ...; a region also accepts what is inside it), "remote", and
   company names (learned from the listings, plus a small nickname table) are recognised in
   the query. A listing in that place or from that company gets a bonus on top of its search
   score; it is never a filter. The query text itself is searched unchanged.
3. **bge-m3** embeddings through Ollama instead of nomic-embed-text. bge-m3 needs no
   query/document prefixes (its model card: "no longer requires adding instructions to the
   queries"); nomic keeps its `search_query: ` / `search_document: ` prefixes. All vectors
   are L2-normalised.
4. **Cross-encoder reranker** (`core/rerank.py`): BAAI/bge-reranker-v2-m3 through
   sentence-transformers, fp16 on the GPU, re-orders the top 30 of a first-stage system. It
   reads the query and the listing's compact text.

One formula serves every mode (`search.fuse_and_boost`):

```
relevance(d) = Σ_i weight_i / (60 + rank_i(d))  +  boost(d) / 61
boost(d)     = w_place · (in a named place + says remote, if asked)  +  w_company · (a named company)
```

so a boost of 1 is worth one more first place. Version 2 rankings are fused in full, not
cut at the top 100.

## Queries and the dev / test split

`data/queries.jsonl` holds 100 queries. q01–q42 are the version 1 queries (drafted by
Claude with the corpus in view, approved by the author). q43–q100 were written for version
2 **without looking at the corpus**; some name companies or places the corpus does not
have. Each query has a category and a `split`.

`split.py` makes the split: **40 dev, 60 test**. Categories are mapped to seven broad
groups (company, location, role & skill, vague, typo, multilingual, constraint); each group
gives 40% of its queries to dev (largest remainders fill up to 40; at least one query of
every group on each side); inside a group the queries with the smallest sha256(qid) are
dev. The split therefore depends only on qids and categories.

**Everything that was tuned was tuned on dev only** with `tune.py`: which embedding model,
what text is embedded (length, boilerplate rules, compact or chunks), the boost weights,
whether to drop the recognised words from the query, the fusion weights, the default search
mode, and which first stage feeds the reranker and how deep. Rule against chasing noise on
40 queries: the simpler or existing setting stays unless an alternative is better by at
least 0.005 nDCG@10. The tables and choices are in `results/tuning_dev.json` and in the
report. **Test was scored once, at the end**, and is the headline.

## What is measured

| metric | definition |
|---|---|
| recall@10 | relevant docs in the top 10 ÷ all judged relevant docs for the query. Below 1 whenever a query has more than 10 relevant docs, which is most of them here. |
| MRR@10 | 1 ÷ rank of the first relevant doc in the top 10, or 0 if none. |
| nDCG@10 | graded: gain 2^label − 1 (a "2" is worth 3, a "1" is worth 1), discount log2(rank + 1). The ideal ranking is built from all judged labels of the query. |
| search latency | time of the `search()` call only: query vector precomputed, reranker scores looked up. Warm-up on 3 queries, then each query 5 times per system; p50/p95. |
| end-to-end latency | the same plus the real Ollama query embedding and/or the real cross-encoder on the GPU (`--live-*`). |

- Relevant means label ≥ 1.
- Queries with no relevant doc at all are left out of the averages and counted separately.
- Metrics are averaged over queries (macro average).
- Differences between two systems come with a **paired bootstrap** 95% confidence interval
  (10,000 resamples of the queries, seed 20261107). If the interval contains 0, this test
  set can't tell the two systems apart.

## Data

### Corpus

`data/corpus.jsonl.gz` holds **5,510 public internship listings** that the app scraped on
**2026-11-06** from company job boards (Greenhouse, Lever, Ashby, Workday, Amazon,
Microsoft, TikTok, the SimplifyJobs list, and others): 4,045 `intern_tech` and 1,465
`intern_bigtech`. Only listings still active at that scrape are included. CityU's own jobs
(AIMS, ITC STEM) sit behind a student login and are **not** in this corpus;
`export_corpus.py` asserts that only public ids are written.

Each line holds `doc_id, source, company, title, location, url, text` and, since version 2,
`job_type, department, description, requirements`. `text` is the full listing text
(`cityu_jobs.core.models.full_text`): what keyword search indexes, what version 1 embedded
and what the judge was shown. The four new fields are what the compact text is built from.
They were added with `export_corpus.py --add-fields`, which kept doc_ids, order and every
`text` byte-identical: the fields are read back out of `text` (title, company, job type,
department, description joined by newlines; no listing has separate requirements) and were
cross-checked against the app's cache for the 5,490 listings it still held unchanged. A
fresh export was not an option: the cache has moved on since the snapshot and the labels
belong to the snapshot.

### Vectors (all L2-normalised, one row per corpus line unless noted)

| file | model | text |
|---|---|---|
| `doc_vectors.npy` | nomic-embed-text | full text (version 1, taken from the app's cache; unchanged) |
| `doc_vectors_compact.npy` | nomic-embed-text | compact text |
| `chunk_vectors.npy` | nomic-embed-text | pieces 2..n of the 1,014 listings with more than one piece (5,144 rows) |
| `doc_vectors_bge_m3.npy` | bge-m3 | compact text |
| `chunk_vectors_bge_m3.npy` | bge-m3 | pieces 2..n |
| `query_vectors.npy`, `query_vectors_bge_m3.npy` | both | the 100 queries, each embedded on its own like the app does |

A matrix over 20 MB as float32 is stored as float16 and widened on load (the bge-m3 files;
it changes cosines in the 4th decimal). `query_vectors.npy` is append-only: its first 42
rows are byte-identical to version 1. `data/meta.json` records the Ollama model digests,
sizes and sha256 of every file, and a hash of the texts each matrix was made from (a test
fails if `core/text.py` no longer produces those texts).

`data/rerank_scores.jsonl.gz` holds the cross-encoder's score for every (query, candidate)
pair a reranked system needs: the top 30 of its first stage, for all 100 queries.

### Relevance labels

1. **Pooling** (`pool.py`). For each of the 100 queries, the top 20 of **all 17 systems**
   are merged into one candidate set (`data/pool.json`). Candidates are stored in a fixed
   pseudo-random order (sha256 of qid + doc_id), so the judge can't see which system found
   a listing or at what rank. For dev queries, the top 10 of every variant that `tune.py`
   tried was judged too (`data/pool_dev_tuning.json`), so tuning never compared a judged
   result with an unjudged one.
2. **Labels** (`prelabel.py`). The judge is Claude Fable (`claude -p --model fable`; the
   model id recorded with every call is `claude-fable-5-2`), on the author's Claude plan
   through the Claude Code CLI: no tools, no API key, structured JSON output, validated, one
   retry. The version 1 labels were kept; only pairs without a label were judged, in
   batches of at most 50 candidates in the pool's blind order, each shown as title,
   company, location and the first 600 characters of `text`: the same view as in version 1.
   The rubric:
   - 2 = clearly what the student asked for;
   - 1 = partly matches (right role but wrong place or level, or the other way round);
   - 0 = not relevant.

   Each label comes with a one-sentence reason (`data/labels_fable.jsonl`); the same labels
   without reasons are in `data/qrels.jsonl`, which the metrics read and which
   `prelabel.py --qrels-only` regenerates.

## Design choices fixed before any label existed (version 1)

- **Keyword tokenizer**: `porter unicode61 remove_diacritics 2`. Listings and queries are
  mostly English, where inflections are common ("developer"/"development",
  "internships"). The Porter stemmer conflates them, and `remove_diacritics` folds
  "café" to "cafe".
- **Column weights** for BM25: title 3, company 2, location 2, text 1.
- **Query escaping**: the query is split into word characters and each word is quoted, then
  the words are OR-joined. FTS5 operators in user text become plain words and can never
  cause a syntax error. An empty or punctuation-only query returns no results.
- **Chinese/Japanese/Korean**: every CJK run is rewritten into overlapping character
  bigrams ("机器学习" → "机器 器学 学习"), in documents and queries. Traditional and
  simplified characters are not mapped onto each other (實習 ≠ 实习). Only 12 listings in
  the corpus contain Chinese characters at all.
- **Vector search**: exact search, a numpy dot product with all unit document vectors, then
  a stable sort.
- **RRF** constant k = 60 (Cormack et al., 2009). **Ties** are broken by `doc_id`
  everywhere, so rankings are deterministic.

## Limitations

- **Pooling bias.** Only the union of the 17 systems' top 20 was judged. At depth 10 every
  evaluated system's results are all judged, so no system is penalised for unjudged
  results. But relevant listings that no system ranked in its top 20 are unknown, so recall
  is measured against "relevant docs any of these systems found". A new system evaluated
  later against these labels would be at a disadvantage until its results are judged.
- **Sizes.** 60 test queries give usable but wide confidence intervals; per-group numbers
  rest on 3 to 27 queries. The 40 dev queries were used to choose settings, so dev numbers
  flatter version 2. Small dev differences (a few thousandths) decided some settings; they
  may not hold on other queries.
- **The first 42 queries were drafted with the corpus in view**, which may favour wording
  that appears in listings, and so keyword search. The 58 new ones were not.
- **Snapshot.** Listings change daily, so this measures the 2026-11-06 corpus only: public
  tech internships, a fifth with long descriptions. CityU's own jobs are not in it.
- **Latency** is hardware-dependent and measured on one machine (see the report). The app
  itself computes cosines in plain Python, not numpy, so its vector step is slower than the
  `search` column here (about 0.2 s for 10,654 vectors).
- **Not the full production ranking.** The app adds department, hours, AI-level and scheme
  terms on top of the relevance (`core/ranking.py`). This evaluation measures the relevance
  step only.
- **Gazetteer and company rules are hand-written** (`core/places.py`, `core/query.py`).
  They were written with the corpus's location strings in view and cover the places a
  student is likely to type, not the world.
- **float16 storage and cross-machine bit-identity.** The metrics are deterministic for
  the committed inputs. On another CPU/BLAS, float32 dot products can differ in the last
  bits, which could swap two listings whose cosines differ by ~1e-7.

## Rebuilding the data (author only)

These steps need a local Ollama, the Claude Code CLI on a Claude plan, and for the
reranker scores the `rerank` group and a GPU. A stranger doesn't need them; the outputs
are committed.

```sh
uv run --group eval python eval/export_corpus.py --add-fields   # once: v2 fields into the v1 corpus
uv run python eval/split.py --append NEW_QUERIES.jsonl          # add queries, write the split
uv run --group eval python eval/embed_queries.py                # query vectors (appends rows)
uv run --group eval python eval/embed.py                        # document vectors: compact, chunks, bge-m3
uv run --group eval --group rerank python eval/tune.py --write-pool   # dev tuning; lists unjudged dev results
uv run python eval/prelabel.py --pool eval/data/pool_dev_tuning.json  # judge them; repeat the two until 0 unjudged
uv run --group eval --group rerank python eval/tune.py --save   # results/tuning_dev.json; put the choices in config.py
uv run --group eval --group rerank python eval/rerank_scores.py # cross-encoder scores for the reranked systems
uv run --group eval python eval/pool.py                         # judging pool: top 20 of every system
uv run python eval/prelabel.py                                  # judge what has no label yet, then qrels.jsonl
uv run --group eval --group rerank python -m eval.run --live-embed --live-rerank   # results
```

## Files

| file | what |
|---|---|
| `export_corpus.py`, `split.py`, `embed.py`, `embed_queries.py`, `rerank_scores.py`, `pool.py` | build `data/` |
| `tune.py` | choose the version 2 settings on the dev queries |
| `prelabel.py` | relevance labels with Claude |
| `systems.py`, `retrievers.py`, `metrics.py`, `run.py` | the systems, the metrics, the runner |
| `compare_v1.py` | check a `--systems v1` run against `results/before_v1/` |
| `length_bias.py` | the length-bias tables in "Why long listings won with nomic-embed-text" |
| `data/corpus.jsonl.gz`, `*.npy`, `queries.jsonl`, `rerank_scores.jsonl.gz`, `meta.json` | inputs |
| `data/pool.json`, `pool_dev_tuning.json`, `labels_fable.jsonl`, `qrels.jsonl` | pools, labels with reasons, labels the metrics read |
| `results/` | `report.md`, `summary.json`, `per_query.jsonl`, `tuning_dev.json`, `live_latency.json` |
| `results/before_v1/` | frozen copy of the first evaluation (results and exact inputs) |
| `.tuning/` | git-ignored scratch: embedding and reranker caches of the variants that were tried |
