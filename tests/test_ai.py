"""T16 (part 1): Ollama HTTP shapes, FakeProvider, level rating and embeddings.

OllamaProvider is tested against `httpx.MockTransport`, a fake server inside httpx that
lets us check the exact request we send and feed back any response, with no Ollama running.
"""

import json

import httpx
import pytest

from cityu_jobs.core.ai.providers import FakeProvider, InvalidJSONResponse, OllamaProvider, ProviderError
from cityu_jobs.core.analysis import analyze_jobs, rate_level
from cityu_jobs.core.embeddings import cosine, embed_jobs, embed_query, similarities

from conftest import make_job

# --- OllamaProvider ---------------------------------------------------------------


def _ollama(handler, embed_model="nomic-embed-text") -> tuple[OllamaProvider, list[httpx.Request]]:
    """An OllamaProvider whose HTTP calls go to `handler`; also returns the requests it saw."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(record))
    return OllamaProvider("http://ollama.test/", "llama3.2:3b", embed_model, client=client), seen


def test_complete_json_request_shape_and_parsing():
    provider, seen = _ollama(
        lambda r: httpx.Response(200, json={"message": {"content": '{"level": 4, "reason": "ok"}'}})
    )
    assert provider.complete_json("SYS", "USER") == {"level": 4, "reason": "ok"}

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert body["model"] == "llama3.2:3b"
    assert body["format"] == "json"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"just a string"'])
def test_complete_json_rejects_non_objects(content):
    provider, _ = _ollama(lambda r: httpx.Response(200, json={"message": {"content": content}}))
    with pytest.raises(InvalidJSONResponse):
        provider.complete_json("s", "u")


def test_embed_adds_nomic_prefixes():
    provider, seen = _ollama(lambda r: httpx.Response(200, json={"embeddings": [[1.0], [2.0]]}))
    assert provider.embed(["a", "b"]) == [[1.0], [2.0]]
    provider.embed(["q"], kind="query")

    assert seen[0].url.path == "/api/embed"
    assert json.loads(seen[0].content) == {
        "model": "nomic-embed-text",
        "input": ["search_document: a", "search_document: b"],
    }
    assert json.loads(seen[1].content)["input"] == ["search_query: q"]


def test_embed_no_prefix_for_other_models():
    provider, seen = _ollama(lambda r: httpx.Response(200, json={"embeddings": [[1.0]]}), embed_model="mxbai")
    provider.embed(["a"], kind="query")
    assert json.loads(seen[0].content)["input"] == ["a"]


def test_connection_error_says_ollama_not_running():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    provider, _ = _ollama(refuse)
    with pytest.raises(ProviderError, match="not running"):
        provider.complete_json("s", "u")


def test_missing_model_says_ollama_pull():
    provider, _ = _ollama(lambda r: httpx.Response(404, json={"error": "model 'llama3.2:3b' not found"}))
    with pytest.raises(ProviderError, match="ollama pull llama3.2:3b"):
        provider.complete_json("s", "u")


def test_http_error_is_provider_error_not_invalid_json():
    provider, _ = _ollama(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError) as info:
        provider.embed(["a"])
    assert not isinstance(info.value, InvalidJSONResponse)  # must not be retried as bad JSON


def test_timeout_is_provider_error():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    provider, _ = _ollama(slow)
    with pytest.raises(ProviderError, match="did not answer"):
        provider.complete_json("s", "u")


# --- Level rating -------------------------------------------------------------------


def test_rate_level_valid_answer():
    assert rate_level(make_job(), FakeProvider(level=4)) == (4, "Fake rating.")


def test_rate_level_retries_once_after_invalid_json():
    llm = FakeProvider(responses=["garbage", {"level": 2, "reason": "simple"}])
    assert rate_level(make_job(), llm) == (2, "simple")
    assert llm.llm_calls == 2


def test_rate_level_gives_up_after_two_failures():
    llm = FakeProvider(responses=["garbage", "garbage again", {"level": 5, "reason": "too late"}])
    level, reason = rate_level(make_job(), llm)
    assert level is None
    assert reason.startswith("Unrated")
    assert llm.llm_calls == 2


@pytest.mark.parametrize("bad", [{"level": 0}, {"level": 6}, {"level": True}, {"level": 3.5}, {"reason": "no level"}, {"level": "high"}])
def test_rate_level_rejects_out_of_range_or_wrong_type(bad):
    llm = FakeProvider(responses=[bad, bad])
    assert rate_level(make_job(), llm)[0] is None
    assert llm.llm_calls == 2


def test_rate_level_accepts_numeric_string():
    assert rate_level(make_job(), FakeProvider(responses=[{"level": " 4 ", "reason": "r"}]))[0] == 4


def test_rate_level_does_not_retry_connection_errors():
    class Down:
        llm_model = "down"
        calls = 0

        def complete_json(self, system, user):
            self.calls += 1
            raise ProviderError("Ollama is not running")

    llm = Down()
    with pytest.raises(ProviderError):
        rate_level(make_job(), llm)
    assert llm.calls == 1


def test_analyze_jobs_reuses_cached_levels(cache):
    jobs = [make_job("aims:A"), make_job("aims:B")]
    for job in jobs:
        cache.upsert_job(job)
    llm = FakeProvider(level=3)

    assert analyze_jobs(jobs, cache, llm) == {"aims:A": 3, "aims:B": 3}
    assert llm.llm_calls == 2
    assert analyze_jobs(jobs, cache, llm) == {"aims:A": 3, "aims:B": 3}
    assert llm.llm_calls == 2  # second run: all cached
    assert cache.get_level("aims:A", jobs[0].content_hash)["model"] == "fake-llm"


def test_analyze_jobs_rerates_edited_and_failed_jobs(cache):
    job = make_job("aims:A")
    cache.upsert_job(job)
    analyze_jobs([job], cache, FakeProvider(responses=["bad", "bad"]))
    assert cache.get_level(job.job_id, job.content_hash)["value"] is None

    llm = FakeProvider(level=5)
    assert analyze_jobs([job], cache, llm) == {"aims:A": 5}  # earlier failure retried
    assert llm.llm_calls == 1

    edited = make_job("aims:A", raw={"edited": True})
    cache.upsert_job(edited)
    llm = FakeProvider(level=2)
    assert analyze_jobs([edited], cache, llm) == {"aims:A": 2}  # new content -> new rating
    assert llm.llm_calls == 1


def test_analyze_jobs_reports_progress(cache):
    jobs = [make_job(f"aims:{i}") for i in range(3)]
    for job in jobs:
        cache.upsert_job(job)
    calls = []
    analyze_jobs(jobs, cache, FakeProvider(), progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 3), (2, 3), (3, 3)]


# --- Embeddings ---------------------------------------------------------------------


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([0, 0], [1, 1]) == 0.0  # zero vector -> 0, not a crash
    assert cosine([3, 4], [6, 8]) == pytest.approx(1.0)  # length doesn't matter


def test_embed_jobs_batches_and_caches(cache):
    jobs = [make_job(f"aims:{i}") for i in range(20)]  # BATCH_SIZE is 16 -> 2 requests
    for job in jobs:
        cache.upsert_job(job)
    embedder = FakeProvider()

    vectors = embed_jobs(jobs, cache, embedder)
    assert set(vectors) == {j.job_id for j in jobs}
    assert embedder.embed_calls == 2

    embed_jobs(jobs, cache, embedder)
    assert embedder.embed_calls == 2  # all cached


def test_embed_jobs_recomputes_after_model_change(cache):
    job = make_job()
    cache.upsert_job(job)
    embed_jobs([job], cache, FakeProvider(dim=64))
    other = FakeProvider(dim=32)  # a different embed_model name
    vectors = embed_jobs([job], cache, other)
    assert other.embed_calls == 1
    assert len(vectors[job.job_id]) == 32


def test_query_closer_to_matching_job():
    embedder = FakeProvider()
    ml = make_job("aims:ML", title="Machine learning research", description="neural networks deep learning")
    office = make_job("aims:OF", title="Office helper", description="filing documents front desk")
    vectors = {j.job_id: embedder.embed([j.description + " " + j.title])[0] for j in (ml, office)}
    sims = similarities(embed_query("machine learning research", embedder), vectors)
    assert sims["aims:ML"] > sims["aims:OF"]


# --- Long texts vs the embedding model's context ---------------------------------------


def test_fit_context_counts_dense_scripts_as_more_tokens():
    from cityu_jobs.core.ai.providers import fit_context

    assert fit_context("a" * 100, max_tokens=10) == "a" * 40
    assert 9 <= len(fit_context("機" * 100, max_tokens=11)) <= 10
    assert fit_context("short") == "short"


def test_embed_falls_back_to_shorter_texts_on_context_errors():
    def handler(request):
        body = json.loads(request.content)
        if any(len(t) > 60 for t in body["input"]):
            return httpx.Response(400, json={"error": "the input length exceeds the context length"})
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0] for _ in body["input"]]})

    provider, seen = _ollama(handler, embed_model="other")
    vectors = provider.embed(["x" * 100, "short"])
    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert len(seen) == 4  # the batch, then text 1 (too long), text 1 halved, then text 2
