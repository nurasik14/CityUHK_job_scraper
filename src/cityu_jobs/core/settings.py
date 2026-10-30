"""The student's settings: which AI rates jobs, which Ollama server and models, and which
internship regions to fetch. Stored in .data/settings.json.

There is no API key to store: Claude runs through the `claude` CLI, which has its own
login (the student's Claude plan), and Ollama needs none.
"""

import json
from dataclasses import asdict, dataclass, field, fields

from cityu_jobs import config
from cityu_jobs.core.ai.providers import ClaudeCLIProvider, HybridProvider, OllamaProvider


@dataclass
class Settings:
    ai_provider: str = config.DEFAULT_AI_PROVIDER    # "claude" or "ollama": who rates job levels
    claude_model: str = config.DEFAULT_CLAUDE_MODEL  # haiku | sonnet | opus
    ollama_url: str = config.OLLAMA_URL
    llm_model: str = config.DEFAULT_LLM_MODEL        # Ollama model that rates levels (if ai_provider == "ollama")
    embed_model: str = config.DEFAULT_EMBED_MODEL    # Ollama model that turns text into vectors (always)
    intern_regions: list[str] = field(default_factory=lambda: list(config.DEFAULT_INTERN_REGIONS))

    @classmethod
    def load(cls) -> "Settings":
        """Saved settings, with defaults for anything missing or invalid (or if the file is unreadable)."""
        try:
            data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        values: dict = {k: v for k, v in data.items() if k in known and isinstance(v, str) and v.strip()}
        regions = data.get("intern_regions")
        if isinstance(regions, list):
            values["intern_regions"] = [r for r in regions if r in config.INTERN_REGIONS]
        if values.get("ai_provider") not in config.AI_PROVIDERS:
            values.pop("ai_provider", None)
        if values.get("claude_model") not in config.CLAUDE_MODELS:
            values.pop("claude_model", None)
        return cls(**values)

    def save(self) -> None:
        config.DATA_DIR.mkdir(exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def ollama(self) -> OllamaProvider:
        return OllamaProvider(self.ollama_url, self.llm_model, self.embed_model)

    def provider(self) -> HybridProvider | OllamaProvider:
        """The AI the pipeline uses: Claude (plan) for rating + Ollama for embeddings, or Ollama only."""
        if self.ai_provider == "claude":
            return HybridProvider(ClaudeCLIProvider(self.claude_model), self.ollama())
        return self.ollama()
