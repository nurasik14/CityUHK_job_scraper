"""Retrieval evaluation v2: the dev/test split, labelling only what is missing, stored reranker
scores, the v2 retrievers, and that the committed data still matches the code. Offline."""

import json

import pytest

from eval import common, prelabel, split
from eval.retrievers import KeywordRetriever, RerankRetriever, StoredScores, V2Retriever
from eval.systems import Spec, default_system, select, system_specs

from cityu_jobs import config
from cityu_jobs.core.query import CompanyIndex

# --- dev / test split -------------------------------------------------------------------


def _committed_queries():
    return common.read_jsonl(common.QUERIES)


def test_split_is_deterministic_and_stratified():
    queries = [{k: v for k, v in q.items() if k != "split"} for q in _committed_queries()]
    splits = split.assign_splits(queries)
    assert splits == split.assign_splits(list(reversed(queries)))  # independent of file order
    assert sum(1 for s in splits.values() if s == "dev") == split.DEV_SIZE == 40
    assert sum(1 for s in splits.values() if s == "test") == 60
    groups = {(split.group_of(q["category"]), splits[q["qid"]]) for q in queries}
    assert groups == {(g, s) for g in split.GROUPS for s in ("dev", "test")}  # every group on both sides


def test_committed_queries_carry_exactly_that_split_and_v1_lines_are_unchanged():
    queries = _committed_queries()
    assert len(queries) == 100 and [q["qid"] for q in queries] == [f"q{i:02d}" for i in range(1, 101)]
    assert {q["qid"]: q["split"] for q in queries} == split.assign_splits(queries)
    v1 = common.read_jsonl(common.V1_DIR / "queries_v1.jsonl")
    assert [{k: v for k, v in q.items() if k != "split"} for q in queries[:42]] == v1


def test_group_of_maps_categories_to_broad_groups():
    cases = {"company+role+location": "company", "company+date": "company", "role+location+date": "location",
             "location": "location", "role+constraint": "constraint", "role+date": "constraint", "typo": "typo",
             "multilingual": "multilingual", "vague": "vague", "role": "role_skill", "skill+industry": "role_skill"}
    assert {c: split.group_of(c) for c in cases} == cases


def test_small_split_keeps_one_query_on_each_side():
    queries = [{"qid": f"t{i}", "category": "typo"} for i in range(2)] + [{"qid": f"r{i}", "category": "role"} for i in range(8)]
    splits = split.assign_splits(queries, dev_size=4)
    assert sorted(splits[f"t{i}"] for i in range(2)) == ["dev", "test"]
    assert sum(1 for s in splits.values() if s == "dev") == 4


# --- labelling only the missing pairs, in batches -----------------------------------------


QUERIES = [{"qid": "q1", "query": "ml intern", "category": "role"}, {"qid": "q2", "query": "web", "category": "skill"}]
DOCS = {f"d{i}": {"doc_id": f"d{i}", "title": f"T{i}", "company": "Co", "location": "HK", "text": "x"} for i in range(130)}


def _answer_all(system, user, schema):
    n = user.count('"id"')
    return {"judgments": [{"id": f"C{i}", "label": 1, "reason": "ok"} for i in range(1, n + 1)]}, ["claude-test"]


def test_batches_are_even_at_most_fifty_and_keep_the_order():
    ids = [f"d{i}" for i in range(120)]
    parts = prelabel.batches(ids)
    assert [len(p) for p in parts] == [40, 40, 40] and sum(parts, []) == ids
    assert [len(p) for p in prelabel.batches(ids[:51])] == [26, 25]
    assert prelabel.batches(ids[:50]) == [ids[:50]] and prelabel.batches([]) == []
    assert max(len(p) for n in range(1, 130) for p in prelabel.batches(ids[:n] if n <= 120 else ids)) <= prelabel.BATCH_SIZE


def test_only_missing_pairs_are_labelled_and_existing_labels_stay(tmp_path):
    drafts = tmp_path / "labels.jsonl"
    old = {"qid": "q1", "query": "ml intern", "model": "claude:fable", "model_ids": ["m"], "attempts": 1,
           "created_at": "2026-11-07T00:00:00+00:00", "labels": [{"doc_id": "d0", "label": 2, "reason": "old"}]}
    drafts.write_text(json.dumps(old) + "\n")
    pool = {"q1": {"doc_ids": [f"d{i}" for i in range(61)]}, "q2": {"doc_ids": ["d5", "d6"]}}
    prompts = []

    def fake(system, user, schema):
        prompts.append(user)
        return _answer_all(system, user, schema)

    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=fake) == []
    # q1: 60 missing -> two calls of 30, in pool order, without the already judged d0; q2: one call.
    assert sorted(p.count('"id"') for p in prompts) == [2, 30, 30]
    q1_prompts = sorted((p for p in prompts if "ml intern" in p), key=lambda p: p.index('"T'))
    assert all('"title": "T0"' not in p for p in prompts)
    assert '"title": "T1"' in "".join(q1_prompts) and '"title": "T60"' in "".join(q1_prompts)
    labels = prelabel.read_labels(drafts)
    assert labels["q1"]["d0"] == 2 and len(labels["q1"]) == 61 and len(labels["q2"]) == 2
    rows = [json.loads(line) for line in drafts.read_text().splitlines()]
    assert rows[0] == old and [r["qid"] for r in rows] == ["q1", "q1", "q1", "q2"]
    # Resumable: nothing left to do.
    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=lambda *a: pytest.fail("no call expected")) == []


def test_failed_call_is_reported_and_retried_on_the_next_run(tmp_path):
    drafts = tmp_path / "labels.jsonl"
    pool = {"q1": ["d1"], "q2": ["d2"]}

    def flaky(system, user, schema):
        if '"web"' in user:
            raise RuntimeError("claude is busy")
        return _answer_all(system, user, schema)

    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=flaky) == ["q2"]
    assert prelabel.run(QUERIES, pool, DOCS, drafts, complete=_answer_all) == []
    assert set(prelabel.read_labels(drafts)) == {"q1", "q2"}


def test_merge_pools_and_qrels_need_every_pooled_pair(tmp_path):
    merged = prelabel.merge_pools({"q1": {"doc_ids": ["a", "b"]}}, {"q1": {"doc_ids": ["b", "c"]}, "q2": {"doc_ids": ["x"]}}, None)
    assert merged == {"q1": ["a", "b", "c"], "q2": ["x"]}
    labels, qrels = tmp_path / "labels.jsonl", tmp_path / "qrels.jsonl"
    rows = [{"qid": "q1", "labels": [{"doc_id": "a", "label": 2, "reason": "r"}, {"doc_id": "b", "label": 0, "reason": "r"}]},
            {"qid": "q1", "labels": [{"doc_id": "extra", "label": 1, "reason": "r"}, {"doc_id": "a", "label": 0, "reason": "again"}]}]
    labels.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert prelabel.write_qrels(labels, qrels, {"q1": ["a", "b", "c"]}) == 0 and not qrels.exists()  # c is not judged
    assert prelabel.write_qrels(labels, qrels, {"q1": ["a", "b"]}) == 3  # every judged pair is written, first judgment wins
    assert [json.loads(line) for line in qrels.read_text().splitlines()] == [
        {"doc_id": "a", "label": 2, "qid": "q1"}, {"doc_id": "b", "label": 0, "qid": "q1"}, {"doc_id": "extra", "label": 1, "qid": "q1"}]


# --- v2 retrievers and stored reranker scores ---------------------------------------------


CORPUS = [
    {"doc_id": "d1", "title": "Machine Learning Intern", "company": "Acme", "location": "Singapore", "text": "Train models."},
    {"doc_id": "d2", "title": "Machine Learning Intern", "company": "Acme", "location": "London, UK", "text": "Train models."},
    {"doc_id": "d3", "title": "Data Analyst", "company": "TikTok", "location": "Singapore", "text": "SQL dashboards."},
    {"doc_id": "d4", "title": "Frontend Intern", "company": "Beta", "location": "Hong Kong", "text": "React."},
]
FACTS = {d["doc_id"]: (d["company"], d["location"], d["text"]) for d in CORPUS}


class _FixedVector:
    def ranking(self, query_vec, k=None):
        return ["d4", "d3", "d2", "d1"][:k]


def _system(**kwargs):
    return V2Retriever("s", FACTS, CompanyIndex(CORPUS), **kwargs)


def test_v2_retriever_without_boost_is_the_plain_ranking():
    keyword = KeywordRetriever(CORPUS)
    assert _system(keyword=keyword).search("machine learning", None, 10) == keyword.search("machine learning", None, 10)
    assert _system(vector=_FixedVector(), model="m").search("anything", {"m": [1.0]}, 3) == ["d4", "d3", "d2"]
    with pytest.raises(ValueError):
        _system()


def test_v2_retriever_boost_and_weights():
    keyword = KeywordRetriever(CORPUS)
    boosted = _system(keyword=keyword, w_location=1.0, w_company=1.0)
    assert boosted.search("machine learning intern london", None, 2)[0] == "d2"
    assert boosted.search("tiktok machine learning", None, 1) == ["d3"]  # the company boost beats two word matches
    hybrid = _system(keyword=keyword, vector=_FixedVector(), model="m", weights=(1.0, 1.0))
    keyword_heavy = _system(keyword=keyword, vector=_FixedVector(), model="m", weights=(1.0, 0.0))
    assert keyword_heavy.search("machine learning", {"m": None}, 4) == keyword.search("machine learning", None, 4)
    assert set(hybrid.search("machine learning", {"m": None}, 4)) == {"d1", "d2", "d3", "d4"}


def test_stored_rerank_scores_load_and_reorder(tmp_path):
    path = tmp_path / "rerank_scores.jsonl.gz"
    rows = [{"qid": "q1", "doc_id": d, "score": s} for d, s in (("d1", 0.1), ("d2", 0.9), ("d3", 0.5))]
    common.write_jsonl_gz(path, rows + [{"qid": "unknown-query", "doc_id": "d1", "score": 1.0}])
    scores = StoredScores(common.read_jsonl(path), QUERIES)
    assert scores("ml intern", ["d1", "d2", "d3"]) == [0.1, 0.9, 0.5]
    with pytest.raises(KeyError, match="rerank_scores.py"):
        scores("ml intern", ["d1", "d4"])

    class _First:
        def search(self, query_text, query_vecs, k):
            return ["d1", "d2", "d3", "d4"][:k]

    reranked = RerankRetriever("r", _First(), scores, depth=3)
    assert reranked.search("ml intern", None, 10) == ["d2", "d3", "d1", "d4"]  # top 3 re-ordered, the rest as it was
    assert RerankRetriever("r", _First(), scores, depth=2).search("ml intern", None, 2) == ["d2", "d1"]
    assert reranked.candidates("ml intern", None) == ["d1", "d2", "d3"]


def test_chunk_retriever_scores_a_listing_by_its_best_piece():
    np = pytest.importorskip("numpy")
    from eval.retrievers import ChunkRetriever

    first = np.array([[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]], dtype=np.float32)
    further = np.array([[0.0, 1.0], [0.8, 0.6]], dtype=np.float32)  # second pieces of listings a and a
    retriever = ChunkRetriever(["a", "b", "c"], first, further, [0, 0])
    assert retriever.scores([0.0, 1.0]).tolist() == pytest.approx([1.0, 1.0, 0.8])  # a's best piece matches fully
    assert retriever.search("", [0.0, 1.0], 3) == ["a", "b", "c"]                    # ties by doc id
    assert ChunkRetriever(["a", "b", "c"], first, further[:0], []).search("", [0.0, 1.0], 1) == ["b"]
    with pytest.raises(ValueError):
        ChunkRetriever(["a", "b", "c"], first, further, [0])


# --- the list of systems and the committed data -------------------------------------------


def test_system_list_uses_the_app_settings():
    specs = {s.name: s for s in system_specs()}
    assert [s.name for s in select("v1")] == ["keyword", "vector", "hybrid"] and all(s.v1 for s in select("v1"))
    app = specs["app_hybrid"]
    assert app.weights == tuple(config.HYBRID_WEIGHTS) and app.keyword and app.needs_vectors
    assert (app.w_location, app.w_company, app.strip) == (config.BOOST_LOCATION, config.BOOST_COMPANY, config.QUERY_STRIP_NAMES)
    assert ("bge" in app.vectors) == ("bge" in config.DEFAULT_EMBED_MODEL) and app.vectors.endswith("chunks") == config.EMBED_CHUNKS
    assert specs["app_hybrid_rerank"].rerank == config.RERANK_TOP_N and specs["app_hybrid_rerank"].first_stage == app
    assert default_system() in specs
    with pytest.raises(SystemExit):
        select("telepathy")
    assert [s.name for s in select("keyword, app_hybrid")] == ["keyword", "app_hybrid"]


def test_committed_vectors_were_made_from_the_texts_the_code_produces_now():
    """If core/text.py changes, the stored document vectors no longer belong to its texts: re-run eval/embed.py."""
    from eval import embed

    meta = common.read_json(common.META)
    docs = common.load_corpus()
    compact = embed.texts_sha256(embed.compact_texts(docs))
    further, owners = embed.extra_chunks(embed.chunk_lists(docs))
    for name in ("doc_vectors_compact.npy", "doc_vectors_bge_m3.npy"):
        assert meta["embeddings"][name]["texts_sha256"] == compact, name
    for name in ("chunk_vectors.npy", "chunk_vectors_bge_m3.npy"):
        assert meta["embeddings"][name]["texts_sha256"] == embed.texts_sha256(further), name
        assert meta["embeddings"][name]["rows"] == len(owners), name


def test_corpus_keeps_the_v1_text_and_has_the_v2_fields():
    docs = common.load_corpus()
    assert len(docs) == 5510 and [d["doc_id"] for d in docs] == sorted(d["doc_id"] for d in docs)
    for d in docs[:200] + docs[-200:]:
        parts = (d["title"], d["company"], d["job_type"], d["department"], d["description"], d["requirements"])
        assert "\n".join(p for p in parts if p) == d["text"]


def test_qrels_cover_the_whole_pool():
    pool = common.read_json(common.POOL)["queries"]
    qrels = {(r["qid"], r["doc_id"]) for r in common.read_jsonl(common.QRELS)}
    assert len(pool) == 100
    missing = [(qid, d) for qid, entry in pool.items() for d in entry["doc_ids"] if (qid, d) not in qrels]
    assert not missing, f"{len(missing)} pooled pairs have no label, e.g. {missing[:3]}"
