"""The student's settings: which Ollama server and models to use. Stored in .data/settings.json.

Owner decision: Ollama is the only provider, so there is no API key to store.
"""

import json
from dataclasses import asdict, dataclass, fields

from cityu_jobs import config
from cityu_jobs.core.ai.providers import OllamaProvider


@dataclass
class Settings:
    ollama_url: str = config.OLLAMA_URL
    llm_model: str = config.DEFAULT_LLM_MODEL      # rates job levels
    embed_model: str = config.DEFAULT_EMBED_MODEL  # turns text into vectors

    @classmethod
    def load(cls) -> "Settings":
        """Saved settings, with defaults for anything missing (or if the file is unreadable)."""
        try:
            data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and isinstance(v, str) and v.strip()})

    def save(self) -> None:
        config.DATA_DIR.mkdir(exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def provider(self) -> OllamaProvider:
        return OllamaProvider(self.ollama_url, self.llm_model, self.embed_model)
