"""Small, swappable interfaces to AI models, using plain HTTP (httpx) instead of vendor SDKs.

Two jobs need AI:
- an LLM ("large language model") that reads a job and answers in JSON (its level),
- an embedding model that turns text into a vector of numbers, so that texts with similar
  meaning end up close together (used for semantic search).

Owner decision: Ollama is the only real provider, so models run locally and job text
never leaves this machine. The rest of the code only depends on the two Protocols below,
so Ollama and the FakeProvider used in tests can be swapped freely.
"""

import hashlib
import json
import math
import re
from typing import Literal, Protocol

import httpx

from cityu_jobs import config

EmbedKind = Literal["document", "query"]


class ProviderError(RuntimeError):
    """The AI service could not be reached or answered with an error."""


class InvalidJSONResponse(ProviderError):
    """The model answered, but not with a JSON object. Callers may retry."""


class LLMProvider(Protocol):
    llm_model: str

    def complete_json(self, system: str, user: str) -> dict:
        """Send a system + user prompt; return the model's answer parsed as a JSON object."""
        ...


class EmbeddingProvider(Protocol):
    embed_model: str

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        """One vector per text. `kind` says whether texts are jobs ("document") or a search ("query")."""
        ...


def _parse_json_object(content: str) -> dict:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        raise InvalidJSONResponse(f"model did not return valid JSON: {content[:200]!r}") from e
    if not isinstance(value, dict):
        raise InvalidJSONResponse(f"model returned JSON that is not an object: {content[:200]!r}")
    return value


def _nomic_prefix(model: str, kind: EmbedKind) -> str:
    """nomic-embed-text was trained with task prefixes and works noticeably better with them."""
    if "nomic" not in model.lower():
        return ""
    return "search_query: " if kind == "query" else "search_document: "


class OllamaProvider:
    """Local models served by Ollama (https://ollama.com). Free, and job text stays on this machine."""

    def __init__(
        self,
        base_url: str = config.OLLAMA_URL,
        llm_model: str = config.DEFAULT_LLM_MODEL,
        embed_model: str = config.DEFAULT_EMBED_MODEL,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.llm_model = llm_model
        self.embed_model = embed_model
        # `client` can be injected with a mock transport in tests.
        self._client = client or httpx.Client(base_url=self.base_url)

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        try:
            response = self._client.post(path, json=body, timeout=timeout)
        except httpx.ConnectError as e:
            raise ProviderError(
                f"Ollama is not running at {self.base_url}. Start it (`ollama serve`) and try again."
            ) from e
        except httpx.TimeoutException as e:
            raise ProviderError(f"Ollama did not answer within {timeout:.0f} s ({path}).") from e
        if response.status_code == 404 and "not found" in response.text:
            raise ProviderError(f"Ollama model {body['model']!r} is missing: run `ollama pull {body['model']}`.")
        if response.is_error:
            raise ProviderError(f"Ollama error {response.status_code} on {path}: {response.text[:200]}")
        return response.json()

    def complete_json(self, system: str, user: str) -> dict:
        data = self._post(
            "/api/chat",
            {
                "model": self.llm_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "format": "json",  # Ollama constrains the output to valid JSON
                "stream": False,
                "options": {"temperature": 0},  # same job -> same answer, as far as possible
            },
            timeout=config.LLM_TIMEOUT_S,
        )
        return _parse_json_object(data["message"]["content"])

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        prefix = _nomic_prefix(self.embed_model, kind)
        data = self._post(
            "/api/embed",
            {"model": self.embed_model, "input": [prefix + t for t in texts]},
            timeout=config.EMBED_TIMEOUT_S,
        )
        return data["embeddings"]


class FakeProvider:
    """Deterministic stand-in for tests and offline smoke checks. Needs no model or network.

    - `complete_json` returns the queued `responses` in order (a string is returned as an
      invalid-JSON error, to test retries), then `{"level": level, "reason": ...}` forever.
    - `embed` is a "bag of words" vector: each word adds 1 to a slot chosen by its hash.
      Texts sharing words get similar vectors, which is enough to exercise ranking.
    """

    def __init__(self, level: int = 3, responses: list[dict | str] | None = None, dim: int = 64):
        self.llm_model = "fake-llm"
        self.embed_model = f"fake-embed-{dim}"
        self.level = level
        self.responses = list(responses or [])
        self.dim = dim
        self.llm_calls = 0
        self.embed_calls = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.llm_calls += 1
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, str):
                raise InvalidJSONResponse(f"fake invalid response: {response!r}")
            return response
        return {"level": self.level, "reason": "Fake rating."}

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        self.embed_calls += 1
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            slot = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim
            vector[slot] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
