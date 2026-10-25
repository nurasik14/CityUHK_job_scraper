"""T6: the JSON cache: reuse, invalidation, inactive marking, staleness, pruning, atomic save."""

import json
from datetime import date, datetime, timedelta

import pytest

from cityu_jobs.core.cache import SCHEMA_VERSION, Cache

from conftest import make_job


def test_missing_file_starts_empty(tmp_path):
    cache = Cache.load(tmp_path / "nope.json")
    assert cache.active_jobs() == []


def test_old_schema_version_starts_empty(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "sources": {}, "jobs": {"x": {}}}))
    assert Cache.load(path).data["jobs"] == {}


def test_save_and_load_round_trip(cache):
    job = make_job()
    cache.upsert_job(job, summary_hash="s1")
    cache.set_level(job.job_id, 4, "why", "m", job.content_hash)
    cache.set_embedding(job.job_id, [0.1, 0.2], "e", job.content_hash)
    cache.save()

    loaded = Cache.load(cache.path)
    assert loaded.get_job(job.job_id) == job
    assert loaded.get_summary_hash(job.job_id) == "s1"
    assert loaded.get_level(job.job_id, job.content_hash)["value"] == 4
    assert loaded.get_embedding(job.job_id, job.content_hash, "e") == [0.1, 0.2]


def test_save_is_atomic_and_leaves_no_temp_files(cache):
    cache.upsert_job(make_job())
    cache.save()
    cache.save()
    files = [p.name for p in cache.path.parent.iterdir()]
    assert files == ["cache.json"]
    json.loads(cache.path.read_text())  # valid JSON


def test_failed_save_keeps_old_file(cache):
    cache.upsert_job(make_job())
    cache.save()
    before = cache.path.read_text()
    cache.data["jobs"]["aims:T001"]["bad"] = object()  # not JSON-serialisable
    with pytest.raises(TypeError):
        cache.save()
    assert cache.path.read_text() == before
    assert [p.name for p in cache.path.parent.iterdir()] == ["cache.json"]


# --- Invalidation ----------------------------------------------------------------


def test_level_invalidated_when_content_hash_changes(cache):
    job = make_job()
    cache.upsert_job(job)
    cache.set_level(job.job_id, 3, "r", "m", job.content_hash)

    edited = make_job(raw={"id": job.job_id, "edited": True})
    cache.upsert_job(edited)
    assert edited.content_hash != job.content_hash
    assert cache.get_level(edited.job_id, edited.content_hash) is None


def test_embedding_invalidated_when_content_hash_changes(cache):
    job = make_job()
    cache.upsert_job(job)
    cache.set_embedding(job.job_id, [1.0], "e", job.content_hash)
    edited = make_job(raw={"edited": True})
    assert cache.get_embedding(job.job_id, edited.content_hash, "e") is None


def test_embedding_invalidated_when_model_changes(cache):
    job = make_job()
    cache.upsert_job(job)
    cache.set_embedding(job.job_id, [1.0], "nomic-embed-text", job.content_hash)
    assert cache.get_embedding(job.job_id, job.content_hash, "nomic-embed-text") == [1.0]
    assert cache.get_embedding(job.job_id, job.content_hash, "other-model") is None


def test_level_kept_when_llm_model_changes(cache):
    """Delegated decision (architecture.md "AI level rating"): only the content hash invalidates a level."""
    job = make_job()
    cache.upsert_job(job)
    cache.set_level(job.job_id, 2, "r", "llama3.2:3b", job.content_hash)
    assert cache.get_level(job.job_id, job.content_hash)["model"] == "llama3.2:3b"


def test_upsert_keeps_first_seen_and_ai_results(cache):
    job = make_job()
    cache.upsert_job(job)
    first_seen = cache.data["jobs"][job.job_id]["first_seen"]
    cache.set_level(job.job_id, 5, "r", "m", job.content_hash)
    cache.upsert_job(job)
    assert cache.data["jobs"][job.job_id]["first_seen"] == first_seen
    assert cache.get_level(job.job_id, job.content_hash)["value"] == 5


def test_getters_for_unknown_job(cache):
    assert cache.get_job("x") is None
    assert cache.get_summary_hash("x") is None
    assert cache.get_level("x", "h") is None
    assert cache.get_embedding("x", "h", "m") is None


# --- Inactive marking and staleness -----------------------------------------------


def test_mark_scrape_complete_deactivates_unseen_jobs_of_that_source_only(cache):
    a, b = make_job("aims:A"), make_job("aims:B")
    itc = make_job("itc:cc:X", source="itc_stem")
    for job in (a, b, itc):
        cache.upsert_job(job)

    cache.mark_scrape_complete("aims_cis", {"aims:A"})
    assert {j.job_id for j in cache.active_jobs()} == {"aims:A", "itc:cc:X"}

    cache.mark_seen("aims:B")  # found again later
    assert "aims:B" in {j.job_id for j in cache.active_jobs()}


def test_is_stale(cache):
    hour = timedelta(hours=6)
    assert cache.is_stale("aims_cis", hour)  # never scraped
    cache.mark_scrape_complete("aims_cis", set())
    assert not cache.is_stale("aims_cis", hour)
    assert cache.is_stale("aims_cis", timedelta(0))  # 0 h = always stale

    old = datetime.now().astimezone() - timedelta(hours=7)
    cache.data["sources"]["aims_cis"]["last_scraped_at"] = old.isoformat()
    assert cache.is_stale("aims_cis", hour)
    assert cache.last_scraped_at("aims_cis") == old


# --- Retention --------------------------------------------------------------------


def test_prune_removes_jobs_closed_over_30_days_ago(cache):
    today = date(2026, 12, 1)
    cache.upsert_job(make_job("aims:OLD", closing_date=today - timedelta(days=31)))
    cache.upsert_job(make_job("aims:EDGE", closing_date=today - timedelta(days=30)))
    cache.upsert_job(make_job("aims:OPEN", closing_date=today + timedelta(days=5)))

    assert cache.prune(today) == 1
    assert set(cache.data["jobs"]) == {"aims:EDGE", "aims:OPEN"}


def test_prune_uses_last_seen_without_closing_date(cache):
    cache.upsert_job(make_job("itc:cc:A", closing_date=None))
    cache.upsert_job(make_job("itc:cc:B", closing_date=None))
    long_ago = datetime.now().astimezone() - timedelta(days=40)
    cache.data["jobs"]["itc:cc:A"]["last_seen"] = long_ago.isoformat()

    assert cache.prune(date.today()) == 1
    assert set(cache.data["jobs"]) == {"itc:cc:B"}
