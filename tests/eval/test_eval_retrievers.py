"""Keyword escaping, CJK handling, RRF fusion, and (with numpy) the vector retriever."""

import pytest

from eval.retrievers import HybridRetriever, KeywordRetriever, cjk_bigrams, fts_query, rrf

DOCS = [
    {"doc_id": "d1", "title": "Machine Learning Intern", "company": "Acme", "location": "Singapore", "text": "Train models."},
    {"doc_id": "d2", "title": "Frontend Developer Intern", "company": "Beta", "location": "Hong Kong", "text": "React and TypeScript."},
    {"doc_id": "d3", "title": "Data Analyst", "company": "Gamma", "location": "Remote", "text": "SQL dashboards; machine data."},
    {"doc_id": "d4", "title": "香港 實習生", "company": "Delta", "location": "香港", "text": "机器学习 实习 项目"},
    {"doc_id": "d5", "title": "Frontend Developer Intern", "company": "Beta", "location": "Hong Kong", "text": "React and TypeScript."},
]


def test_fts_query_quotes_and_or_joins_words():
    assert fts_query("machine learning") == '"machine" OR "learning"'


@pytest.mark.parametrize(
    "text",
    ['say "hi"', "NOT AND OR NEAR", "c++ (dev) -x *y col:umn ^z", 'a"b', "'; DROP TABLE docs; --"],
)
def test_fts_query_is_always_valid_fts5(text):
    query = fts_query(text)
    retriever = KeywordRetriever(DOCS)
    retriever.db.execute("SELECT count(*) FROM docs WHERE docs MATCH ?", (query,)).fetchone()  # no syntax error
    assert retriever.search(text, None, 5) is not None


def test_operators_are_plain_words():
    assert fts_query("NOT AND") == '"NOT" OR "AND"'


def test_empty_or_punctuation_only_query_returns_nothing():
    retriever = KeywordRetriever(DOCS)
    for text in ("", "   ", "!!! ??? ---", "+-*"):
        assert fts_query(text) == ""
        assert retriever.search(text, None, 10) == []


def test_duplicate_words_are_dropped_case_insensitively():
    assert fts_query("Intern intern INTERN") == '"Intern"'


def test_cjk_runs_become_bigrams():
    assert cjk_bigrams("机器学习") == " 机器 器学 学习 "
    assert cjk_bigrams("香港") == " 香港 "
    assert cjk_bigrams("中").strip() == "中"
    assert cjk_bigrams("ML 实习 intern").split() == ["ML", "实习", "intern"]
    assert fts_query("机器学习 实习") == '"机器" OR "器学" OR "学习" OR "实习"'


def test_cjk_query_finds_cjk_doc():
    retriever = KeywordRetriever(DOCS)
    assert retriever.search("机器学习", None, 5) == ["d4"]
    assert retriever.search("香港 實習", None, 5)[0] == "d4"


def test_keyword_ranking_prefers_title_and_breaks_ties_by_doc_id():
    retriever = KeywordRetriever(DOCS)
    assert retriever.search("machine", None, 5)[0] == "d1"  # title hit beats body hit (d3)
    # d2 and d5 are identical: equal BM25, so doc_id order.
    hits = retriever.search("frontend react", None, 5)
    assert hits[:2] == ["d2", "d5"]
    assert retriever.search("porter stems developers", None, 5)[:2] == ["d2", "d5"]


def test_rrf_by_hand():
    # a: 1/61 + 1/63; b: 1/62 + 1/61; c: 1/63 ; d: 1/62
    fused = rrf([["a", "b", "c"], ["b", "d", "a"]], k=60)
    assert fused == ["b", "a", "d", "c"]


def test_rrf_ties_break_by_doc_id():
    assert rrf([["z", "y"], ["y", "z"]], k=60) == ["y", "z"]
    assert rrf([], k=60) == []


class _Fixed:
    def __init__(self, ranking):
        self.ranking = ranking

    def search(self, query_text, query_vec, k):
        return self.ranking[:k]


def test_hybrid_fuses_the_two_lists():
    hybrid = HybridRetriever(_Fixed(["a", "b", "c"]), _Fixed(["b", "d", "a"]))
    assert hybrid.search("q", None, 2) == ["b", "a"]


def test_vector_retriever_exact_cosine_with_doc_id_ties():
    np = pytest.importorskip("numpy")
    from eval.retrievers import VectorRetriever

    vectors = np.array([[1, 0], [0.6, 0.8], [0, 1], [0.6, 0.8]], dtype=np.float32)
    retriever = VectorRetriever(["a", "b", "c", "d"], vectors)
    assert retriever.search("", [0, 1], 4) == ["c", "b", "d", "a"]
    assert retriever.search("", [1, 0], 2) == ["a", "b"]
    with pytest.raises(ValueError):
        VectorRetriever(["b", "a"], vectors[:2])
