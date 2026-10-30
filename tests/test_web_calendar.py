"""Results page: source filters, internship cards and the calendar data."""

import json
import re
from datetime import date

from cityu_jobs.core.dates import job_timeline
from cityu_jobs.core.ranking import RankedJob

from conftest import make_job
from test_web import tmp_settings  # noqa: F401 (autouse fixture)
from test_web_redesign import _results_client, no_ollama  # noqa: F401 (autouse fixture)


def _ranked(job, score=0.6, skills=None) -> RankedJob:
    r = RankedJob(job, score, {"similarity": 0.3, "level": 0.1}, 0.7, 4, "Reason.", skills=skills or [])
    r.timeline = job_timeline(job)
    return r


def _jobs():
    cityu = make_job("aims:N1", title="Lab helper", employment_period="08-Sep-2026 to 25-Oct-2026", closing_date=date(2099, 1, 1))
    big = make_job(
        "intern:gh:stripe:1", source="intern_bigtech", scheme="Big tech internship", title="SWE Intern (Summer 2027)",
        company="Stripe", location="Singapore", url="https://stripe.example/1", closing_date=None,
        department_code="TECH", department_name="Tech industry",
    )
    tech = make_job("intern:lever:x:1", source="intern_tech", title="Data Intern", company="Lalamove", location="Hong Kong", closing_date=None)
    return [_ranked(cityu, 0.7), _ranked(big, 0.65, skills=["Go", "SQL"]), _ranked(tech, 0.5)]


def test_calendar_data_embedded_for_shown_jobs():
    html = _results_client(*_jobs()).get("/results").text
    data = json.loads(re.search(r'<script type="application/json" id="calendar-data">(.*?)</script>', html, re.S).group(1))
    by_id = {d["id"]: d for d in data}
    assert by_id["job-1"]["start"] == "2026-09-08" and by_id["job-1"]["end"] == "2026-10-25"
    assert by_id["job-1"]["deadline"] == "2099-01-01"
    assert by_id["job-2"]["approx"] is True and by_id["job-2"]["label"] == "Summer 2027"  # season in the title
    assert 'data-cal-id="job-2"' in html and 'data-cal-show="job-2"' in html
    assert "Coming up" in html  # the no-JS agenda


def test_internship_card_shows_company_location_skills_and_link():
    html = _results_client(*_jobs()).get("/results").text
    assert "Stripe" in html and "Singapore" in html and "Big tech internship" in html
    assert re.search(r"<li>Go</li>\s*<li>SQL</li>", html)
    assert 'href="https://stripe.example/1"' in html and "View posting" in html
    assert "Summer 2027" in html and "(approx.)" in html


def test_filters_keep_overall_rank_numbers():
    client = _results_client(*_jobs())
    html = client.get("/results?show=bigtech").text
    assert 'aria-current="page">\n    Big tech' in html or re.search(r'aria-current="page">\s*Big tech', html)
    assert "SWE Intern" in html and "Lab helper" not in html.split('<ol class="results">')[1]
    assert re.search(r'<span class="rank" aria-hidden="true">2</span>', html)  # still #2 overall
    cityu = client.get("/results?show=cityu").text
    assert "Lab helper" in cityu and "Data Intern" not in cityu.split('<ol class="results">')[1]


def test_unknown_filter_shows_all_and_empty_filter_explains():
    client = _results_client(_jobs()[0])
    assert "Lab helper" in client.get("/results?show=nope").text
    empty = client.get("/results?show=tech").text
    assert "Nothing in “Other tech” for this search" in empty and 'href="/results"' in empty
