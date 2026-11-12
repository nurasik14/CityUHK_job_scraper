"""Check that the version 1 results can still be reproduced from the current code and data.

    uv run --group eval python -m eval.run --systems v1 \\
        --queries eval/results/before_v1/queries_v1.jsonl \\
        --qrels eval/results/before_v1/qrels_v1.jsonl --out /tmp/v1
    uv run python eval/compare_v1.py /tmp/v1

Compares the new run with the frozen eval/results/before_v1/: the three systems' metrics,
the per-category table, the bootstrap intervals and every per-query row (top 10 and
metrics) must be identical. Latency, the machine and the corpus file's sha256 (new fields
were added to the corpus in v2) are not compared. Exits with an error on any difference.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.common import V1_DIR, read_json, read_jsonl

V1_SYSTEMS = ("keyword", "vector", "hybrid")


def differences(new_dir: Path, old_dir: Path = V1_DIR) -> list[str]:
    old, new = read_json(old_dir / "summary.json"), read_json(new_dir / "summary.json")
    if old is None or new is None:
        return [f"summary.json missing in {old_dir if old is None else new_dir}"]
    found = []
    for key in ("metrics", "per_category", "bootstrap_ndcg@10"):
        if old[key] != new[key]:
            found.append(f"summary.json: {key} differs")
    for key in ("queries", "judged", "included", "no_relevant", "not_judged", "judged_pairs", "relevant_pairs", "unjudged_in_top10"):
        if old["counts"][key] != new["counts"][key]:
            found.append(f"summary.json: counts.{key} differs ({old['counts'][key]} vs {new['counts'][key]})")
    if old["data"]["qrels"]["sha256"] != new["data"]["qrels"]["sha256"]:
        found.append("the qrels file is not the v1 one")
    old_rows = {(r["qid"], r["system"]): r for r in read_jsonl(old_dir / "per_query.jsonl")}
    new_rows = {(r["qid"], r["system"]): r for r in read_jsonl(new_dir / "per_query.jsonl")}
    if set(old_rows) != set(new_rows):
        found.append("per_query.jsonl: different (query, system) rows")
    for key in sorted(set(old_rows) & set(new_rows)):
        if old_rows[key] != new_rows[key]:
            found.append(f"per_query.jsonl: {key} differs")
    return found


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    found = differences(Path(sys.argv[1]))
    if found:
        sys.exit("NOT identical to before_v1:\n  " + "\n  ".join(found[:20]))
    n = len(read_jsonl(Path(sys.argv[1]) / "per_query.jsonl"))
    print(f"identical to eval/results/before_v1: metrics, per-category table, bootstrap intervals and all {n} per-query rows")


if __name__ == "__main__":
    main()
