"""Your gaps: evidence from the target jobs (code) and the weakness summary (AI)."""

import pytest
from fastapi.testclient import TestClient

from cityu_jobs import config
from cityu_jobs.core.gaps import GapsError, canonical, collect, evidence_text, in_profile, load_reports, summarize
from cityu_jobs.core.profile import Profile
from cityu_jobs.core.ranking import RankedJob
from cityu_jobs.web import app as web_app
from cityu_jobs.web import extra_routes
from cityu_jobs.web.tasks import TaskRunner

from conftest import make_job
from test_web import ORIGIN, tmp_settings  # noqa: F401 (autouse fixture)
from test_web_redesign import no_ollama  # noqa: F401 (autouse fixture)

PROFILE = Profile(name="Nur", education="BSc CS, Year 1", skills="Python, PyTorch, FastAPI, TypeScript",
                  projects="RAG chatbot with Ollama embeddings")


def fit(verdict="stretch", missing=(), blockers=()):
    return {"difficulty": 4, "verdict": verdict, "eligible": verdict != "not eligible",
            "blockers": list(blockers), "matched": [], "missing": list(missing), "tip": ""}


def rows():
    a = make_job("intern:gh:a:1", source="intern_tech", title="ML Intern", company="Acme", raw={"a": 1})
    b = make_job("intern:gh:b:2", source="intern_tech", title="Quant Intern", company="Beta", raw={"b": 2})
    c = make_job("aims:C3", title="Web helper", raw={"c": 3})
    return [
        (a, {"skills": ["Python", "C++", "LLMs"]}, fit(missing=["C++ performance work"])),
        (b, {"skills": ["C++", "Statistics", "ML"]}, fit("not eligible", ["C++"], ["Penultimate-year students only"])),
        (c, {"skills": ["JavaScript", "React"]}, fit("good match")),
    ]


class LLM:
    llm_model = "claude:test"

    def __init__(self, answer=None):
        self.prompts = []
        self.answer = answer

    def complete_json(self, system, user, schema=None):
        self.prompts.append(user)
        return self.answer or {
            "headline": "Strong on Python and LLM apps; C++ and maths depth are the gaps.",
            "weaknesses": [
                {"area": "Low-level / C++", "kind": "skill", "severity": "medium", "jobs_affected": 2,
                 "jobs": ["J2", "J1", "J99", "x"], "why": "Two jobs ask for C++.", "fix": ["Build a small C++ project"], "effort": "weeks"},
                {"area": "Year of study", "kind": "eligibility", "severity": "high", "jobs_affected": 50,
                 "jobs": ["J2"], "why": "Penultimate-year only.", "fix": ["Target first-year programmes like J1"], "effort": "months"},
                {"area": "", "kind": "skill", "severity": "low", "jobs_affected": 1, "why": "", "fix": ["x"], "effort": "days"},
            ],
            "strengths": [{"skill": "Python", "evidence": "RAG chatbot"}],
            "cv_fixes": ["Put 'LLM' in your skills line"], "later": ["Year 3 roles"],
            "plan": [{"when": "Weeks 1-2", "do": "C++ basics"}],
        }


# --- Evidence (code) -----------------------------------------------------------------------


def test_keyword_check_handles_variants_and_aliases():
    text = PROFILE.as_text()
    assert in_profile("PyTorch", text) and in_profile("LLM/RAG", text) and in_profile("ML", text)
    assert in_profile("JavaScript", text)  # TypeScript counts as evidence
    assert not in_profile("C++", text) and not in_profile("Kubernetes", text)
    assert in_profile("SQL", "Engineering: Docker, PostgreSQL")
    assert not in_profile("Go", "I love Google")  # whole words only
    assert canonical(" LLMs ") == "llm" and canonical("sklearn") == "scikit-learn"


def test_collect_counts_skills_and_gathers_missing_and_blockers():
    ev = collect(rows(), PROFILE)
    by_name = {d.skill: d for d in ev.demand}
    assert by_name["C++"].count == 2 and not by_name["C++"].in_cv
    assert by_name["Python"].in_cv and by_name["LLMs"].in_cv
    assert ev.demand[0].skill == "C++"  # most asked first
    assert ev.verdicts["not eligible"] == 1 and ev.has_fit
    assert ev.missing == [(1, ["C++ performance work"]), (2, ["C++"])]
    assert ev.blockers == [(2, ["Penultimate-year students only"])]
    text = evidence_text(ev)
    assert "C++: 2 jobs (NOT found in profile)" in text and "J2: Quant Intern · Beta" in text


def test_evidence_key_changes_with_jobs_and_profile():
    ev = collect(rows(), PROFILE)
    other_profile = Profile(skills="Go")
    assert ev.key(PROFILE) != ev.key(other_profile)
    assert ev.key(PROFILE) != collect(rows()[:2], PROFILE).key(PROFILE)


# --- Summary (AI, validated by code) ------------------------------------------------------


def test_summarize_validates_and_maps_job_numbers():
    ev = collect(rows(), PROFILE)
    llm = LLM()
    report = summarize(ev, PROFILE, llm)
    assert "BSc CS, Year 1" in llm.prompts[0] and "C++: 2 jobs" in llm.prompts[0]
    areas = [w["area"] for w in report["weaknesses"]]
    assert areas == ["Year of study", "Low-level / C++"]  # empty area dropped; high severity first
    cpp = report["weaknesses"][1]
    assert [j["id"] for j in cpp["jobs"]] == ["intern:gh:b:2", "intern:gh:a:1"]  # J99 and "x" dropped
    assert cpp["jobs"][0]["title"] == "Quant Intern"
    assert report["weaknesses"][0]["jobs_affected"] == 3  # clamped to the jobs analysed
    assert report["jobs_analysed"] == 3 and report["model"] == "claude:test"
    assert set(report["refs"]) == {"J1", "J2"}  # J99 is not a real job


def test_link_jobs_escapes_and_links():
    from cityu_jobs.web.app import link_jobs

    refs = {"J2": {"id": "intern:gh:b:2", "title": "Quant <Intern>", "employer": "Beta"}}
    html = str(link_jobs("Apply to J2, not J7 <b>", refs))
    assert '<a href="/job/intern:gh:b:2">Quant &lt;Intern&gt; · Beta</a>' in html
    assert "J7" in html and "&lt;b&gt;" in html


def test_summarize_errors():
    with pytest.raises(GapsError, match="search"):
        summarize(collect([], PROFILE), PROFILE, LLM())
    with pytest.raises(GapsError, match="no usable"):
        summarize(collect(rows(), PROFILE), PROFILE, LLM({"headline": "x", "weaknesses": []}))


# --- Web ------------------------------------------------------------------------------------


def _client(monkeypatch, provider=None):
    from cityu_jobs.core.cache import Cache

    PROFILE.save()
    cache = Cache(config.DATA_DIR / "unused.json")
    runner = TaskRunner()
    for job, level, f in rows():
        cache.upsert_job(job)
        cache.set_level(job.job_id, 3, "r", "claude:test", job.content_hash, {"skills": level["skills"]})
        cache.set_fit(job.job_id, f, "claude:test", job.content_hash, PROFILE.fingerprint())
        runner.state.results.append(RankedJob(job, 0.5, {}, 0.5, 3, "r"))
    monkeypatch.setattr(extra_routes.Cache, "load", classmethod(lambda cls, *a, **k: cache))
    if provider is not None:
        monkeypatch.setattr(extra_routes.Settings, "provider", lambda self: provider)
    return TestClient(web_app.create_app(runner))


def test_gaps_page_shows_evidence_before_any_summary(monkeypatch):
    html = _client(monkeypatch).get("/gaps").text
    assert "Skills these jobs ask for" in html and "Not in CV" in html and "C++" in html
    assert "Summarise my weaknesses" in html
    tech = _client(monkeypatch).get("/gaps?scope=cityu").text
    assert "React" in tech and "C++" not in tech.split("Skills these jobs ask for")[1].split("</ul>")[0]


def test_gaps_summary_route_saves_and_renders(monkeypatch):
    llm = LLM()
    client = _client(monkeypatch, llm)
    response = client.post("/gaps?scope=all", headers=ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("#report")
    assert load_reports()["all"]["weaknesses"][0]["area"] == "Year of study"
    html = client.get("/gaps").text
    assert "Strong on Python and LLM apps" in html and "Low-level / C++" in html
    assert 'href="/job/intern:gh:b:2"' in html and "Refresh the summary" in html
    assert 'programmes like <a href="/job/intern:gh:a:1">ML Intern · Acme</a>' in html
    assert "changed since this summary" not in html
    Profile(skills="Go only").save()  # profile changed -> report marked out of date
    assert "changed since this summary" in client.get("/gaps").text


def test_gaps_summary_needs_claude_and_same_origin(monkeypatch):
    class Local(LLM):
        llm_model = "llama3.2:3b"

    client = _client(monkeypatch, Local())
    response = client.post("/gaps", headers=ORIGIN, follow_redirects=False)
    assert "error=" in response.headers["location"] and not load_reports()
    assert client.post("/gaps", headers={"origin": "http://evil.example"}).status_code == 403
