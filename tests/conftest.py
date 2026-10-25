"""Shared test helpers: hand-made jobs, a throwaway cache, and saved-page fixtures.

Every test here is offline: no AIMS, no Ollama, no network. Real AIMS pages contain
personal data, so they live in the git-ignored .data/fixtures/ and tests that need them
skip when they're missing.
"""

from datetime import date
from pathlib import Path

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


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    """An empty cache whose file lives in a temporary directory."""
    return Cache(tmp_path / "cache.json")
