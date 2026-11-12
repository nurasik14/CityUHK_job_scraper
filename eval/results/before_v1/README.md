# Baseline v1 (before the retrieval improvements)

Frozen copy of the first evaluation: 42 queries, 1,679 pooled pairs labelled by Claude Fable
(`claude-fable-5-2`), three systems (keyword BM25, vector nomic-embed-text on the full listing
text, hybrid RRF k=60). Main result, nDCG@10: keyword 0.715, hybrid 0.544, vector 0.337.

Files: `report.md`, `summary.json`, `per_query.jsonl` (results as generated) and the exact inputs
they were scored on: `queries_v1.jsonl`, `pool_v1.json`, `qrels_v1.jsonl`, `labels_fable_v1.jsonl`.
Rerun: `uv run --group eval python -m eval.run --qrels eval/results/before_v1/qrels_v1.jsonl`
with the v1 queries (see eval/README.md, "Baseline v1").

Diagnosis that motivated v2: vector top-10s were 9.8/10 long big-tech descriptions (19% of
the corpus); location is in only 7% of embedded texts; keyword/vector top-10 overlap 0.9/10;
equal-weight RRF mixed in vector's weaker half (precision@10: keyword 0.75, vector 0.50).
