"""Claude client wrapper.

Everything that talks to the model goes through here, so that caching, effort,
refusal handling and fallbacks are configured in exactly one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence, TypeVar

import anthropic
from pydantic import BaseModel

from .config import Config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Safety classifiers on Opus 5 can decline a request, and life-sciences work sits
# close enough to the bio category to trip them occasionally. Server-side
# fallbacks re-serve a declined request on another model inside the same call,
# routed by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Above roughly 16k output tokens a non-streaming request risks an HTTP timeout.
STREAM_THRESHOLD = 16000


class RefusalError(RuntimeError):
    """The model (and any fallback) declined to answer."""

    def __init__(self, category: str | None, explanation: str | None):
        self.category = category
        self.explanation = explanation
        super().__init__(
            f"Model declined this request (category={category or 'unspecified'}). "
            f"{explanation or ''}".strip()
        )


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""


def system_blocks(*parts: str) -> list[dict[str, Any]]:
    """Build a system prompt as cacheable blocks.

    Blocks render in order and caching is a prefix match, so put the stable
    material first and the volatile material last. The breakpoint goes on the
    final block, which caches everything before it.
    """
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": part} for part in parts if part and part.strip()
    ]
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


class LLM:
    def __init__(self, cfg: Config, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()
        # Flipped off permanently the first time the endpoint rejects the beta,
        # so a gateway without fallback support costs one failed call, not one
        # per request.
        self._fallbacks_available = True

    # ------------------------------------------------------------------ text

    def text(
        self,
        system: str | Sequence[str] | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        *,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """Generate prose. Streams whenever the output budget is large."""
        system_param = self._normalise_system(system)
        max_tokens = max_tokens or self.cfg.max_tokens
        effort = effort or self.cfg.effort

        params: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "system": system_param,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }

        message = self._create(params, stream=max_tokens > STREAM_THRESHOLD)
        self._raise_on_refusal(message)

        text = "".join(b.text for b in message.content if b.type == "text")
        usage = message.usage
        return LLMResult(
            text=text.strip(),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            model=message.model,
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
    ) -> T:
        """Generate a validated instance of `schema`.

        Structured outputs constrain the response to the schema, which is what
        keeps extraction and scoring machine-readable instead of prose we have
        to regex afterwards.
        """
        response = self.client.messages.parse(
            model=self.cfg.model,
            max_tokens=max_tokens,
            system=self._normalise_system(system),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort or self.cfg.extraction_effort},
            output_format=schema,
        )
        self._raise_on_refusal(response)
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(
                "Model returned no parseable output. This usually means max_tokens "
                "was hit mid-object; retry with a larger budget."
            )
        return parsed

    # --------------------------------------------------------------- internals

    @staticmethod
    def _normalise_system(
        system: str | Sequence[str] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(system, str):
            return system_blocks(system)
        if system and isinstance(system[0], dict):
            return list(system)  # type: ignore[arg-type]
        return system_blocks(*system)  # type: ignore[arg-type]

    def _create(self, params: dict[str, Any], *, stream: bool):
        if self._fallbacks_available:
            beta_params = dict(params, fallbacks="default", betas=[FALLBACK_BETA])
            try:
                return self._dispatch(self.client.beta.messages, beta_params, stream=stream)
            except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
                if not self._looks_like_missing_beta(exc):
                    raise
                log.info("Server-side fallbacks unavailable on this endpoint; continuing without.")
                self._fallbacks_available = False

        return self._dispatch(self.client.messages, params, stream=stream)

    @staticmethod
    def _dispatch(resource, params: dict[str, Any], *, stream: bool):
        if stream:
            with resource.stream(**params) as s:
                return s.get_final_message()
        return resource.create(**params)

    @staticmethod
    def _looks_like_missing_beta(exc: Exception) -> bool:
        text = str(exc).lower()
        return "fallback" in text or "beta" in text

    @staticmethod
    def _raise_on_refusal(message) -> None:
        if getattr(message, "stop_reason", None) != "refusal":
            return
        details = getattr(message, "stop_details", None)
        raise RefusalError(
            getattr(details, "category", None),
            getattr(details, "explanation", None),
        )
