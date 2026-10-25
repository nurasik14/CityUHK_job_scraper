"""T31: tests for the UI polish round (T30): 0-100 match, card breakdown, hierarchy, search hero,
skeletons. Uses the fake-runner helpers from test_web.py / test_web_redesign.py.
"""

import re

import pytest

from cityu_jobs.core.ranking import Preferences, RankedJob, max_possible_score

from conftest import make_job
from test_web import FakeSearch, _client, _search  # noqa: F401
from test_web import tmp_settings  # noqa: F401 (autouse fixture)
from test_web_redesign import _results_client, no_ollama  # noqa: F401 (autouse fixture)


def _strip_tags(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _ranked(job_id: str, score: float, breakdown: dict | None = None) -> RankedJob:
    breakdown = breakdown or {"similarity": 0.3, "department": 0.1, "hours": 0.0, "student_helper": 0.0,
                              "level": 0.2, "learning_scheme": 0.0}
    return RankedJob(make_job(job_id, title=f"Job {job_id}"), score, breakdown, 0.7, 4, "Reason.")


# --- max_possible_score ---------------------------------------------------------------


@pytest.mark.parametrize("earn, learn, expected", [(1, 1, 0.55), (5, 5, 1.03), (5, 3, 0.93), (3, 1, 0.69), (1, 5, 0.83), (2, 5, 0.88)])
def test_max_possible_score(earn, learn, expected):
    """A job is a Student Helper job *or* an internship, so only the better of the two bonuses counts (Architect 06:27)."""
    assert max_possible_score(Preferences("CS", "", earn, learn)) == pytest.approx(expected)


def test_match_number_is_score_over_max_possible():
    # Prefs in _results_client are earn 2 / learn 5 -> max 0.88; 0.60 / 0.88 = 68.2 -> 68.
    html = _results_client(_ranked("aims:A", 0.60)).get("/results").text
    assert re.search(r'<span class="ring-num">68</span>', html)
    assert "Raw score 0.60 of 0.88 possible for your priorities" in html  # raw score still on the page


def test_match_100_for_best_possible_job():
    html = _results_client(_ranked("aims:A", 0.88)).get("/results").text
    assert '<span class="ring-num">100</span>' in html


# --- Card breakdown -------------------------------------------------------------------


def test_zero_terms_not_listed():
    html = _results_client(_ranked("aims:A", 0.6)).get("/results").text
    why = html[html.index('<ul class="why-list">'): html.index("</ul>", html.index('<ul class="why-list">'))]
    text = _strip_tags(why).lower()
    assert "relevance" in text and "level" in text
    assert "hours" not in text and "student helper" not in text and "internship" not in text
    assert html.count('class="seg t-') == 3  # one bar segment per non-zero term


def test_points_text_for_screen_readers():
    html = _results_client(_ranked("aims:A", 0.6)).get("/results").text
    hidden = re.search(r'<p class="visually-hidden">([^<]*)</p>', html).group(1)
    assert hidden == "Relevance 34, dept 11, level 23 points"  # 0.3 / 0.88, 0.1 / 0.88, 0.2 / 0.88


def test_similarity_label_renamed_to_relevance():
    html = _results_client(_ranked("aims:A", 0.6)).get("/results").text
    assert "relevance" in html.lower()
    assert not re.search(r">\s*match\s+\d", html)  # no old "match 0.30" legend entries


def test_legend_once_per_page():
    html = _results_client(*[_ranked(f"aims:{i}", 0.6 - i / 100) for i in range(5)]).get("/results").text
    assert html.count('class="legend"') == 1


# --- Hierarchy ------------------------------------------------------------------------


def test_top_match_only_on_first_card():
    html = _results_client(*[_ranked(f"aims:{i}", 0.6 - i / 100) for i in range(4)]).get("/results").text
    assert html.count("Top match") == 1
    assert html.index("Top match") < html.index("Job aims:1")
    assert html.count('class="teaser"') == 1


def test_stagger_index_capped_at_10():
    html = _results_client(*[_ranked(f"aims:{i:02d}", 0.6 - i / 100) for i in range(14)]).get("/results").text
    indexes = [int(i) for i in re.findall(r"--i: (\d+)", html)]
    assert indexes[:3] == [0, 1, 2] and max(indexes) == 10


# --- Search hero ----------------------------------------------------------------------


def test_example_chips_never_submit():
    client, _ = _client()
    html = client.get("/").text
    chips = re.findall(r"<button[^>]*data-example=[^>]*>", html)
    assert len(chips) >= 3
    assert all('type="button"' in c for c in chips)
    assert re.search(r'<div class="examples" data-examples hidden>', html)  # shown by app.js only


def test_segment_accessible_names():
    client, _ = _client()
    html = client.get("/").text
    words = ["Not important", "Low", "Medium", "High", "Top priority"]
    for name in ("earn", "learn"):
        for v, word in enumerate(words, start=1):
            label = re.search(rf'<label for="{name}-{v}">(.*?)</label>', html, re.S).group(1)
            assert _strip_tags(label) == f"{v} {word}"


# --- Progress skeletons ---------------------------------------------------------------


def test_skeletons_hidden_from_screen_readers():
    search = FakeSearch()
    search.release.clear()
    client, runner = _client(search)
    _search(client)
    try:
        html = client.get("/results").text
        assert re.search(r'<div class="results skeletons" aria-hidden="true">', html)
        assert html.count('class="card skeleton"') == 3
    finally:
        search.release.set()
