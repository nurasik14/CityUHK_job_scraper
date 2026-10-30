"""Small, swappable interfaces to AI models, using plain HTTP / a CLI instead of vendor SDKs.

Two jobs need AI:
- an LLM ("large language model") that reads a job and answers in JSON (its level),
- an embedding model that turns text into a vector of numbers, so that texts with similar
  meaning end up close together (used for semantic search).

Providers:
- ClaudeCLIProvider (default LLM, owner decision 2026-10-30): runs the `claude` CLI
  (Claude Code) in print mode, so it uses the student's Claude plan (Pro/Max login) and
  needs no API key. Job text is sent to Anthropic; the search query is not (it is only
  embedded, locally).
- OllamaProvider: local models. Still used for embeddings (Claude has no embedding model)
  and as the free, fully offline LLM option on the Settings page.
- HybridProvider: the LLM from one provider + embeddings from another.
- FakeProvider: deterministic stand-in for tests.

The rest of the code only depends on the two Protocols below, so they swap freely.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
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

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        """Send a system + user prompt; return the model's answer parsed as a JSON object.

        `schema` (a JSON Schema) asks providers that support it for exactly that shape.
        Optional attributes the analysis step looks for (see core/analysis.py):
        - `batch_size` (default 1): jobs rated per request; strong models handle several.
        - `max_parallel` (default 1): requests that may run at the same time.
        """
        ...


class EmbeddingProvider(Protocol):
    embed_model: str

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        """One vector per text. `kind` says whether texts are jobs ("document") or a search ("query")."""
        ...


def _parse_json_object(content: str) -> dict:
    """The model's text as a JSON object. Tolerates ```json fences and a sentence around it."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        start, end = text.find("{"), text.rfind("}")
        try:
            value = json.loads(text[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            value = None
        if value is None:
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

    batch_size = 1  # small local models lose track when asked to rate several jobs at once
    max_parallel = 1

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

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        data = self._post(
            "/api/chat",
            {
                "model": self.llm_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # Ollama constrains the output to valid JSON (or to the schema, if given).
                "format": schema or "json",
                "stream": False,
                "options": {"temperature": 0},  # same job -> same answer, as far as possible
            },
            timeout=config.LLM_TIMEOUT_S,
        )
        return _parse_json_object(data["message"]["content"])

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        prefix = _nomic_prefix(self.embed_model, kind)
        inputs = [prefix + fit_context(t) for t in texts]
        try:
            data = self._post("/api/embed", {"model": self.embed_model, "input": inputs}, timeout=config.EMBED_TIMEOUT_S)
        except ProviderError as e:
            if "context length" not in str(e):
                raise
            # Our token estimate was too low for some text (dense scripts): embed one by one,
            # halving any text the model still refuses. The start of a listing matters most.
            return [self._embed_one(text) for text in inputs]
        return data["embeddings"]

    def _embed_one(self, text: str) -> list[float]:
        for _attempt in range(4):
            try:
                body = {"model": self.embed_model, "input": [text]}
                return self._post("/api/embed", body, timeout=config.EMBED_TIMEOUT_S)["embeddings"][0]
            except ProviderError as e:
                if "context length" not in str(e):
                    raise
                text = text[: len(text) // 2]
        raise ProviderError("Ollama refused a job text as too long even after shortening it.")


def fit_context(text: str, max_tokens: int = config.EMBED_MAX_TOKENS) -> str:
    """Cut `text` so it fits the embedding model's context (Ollama errors instead of truncating).

    Rough token estimate: ~4 Latin characters per token, ~1 token per CJK / other character.
    """
    budget, cut = float(max_tokens), len(text)
    for i, ch in enumerate(text):
        budget -= 0.25 if ch.isascii() else 1.1
        if budget < 0:
            cut = i
            break
    return text[:cut]


# subprocess.run, or a fake in tests.
Runner = Callable[..., subprocess.CompletedProcess]


class ClaudeCLIProvider:
    """Claude through the Claude Code CLI (`claude -p`), billed to the student's Claude plan.

    Each call starts `claude` in print mode with every extra stripped (no tools, no MCP
    servers, no settings, no CLAUDE.md, no saved session) and our own system prompt, in an
    empty temporary folder. `--json-schema` makes Claude answer in exactly the given shape.

    ANTHROPIC_API_KEY is removed from the child's environment: if it were set, the CLI
    would bill that API key instead of the plan.
    """

    def __init__(
        self,
        model: str = config.DEFAULT_CLAUDE_MODEL,
        binary: str = "claude",
        timeout_s: float = config.CLAUDE_TIMEOUT_S,
        run: Runner = subprocess.run,
    ):
        self.model = model
        self.llm_model = f"claude:{model}"  # stored with each level, so the cache knows who rated it
        self.binary = binary
        self.timeout_s = timeout_s
        self.batch_size = config.CLAUDE_BATCH_SIZE
        self.max_parallel = config.CLAUDE_MAX_PARALLEL
        self._run = run  # injectable for tests

    def _command(self, system: str, schema: dict | None) -> list[str]:
        command = [
            self.binary, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if schema:
            command += ["--json-schema", json.dumps(schema)]
        return command

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        with tempfile.TemporaryDirectory(prefix="cityu-jobs-claude-") as workdir:
            try:
                done = self._run(
                    self._command(system, schema),
                    input=user,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=workdir,
                    env=env,
                )
            except FileNotFoundError as e:
                raise ProviderError(
                    "Claude Code (`claude`) is not installed. Install it from https://claude.com/claude-code "
                    "and log in with your Claude plan, or pick Ollama on the Settings page."
                ) from e
            except subprocess.TimeoutExpired as e:
                raise ProviderError(f"Claude did not answer within {self.timeout_s:.0f} s.") from e
        return self._parse(done.stdout, done.stderr, done.returncode)

    @staticmethod
    def _parse(stdout: str, stderr: str, returncode: int) -> dict:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            envelope = None
        text = (stdout or "") + (stderr or "")
        if not isinstance(envelope, dict) or envelope.get("is_error") or returncode != 0:
            detail = (envelope or {}).get("result") if isinstance(envelope, dict) else None
            message = str(detail or text).strip()[:300]
            lowered = message.lower()
            if "log in" in lowered or "login" in lowered or "authentication" in lowered or "401" in lowered:
                raise ProviderError("Claude Code is not logged in: run `claude` once and log in with your Claude plan.")
            if "limit" in lowered or "429" in lowered or "overloaded" in lowered:
                raise ProviderError(f"Claude plan usage limit reached or Claude is busy; try later. ({message})")
            raise ProviderError(f"Claude failed: {message}")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return structured
        return _parse_json_object(str(envelope.get("result", "")))

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None


class HybridProvider:
    """One object for the pipeline: rate with `llm`, embed with `embedder`."""

    def __init__(self, llm: LLMProvider, embedder: EmbeddingProvider):
        self.llm = llm
        self.embedder = embedder
        self.llm_model = llm.llm_model
        self.embed_model = embedder.embed_model
        self.batch_size = getattr(llm, "batch_size", 1)
        self.max_parallel = getattr(llm, "max_parallel", 1)

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        return self.llm.complete_json(system, user, schema=schema)

    def embed(self, texts: list[str], kind: EmbedKind = "document") -> list[list[float]]:
        return self.embedder.embed(texts, kind=kind)


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

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
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
