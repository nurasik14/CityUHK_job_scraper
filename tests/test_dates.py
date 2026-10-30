"""Start / end / deadline for the calendar (core/dates.py)."""

from datetime import date

import pytest

from cityu_jobs.core.dates import job_timeline, parse_period, season_range

from conftest import make_job


@pytest.mark.parametrize(
    "text, start, end",
    [
        ("08-Sep-2026 to 25-Oct-2026", date(2026, 9, 8), date(2026, 10, 25)),       # AIMS
        ("21/09/2026, 90 days", date(2026, 9, 21), date(2026, 12, 19)),            # ITC STEM, day/month
        ("2026-10-12, 89 days", date(2026, 10, 12), date(2027, 1, 8)),             # ITC STEM, ISO
        ("2026-10-12, 12 weeks", date(2026, 10, 12), date(2027, 1, 3)),
        ("2026-10-12", date(2026, 10, 12), None),
        ("Summer 2027", None, None),
        (None, None, None),
    ],
)
def test_parse_period(text, start, end):
    assert parse_period(text) == (start, end)


def test_season_range():
    assert season_range("Software Engineer Intern (Summer 2027)") == (date(2027, 6, 1), date(2027, 8, 31), "Summer 2027")
    assert season_range("Intern - Winter '27")[0] == date(2027, 1, 5)
    assert season_range("Data intern") is None


def test_timeline_from_period_and_closing_date():
    t = job_timeline(make_job(employment_period="21/09/2026, 90 days", closing_date=date(2026, 11, 30)))
    assert (t.start, t.end, t.deadline, t.approximate, t.days) == (date(2026, 9, 21), date(2026, 12, 19), date(2026, 11, 30), False, 90)


def test_timeline_prefers_exact_dates_then_ai_then_season():
    exact = make_job(start_date=date(2027, 6, 1), end_date=date(2027, 8, 20), employment_period="Summer 2027")
    assert job_timeline(exact).start == date(2027, 6, 1) and not job_timeline(exact).approximate

    ai = job_timeline(make_job(title="ML Intern", closing_date=None), {"start": "2027-07-01", "weeks": 10, "deadline": "2026-12-01"})
    assert (ai.start, ai.end, ai.deadline) == (date(2027, 7, 1), date(2027, 9, 8), date(2026, 12, 1))

    season = job_timeline(make_job(title="SWE Intern", raw={"terms": ["Summer 2027"]}, closing_date=None))
    assert season.approximate and season.label == "Summer 2027" and season.start == date(2027, 6, 1)


def test_timeline_end_before_start_is_dropped():
    t = job_timeline(make_job(employment_period="25-Oct-2026 to 08-Sep-2026"))
    assert t.start == date(2026, 10, 25) and t.end is None


def test_timeline_to_dict_is_json_safe():
    d = job_timeline(make_job(employment_period="08-Sep-2026 to 25-Oct-2026")).to_dict()
    assert d["start"] == "2026-09-08" and d["end"] == "2026-10-25" and d["days"] == 48
