"""T16 (part 2): ranking properties from architecture.md "Score".

Most tests compare two jobs that differ in exactly one thing, so a failure points at
the single term that misbehaves.
"""

from datetime import date

import pytest

from cityu_jobs import config
from cityu_jobs.core.ranking import Preferences, is_closed, rank

from conftest import make_job

TODAY = date(2026, 10, 25)


def _scores(jobs, prefs, levels=None, sims=None) -> dict[str, float]:
    sims = sims if sims is not None else {j.job_id: 0.5 for j in jobs}
    return {r.job.job_id: r.score for r in rank(jobs, levels or {}, sims, prefs, TODAY)}


def test_earn_priority_favours_more_hours():
    long, short = make_job("aims:LONG", weekly_hours=20), make_job("aims:SHORT", weekly_hours=5)
    scores = _scores([long, short], Preferences("CS", "q", earn=5, learn=1))
    assert scores["aims:LONG"] > scores["aims:SHORT"]
    assert scores["aims:LONG"] - scores["aims:SHORT"] == pytest.approx(config.WEIGHT_HOURS * 15 / 20)


def test_hours_capped_at_full_week():
    a, b = make_job("aims:A", weekly_hours=20), make_job("aims:B", weekly_hours=40)
    scores = _scores([a, b], Preferences("CS", "q", earn=5))
    assert scores["aims:A"] == pytest.approx(scores["aims:B"])


def test_student_helper_beats_cis_at_equal_hours_when_earning():
    sh = make_job("aims:SH", source="aims_sh_regular")
    cis = make_job("aims:CIS", source="aims_cis")
    scores = _scores([sh, cis], Preferences("CS", "q", earn=5, learn=1))
    assert scores["aims:SH"] - scores["aims:CIS"] == pytest.approx(config.WEIGHT_STUDENT_HELPER)


def test_learn_priority_favours_higher_level():
    jobs = [make_job("aims:HI"), make_job("aims:LO")]
    scores = _scores(jobs, Preferences("CS", "q", earn=1, learn=5), levels={"aims:HI": 5, "aims:LO": 1})
    assert scores["aims:HI"] - scores["aims:LO"] == pytest.approx(config.WEIGHT_LEVEL)


def test_learn_priority_favours_internship_and_itc_over_student_helper():
    jobs = [
        make_job("aims:CIS", source="aims_cis"),
        make_job("itc:cc:X", source="itc_stem"),
        make_job("aims:SH", source="aims_sh_summer"),
    ]
    scores = _scores(jobs, Preferences("CS", "q", earn=1, learn=5))
    assert scores["aims:CIS"] == pytest.approx(scores["itc:cc:X"])
    assert scores["aims:CIS"] - scores["aims:SH"] == pytest.approx(config.WEIGHT_LEARNING_SCHEME)


def test_priority_one_switches_term_off():
    """earn = learn = 1 -> only similarity and department count."""
    job = make_job(weekly_hours=20, source="aims_sh_regular", department_code="MA")
    [ranked] = rank([job], {job.job_id: 5}, {job.job_id: 0.9}, Preferences("CS", "q", earn=1, learn=1), TODAY)
    assert ranked.breakdown["hours"] == ranked.breakdown["student_helper"] == 0
    assert ranked.breakdown["level"] == ranked.breakdown["learning_scheme"] == 0
    assert ranked.score == pytest.approx(config.WEIGHT_SIMILARITY)


def test_priorities_are_clamped_to_1_5():
    job = make_job(weekly_hours=20)
    high = _scores([job], Preferences("CS", "q", earn=99, learn=99))
    top = _scores([job], Preferences("CS", "q", earn=5, learn=5))
    assert high == top
    assert _scores([job], Preferences("CS", "q", earn=-3, learn=0)) == _scores(
        [job], Preferences("CS", "q", earn=1, learn=1)
    )


def test_department_match_adds_exactly_the_department_weight():
    mine, other = make_job("aims:CS", department_code="CS"), make_job("aims:MA", department_code="MA")
    scores = _scores([mine, other], Preferences("CS", "q"))
    assert scores["aims:CS"] - scores["aims:MA"] == pytest.approx(config.WEIGHT_DEPARTMENT)


def test_department_match_is_case_insensitive():
    job = make_job(department_code="cs")
    [ranked] = rank([job], {}, {}, Preferences(" cs ", ""), TODAY)
    assert ranked.breakdown["department"] == pytest.approx(config.WEIGHT_DEPARTMENT)


def test_cs_student_also_boosted_on_college_of_computing_rows():
    cc = make_job("itc:cc:X", source="itc_stem", department_code="CC")
    [ranked] = rank([cc], {}, {}, Preferences("CS", ""), TODAY)
    assert ranked.breakdown["department"] == pytest.approx(config.WEIGHT_DEPARTMENT)


def test_alias_is_one_way_and_other_departments_match_only_themselves():
    cs, cc = make_job("aims:CS", department_code="CS"), make_job("itc:cc:X", department_code="CC")
    for dept in ("MA", "CC"):
        ranked = {r.job.job_id: r for r in rank([cs, cc], {}, {}, Preferences(dept, ""), TODAY)}
        assert ranked["aims:CS"].breakdown["department"] == 0
    ma = make_job("aims:MA", department_code="MA")
    [ranked] = rank([ma], {}, {}, Preferences("MA", ""), TODAY)
    assert ranked.breakdown["department"] == pytest.approx(config.WEIGHT_DEPARTMENT)


def test_no_department_gives_no_boost():
    job = make_job(department_code=None)
    for dept in (None, "", "  "):
        [ranked] = rank([job], {}, {}, Preferences(dept, ""), TODAY)
        assert ranked.breakdown["department"] == 0


def test_closed_jobs_excluded_but_closing_day_still_open():
    closed = make_job("aims:CLOSED", closing_date=date(2026, 10, 24))
    today = make_job("aims:TODAY", closing_date=date(2026, 10, 25))
    undated = make_job("itc:cc:X", closing_date=None)
    ids = [r.job.job_id for r in rank([closed, today, undated], {}, {}, Preferences("CS", ""), TODAY)]
    assert sorted(ids) == ["aims:TODAY", "itc:cc:X"]
    assert is_closed(closed, TODAY) and not is_closed(today, TODAY) and not is_closed(undated, TODAY)


def test_similarity_is_min_max_scaled_over_open_jobs():
    jobs = [make_job("aims:A"), make_job("aims:B"), make_job("aims:C")]
    sims = {"aims:A": 0.70, "aims:B": 0.60, "aims:C": 0.45}
    ranked = {r.job.job_id: r for r in rank(jobs, {}, sims, Preferences(None, "q", 1, 1), TODAY)}
    assert ranked["aims:A"].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)
    assert ranked["aims:C"].breakdown["similarity"] == 0
    assert ranked["aims:B"].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY * 0.6)
    assert ranked["aims:B"].similarity == 0.60  # raw value kept for display


def test_closed_job_does_not_affect_similarity_scaling():
    closed = make_job("aims:CLOSED", closing_date=date(2020, 1, 1))
    a, b = make_job("aims:A"), make_job("aims:B")
    sims = {"aims:CLOSED": 0.99, "aims:A": 0.6, "aims:B": 0.5}
    ranked = {r.job.job_id: r for r in rank([closed, a, b], {}, sims, Preferences(None, "q", 1, 1), TODAY)}
    assert ranked["aims:A"].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)


def test_equal_similarities_all_get_full_term_and_missing_gets_zero():
    a, b, c = make_job("aims:A"), make_job("aims:B"), make_job("aims:C")
    ranked = {r.job.job_id: r for r in rank([a, b, c], {}, {"aims:A": 0.5, "aims:B": 0.5}, Preferences(None, "q"), TODAY)}
    assert ranked["aims:A"].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)
    assert ranked["aims:B"].breakdown["similarity"] == pytest.approx(config.WEIGHT_SIMILARITY)
    assert ranked["aims:C"].breakdown["similarity"] == 0
    assert ranked["aims:C"].similarity is None


def test_unknown_hours_and_unrated_level_count_as_middle():
    job = make_job(weekly_hours=None)
    [ranked] = rank([job], {job.job_id: None}, {}, Preferences(None, "", earn=5, learn=5), TODAY)
    assert ranked.breakdown["hours"] == pytest.approx(config.WEIGHT_HOURS * 0.5)
    assert ranked.breakdown["level"] == pytest.approx(config.WEIGHT_LEVEL * 0.5)
    assert ranked.level is None


def test_breakdown_sums_to_score_and_max_score():
    job = make_job(weekly_hours=30, source="aims_sh_regular", department_code="CS")
    [ranked] = rank([job], {job.job_id: 5}, {job.job_id: 0.8}, Preferences("CS", "q", 5, 5), TODAY)
    assert sum(ranked.breakdown.values()) == pytest.approx(ranked.score)
    # A Student Helper job can't also get the learning-scheme term, so it's below the max.
    assert ranked.score == pytest.approx(0.45 + 0.10 + 0.20 + 0.08 + 0.20)


def test_sorted_best_first_with_stable_tie_break():
    jobs = [make_job("aims:B"), make_job("aims:A"), make_job("aims:C", department_code="CS")]
    jobs[0].department_code = jobs[1].department_code = "MA"
    order = [r.job.job_id for r in rank(jobs, {}, {}, Preferences("CS", ""), TODAY)]
    assert order == ["aims:C", "aims:A", "aims:B"]


def test_empty_job_list():
    assert rank([], {}, {}, Preferences("CS", "q"), TODAY) == []
