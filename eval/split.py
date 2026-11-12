"""The dev / test split of the queries, and adding new queries to queries.jsonl.

    uv run python eval/split.py                          # (re)write the `split` field
    uv run python eval/split.py --append NEW.jsonl       # first append queries from NEW.jsonl

40 queries are "dev" and the rest "test". Everything that is tuned (text length, boost and
fusion weights, what the reranker reads) is tuned on dev only; test is scored once at the end.

The split is deterministic and stratified:

1. Every query's category is mapped to one of seven broad groups (GROUPS).
2. Each group gives 40% of its queries to dev: the group's share is rounded down, and the
   seats still missing to reach 40 go to the groups with the largest remainders (ties by
   group name). Every group keeps at least one query on each side.
3. Inside a group, queries are ordered by sha256(qid); the first ones are dev.

So the split depends only on the qids and categories in the file, not on any result.
Existing lines keep their fields and order; only `split` is added.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import QUERIES, atomic_write_bytes, read_jsonl

DEV_SIZE = 40
GROUPS = ("company", "location", "role_skill", "vague", "typo", "multilingual", "constraint")


def group_of(category: str) -> str:
    """Broad group of a query category such as "role+location" or "company+date"."""
    parts = set(category.split("+"))
    for single in ("typo", "multilingual", "vague"):
        if single in parts:
            return single
    if "company" in parts:
        return "company"
    if "location" in parts:
        return "location"
    if parts & {"constraint", "date"}:
        return "constraint"
    return "role_skill"  # role, skill, industry and their combinations


def assign_splits(queries: list[dict], dev_size: int = DEV_SIZE) -> dict[str, str]:
    """{qid: "dev" | "test"}."""
    members: dict[str, list[str]] = {g: [] for g in GROUPS}
    for q in queries:
        members[group_of(q["category"])].append(q["qid"])
    total = len(queries)
    share = {g: len(ids) * dev_size / total for g, ids in members.items() if ids}
    seats = {g: int(share[g]) for g in share}
    for g in sorted(share, key=lambda g: (-(share[g] - int(share[g])), g))[: dev_size - sum(seats.values())]:
        seats[g] += 1
    for g, ids in members.items():
        if len(ids) >= 2:
            seats[g] = min(max(seats[g], 1), len(ids) - 1)
    splits = {}
    for g, ids in members.items():
        ordered = sorted(ids, key=lambda qid: (hashlib.sha256(qid.encode()).hexdigest(), qid))
        for i, qid in enumerate(ordered):
            splits[qid] = "dev" if i < seats.get(g, 0) else "test"
    return splits


def write_queries(path: Path, queries: list[dict]) -> None:
    """One query per line, keys in their given order (v1 lines stay as they were, plus `split`)."""
    text = "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in queries)
    atomic_write_bytes(path, text.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Add queries and write the dev/test split into queries.jsonl.")
    parser.add_argument("--append", type=Path, help="JSONL of new queries (qid, query, category) to add first")
    parser.add_argument("--queries", type=Path, default=QUERIES)
    args = parser.parse_args()

    queries = [{k: v for k, v in q.items() if k != "split"} for q in read_jsonl(args.queries)]
    if args.append:
        have = {q["qid"] for q in queries}
        new = [q for q in read_jsonl(args.append) if q["qid"] not in have]
        for q in new:
            if set(q) != {"qid", "query", "category"}:
                sys.exit(f"bad query line: {q}")
        queries += new
        print(f"appended {len(new)} queries")
    if len({q["qid"] for q in queries}) != len(queries):
        sys.exit("duplicate qid")
    splits = assign_splits(queries)
    write_queries(args.queries, [{**q, "split": splits[q["qid"]]} for q in queries])
    counts = Counter((group_of(q["category"]), splits[q["qid"]]) for q in queries)
    for g in GROUPS:
        print(f"{g:13} dev {counts[(g, 'dev')]:2}  test {counts[(g, 'test')]:2}")
    print(f"{'total':13} dev {sum(1 for s in splits.values() if s == 'dev'):2}  test {sum(1 for s in splits.values() if s == 'test'):2}")


if __name__ == "__main__":
    main()
