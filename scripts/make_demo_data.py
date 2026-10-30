"""Build a demo data folder for screenshots: public internships + fictional CityU jobs.

The README screenshots must not show anything private, so the demo uses:
- the tech internships from your own cache (public job-board listings), with their
  cached AI levels and embeddings, so no re-rating is needed;
- a few FICTIONAL Campus Internship / Student Helper jobs (AIMS listings are behind the
  CityU login, so real ones are not published);
- a FICTIONAL ITC STEM spreadsheet (the real list is behind the CityU portal login);
- a FICTIONAL student profile.

Run:
    uv run python scripts/make_demo_data.py /tmp/cityu-demo
    CITYU_JOBS_DATA_DIR=/tmp/cityu-demo uv run uvicorn cityu_jobs.web.app:app --port 8765

Then search in the browser. With Claude selected, the first search rates the new jobs
and estimates "difficulty for you" for the demo profile (a few minutes of Claude usage).
"""

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from cityu_jobs import config
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.internships.scraper import SOURCES as INTERN_SOURCES
from cityu_jobs.core.models import Job
from cityu_jobs.core.profile import Profile

TODAY = date.today()


def _d(days: int) -> date:
    return TODAY + timedelta(days=days)


def _aims(number, source, scheme, title, dept, dept_name, hours, total, period, closes, description, requirements, nature):
    raw = {
        "summary": {"Job No.": number, "Brief Job Description": title, "Closing Date": f"{closes:%d-%b-%Y}"},
        "scheme": scheme,
        "fields": {
            "Recruiting Dept/Unit": f"{dept} -- {dept_name}",
            "Job no.": number,
            "Contact Person": "Dr. Demo Contact (fictional)",
            "Supervisor": "Dr. Demo Supervisor (fictional)",
            "Computer Skills": requirements.split("\n")[0].removeprefix("Computer Skills: "),
        },
        "sections": {"Detail Job Description": description},
    }
    return Job(
        job_id=f"aims:{number}", source=source, scheme=scheme, title=title, department_code=dept,
        department_name=dept_name, job_nature=nature, description=description, requirements=requirements,
        part_time=True, weekly_hours=hours, total_hours=total, employment_period=period,
        closing_date=closes, positions=2, raw=raw,
    )


def fictional_aims_jobs() -> list[Job]:
    start, end = _d(32), _d(32 + 150)
    period = f"{start:%d-%b-%Y} to {end:%d-%b-%Y}"
    return [
        _aims(
            "N26CS901", "aims_cis", "Campus Internship Scheme",
            "Research Intern: Evaluating LLM Coding Assistants", "CS", "Computer Science", 12, 180, period, _d(18),
            "[Fictional demo listing] Join a research group studying how well large language model coding "
            "assistants solve undergraduate programming tasks. You will build an evaluation harness in Python, "
            "run open-source models locally, collect results and help write a short technical report.",
            "Computer Skills: Python, Git; experience with PyTorch or Hugging Face is a plus\n"
            "Language Skills: English\nOther Requirements: Year 1-3 undergraduates in CS or related programmes.",
            "Research\nProgramming\nData Processing",
        ),
        _aims(
            "N26DS902", "aims_cis", "Campus Internship Scheme",
            "Data Visualisation Assistant for Campus Analytics", "DS", "Data Science", 10, 120, period, _d(25),
            "[Fictional demo listing] Help a data science team turn campus survey data into interactive "
            "dashboards. Tasks: clean data with pandas, design charts, and present findings to staff.",
            "Computer Skills: Python (pandas), SQL; Tableau or Plotly is a plus\nLanguage Skills: English",
            "Data Processing\nStatistical and Mathematical Work",
        ),
        _aims(
            "S26LIB903", "aims_sh_regular", "Student Helper Scheme (Regular)",
            "Library Digital Services Helper", "LIB", "Library", 8, 96, period, _d(12),
            "[Fictional demo listing] Support the library's digital services desk: help students with e-resources, "
            "test the library website, and update FAQ pages.",
            "Computer Skills: Microsoft Office, basic HTML\nLanguage Skills: English, Cantonese or Mandarin",
            "Clerical Support\nIT Support",
        ),
    ]


ITC_HEADERS = [
    "No.", "Date", "Company/Organisation Name", "Company /Organisation Overview", "Company Website", "Job Title",
    "Job Description(the internship should be Information & Technology related)", "Job Requirements",
    "Working location", "Proposed Internship Start Date", "Proposed Internship Duration",
    "No. of Vacancies for this position", "Application Procedures", "Email for Submission of Application",
    "Application Deadline",
]


def fictional_itc_rows() -> list[list]:
    def row(no, company, overview, site, title, description, requirements, start, days, email, deadline):
        return [no, f"{_d(-20):%Y-%m-%d}", company, overview, site, title, description, requirements,
                "Working in Hong Kong ONLY", f"{start:%d/%m/%Y}", f"{days} days", 2,
                f"Interested students should send their CV and a short cover letter to {email}.", email,
                f"{deadline:%d/%m/%Y}"]

    return [
        row(1, "Harbourlight AI Limited (fictional)", "A Hong Kong start-up building multilingual customer-service agents.",
            "https://example.com/harbourlight", "LLM Application Intern",
            "Build retrieval-augmented chatbots with Python and FastAPI; write evaluation scripts; deploy with Docker.",
            "Python; basic knowledge of LLM APIs, embeddings or RAG; Git. Year 1-4 undergraduates welcome.",
            _d(40), 90, "careers@example.com", _d(30)),
        row(2, "Kowloon Robotics Co. (fictional)", "Builds warehouse robots and their fleet-management software.",
            "https://example.com/kowloon-robotics", "Computer Vision Intern",
            "Train and evaluate object-detection models; optimise inference on edge devices.",
            "Python, PyTorch or OpenCV; C++ is a plus.", _d(45), 59, "jobs@example.com", _d(21)),
        row(3, "Peak Data Studio (fictional)", "A data consultancy for Hong Kong retailers.",
            "https://example.com/peak-data", "Data Engineering Intern",
            "Build ETL pipelines in Python and SQL; maintain dashboards; document data models.",
            "SQL, Python; cloud experience (AWS/Azure) is a plus.", _d(35), 90, "hello@example.com", _d(16)),
        row(4, "Tsim Labs (fictional)", "Develops learning apps for secondary-school students.",
            "https://example.com/tsim-labs", "Full-Stack Developer Intern",
            "Ship features in React and Node.js; write API tests; join code reviews.",
            "JavaScript/TypeScript, React; any backend framework.", _d(50), 59, "team@example.com", _d(35)),
    ]


DEMO_PROFILE = Profile(
    name="Alex Chan (demo)",
    email="alex.chan@example.com",
    education="BSc Computer Science, City University of Hong Kong, Year 2 (2025-2029)",
    skills="Python, PyTorch, scikit-learn, SQL, JavaScript, React, Git, Docker",
    projects=(
        "Course-review RAG chatbot: answers questions about CityU course reviews using sentence embeddings, "
        "FAISS and a local Llama model through Ollama; FastAPI backend, React frontend; evaluated on 50 questions.\n\n"
        "Bus arrival predictor: gradient-boosted model on 6 months of open KMB data; dashboard in Plotly."
    ),
    experience="Teaching assistant for an introductory Python workshop (2026). Hackathon finalist (HK Tech 2026).",
    extra="Languages: English (fluent), Cantonese (native), Mandarin (good). Available part-time during term, full-time in summer.",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("target", type=Path, help="empty folder to create, e.g. /tmp/cityu-demo")
    args = parser.parse_args()
    target: Path = args.target
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty")
    target.mkdir(parents=True, exist_ok=True)

    source = json.loads(config.CACHE_FILE.read_text(encoding="utf-8"))
    cache = Cache(target / "cache.json")
    kept = 0
    for job_id, entry in source["jobs"].items():
        if entry["job"]["source"] in INTERN_SOURCES and entry.get("active"):
            entry = {k: v for k, v in entry.items() if k != "fit"}  # fit is per profile
            location = entry["job"].get("location")
            if location:  # older caches have "Singapore, Singapore, Singapore"; the scraper now dedupes
                entry["job"]["location"] = ", ".join(dict.fromkeys(p.strip() for p in location.split(",")))
            cache.data["jobs"][job_id] = entry
            kept += 1
    for source_name in INTERN_SOURCES:  # marked fresh so the demo search doesn't fetch them again
        cache.mark_scrape_complete(source_name, {j for j, e in cache.data["jobs"].items() if e["job"]["source"] == source_name})
    aims = fictional_aims_jobs()
    for job in aims:
        cache.upsert_job(job)
    for scheme in ("aims_cis", "aims_sh_summer", "aims_sh_regular"):
        cache.mark_scrape_complete(scheme, {j.job_id for j in aims if j.source == scheme})
    cache.save()

    (target / "itc").mkdir()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # a new workbook always has one sheet
    sheet.title = "Job List"
    sheet.append(ITC_HEADERS)
    for row in fictional_itc_rows():
        sheet.append(row)
    workbook.save(target / "itc" / "Demo ITC STEM Job List.xlsx")

    config.PROFILE_FILE = target / "profile.json"
    DEMO_PROFILE.save()
    (target / "settings.json").write_text(json.dumps({"ai_provider": "claude"}), encoding="utf-8")
    print(f"Demo data in {target}: {kept} internships, {len(aims)} fictional AIMS jobs, "
          f"{len(fictional_itc_rows())} fictional ITC jobs, fictional profile.")
    if shutil.which("ollama") is None:
        print("Note: Ollama is needed for the search (embeddings).")


if __name__ == "__main__":
    main()
