"""Profile (CV upload), difficulty for you, cover letters, tracker, ITC spreadsheet page, apply info."""

import io
import stat
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from cityu_jobs import config
from cityu_jobs.core.apply_info import apply_info, employer, facts
from cityu_jobs.core.fit import _valid, assess_fit, can_assess
from cityu_jobs.core.letters import load_letter, load_tracker, set_status, write_letter
from cityu_jobs.core.pipeline import fit_candidates
from cityu_jobs.core.profile import CVError, Profile, extract_cv_text
from cityu_jobs.core.ranking import RankedJob
from cityu_jobs.web import app as web_app
from cityu_jobs.web.tasks import TaskRunner

from conftest import make_job
from test_web import ORIGIN, tmp_settings  # noqa: F401 (autouse fixture)
from test_web_redesign import _results_client, no_ollama  # noqa: F401 (autouse fixture)

ITC_RAW = {
    "No.": "7", "Company/Organisation Name": "RightPick Technology Limited", "Company Website": "rightpickhk.com",
    "Job Title": "AI Intern", "Working location": "Hong Kong",
    "Application Procedures": "Interested students should submit your cv directly to rp@rightpickhk.com",
    "Email for Submission of Application": "rp@rightpickhk.com", "Application Deadline": "30/11/2026", "_sheet": "Job List",
}
AIMS_RAW = {"fields": {"Recruiting Dept/Unit": "MA -- Mathematics", "Contact Person": "Prof. ZHUANG", "Supervisor": "Prof. ZHUANG",
                       "Computer Skills": "Python", "Other Skills": "N/A"},
            "sections": {"Detail Job Description": "Implement fast transforms."}, "learning_objectives": ["Learn spherical designs"]}


def itc_job():
    return make_job("itc:cc:abc", source="itc_stem", scheme="ITC STEM", title="AI Intern", raw=ITC_RAW, closing_date=date(2026, 11, 30))


def aims_job():
    return make_job("aims:N26MA004", raw=AIMS_RAW, closing_date=date(2026, 11, 2))


class LLM:
    """Claude stand-in that answers fit and letter prompts."""

    llm_model = "claude:test"
    batch_size = 8
    max_parallel = 2

    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, schema=None):
        self.calls.append(user)
        if "fits" in (schema or {}).get("properties", {}):
            ids = [l.split(": ", 1)[1] for l in user.splitlines() if l.startswith("### id: ")]
            return {"fits": [{"id": i, "difficulty": 4, "verdict": "not eligible", "eligible": False,
                              "blockers": ["PhD students only"], "matched": ["Python"], "missing": ["C++"], "tip": "Apply next year."} for i in ids]}
        return {"subject": "Application: AI Intern – Nur", "letter": "Dear Hiring Team,\n" + "I built a RAG system. " * 20,
                "email": "Hello, please find my CV attached.", "answers": [{"question": "Why us?", "answer": "Because of your AI focus."}]}


def profile(**kw) -> Profile:
    return Profile(name="Nur", email="nur@example.com", education="BSc CS, Year 1", skills="Python", **kw)


# --- Apply info ---------------------------------------------------------------------


def test_itc_apply_info_has_employer_email_and_steps():
    info = apply_info(itc_job())
    assert info.employer == "RightPick Technology Limited" and info.method == "email"
    assert info.email == "rp@rightpickhk.com" and info.website == "https://rightpickhk.com"
    assert any("submit your cv" in s for s in info.steps) and any("30 Nov 2026" in s for s in info.steps)
    labels = [label for label, _ in info.facts]
    assert "Application Procedures" in labels and "_sheet" not in labels


def test_aims_apply_info_uses_job_number_and_contact_but_no_aims_link():
    info = apply_info(aims_job())
    assert info.employer == "CityU · Mathematics" and info.method == "aims" and info.contact == "Prof. ZHUANG"
    assert any("N26MA004" in s for s in info.steps)
    assert not any("banweb" in s for s in info.steps)  # never link into AIMS (owner rule)
    labels = dict(facts(aims_job()))
    assert labels["Computer Skills"] == "Python" and "Other Skills" not in labels  # "N/A" dropped
    assert "Learn spherical designs" in labels["Learning objectives"]


def test_mojibake_fixed_in_details():
    from cityu_jobs.core.itc_stem_scraper import _cell_text

    assert _cell_text("ICT Award 2023 €“ Smart onÂ\u00a0open") == "ICT Award 2023 – Smart on open"
    job = make_job("itc:cc:m", source="itc_stem", raw={"Job Description": "based onÂ\u00a0open-source â€” fast"})
    assert dict(facts(job))["Job Description"] == "based on open-source — fast"


def test_internship_apply_info_points_to_posting():
    job = make_job("intern:gh:x:1", source="intern_tech", company="Stripe", url="https://x/1", closing_date=None,
                   raw={"company": "Stripe", "sponsorship": "Offers Sponsorship"})
    info = apply_info(job)
    assert employer(job) == "Stripe" and info.url == "https://x/1"
    assert any("Sponsorship" in s for s in info.steps) and any("apply early" in s for s in info.steps)


# --- Profile and CV extraction ------------------------------------------------------------


def test_profile_saved_private_and_fingerprint_changes():
    p = profile()
    p.save()
    assert stat.S_IMODE(config.PROFILE_FILE.stat().st_mode) == 0o600
    loaded = Profile.load()
    assert loaded.name == "Nur" and not loaded.is_empty()
    before = loaded.fingerprint()
    loaded.projects = "RAG chatbot"
    assert loaded.fingerprint() != before


def test_cv_extraction_tex_docx_txt_and_errors():
    tex = r"""\documentclass{article}\usepackage{x}\begin{document}
    \section{Education} \textbf{City University of Hong Kong} -- BSc CS % secret comment
    \begin{itemize}\item Built a \href{https://github.com/n}{RAG system} in \emph{Python}\end{itemize}\end{document}"""
    text = extract_cv_text("cv.tex", tex.encode())
    assert "City University of Hong Kong" in text and "RAG system (https://github.com/n)" in text
    assert "secret" not in text and "\\" not in text and "usepackage" not in text

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>Nurassyl Serikov &amp; co</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>Python, PyTorch, FastAPI and more skills here</w:t></w:r></w:p></w:body></w:document>')
    assert "Nurassyl Serikov & co\nPython" in extract_cv_text("cv.docx", buffer.getvalue())
    assert extract_cv_text("cv.txt", b"Plain text CV with enough characters to count as a CV.").startswith("Plain")
    with pytest.raises(CVError, match="unsupported"):
        extract_cv_text("cv.exe", b"x" * 100)
    with pytest.raises(CVError, match="no text"):
        extract_cv_text("cv.txt", b"   ")


# --- Difficulty for you -----------------------------------------------------------------


def test_valid_fit_clamps_and_normalises():
    assert _valid({"difficulty": 9, "verdict": "x", "eligible": True, "tip": ""}) is None
    fit = _valid({"difficulty": "2", "verdict": "weird", "eligible": True, "tip": "t", "missing": ["a", "b", "c", "d", "e"]})
    assert fit["difficulty"] == 2 and fit["verdict"] == "good match" and len(fit["missing"]) == 4
    assert _valid({"difficulty": 5, "verdict": "long shot", "eligible": False, "tip": ""})["eligible"] is False


def test_assess_fit_caches_per_profile(cache):
    jobs = [make_job(f"aims:{i}") for i in range(3)]
    for j in jobs:
        cache.upsert_job(j)
    llm = LLM()
    fits = assess_fit(jobs, cache, llm, profile())
    assert fits["aims:0"]["blockers"] == ["PhD students only"] and len(llm.calls) == 1
    assert "BSc CS, Year 1" in llm.calls[0]  # the profile is in the prompt
    assess_fit(jobs, cache, llm, profile())
    assert len(llm.calls) == 1  # cached
    assess_fit(jobs, cache, llm, profile(projects="new project"))
    assert len(llm.calls) == 2  # profile changed -> redone


def test_assess_fit_needs_claude_and_a_profile(cache):
    class Local(LLM):
        llm_model = "llama3.2:3b"

    assert not can_assess(Local()) and can_assess(LLM())
    assert assess_fit([make_job()], cache, Local(), profile()) == {}
    assert assess_fit([make_job()], cache, LLM(), Profile()) == {}


def test_fit_candidates_cover_every_group():
    ranked = [RankedJob(make_job(f"aims:{i}"), 1 - i / 100, {}, None, None) for i in range(40)]
    ranked += [RankedJob(make_job(f"intern:x:{i}", source="intern_bigtech"), 0.1, {}, None, None) for i in range(5)]
    picked = fit_candidates(ranked, top_n=10)
    assert len(picked) == 15 and picked[0].job.job_id == "aims:0"
    assert sum(r.job.source == "intern_bigtech" for r in picked) == 5


# --- Letters and tracker -------------------------------------------------------------------


def test_write_letter_uses_profile_and_saves():
    job = itc_job()
    letter = write_letter(job, apply_info(job), profile(), LLM(), notes="I can start in June")
    assert letter["employer"] == "RightPick Technology Limited" and letter["answers"][0]["question"] == "Why us?"
    assert load_letter(job.job_id)["letter"].startswith("Dear Hiring Team")


def test_tracker_set_and_clear():
    job = itc_job()
    set_status(job, "applied")
    assert load_tracker()[job.job_id]["status"] == "applied"
    set_status(job, "")
    assert job.job_id not in load_tracker()
    with pytest.raises(ValueError):
        set_status(job, "hired")


# --- Web: job page, profile, ITC --------------------------------------------------------------


def _ranked(job, fit=None):
    r = RankedJob(job, 0.6, {"similarity": 0.3, "level": 0.1}, 0.7, 4, "Reason.")
    r.fit = fit
    return r


FIT = {"difficulty": 5, "verdict": "not eligible", "eligible": False, "blockers": ["PhD only"], "matched": [], "missing": ["C++"], "tip": "t", "model": "claude:test"}


def test_results_show_employer_fit_and_hide_ineligible():
    client = _results_client(_ranked(itc_job(), FIT), _ranked(aims_job()))
    html = client.get("/results").text
    assert "RightPick Technology Limited" in html and "CityU · Mathematics" in html
    assert "Not eligible" in html and "PhD only" in html and 'href="/job/itc:cc:abc"' in html
    hidden = client.get("/results?eligible=1").text.split('<ol class="results">')[1]
    assert "AI Intern" not in hidden and "Research assistant" in hidden


def test_job_page_shows_how_to_apply_details_and_mailto():
    client = _results_client(_ranked(itc_job(), FIT))
    html = client.get("/job/itc:cc:abc").text
    assert "How to apply" in html and "submit your cv directly" in html
    assert 'href="mailto:rp@rightpickhk.com?subject=' in html
    assert "Application Procedures" in html and 'href="/itc#itc-row-7"' in html
    assert "PhD only" in html


def test_letter_route_writes_with_profile(monkeypatch):
    profile().save()
    llm = LLM()
    monkeypatch.setattr(web_app.extra_routes.Settings, "provider", lambda self: llm)
    client = _results_client(_ranked(itc_job()))
    response = client.post("/job/itc:cc:abc/letter", data={"notes": "short"}, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "written=1" in response.headers["location"]
    page = client.get("/job/itc:cc:abc").text
    assert "Dear Hiring Team" in page and "Hello, please find my CV attached." in page
    assert client.get("/job/itc:cc:abc/letter.txt").text.startswith("Dear Hiring Team")


def test_letter_route_without_profile_explains():
    client = _results_client(_ranked(itc_job()))
    response = client.post("/job/itc:cc:abc/letter", data={}, headers=ORIGIN, follow_redirects=False)
    assert "Profile" in response.headers["location"]


def test_status_route_and_unknown_job_404():
    client = _results_client(_ranked(itc_job()))
    client.post("/job/itc:cc:abc/status", data={"status": "saved"}, headers=ORIGIN)
    assert load_tracker()["itc:cc:abc"]["status"] == "saved"
    assert "Saved" in client.get("/results").text
    assert client.get("/job/aims:NOPE").status_code == 404


def test_cross_site_posts_refused_on_new_routes():
    client = _results_client(_ranked(itc_job()))
    evil = {"origin": "http://evil.example"}
    for path in ["/profile", "/profile/cv", "/profile/clear", "/itc/upload", "/job/itc:cc:abc/letter", "/job/itc:cc:abc/status"]:
        assert client.post(path, data={"name": "x"}, headers=evil).status_code == 403, path


def test_profile_page_cv_upload_and_save():
    client = TestClient(web_app.create_app(TaskRunner()))
    files = {"cv": ("cv.txt", b"Nurassyl Serikov, BSc Computer Science at CityU, Python and PyTorch.", "text/plain")}
    response = client.post("/profile/cv", files=files, headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and "cv=" in response.headers["location"]
    assert "PyTorch" in Profile.load().cv_text
    client.post("/profile", data={"name": "Nur", "projects": "RAG chatbot"}, headers=ORIGIN)
    assert (Profile.load().name, Profile.load().projects) == ("Nur", "RAG chatbot")
    page = client.get("/profile").text
    assert "RAG chatbot" in page and "Anthropic" in page
    bad = client.post("/profile/cv", files={"cv": ("cv.exe", b"x" * 50, "application/octet-stream")}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Job List"
    ws.append(["No.", "Company/Organisation Name", "Job Title", "Job Description", "Application Deadline", "Email for Submission of Application"])
    ws.append([1, "Acme AI", "ML Intern", "Train models", "30/11/2026", "jobs@acme.ai"])
    ws.append([2, "Beta", "Web Intern", "Build pages", "01/12/2026", "hr@beta.hk"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_itc_upload_view_and_download():
    client = TestClient(web_app.create_app(TaskRunner()))
    assert "No list yet" in client.get("/itc").text
    files = {"file": ("2026.11.01 Job List.xlsx", _xlsx(), "application/octet-stream")}
    response = client.post("/itc/upload", files=files, headers=ORIGIN, follow_redirects=False)
    assert response.headers["location"] == "/itc?uploaded=2"
    page = client.get("/itc").text
    assert "Acme AI" in page and 'id="itc-row-1"' in page and 'href="mailto:jobs@acme.ai"' in page and "in use" in page
    download = client.get("/itc/file/2026.11.01 Job List.xlsx")
    assert download.status_code == 200 and download.content.startswith(b"PK")
    assert client.get("/itc/file/..%2F..%2Fsettings.json").status_code == 404  # only files in .data/itc/
    bad = client.post("/itc/upload", files={"file": ("x.xlsx", b"not a zip", "application/octet-stream")}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_new_itc_file_makes_itc_stale(cache, tmp_path):
    from cityu_jobs.core import pipeline

    cache.mark_scrape_complete("itc_stem", set())
    assert not pipeline._new_itc_file(cache)
    config.ITC_DIR.mkdir(parents=True, exist_ok=True)
    (config.ITC_DIR / "new.xlsx").write_bytes(_xlsx())
    assert pipeline._new_itc_file(cache)
