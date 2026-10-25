"""Offline parser tests against saved AIMS pages.

The fixtures are real pages (staff names, the student's own status), so they live in the
git-ignored .data/fixtures/ and these tests skip when they're missing.
"""

import json

import pytest

from cityu_jobs import config
from cityu_jobs.core.aims_scraper import parse_job_detail, parse_results

FIXTURES = config.DATA_DIR / "fixtures"


def _fixture(name: str) -> str:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"missing fixture {path}")
    return path.read_text()


def test_parse_results():
    rows = parse_results(_fixture("aims_results_cis.html"))
    assert len(rows) == 45
    first = rows[0]
    assert first["Job No."] == "N26MA004"
    assert first["Recruit. Dept"] == "MA"
    assert first["Avg Weekly Work hrs per Student"] == "16.5"
    assert first["Total Work hrs per Student"] == "80"


def test_parse_job_detail():
    job = parse_job_detail(_fixture("aims_job_detail.html"))
    fields = job["fields"]

    assert job["scheme"] == "Campus Internship Scheme"
    assert fields["Job no."] == "N26MA004"
    assert fields["Recruiting Dept/Unit"] == "MA -- Mathematics"
    assert fields["Research Project no."] == "9043868"
    assert fields["Average Weekly Work Hours per Student"] == "16.5"
    assert fields["Closing Date"] == "25-Oct-2026"
    assert "Numerical Competency" in fields["Graduate Attributes"]
    assert fields["Other areas of development that you wish students to acquire"] == "N/A"

    assert job["sections"]["Project Description"].startswith("One of the most important")
    assert "spherical framelets" in job["sections"]["Detail Job Description"]
    assert len(job["learning_objectives"]) == 1
    assert [s["Type"] for s in job["training_plan"]] == ["Pre-internship", "Interim", "Post-internship"]
    assert job["evaluation"] == [{"Type": "Final Evaluation", "Date": "25-Oct-2026"}]
    assert job["professional_development"]["Attitudes Toward Work"][
        "Sense of responsibility and commitment"
    ] == "Very important"


def test_applied_banner_not_captured():
    job = parse_job_detail(_fixture("aims_job_detail.html"))
    assert "you have applied" not in json.dumps(job).lower()
