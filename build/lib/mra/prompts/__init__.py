"""Prompt loader.

Prompts live as Markdown files next to this module so the researcher can edit
them without touching Python. Anything in `{braces}` is substituted at call time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in PROMPT_DIR.glob("*.md")))
        raise FileNotFoundError(f"No prompt named {name!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def load(name: str, **substitutions: object) -> str:
    """Load a prompt, filling in {placeholders}."""
    text = _read(name)
    for key, value in substitutions.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def core(language: str = "zh") -> str:
    """The shared operating rules, prepended to every system prompt."""
    instruction = {
        "zh": "与研究者对话时使用中文。手稿正文、标题、摘要一律使用英文。",
        "en": "Converse in English. Manuscript text is English.",
    }.get(language, "Converse in the researcher's language. Manuscript text is English.")
    return load("core", language_instruction=instruction)
