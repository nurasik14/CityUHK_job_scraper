"""JSON cache of scraped jobs, AI levels and embeddings, keyed by job ID.

Layout of the file (see architecture.md "Cache"):

    {"schema_version": 1,
     "sources": {"aims_cis": {"last_scraped_at": "<ISO time>"}},
     "jobs": {"aims:N26MA004": {"job": {...}, "content_hash": "...", "summary_hash": "...",
                                "active": true, "first_seen": "...", "last_seen": "...",
                                "level": {...}, "embedding": {...}}}}

Invalidation rule: a stored level or embedding remembers the content hash (and, for
embeddings, the model) it was computed from. If the job's text changes, its hash
changes, so the old result simply stops matching and gets recomputed. Nothing has to
be deleted by hand.
"""

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from cityu_jobs import config
from cityu_jobs.core.models import Job

SCHEMA_VERSION = 1


def _now() -> datetime:
    return datetime.now().astimezone()  # local time, with the UTC offset included


class Cache:
    """In-memory view of the cache file. Call `save()` to write changes back."""

    def __init__(self, path: Path, data: dict | None = None):
        self.path = path
        self.data = data or {"schema_version": SCHEMA_VERSION, "sources": {}, "jobs": {}}

    @classmethod
    def load(cls, path: Path = config.CACHE_FILE) -> "Cache":
        """Read the cache file; a missing or outdated file starts an empty cache."""
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            return cls(path)  # old layout: cheaper to re-scrape than to migrate
        return cls(path, data)

    def save(self) -> None:
        """Write atomically: a crash mid-write leaves the old file intact, never half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cache-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)  # atomic rename on the same filesystem
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # --- Jobs -----------------------------------------------------------------

    @property
    def _jobs(self) -> dict:
        return self.data["jobs"]

    def upsert_job(self, job: Job, summary_hash: str | None = None) -> None:
        """Insert or update a scraped job and mark it as seen now.

        Cached level/embedding entries are kept; if `job.content_hash` changed they no
        longer match and the getters below return None.
        """
        now = _now().isoformat()
        entry = self._jobs.setdefault(job.job_id, {"first_seen": now})
        entry.update(job=job.to_dict(), content_hash=job.content_hash, active=True, last_seen=now)
        if summary_hash is not None:
            entry["summary_hash"] = summary_hash

    def mark_seen(self, job_id: str) -> None:
        """A job was found again but not re-opened (its summary row was unchanged)."""
        entry = self._jobs[job_id]
        entry.update(active=True, last_seen=_now().isoformat())

    def get_job(self, job_id: str) -> Job | None:
        entry = self._jobs.get(job_id)
        return Job.from_dict(entry["job"]) if entry else None

    def get_summary_hash(self, job_id: str) -> str | None:
        return self._jobs.get(job_id, {}).get("summary_hash")

    def active_jobs(self) -> list[Job]:
        """Jobs present in the latest successful scrape of their source."""
        return [Job.from_dict(e["job"]) for e in self._jobs.values() if e.get("active")]

    # --- AI results -----------------------------------------------------------

    def get_level(self, job_id: str, content_hash: str) -> dict | None:
        """Cached {"value", "reason", "model", "content_hash"[, "skills", "dates"]}, only if computed from this content."""
        level = self._jobs.get(job_id, {}).get("level")
        return level if level and level.get("content_hash") == content_hash else None

    def set_level(
        self, job_id: str, value: int | None, reason: str, model: str, content_hash: str, extra: dict | None = None
    ) -> None:
        """Store an AI level. `extra` holds what strong models also read out: "skills", "dates"."""
        self._jobs[job_id]["level"] = {
            "value": value,
            "reason": reason,
            "model": model,
            "content_hash": content_hash,
            **(extra or {}),
        }

    def get_embedding(self, job_id: str, content_hash: str, model: str) -> list[float] | None:
        """Cached vector, only if made from this content *and* by this embedding model.

        Vectors from different models live in different spaces, so comparing them is meaningless.
        """
        emb = self._jobs.get(job_id, {}).get("embedding")
        if emb and emb.get("content_hash") == content_hash and emb.get("model") == model:
            return emb["vector"]
        return None

    def set_embedding(self, job_id: str, vector: list[float], model: str, content_hash: str) -> None:
        self._jobs[job_id]["embedding"] = {"model": model, "content_hash": content_hash, "vector": vector}

    def get_fit(self, job_id: str, content_hash: str, profile: str) -> dict | None:
        """Cached "difficulty for you", only if made from this listing *and* this profile."""
        fit = self._jobs.get(job_id, {}).get("fit")
        if fit and fit.get("content_hash") == content_hash and fit.get("profile") == profile:
            return fit
        return None

    def set_fit(self, job_id: str, fit: dict, model: str, content_hash: str, profile: str) -> None:
        self._jobs[job_id]["fit"] = {**fit, "model": model, "content_hash": content_hash, "profile": profile}

    # --- Sources and retention ------------------------------------------------

    def mark_scrape_complete(self, source: str, seen_ids: set[str]) -> None:
        """After a *full* scrape of `source`: its jobs not seen this time become inactive.

        Only call this when the scrape finished; a half-finished scrape would wrongly
        deactivate every job it didn't reach.
        """
        for job_id, entry in self._jobs.items():
            if entry["job"]["source"] == source:
                entry["active"] = job_id in seen_ids
        self.data["sources"].setdefault(source, {})["last_scraped_at"] = _now().isoformat()

    def last_scraped_at(self, source: str) -> datetime | None:
        value = self.data["sources"].get(source, {}).get("last_scraped_at")
        return datetime.fromisoformat(value) if value else None

    def is_stale(self, source: str, max_age: timedelta) -> bool:
        """True if `source` was never scraped, or its last successful scrape is older than `max_age`."""
        last = self.last_scraped_at(source)
        return last is None or _now() - last >= max_age

    def prune(self, today: date, retention_days: int = config.CACHE_RETENTION_DAYS) -> int:
        """Delete jobs closed (or, without a closing date, last seen) over `retention_days` ago."""
        cutoff = today - timedelta(days=retention_days)
        expired = []
        for job_id, entry in self._jobs.items():
            closing = entry["job"].get("closing_date")
            end = date.fromisoformat(closing) if closing else datetime.fromisoformat(entry["last_seen"]).date()
            if end < cutoff:
                expired.append(job_id)
        for job_id in expired:
            del self._jobs[job_id]
        return len(expired)
