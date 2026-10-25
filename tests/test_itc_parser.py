"""T11: ITC STEM spreadsheet parser (parse_job_list), offline only.

Part 1 builds small spreadsheets in tmp_path with openpyxl to pin down each rule.
Part 2 runs on the real College of Computing list (2026.10.22, 98 jobs), saved as the
git-ignored .data/fixtures/itc/cc_job_list.xlsx; those tests skip when it's missing.
"""

import io
import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from cityu_jobs import config
from cityu_jobs.core import itc_stem_scraper
from cityu_jobs.core.itc_stem_scraper import ITC_PARSER_VERSION, OVERVIEW_MAX_CHARS, parse_job_list
from cityu_jobs.core.models import compute_content_hash
from cityu_jobs.core.ranking import Preferences, RankedJob, rank
from cityu_jobs.web.app import create_app
from cityu_jobs.web.tasks import TaskRunner

from conftest import FIXTURES

CC = config.ITC_STEM_SOURCES[0]


def _xlsx(*sheets: tuple[str, list[list]]) -> bytes:
    """Build a workbook from (sheet title, rows) pairs and return its bytes."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


HEADER = ["Ref. No.", "Job Title", "Company", "Job Description", "Preferred Major", "Skills Required",
          "Working Hours/Week", "No. of Vacancies", "Application Deadline"]


def _row(ref="R1", title="ML Intern", company="Acme", major="Computer Science", hours="40 hours/week"):
    return [ref, title, company, "Train models", major, "Python", hours, 2, datetime(2026, 11, 30)]


# --- Part 1: generated spreadsheets ------------------------------------------------


def test_banner_rows_and_notes_sheet_are_skipped():
    data = _xlsx(
        ("Notes", [["Please read the instructions"], ["Contact the college office"]]),
        ("Jobs", [["ITC STEM Internship Scheme - Job list"], [], HEADER, _row()]),
    )
    [job] = parse_job_list(data, CC)
    assert job.title == "ML Intern"
    assert job.raw["_sheet"] == "Jobs"


def test_columns_mapped_by_header_name():
    [job] = parse_job_list(_xlsx(("Jobs", [HEADER, _row()])), CC)
    assert job.job_id == "itc:cc:R1"
    assert job.source == "itc_stem"
    assert job.description == "Company: Acme\n\nTrain models"
    assert job.weekly_hours == 40
    assert job.positions == 2
    assert job.closing_date == date(2026, 11, 30)
    assert job.url == CC["page_url"]


def test_several_requirement_columns_all_kept_and_preferred_is_not_ref():
    """Whole-word aliases: "Preferred Major" must go to requirements, not to ref ("pREFerred")."""
    header = ["Job Title", "Preferred Major", "Skills Required"]
    [job] = parse_job_list(_xlsx(("Jobs", [header, ["Dev", "Computer Science", "Python"]])), CC)
    assert job.requirements == "Preferred Major: Computer Science\nSkills Required: Python"
    assert not job.job_id.endswith("ComputerScience")


def test_words_containing_aliases_match_nothing():
    header = ["Job Title", "Postgraduate Only", "Community Service", "Accommodation", "Deadline"]
    [job] = parse_job_list(_xlsx(("Jobs", [header, ["Dev", "Y", "Y", "Y", "2026-11-30"]])), CC)
    assert job.employment_period is None and job.department_code == "CC" and job.part_time is None
    assert job.raw["Postgraduate Only"] == "Y"  # unmapped columns are kept in raw


def test_missing_columns_tolerated_and_department_defaults_to_college():
    [job] = parse_job_list(_xlsx(("Jobs", [["Job Title", "Company"], ["Dev", "Acme"]])), CC)
    assert (job.department_code, job.department_name) == ("CC", "College of Computing")
    assert job.weekly_hours is None and job.closing_date is None and job.requirements == ""


def test_department_column_used_when_present():
    rows = [["Job Title", "Department"], ["A", "CS - Computer Science"], ["B", "Computer Science"]]
    a, b = parse_job_list(_xlsx(("Jobs", rows)), CC)
    assert (a.department_code, a.department_name) == ("CS", "Computer Science")
    assert (b.department_code, b.department_name) == (None, "Computer Science")


def test_blank_rows_skipped():
    rows = [HEADER, _row(), [None] * len(HEADER), [], _row(ref="R2")]
    assert [j.job_id for j in parse_job_list(_xlsx(("Jobs", rows)), CC)] == ["itc:cc:R1", "itc:cc:R2"]


def test_job_id_without_ref_is_stable_and_distinct_for_same_major():
    header = ["Job Title", "Company", "Preferred Major"]
    rows = [header, ["Dev", "Acme", "Computer Science"], ["Tester", "Acme", "Computer Science"]]
    data = _xlsx(("Jobs", rows))
    first, second = parse_job_list(data, CC), parse_job_list(data, CC)
    assert [j.job_id for j in first] == [j.job_id for j in second]
    assert first[0].job_id != first[1].job_id
    assert all(len(j.job_id.split(":")[-1]) == 12 for j in first)  # hash suffix


def test_identical_rows_both_kept_with_distinct_ids():
    rows = [["Job Title", "Company"], ["Dev", "Acme"], ["Dev", "Acme"]]
    a, b = parse_job_list(_xlsx(("Jobs", rows)), CC)
    assert a.job_id != b.job_id


def test_raw_is_json_safe_with_date_cells():
    row = _row()
    [job] = parse_job_list(_xlsx(("Jobs", [HEADER, row])), CC)
    assert job.raw["Application Deadline"] == "2026-11-30"
    json.dumps(job.raw)
    assert job.content_hash == compute_content_hash(job.raw)


@pytest.mark.parametrize("cell, expected", [
    ("30/11/2026", date(2026, 11, 30)),
    ("2026-11-30", date(2026, 11, 30)),
    ("30-Nov-2026", date(2026, 11, 30)),
    ("30 November 2026", date(2026, 11, 30)),
    ("ASAP", None),
])
def test_text_deadline_formats(cell, expected):
    [job] = parse_job_list(_xlsx(("Jobs", [["Job Title", "Deadline"], ["Dev", cell]])), CC)
    assert job.closing_date == expected


def test_full_and_part_time_column():
    rows = [["Job Title", "Mode of Employment"], ["A", "Full-time"], ["B", "Part-time"], ["C", "Flexible"]]
    assert [j.part_time for j in parse_job_list(_xlsx(("Jobs", rows)), CC)] == [False, True, None]


# --- Part 2: the real College of Computing list (2026.10.22) -------------------------

REAL = FIXTURES / "itc" / "cc_job_list.xlsx"


@pytest.fixture(scope="module")
def real_jobs():
    if not REAL.exists():
        pytest.skip(f"missing fixture {REAL}")
    return parse_job_list(REAL.read_bytes(), CC)


def test_real_every_row_parsed(real_jobs):
    assert len(real_jobs) == 98  # 98 data rows under the banner + header rows, all with a title
    assert all(j.raw["_sheet"] == "Job List" for j in real_jobs)


def test_real_job_ids_unique_and_stable(real_jobs):
    ids = [j.job_id for j in real_jobs]
    assert len(set(ids)) == 98
    assert ids == [j.job_id for j in parse_job_list(REAL.read_bytes(), CC)]
    # The sheet has no reference column ("No." is just the row number), so ids are hashes.
    assert all(i.startswith("itc:cc:") and len(i) == len("itc:cc:") + 12 for i in ids)


def test_real_fields_mapped(real_jobs):
    assert all((j.department_code, j.department_name) == ("CC", "College of Computing") for j in real_jobs)
    assert all(j.description.startswith("Company: ") for j in real_jobs)
    assert all(j.requirements for j in real_jobs)
    assert all(j.closing_date is not None for j in real_jobs)
    assert all(isinstance(j.positions, int) and j.positions >= 1 for j in real_jobs)
    assert all(j.weekly_hours is None for j in real_jobs)  # the CC list has no hours column
    json.dumps([j.raw for j in real_jobs])


def test_real_sample_row(real_jobs):
    job = next(j for j in real_jobs if j.title == "Software Development & QA Intern")
    assert "Company: TELESHOP (HK) LIMITED" in job.description
    assert "Computer Science" in job.requirements
    assert job.closing_date == date(2026, 11, 27)  # text cell "27/11/2026" (day first)
    assert job.positions == 2


def test_real_cs_jobs_recognisable(real_jobs):
    titles = [j.title.lower() for j in real_jobs]
    assert sum(any(w in t for w in ("software", "developer", "machine learning", "data", " ai")) for t in titles) >= 20


def test_real_swapped_excel_dates_are_corrected(real_jobs):
    """Rows posted 2026-09-14 store deadline 2026-06-11 / start 2026-05-10 as Excel dates.

    These are day/month swaps of 6 Nov / 5 Oct (the text cells in this sheet are all
    day-first). Taken literally, these CS jobs look closed and are never recommended.
    """
    job = next(j for j in real_jobs if j.title.startswith("AI / Machine Learning Research"))
    assert job.closing_date == date(2026, 11, 6)


def test_real_swap_fix_covers_all_affected_rows_and_leaves_text_dates(real_jobs):
    by_no = {j.raw["No."]: j for j in real_jobs}
    for no in ("6", "7", "8"):
        assert by_no[no].closing_date == date(2026, 11, 6)
        # start date swapped back too, then the duration (T24 "start, duration" format)
        duration = by_no[no].raw["Proposed Internship Duration"]
        assert by_no[no].employment_period == f"2026-10-05, {duration}"
    assert by_no["9"].closing_date == date(2026, 10, 5)
    assert by_no["112"].employment_period.startswith("2026-11-02, ")
    assert by_no["5"].closing_date == date(2026, 11, 27)  # day-first text cell, untouched


# --- T24: mapping follow-ups -------------------------------------------------------

T24_HEADER = ["Date", "Job Title", "Company/Organisation Name", "Company /Organisation Overview",
              "Proposed Internship Start Date", "Proposed Internship Duration", "Application Deadline",
              "Will the company provide any monthly honorarium ON TOP of the allowance?"]


def _t24_row(**cells):
    row = dict(zip(T24_HEADER, [datetime(2026, 9, 14), "ML Intern", "Acme", "We build robots.",
                                "21/09/2026", "90 days", "30/11/2026", "No"]))
    row.update(cells)
    return [row[h] for h in T24_HEADER]


def _t24_job(**cells):
    [job] = parse_job_list(_xlsx(("Jobs", [T24_HEADER, _t24_row(**cells)])), CC)
    return job


def test_t24a_unswappable_early_date_cell_becomes_none():
    # 13 Jun 2026 (posted 14 Sep): day 13 can't be a month -> wrong either way -> None (job stays visible)
    assert _t24_job(**{"Application Deadline": datetime(2026, 6, 13)}).closing_date is None


def test_t24a_swappable_early_date_cell_is_swapped():
    # 10 Sep 2026 is before the 14 Sep posting; swapped = 9 Oct 2026
    assert _t24_job(**{"Application Deadline": datetime(2026, 9, 10)}).closing_date == date(2026, 10, 9)


def test_t24a_early_date_still_before_posting_when_swapped_becomes_none():
    # 2 Mar 2026 (posted 14 Sep): swapped = 3 Feb 2026, still before posting -> None
    assert _t24_job(**{"Application Deadline": datetime(2026, 3, 2)}).closing_date is None


def test_t24a_dates_on_or_after_posting_and_text_cells_untouched():
    assert _t24_job(**{"Application Deadline": datetime(2026, 11, 5)}).closing_date == date(2026, 11, 5)
    assert _t24_job(**{"Application Deadline": datetime(2026, 9, 14)}).closing_date == date(2026, 9, 14)
    assert _t24_job(**{"Application Deadline": "01/06/2026"}).closing_date == date(2026, 6, 1)  # text: left alone


def test_t24a_no_posting_date_leaves_date_cells_alone():
    assert _t24_job(Date=None, **{"Application Deadline": datetime(2026, 6, 11)}).closing_date == date(2026, 6, 11)


def test_t24b_period_is_start_and_duration():
    assert _t24_job().employment_period == "21/09/2026, 90 days"
    assert _t24_job(**{"Proposed Internship Duration": None}).employment_period == "21/09/2026"
    assert _t24_job(**{"Proposed Internship Start Date": None}).employment_period == "90 days"
    assert _t24_job(**{"Proposed Internship Start Date": None, "Proposed Internship Duration": None}).employment_period is None
    # A swapped start-date cell is fixed before joining.
    assert _t24_job(**{"Proposed Internship Start Date": datetime(2026, 5, 10)}).employment_period == "2026-10-05, 90 days"


def test_t24c_overview_after_job_text_and_capped():
    job = _t24_job()
    assert job.description.endswith("About the company: We build robots.")
    assert job.description.startswith("Company: Acme")
    long = _t24_job(**{"Company /Organisation Overview": "x" * 2000})
    about = long.description.split("About the company: ")[1]
    assert len(about) <= OVERVIEW_MAX_CHARS == 500
    assert "About the company" not in _t24_job(**{"Company /Organisation Overview": None}).description


@pytest.mark.parametrize("text, offered", [
    ("No", False), ("NA", False), ("N/A", False), ("0", False), ("No.", False), (None, False),
    ("Acme will not provide extra honorarium", False),
    ("HKD 2,000 per month", True), ("Yes, $1000/month", True),
])
def test_t24d_honorarium_only_in_raw(text, offered):
    job = _t24_job(**{T24_HEADER[-1]: text})
    assert ("_honorarium" in job.raw) is offered
    if offered:
        assert job.raw["_honorarium"] == text
    assert "honorarium" not in job.description.lower() and "honorarium" not in job.requirements.lower()


def test_t24d_honorarium_not_scored():
    plain = _t24_job(**{"Job Title": "A"})
    paid = _t24_job(**{"Job Title": "B", T24_HEADER[-1]: "HKD 2,000 per month"})
    scores = [r.score for r in rank([plain, paid], {}, {}, Preferences("CS", "", earn=5, learn=5), date(2026, 10, 25))]
    assert scores[0] == pytest.approx(scores[1])


def test_t24d_honorarium_shown_in_results_details():
    paid = _t24_job(**{T24_HEADER[-1]: "HKD 2,000 per month"})
    runner = TaskRunner()
    runner.state.prefs = Preferences("CS", "")
    runner.state.results = [RankedJob(paid, 0.5, {}, None, None)]
    assert "Extra honorarium: HKD 2,000 per month" in TestClient(create_app(runner)).get("/results").text


def test_t24e_parser_version_in_content_hash(monkeypatch):
    job = _t24_job()
    assert job.raw["_parser_version"] == ITC_PARSER_VERSION >= 3  # 3 since _shorten (T30)
    monkeypatch.setattr(itc_stem_scraper, "ITC_PARSER_VERSION", ITC_PARSER_VERSION + 1)
    bumped = _t24_job()
    assert bumped.content_hash != job.content_hash
    assert bumped.job_id == job.job_id  # same job, so its cached AI results are simply recomputed


# --- _shorten (T30: overview cut at a word boundary) ---------------------------------


def test_shorten_keeps_short_text():
    assert itc_stem_scraper._shorten("We build robots.", 500) == "We build robots."
    assert itc_stem_scraper._shorten("x" * 500, 500) == "x" * 500  # exactly at the limit: no "…"


def test_shorten_cuts_at_word_boundary_with_ellipsis():
    text = "We build robots, drones and tools for schools across Hong Kong."
    out = itc_stem_scraper._shorten(text, 30)
    assert out == "We build robots, drones and…"
    assert len(out) <= 30 and out.endswith("…")


def test_shorten_trims_trailing_punctuation_before_ellipsis():
    assert itc_stem_scraper._shorten("Alpha beta, gamma delta epsilon", 13) == "Alpha beta…"


def test_shorten_single_long_word_is_hard_cut():
    out = itc_stem_scraper._shorten("x" * 2000, 500)
    assert out == "x" * 499 + "…" and len(out) == 500


def test_real_overviews_cut_within_limit():
    if not REAL.exists():
        pytest.skip(f"missing fixture {REAL}")
    abouts = [j.description.split("About the company: ")[1] for j in parse_job_list(REAL.read_bytes(), CC)
              if "About the company: " in j.description]
    assert abouts and all(len(a) <= OVERVIEW_MAX_CHARS for a in abouts)
    assert any(a.endswith("…") for a in abouts)
