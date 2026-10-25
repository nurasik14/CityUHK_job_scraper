"""T4: the common Job schema, content hashes, and the AIMS -> Job mapping."""

import json
from datetime import date

import pytest
from bs4 import BeautifulSoup

from cityu_jobs.core.aims_scraper import (
    aims_to_job,
    parse_job_detail,
    parse_matched_count,
    parse_results,
    summary_hash,
)
from cityu_jobs.core.departments import load_departments
from cityu_jobs.core.models import Job, compute_content_hash, embedding_text

from conftest import fixture_text, make_job

# --- Job / hashing --------------------------------------------------------------


def test_to_dict_from_dict_round_trip():
    job = make_job(closing_date=date(2026, 10, 25), raw={"a": 1, "nested": {"b": [1, 2]}})
    data = job.to_dict()
    assert data["closing_date"] == "2026-10-25"
    json.dumps(data)  # must be JSON-safe
    assert Job.from_dict(data) == job


def test_from_dict_ignores_unknown_keys_and_missing_date():
    data = make_job(closing_date=None).to_dict()
    data["some_future_field"] = "x"
    job = Job.from_dict(data)
    assert job.closing_date is None


def test_content_hash_ignores_key_order():
    assert compute_content_hash({"a": 1, "b": {"x": 1, "y": 2}}) == compute_content_hash(
        {"b": {"y": 2, "x": 1}, "a": 1}
    )


def test_content_hash_changes_with_content():
    assert compute_content_hash({"a": 1}) != compute_content_hash({"a": 2})


def test_embedding_text_skips_empty_parts():
    job = make_job(title="T", job_nature=None, department_name="D", description="Desc", requirements="")
    assert embedding_text(job) == "T\nD\nDesc"


# --- AIMS mapping on synthetic input (always runs) --------------------------------


def _summary(**overrides) -> dict:
    row = {
        "Job No.": "N26CS001",
        "Brief Job Description": "Web developer",
        "Recruit. Dept": "CS",
        "Avg Weekly Work hrs per Student": "12",
        "Total Work hrs per Student": "100",
        "Closing Date": "01-Dec-2026",
        "Part-time/ Full-time": "Full-time",
    }
    row.update(overrides)
    return row


def test_aims_to_job_from_summary_only():
    job = aims_to_job(_summary(), {}, "sh_regular")
    assert job.job_id == "aims:N26CS001"
    assert job.source == "aims_sh_regular"
    assert job.scheme == "Student Helpers (Regular Term)"
    assert job.department_code == "CS"
    assert job.department_name is None
    assert job.weekly_hours == 12.0
    assert job.total_hours == 100.0
    assert job.closing_date == date(2026, 12, 1)
    assert job.part_time is False
    assert job.content_hash == compute_content_hash(job.raw)


def test_aims_to_job_treats_na_as_missing():
    detail = {"fields": {"Average Weekly Work Hours per Student": "N/A", "Computer Skills": "N/A",
                         "Language Skills": "English", "Recruiting Dept/Unit": "CS -- Computer Science"}}
    job = aims_to_job(_summary(**{"Avg Weekly Work hrs per Student": "-"}), detail, "cis")
    assert job.weekly_hours is None
    assert job.requirements == "Language Skills: English"
    assert (job.department_code, job.department_name) == ("CS", "Computer Science")


def test_aims_to_job_bad_date_and_hours_become_none():
    job = aims_to_job(_summary(**{"Closing Date": "soon", "Avg Weekly Work hrs per Student": "ten"}), {}, "cis")
    assert job.closing_date is None
    assert job.weekly_hours is None


def test_summary_hash_ignores_key_order_but_not_values():
    a = _summary()
    b = dict(reversed(list(a.items())))
    assert summary_hash(a) == summary_hash(b)
    assert summary_hash(a) != summary_hash(_summary(**{"Closing Date": "02-Dec-2026"}))


def test_summary_hash_changes_with_parser_version(monkeypatch):
    """Bumping PARSER_VERSION must force every job to be re-opened once."""
    from cityu_jobs.core import aims_scraper

    before = summary_hash(_summary())
    monkeypatch.setattr(aims_scraper, "PARSER_VERSION", aims_scraper.PARSER_VERSION + 1)
    assert summary_hash(_summary()) != before


# --- Parser edge cases (always run) ---------------------------------------------


def test_parse_results_empty_search_returns_empty_list():
    html = "<html><body><p>No. of matched record: 0</p><table><tr><td>nothing</td></tr></table></body></html>"
    assert parse_results(html) == []
    assert parse_matched_count(html) == 0


def test_parse_matched_count_missing():
    assert parse_matched_count("<html><body>hello</body></html>") is None


def test_parse_job_detail_without_body_raises_clear_error():
    with pytest.raises(ValueError, match="not an AIMS job description page"):
        parse_job_detail("<html><title>Session Timeout</title><body>Please log in</body></html>")


# --- AIMS mapping on the saved real pages (skip without fixtures) ----------------


def test_aims_to_job_on_fixtures():
    summary = parse_results(fixture_text("aims_results_cis.html"))[0]
    detail = parse_job_detail(fixture_text("aims_job_detail.html"))
    job = aims_to_job(summary, detail, "cis")

    assert job.job_id == "aims:N26MA004"
    assert job.source == "aims_cis"
    assert job.department_code == "MA"
    assert job.department_name == "Mathematics"
    assert job.weekly_hours == 16.5
    assert job.total_hours == 80
    assert job.part_time is True
    assert job.closing_date == date(2026, 10, 25)
    assert job.positions == 1
    assert job.description  # free-text sections were mapped
    assert "you have applied" not in embedding_text(job).lower()
    # The job round-trips through the cache format unchanged.
    assert Job.from_dict(json.loads(json.dumps(job.to_dict()))) == job


def test_aims_to_job_hash_is_stable():
    summary = parse_results(fixture_text("aims_results_cis.html"))[0]
    detail = parse_job_detail(fixture_text("aims_job_detail.html"))
    assert aims_to_job(summary, detail, "cis").content_hash == aims_to_job(summary, detail, "cis").content_hash


def test_parse_matched_count_on_fixture():
    html = fixture_text("aims_results_cis.html")
    assert parse_matched_count(html) == len(parse_results(html))


def test_department_list_matches_saved_search_page():
    """data/aims_departments.json was extracted from the job search page; check it still agrees."""
    soup = BeautifulSoup(fixture_text("aims_job_search.html"), "html.parser")
    options = [o.get_text(" ", strip=True) for o in soup.select("select[name=p_empr_dept] option")]
    page_codes = {o.split(" - ", 1)[0].strip() for o in options if " - " in o}
    json_codes = {d["code"] for d in load_departments()}
    assert len(load_departments()) == 210
    assert json_codes == page_codes
    assert load_departments()[0]["code"] == "CS"  # owner decision: CS first
