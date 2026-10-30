"""Claude provider (through the `claude` CLI), batch rating and the re-rate rule. All offline:
the CLI is replaced by a fake `run` function, so no process is started and nothing is billed.
"""

import json
import subprocess

import pytest

from cityu_jobs.core.ai.providers import ClaudeCLIProvider, FakeProvider, HybridProvider, ProviderError
from cityu_jobs.core.analysis import BATCH_SCHEMA, _needs_rating, analyze_jobs, rate_batch

from conftest import make_job


class FakeRun:
    """Stands in for subprocess.run: records the call, returns a scripted CLI result."""

    def __init__(self, stdout="", stderr="", returncode=0, exc=None):
        self.stdout, self.stderr, self.returncode, self.exc = stdout, stderr, returncode, exc
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def envelope(**fields) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, **fields})


def test_command_strips_extras_and_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    run = FakeRun(stdout=envelope(structured_output={"ratings": []}))
    provider = ClaudeCLIProvider("haiku", run=run)
    assert provider.complete_json("SYS", "USER", schema=BATCH_SCHEMA) == {"ratings": []}

    command, kwargs = run.calls[0]
    assert command[:2] == ["claude", "-p"]
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in command
    assert command[command.index("--tools") + 1] == ""          # no tools at all
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--system-prompt") + 1] == "SYS"
    assert json.loads(command[command.index("--json-schema") + 1]) == BATCH_SCHEMA
    assert kwargs["input"] == "USER"                               # the prompt goes in on stdin
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]                # plan billing, never the API key
    assert provider.llm_model == "claude:haiku"


def test_result_text_parsed_when_no_structured_output():
    run = FakeRun(stdout=envelope(result='```json\n{"level": 4, "reason": "ok"}\n```'))
    assert ClaudeCLIProvider(run=run).complete_json("s", "u") == {"level": 4, "reason": "ok"}


@pytest.mark.parametrize(
    "run, message",
    [
        (FakeRun(exc=FileNotFoundError()), "not installed"),
        (FakeRun(exc=subprocess.TimeoutExpired("claude", 1)), "did not answer"),
        (FakeRun(stdout=json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"}), returncode=1), "not logged in"),
        (FakeRun(stdout=json.dumps({"is_error": True, "result": "Claude usage limit reached"}), returncode=1), "usage limit"),
        (FakeRun(stdout="garbage", returncode=2), "Claude failed"),
    ],
)
def test_errors_become_provider_errors(run, message):
    with pytest.raises(ProviderError, match=message):
        ClaudeCLIProvider(run=run).complete_json("s", "u")


class BatchLLM:
    """A strong-model stand-in: answers batch prompts with level 4 for every id it sees."""

    llm_model = "claude:test"
    batch_size = 3
    max_parallel = 2

    def __init__(self, drop_last=False):
        self.calls = 0
        self.drop_last = drop_last

    def complete_json(self, system, user, schema=None):
        self.calls += 1
        ids = [line.split(": ", 1)[1] for line in user.splitlines() if line.startswith("### id: ")]
        if self.drop_last:
            ids = ids[:-1]
        return {"ratings": [
            {"id": i, "level": 4, "reason": "Builds models.", "skills": ["Python", "PyTorch"],
             "start": "2027-06-01", "end": None, "weeks": 12, "deadline": "2026-11-30"} for i in ids
        ]}


def test_rate_batch_reads_levels_skills_dates():
    jobs = [make_job(f"aims:{i}") for i in range(3)]
    results = rate_batch(jobs, BatchLLM())
    level, reason, extra = results["aims:0"]
    assert (level, reason) == (4, "Builds models.")
    assert extra == {"skills": ["Python", "PyTorch"], "dates": {"start": "2027-06-01", "deadline": "2026-11-30", "weeks": 12}}


def test_rate_batch_missing_job_is_unrated():
    jobs = [make_job(f"aims:{i}") for i in range(3)]
    results = rate_batch(jobs, BatchLLM(drop_last=True))
    assert results["aims:2"][0] is None and results["aims:0"][0] == 4


def test_analyze_jobs_batches_and_stores_extras(cache):
    jobs = [make_job(f"aims:{i}") for i in range(7)]
    for j in jobs:
        cache.upsert_job(j)
    llm = BatchLLM()
    progress = []
    levels = analyze_jobs(jobs, cache, llm, progress=lambda done, total: progress.append((done, total)))
    assert set(levels.values()) == {4}
    assert llm.calls == 3  # 7 jobs, 3 per request
    assert progress[-1] == (7, 7)
    stored = cache.get_level("aims:0", jobs[0].content_hash)
    assert stored["model"] == "claude:test" and stored["skills"] == ["Python", "PyTorch"]
    assert analyze_jobs(jobs, cache, llm) and llm.calls == 3  # cached: no new requests


def test_local_levels_rerated_once_by_claude(cache):
    job = make_job()
    cache.upsert_job(job)
    cache.set_level(job.job_id, 2, "old", "llama3.2:3b", job.content_hash)
    assert _needs_rating(cache.get_level(job.job_id, job.content_hash), BatchLLM())
    assert not _needs_rating(cache.get_level(job.job_id, job.content_hash), FakeProvider())  # local -> local: keep
    analyze_jobs([job], cache, BatchLLM())
    assert cache.get_level(job.job_id, job.content_hash)["value"] == 4
    assert not _needs_rating(cache.get_level(job.job_id, job.content_hash), BatchLLM())


def test_hybrid_provider_splits_rating_and_embedding():
    fake = FakeProvider(level=5)
    hybrid = HybridProvider(BatchLLM(), fake)
    assert hybrid.llm_model == "claude:test" and hybrid.embed_model == fake.embed_model
    assert hybrid.batch_size == 3
    hybrid.embed(["a"])
    assert fake.embed_calls == 1
