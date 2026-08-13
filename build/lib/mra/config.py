"""Configuration for the research intermediary agent.

Settings resolve in this order (first wins):
  1. explicit argument
  2. environment variable
  3. mra.config.json in the workspace
  4. built-in default
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Opus 5 is the default: this workload is long-horizon reasoning over literature,
# which is exactly where the extra capability pays for itself.
DEFAULT_MODEL = "claude-opus-5"

# `effort` controls how much the model thinks and acts. `high` is the API default.
# Drop to `medium` for bulk extraction jobs where cost matters more than depth.
DEFAULT_EFFORT = "high"
DEFAULT_EXTRACTION_EFFORT = "medium"

CONFIG_FILENAME = "mra.config.json"


@dataclass
class Config:
    # --- workspace ---
    workspace: Path = field(default_factory=lambda: Path.cwd() / ".mra")

    # --- model ---
    # `provider` picks the backend: "anthropic" (default, full features) or
    # "openai" for any OpenAI-compatible endpoint such as DeepSeek. See
    # docs/PROVIDERS.md for what each one can and cannot do.
    provider: str = "anthropic"
    base_url: str = ""
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    extraction_effort: str = DEFAULT_EXTRACTION_EFFORT
    max_tokens: int = 32000

    # --- PubMed / NCBI E-utilities ---
    # An email is requested by NCBI's usage policy. An API key raises the rate
    # limit from 3 to 10 requests/second.
    ncbi_email: str = ""
    ncbi_api_key: str = ""

    # --- behaviour ---
    # Language for conversation with the researcher. Manuscript output is always
    # English (that is the point of the nativization step).
    chat_language: str = "zh"
    retrieval_k: int = 12

    # Override the built-in price snapshot, e.g. {"claude-opus-5": [5.0, 25.0]}.
    # Useful when rates change or an institutional gateway bills differently.
    prices: dict = field(default_factory=dict)

    # Spend ceiling for one unattended `mra sync`. Unattended plus metered
    # billing needs a brake; this one is deliberately low.
    default_max_cost: float = 2.00

    @property
    def db_path(self) -> Path:
        return self.workspace / "knowledge.db"

    @property
    def memory_path(self) -> Path:
        return self.workspace / "memory.json"

    @property
    def usage_path(self) -> Path:
        return self.workspace / "usage.json"

    @property
    def drafts_dir(self) -> Path:
        return self.workspace / "drafts"

    @property
    def briefs_dir(self) -> Path:
        return self.workspace / "briefs"

    def ensure_workspace(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.briefs_dir.mkdir(parents=True, exist_ok=True)

    # --- persistence ---

    @classmethod
    def load(cls, workspace: str | Path | None = None) -> "Config":
        ws = Path(workspace) if workspace else Path(os.environ.get("MRA_WORKSPACE", Path.cwd() / ".mra"))
        cfg = cls(workspace=ws)

        path = ws / CONFIG_FILENAME
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for key, value in data.items():
                if key == "workspace":
                    continue  # workspace is positional, never read back from the file
                if hasattr(cfg, key):
                    setattr(cfg, key, value)

        # Environment always outranks the config file, so a shared config can be
        # checked in while secrets stay in the environment.
        env_map = {
            "MRA_PROVIDER": "provider",
            "MRA_BASE_URL": "base_url",
            "MRA_MODEL": "model",
            "MRA_EFFORT": "effort",
            "MRA_CHAT_LANGUAGE": "chat_language",
            "NCBI_EMAIL": "ncbi_email",
            "NCBI_API_KEY": "ncbi_api_key",
        }
        for env_key, attr in env_map.items():
            value = os.environ.get(env_key)
            if value:
                setattr(cfg, attr, value)

        return cfg

    def save(self) -> Path:
        self.ensure_workspace()
        path = self.workspace / CONFIG_FILENAME
        data = asdict(self)
        data.pop("workspace")
        # Secrets live in the environment, not on disk.
        data.pop("ncbi_api_key")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
