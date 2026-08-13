"""Model client wrapper.

Everything that talks to a model goes through here, so caching, effort, refusal
handling, fallbacks and cost accounting are configured in exactly one place.
*Which* model is a separate question, answered in `backends.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from . import backends
from .backends import RefusalError  # re-exported: callers catch it from here
from .config import Config
from .usage import Ledger, Usage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

STREAM_THRESHOLD = backends.STREAM_THRESHOLD
FALLBACK_BETA = backends.FALLBACK_BETA

__all__ = ["LLM", "LLMResult", "RefusalError", "system_blocks", "STREAM_THRESHOLD"]


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""


def system_blocks(*parts: str, cache_upto: int = -1) -> list[dict[str, Any]]:
    """Build a system prompt as cacheable blocks.

    Blocks render in order and caching is a prefix match, so stable material
    goes first and volatile material last. `cache_upto` is the index of the last
    block to cache; everything after it stays outside the cached prefix.

    Getting this wrong costs money in both directions. Put the breakpoint after
    a volatile block and the entry is never reused *and* you pay the 1.25x write
    premium on tokens that will never be read again. Put it too early and you
    leave reusable prefix uncached.
    """
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": part} for part in parts if part and part.strip()
    ]
    if not blocks:
        return blocks

    index = cache_upto if cache_upto >= 0 else len(blocks) + cache_upto
    index = max(0, min(index, len(blocks) - 1))
    blocks[index]["cache_control"] = {"type": "ephemeral"}
    return blocks


class LLM:
    def __init__(self, cfg: Config, client=None, ledger: Ledger | None = None):
        self.cfg = cfg
        self.backend = backends.build(cfg, client=client)
        # Every call is recorded here so the CLI can report what a command cost.
        self.ledger = ledger

    @property
    def client(self):
        """The underlying SDK client. Kept for tests that assert on the wire."""
        return self.backend.client

    # ------------------------------------------------------------------ text

    def text(
        self,
        system: str | Sequence[str] | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        *,
        effort: str | None = None,
        max_tokens: int | None = None,
        cache_upto: int = -1,
    ) -> LLMResult:
        """Generate prose. Streams whenever the output budget is large.

        Pass `cache_upto=0` when later system blocks embed per-request material
        (retrieval context, the researcher's data, a document being rewritten):
        only the stable prefix is then cached.
        """
        max_tokens = max_tokens or self.cfg.max_tokens
        reply = self.backend.text(
            model=self.cfg.model,
            system=self._normalise_system(system, cache_upto),
            messages=messages,
            max_tokens=max_tokens,
            effort=effort or self.cfg.effort,
        )
        self._record(reply.usage)
        return LLMResult(
            text=reply.text,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cache_read_tokens=reply.usage.cache_read_tokens,
            cache_write_tokens=reply.usage.cache_write_tokens,
            model=reply.model,
        )

    # ----------------------------------------------------------------- parse

    def parse(
        self,
        system: str | Sequence[str] | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[T],
        *,
        effort: str | None = None,
        max_tokens: int = 8000,
        cache_upto: int = -1,
    ) -> T:
        """Generate a validated instance of `schema`.

        On Anthropic the response is constrained to the schema; on an
        OpenAI-compatible endpoint it is a forced tool call that is then
        validated. Either way the caller gets an instance or an exception —
        never a half-filled object.
        """
        reply = self.backend.parse(
            model=self.cfg.model,
            system=self._normalise_system(system, cache_upto),
            messages=messages,
            schema=schema,
            max_tokens=max_tokens,
            effort=effort or self.cfg.extraction_effort,
        )
        self._record(reply.usage)
        return reply.parsed  # type: ignore[return-value]

    # --------------------------------------------------------------- internals

    def _normalise_system(
        self,
        system: str | Sequence[str] | list[dict[str, Any]],
        cache_upto: int = -1,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]]
        if isinstance(system, str):
            blocks = system_blocks(system, cache_upto=cache_upto)
        elif system and isinstance(system[0], dict):
            blocks = list(system)  # type: ignore[arg-type]
        else:
            blocks = system_blocks(*system, cache_upto=cache_upto)  # type: ignore[arg-type]

        if not self.backend.supports_caching:
            # A cache_control key on an endpoint that does not know it is at best
            # ignored and at worst a 400. Strip it rather than hope.
            blocks = [{k: v for k, v in b.items() if k != "cache_control"} for b in blocks]
        return blocks

    def _record(self, usage: Usage) -> Usage:
        if self.ledger is not None:
            self.ledger.record(usage)
        return usage
