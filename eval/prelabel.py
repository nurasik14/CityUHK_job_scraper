"""Relevance labels with Claude, for the retrieval evaluation.

    uv run python eval/prelabel.py              # every pooled pair without a label, then writes qrels.jsonl
    uv run python eval/prelabel.py --limit 2    # smoke test: the next 2 calls only
    uv run python eval/prelabel.py --pool eval/data/pool_dev_tuning.json   # only that pool
    uv run python eval/prelabel.py --qrels-only # just rebuild qrels.jsonl from the labels file

One `claude -p --model fable` call per batch of candidates (Claude Code CLI on the owner's
Claude plan, never an API key). Through the app's ClaudeCLIProvider: no tools, no MCP
servers, no settings or CLAUDE.md, no saved session, an empty temp dir as cwd,
ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN removed from the env. Answers are forced into a
JSON schema and validated; one retry per call on failure.

What gets labelled: every (query, listing) pair of the pool(s) that has no label yet. A
query that already has labels only gets its *missing* pairs judged; existing labels are
never asked again or changed. A query's missing candidates stay in the pool's blind order
and are cut into batches of at most 50 (BATCH_SIZE), so one call never has to judge more.

Appends one row per call to eval/data/labels_fable.jsonl (labels with reasons and the exact
model id); a pair that is already there is skipped (resumable, and failed calls are simply
retried by running again). When every pooled pair has a label it rewrites
eval/data/qrels.jsonl (qid, doc_id, label), which eval/run.py scores against.
"""

import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import (
    DRAFTS,
    POOL,
    POOL_TUNING,
    QRELS,
    dumps_line,
    load_corpus,
    load_queries,
    read_json,
    read_jsonl,
    write_jsonl,
)

MODEL = "fable"
EXCERPT_CHARS = 600
MAX_PARALLEL = 3
TIMEOUT_S = 600
BATCH_SIZE = 50  # candidates per call, at most

RUBRIC = """\
You are judging search results for a retrieval evaluation. A university student in Hong Kong \
typed a search query into a job recommender for tech internships. For every candidate listing, \
grade how well it matches what the student asked for.

Grades:
2 = clearly what the student asked for.
1 = partly matches: the right role but the wrong place or level (or another constraint the \
query states is not met), or the other way round: the place, level or company fits but the \
role only partly matches.
0 = not relevant.

Rules:
- Judge only from the listing text shown. Do not assume facts that are not there.
- A constraint the query states (place, company, level, date, eligibility) counts as met only \
if the listing supports it.
- Understand vague queries the way a reasonable student would mean them; read typos as the \
intended words; read queries in Chinese as written.
- When torn between two grades, give the lower one.
- The candidates are in random order; their order says nothing about relevance.
- Return exactly one judgment per candidate id, with a one-sentence reason (under 25 words)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "integer", "enum": [0, 1, 2]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "label", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["judgments"],
    "additionalProperties": False,
}


def set_model(alias: str) -> None:
    """Choose the judge model (a Claude Code alias such as sonnet, opus or fable)."""
    global MODEL
    MODEL = alias


class InvalidDraft(ValueError):
    pass


def candidate_view(doc: dict) -> dict:
    return {
        "title": doc["title"],
        "company": doc["company"],
        "location": doc["location"],
        "text": doc["text"][:EXCERPT_CHARS],
    }


def build_prompt(query: dict, doc_ids: list[str], docs_by_id: dict) -> str:
    candidates = [{"id": f"C{i}", **candidate_view(docs_by_id[d])} for i, d in enumerate(doc_ids, start=1)]
    return (
        f"Query: {json.dumps(query['query'], ensure_ascii=False)}\n\n"
        f"Grade each of these {len(candidates)} candidate listings (ids C1 to C{len(candidates)}):\n"
        + json.dumps(candidates, ensure_ascii=False, indent=1)
    )


def validate(answer: dict, doc_ids: list[str]) -> list[dict]:
    """Claude's answer as [{doc_id, label, reason}] in pool order, or InvalidDraft."""
    judgments = answer.get("judgments") if isinstance(answer, dict) else None
    if not isinstance(judgments, list):
        raise InvalidDraft("no judgments list")
    ids = {f"C{i}": d for i, d in enumerate(doc_ids, start=1)}
    by_id = {}
    for j in judgments:
        if not isinstance(j, dict) or j.get("id") not in ids:
            raise InvalidDraft(f"unknown candidate id: {j!r:.80}")
        if j["id"] in by_id:
            raise InvalidDraft(f"duplicate id {j['id']}")
        label, reason = j.get("label"), j.get("reason")
        if type(label) is not int or label not in (0, 1, 2):
            raise InvalidDraft(f"bad label for {j['id']}: {label!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidDraft(f"missing reason for {j['id']}")
        by_id[j["id"]] = j
    missing = [cid for cid in ids if cid not in by_id]
    if missing:
        raise InvalidDraft(f"{len(missing)} candidates not judged, e.g. {missing[:3]}")
    return [{"doc_id": ids[cid], "label": by_id[cid]["label"], "reason": by_id[cid]["reason"].strip()} for cid in ids]


def claude_complete(system: str, user: str, schema: dict) -> tuple[dict, list[str]]:
    """One Claude CLI call via the app's provider. Returns (answer, exact model ids used)."""
    from cityu_jobs.core.ai.providers import ClaudeCLIProvider

    seen = {}

    def run(*args, **kwargs):
        done = subprocess.run(*args, **kwargs)
        seen["stdout"] = done.stdout
        return done

    provider = ClaudeCLIProvider(model=MODEL, timeout_s=TIMEOUT_S, run=run)
    answer = provider.complete_json(system, user, schema=schema)
    try:
        model_ids = sorted(json.loads(seen.get("stdout") or "{}").get("modelUsage", {}))
    except (json.JSONDecodeError, AttributeError):
        model_ids = []
    return answer, model_ids


def label_query(query: dict, doc_ids: list[str], docs_by_id: dict, complete=claude_complete) -> dict:
    """Draft labels for one query: one call, validated, one retry. Raises after two failures."""
    prompt = build_prompt(query, doc_ids, docs_by_id)
    errors = []
    for attempt in (1, 2):
        try:
            answer, model_ids = complete(RUBRIC, prompt, SCHEMA)
            labels = validate(answer, doc_ids)
        except Exception as e:  # provider errors and invalid answers both get the one retry
            errors.append(f"attempt {attempt}: {e}")
            continue
        return {
            "qid": query["qid"],
            "query": query["query"],
            "model": f"claude:{MODEL}",
            "model_ids": model_ids,
            "attempts": attempt,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "labels": labels,
        }
    raise RuntimeError(f"{query['qid']}: no valid answer ({'; '.join(errors)})")


def read_labels(labels_path: Path) -> dict[str, dict[str, int]]:
    """{qid: {doc_id: label}} from the labels file. If a pair was judged twice, the first judgment counts."""
    labels: dict[str, dict[str, int]] = {}
    for row in read_jsonl(labels_path):
        for x in row["labels"]:
            labels.setdefault(row["qid"], {}).setdefault(x["doc_id"], x["label"])
    return labels


def merge_pools(*pools: dict | None) -> dict[str, list[str]]:
    """{qid: doc_ids to judge} from one or more pool files' "queries" sections, each kept in its blind order."""
    merged: dict[str, list[str]] = {}
    for pool in pools:
        for qid, entry in (pool or {}).items():
            have = merged.setdefault(qid, [])
            seen = set(have)
            have += [d for d in entry["doc_ids"] if d not in seen]
    return merged


def batches(doc_ids: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """`doc_ids` in order, cut into the fewest batches of at most `size`, as even as possible."""
    if not doc_ids:
        return []
    n = -(-len(doc_ids) // size)  # ceil
    base, extra = divmod(len(doc_ids), n)
    out, start = [], 0
    for i in range(n):
        end = start + base + (1 if i < extra else 0)
        out.append(doc_ids[start:end])
        start = end
    return out


def missing_batches(queries: list[dict], pool: dict[str, list[str]], labels: dict[str, dict[str, int]],
                    size: int = BATCH_SIZE) -> list[tuple[dict, list[str]]]:
    """(query, doc_ids) for every call still to make: each query's unlabelled candidates, in pool order."""
    todo = []
    for q in queries:
        have = labels.get(q["qid"], {})
        missing = [d for d in pool.get(q["qid"], []) if d not in have]
        todo += [(q, batch) for batch in batches(missing, size)]
    return todo


def run(queries, pool, docs_by_id, drafts_path: Path, limit=None, parallel=MAX_PARALLEL, complete=claude_complete,
        batch_size: int = BATCH_SIZE) -> list[str]:
    """Label every pooled pair missing from drafts_path (at most `limit` calls). Returns the qids with a failed call.

    `pool` is {qid: {"doc_ids": [...]}} (a pool file's "queries") or {qid: [doc_ids]}.
    """
    pool = {qid: (entry["doc_ids"] if isinstance(entry, dict) else list(entry)) for qid, entry in pool.items()}
    labels = read_labels(drafts_path)
    todo = missing_batches(queries, pool, labels, batch_size)
    if limit is not None:
        todo = todo[:limit]
    print(f"{sum(len(v) for v in labels.values())} pairs already labelled, "
          f"{sum(len(b) for _, b in todo)} to do in {len(todo)} calls", flush=True)
    lock, failed = threading.Lock(), []
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, MAX_PARALLEL))) as executor:
        futures = {executor.submit(label_query, q, batch, docs_by_id, complete): q for q, batch in todo}
        for future in as_completed(futures):
            qid = futures[future]["qid"]
            try:
                row = future.result()
            except Exception as e:
                failed.append(qid)
                print(f"FAILED {e}", file=sys.stderr, flush=True)
                continue
            with lock, open(drafts_path, "a", encoding="utf-8") as f:
                f.write(dumps_line(row) + "\n")
            counts = [sum(1 for x in row["labels"] if x["label"] == g) for g in (0, 1, 2)]
            print(f"{qid}: {len(row['labels'])} candidates, labels 0/1/2 = {counts[0]}/{counts[1]}/{counts[2]}", flush=True)
    # Rewrite in qid order so the file doesn't depend on which call finished first
    # (a stable sort: a query's batches stay in the order they were written).
    rows = sorted(read_jsonl(drafts_path), key=lambda r: r["qid"])
    write_jsonl(drafts_path, rows)
    return sorted(set(failed))


def write_qrels(labels_path: Path, qrels_path: Path, pool) -> int:
    """qrels.jsonl from the labels file, once everything pooled has a label. Returns rows written (0 if incomplete).

    `pool` is {qid: doc_ids}: every one of those pairs must be labelled. (A plain set of qids
    is also accepted and only requires each query to have labels.) Every judged pair is
    written, including pairs judged for an earlier pool.
    """
    labels = read_labels(labels_path)
    if isinstance(pool, dict):
        if any(d not in labels.get(qid, {}) for qid, doc_ids in pool.items() for d in doc_ids):
            return 0
    elif set(labels) != set(pool):
        return 0
    qrels = sorted(
        ({"qid": qid, "doc_id": d, "label": label} for qid, docs in labels.items() for d, label in docs.items()),
        key=lambda r: (r["qid"], r["doc_id"]),
    )
    write_jsonl(qrels_path, qrels)
    return len(qrels)


def main() -> None:
    parser = argparse.ArgumentParser(description="Label the pooled candidates with Claude (resumable).")
    parser.add_argument("--limit", type=int, help="make at most this many calls (smoke test)")
    parser.add_argument("--parallel", type=int, default=MAX_PARALLEL, help=f"calls at once (max {MAX_PARALLEL})")
    parser.add_argument("--model", default=MODEL, help="Claude model alias for the judge (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=DRAFTS, help="labels file to append to (default: %(default)s)")
    parser.add_argument("--pool", type=Path, action="append",
                        help="pool file(s) to label (default: pool.json and, if present, pool_dev_tuning.json)")
    parser.add_argument("--split", choices=("dev", "test"), help="only queries of this split")
    parser.add_argument("--qrels-only", action="store_true", help="make no calls; rebuild qrels.jsonl from the labels file")
    args = parser.parse_args()
    set_model(args.model)
    paths = args.pool or [p for p in (POOL, POOL_TUNING) if p.exists()]
    pools = [read_json(p) for p in paths]
    if not pools or any(p is None for p in pools):
        sys.exit("pool file missing: run eval/pool.py first")
    pool = merge_pools(*(p["queries"] for p in pools))
    queries = [q for q in load_queries() if args.split in (None, q.get("split"))]
    failed = []
    if not args.qrels_only:
        docs_by_id = {d["doc_id"]: d for d in load_corpus()}
        start = datetime.now(timezone.utc)
        failed = run(queries, pool, docs_by_id, args.out, args.limit, args.parallel)
        print(f"labelling took {(datetime.now(timezone.utc) - start).total_seconds():.0f} s")
    if not failed and args.out == DRAFTS and not args.pool and not args.split:
        n = write_qrels(args.out, QRELS, pool)
        print(f"wrote {QRELS} ({n} judgments)" if n else "qrels.jsonl not written: some pooled pairs have no label yet")
    if failed:
        sys.exit(f"failed: {', '.join(failed)} (run again to retry them)")


if __name__ == "__main__":
    main()
