"""Shared test helpers: hand-made jobs, a throwaway cache, and saved-page fixtures.

Every test here is offline: no AIMS, no Ollama, no network. Real AIMS pages contain
personal data, so they live in the git-ignored .data/fixtures/ and tests that need them
skip when they're missing.
"""

from datetime import date
from pathlib import Path

import httpx
import pytest

from cityu_jobs import config
from cityu_jobs.core.cache import Cache
from cityu_jobs.core.models import Job, compute_content_hash

FIXTURES = config.DATA_DIR / "fixtures"


def fixture_text(name: str) -> str:
    """Read a saved page from .data/fixtures/, or skip the test if it isn't there."""
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"missing fixture {path}")
    return path.read_text()


def make_job(job_id: str = "aims:T001", **overrides) -> Job:
    """A small open CIS job; pass keyword arguments to change any field.

    The content hash is computed from `raw`, so give a different `raw` to simulate an
    edited listing.
    """
    values = dict(
        job_id=job_id,
        source="aims_cis",
        scheme="Campus Internship Scheme",
        title="Research assistant",
        department_code="CS",
        department_name="Computer Science",
        description="Help with machine learning experiments.",
        weekly_hours=10.0,
        closing_date=date(2099, 1, 1),
        raw={"id": job_id},
    )
    values.update(overrides)
    job = Job(**values)
    job.content_hash = compute_content_hash(job.raw)
    return job


@pytest.fixture(autouse=True)
def _offline(tmp_path: Path, monkeypatch):
    """Every test: the real settings file is never read or written, and no real HTTP goes out.

    httpx.MockTransport (used by the provider and fetcher tests) is unaffected; only the
    real network transport refuses.
    """
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(config, "LETTERS_DIR", tmp_path / "letters")
    monkeypatch.setattr(config, "TRACKER_FILE", tmp_path / "applications.json")
    monkeypatch.setattr(config, "GAPS_FILE", tmp_path / "gaps.json")
    monkeypatch.setattr(config, "ITC_DIR", tmp_path / "itc")

    def no_network(self, request):
        raise AssertionError(f"test tried to reach the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    # The Settings page asks the `claude` CLI whether it is logged in; never run it in tests.
    from cityu_jobs.web import app as web_app

    monkeypatch.setattr(web_app, "claude_status", lambda: (True, "Logged in with your Claude plan (test)."))


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    """An empty cache whose file lives in a temporary directory."""
    return Cache(tmp_path / "cache.json")
