"""Pooling determinism, prelabel validation (without starting `claude`) and the qrels export."""

import json

import pytest

from eval import prelabel
from eval.pool import blind_order, build_pool


class _Fixed:
    def __init__(self, ranking):
        self.ranking = ranking

    def search(self, query_text, query_vec, k):
        return self.ranking[:k]


QUERIES = [{"qid": "q1", "query": "ml intern", "category": "role"}, {"qid": "q2", "query": "web", "category": "skill"}]


def _systems():
    return {"keyword": _Fixed(["a", "b", "c"]), "vector": _Fixed(["c", "d", "e"]), "hybrid": _Fixed(["c", "a", "d"])}


def test_pool_is_union_of_top_depth_and_deterministic():
    one = build_pool(QUERIES, _systems(), [None, None], depth=2)
    two = build_pool(QUERIES, dict(reversed(list(_systems().items()))), [None, None], depth=2)
    assert one == two
    assert set(one["q1"]["doc_ids"]) == {"a", "b", "c", "d"}
    assert one["q1"]["systems"]["c"] == {"hybrid": 1, "vector": 1}  # keyword had c at rank 3 > depth
    assert one["q1"]["systems"]["a"] == {"keyword": 1, "hybrid": 2}


def test_blind_order_hides_rank():
    order = blind_order("q1", ["a", "b", "c", "d"])
    assert sorted(order) == ["a", "b", "c", "d"]
    assert order == blind_order("q1", ["d", "c", "b", "a"])  # independent of input order


DOCS = {d: {"doc_id": d, "title": f"T {d}", "company": "Co", "location": "HK", "url": "https://x/" + d, "text": "x" * 900}
        for d in "abc"}


def test_prompt_has_short_ids_and_excerpts():
    prompt = prelabel.build_prompt(QUERIES[0], ["a", "b"], DOCS)
    assert '"id": "C1"' in prompt and '"id": "C2"' in prompt
    assert "x" * prelabel.EXCERPT_CHARS in prompt and "x" * (prelabel.EXCERPT_CHARS + 1) not in prompt


def _answer(*labels):
    return {"judgments": [{"id": f"C{i}", "label": label, "reason": "ok"} for i, label in enumerate(labels, start=1)]}


def test_validate_maps_ids_back_and_rejects_bad_answers():
    assert prelabel.validate(_answer(2, 0), ["a", "b"]) == [
        {"doc_id": "a", "label": 2, "reason": "ok"},
        {"doc_id": "b", "label": 0, "reason": "ok"},
    ]
    for bad in (_answer(2), _answer(2, 3), _answer(2, True), {"judgments": "no"}, {},
                {"judgments": [{"id": "C1", "label": 1, "reason": "r"}] * 2}):
        with pytest.raises(prelabel.InvalidDraft):
            prelabel.validate(bad, ["a", "b"])


def test_label_query_retries_once_then_gives_up():
    calls = []

    def flaky(system, user, schema):
        calls.append(1)
        return (_answer(1) if len(calls) == 1 else _answer(1, 2)), ["claude-test"]

    row = prelabel.label_query(QUERIES[0], ["a", "b"], DOCS, complete=flaky)
    assert row["attempts"] == 2 and [x["label"] for x in row["labels"]] == [1, 2]

    with pytest.raises(RuntimeError):
        prelabel.label_query(QUERIES[0], ["a", "b"], DOCS, complete=lambda *a: (_answer(1), []))


def test_run_is_resumable(tmp_path):
    drafts = tmp_path / "drafts.jsonl"
    pool = {"q1": {"doc_ids": ["a"]}, "q2": {"doc_ids": ["b", "c"]}}
    seen = []

    def fake(system, user, schema):
        seen.append(user)
        return _answer(*([1] * user.count('"id"'))), []

    assert prelabel.run(QUERIES, pool, DOCS, drafts, limit=1, complete=fake) == []
    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=fake) == []
    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=fake) == []
    assert len(seen) == 2
    assert [json.loads(line)["qid"] for line in drafts.read_text().splitlines()] == ["q1", "q2"]


def test_write_qrels_only_when_every_query_is_labelled(tmp_path):
    labels, qrels = tmp_path / "labels.jsonl", tmp_path / "qrels.jsonl"
    rows = [{"qid": "q1", "labels": [{"doc_id": "b", "label": 0, "reason": "r"}, {"doc_id": "a", "label": 2, "reason": "r"}]}]
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert prelabel.write_qrels(labels, qrels, {"q1", "q2"}) == 0 and not qrels.exists()
    assert prelabel.write_qrels(labels, qrels, {"q1"}) == 2
    assert [json.loads(line) for line in qrels.read_text().splitlines()] == [
        {"doc_id": "a", "label": 2, "qid": "q1"}, {"doc_id": "b", "label": 0, "qid": "q1"}]
