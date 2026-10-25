"""T9: parser tests on Student Helper pages saved during the live run (2026-10-25).

Fixtures (git-ignored, they name AIMS staff): aims_results_sh_summer.html (a search with no
jobs), aims_results_sh_regular.html (72 jobs) and aims_job_detail_sh_regular.html (P26FO006).
Assertions use only job data, never contact names.
"""

from datetime import date

from cityu_jobs.core.aims_scraper import aims_to_job, parse_job_detail, parse_matched_count, parse_results
from cityu_jobs.core.models import embedding_text

from conftest import fixture_text


def test_empty_summer_search():
    html = fixture_text("aims_results_sh_summer.html")
    assert parse_results(html) == []
    assert parse_matched_count(html) == 0


def test_regular_results_table():
    html = fixture_text("aims_results_sh_regular.html")
    rows = parse_results(html)
    assert len(rows) == parse_matched_count(html) == 72
    assert all(r["Job No."] for r in rows)
    assert len({r["Job No."] for r in rows}) == 72  # job numbers are unique
    # Student Helper results have no Part-time/Full-time column (CIS results do).
    assert "Part-time/ Full-time" not in rows[0]
    first = rows[0]
    assert first["Job No."] == "P26FO006"
    assert first["Recruit. Dept"] == "FO"
    assert first["Avg Weekly Work hrs per Student"] == "16.5"


def test_regular_detail_page():
    detail = parse_job_detail(fixture_text("aims_job_detail_sh_regular.html"))
    assert detail["scheme"] == "Student Helpers (Regular Term)"
    assert detail["fields"]["Job no."] == "P26FO006"
    assert list(detail["sections"]) == ["Detail Job Description"]
    # The CIS-only parts are simply empty on a Student Helper page.
    assert detail["learning_objectives"] == [] and detail["training_plan"] == []
    assert detail["evaluation"] == [] and detail["professional_development"] == {}


def test_regular_aims_to_job():
    rows = parse_results(fixture_text("aims_results_sh_regular.html"))
    detail = parse_job_detail(fixture_text("aims_job_detail_sh_regular.html"))
    job = aims_to_job(rows[0], detail, "sh_regular")

    assert job.job_id == "aims:P26FO006"
    assert job.source == "aims_sh_regular"
    assert (job.department_code, job.department_name) == ("FO", "Finance Office")
    assert job.weekly_hours == 16.5
    assert job.total_hours == 700
    assert job.closing_date == date(2026, 10, 30)
    assert job.positions == 1
    assert job.part_time is None  # not stated on Student Helper pages
    assert "Detail Job Description" in job.description
    assert job.requirements.startswith("Computer Skills:")


def test_staff_names_not_in_embedding_text():
    """Contact person and supervisor are kept in raw, but never sent to the AI as job text."""
    rows = parse_results(fixture_text("aims_results_sh_regular.html"))
    detail = parse_job_detail(fixture_text("aims_job_detail_sh_regular.html"))
    text = embedding_text(aims_to_job(rows[0], detail, "sh_regular"))
    for label in ("Contact Person", "Supervisor"):
        name = detail["fields"].get(label)
        if name and name.upper() != "N/A":
            assert name not in text
