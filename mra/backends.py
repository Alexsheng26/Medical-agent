"""Model backends.

`llm.py` is the one place the rest of the program talks to a model; this is the
one place that knows *which* model. Two of them:

- **anthropic** — the default, and the only one with everything: schema-
  constrained structured output, prompt caching, effort control, and
  server-side fallbacks when a request is declined.
- **openai** — any OpenAI-compatible endpoint, DeepSeek among them. Cheaper,
  and enough for the prose commands. Structured output is emulated through tool
  calling plus validation, since that is the widely supported path; caching,
  effort and fallbacks do not exist there and are dropped rather than faked.

The split is at the wire, not at the prompt. Both backends receive the same
system blocks and the same schema, so changing provider changes what answers,
never what is asked — which is what makes a side-by-side comparison on the same
input mean something.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .usage import Usage

log = logging.getLogger(__name__)

# Above roughly 16k output tokens a non-streaming request risks an HTTP timeout.
STREAM_THRESHOLD = 16000

# One retry when a structured reply fails validation. The second attempt is told
# exactly what was wrong, which recovers most near-misses; a third rarely adds
# anything and every attempt is paid for.
PARSE_ATTEMPTS = 2

FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
class Reply:
    text: str = ""
    parsed: BaseModel | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""


# ------------------------------------------------------------------- anthropic


class AnthropicBackend:
    """Full-featured path: caching, effort, structured output, fallbacks."""

    name = "anthropic"
    supports_caching = True

    def __init__(self, client=None):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        # Flipped off permanently the first time the endpoint rejects the beta,
        # so a gateway without fallback support costs one failed call rather
        # than one per request.
        self._fallbacks_available = True

    def text(self, *, model, system, messages, max_tokens, effort) -> Reply:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        message = self._create(params, stream=max_tokens > STREAM_THRESHOLD)
        _raise_on_refusal(message)
        text = "".join(b.text for b in message.content if b.type == "text")
        return Reply(text=text.strip(), usage=_anthropic_usage(message), model=message.model)

    def parse(self, *, model, system, messages, schema, max_tokens, effort) -> Reply:
        response = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            output_format=schema,
        )
        _raise_on_refusal(response)
        if response.parsed_output is None:
            raise RuntimeError(
                "Model returned no parseable output. This usually means max_tokens "
                "was hit mid-object; retry with a larger budget."
            )
        return Reply(
            parsed=response.parsed_output,
            usage=_anthropic_usage(response),
            model=getattr(response, "model", model),
        )

    def _create(self, params: dict[str, Any], *, stream: bool):
        if self._fallbacks_available:
            beta_params = dict(params, fallbacks="default", betas=[FALLBACK_BETA])
            try:
                return self._dispatch(self.client.beta.messages, beta_params, stream=stream)
            except (self._anthropic.BadRequestError, self._anthropic.NotFoundError) as exc:
                if not _looks_like_missing_beta(exc):
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


def _anthropic_usage(message) -> Usage:
    raw = getattr(message, "usage", None)
    return Usage(
        calls=1,
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )


def _looks_like_missing_beta(exc: Exception) -> bool:
    text = str(exc).lower()
    return "fallback" in text or "beta" in text


def _raise_on_refusal(message) -> None:
    if getattr(message, "stop_reason", None) != "refusal":
        return
    details = getattr(message, "stop_details", None)
    raise RefusalError(
        getattr(details, "category", None),
        getattr(details, "explanation", None),
    )


# ---------------------------------------------------------------------- openai


TOOL_NAME = "record_result"


class OpenAIBackend:
    """Any OpenAI-compatible endpoint. DeepSeek is the reason this exists.

    Three Anthropic features have no counterpart here and are dropped, not
    approximated: prompt caching (the endpoint may cache on its own terms, which
    we read back from usage but cannot direct), effort control, and refusal
    fallbacks. Dropping them changes cost and robustness, not correctness.
    """

    name = "openai"
    supports_caching = False

    def __init__(self, api_key: str | None = None, base_url: str | None = None, client=None):
        if client is not None:
            self.client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise BackendUnavailable(
                "The openai package is needed for an OpenAI-compatible provider.\n"
                "  pip install openai"
            ) from exc
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def text(self, *, model, system, messages, max_tokens, effort) -> Reply:
        # effort has no counterpart here; the argument is accepted so callers
        # stay identical across backends.
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=_to_openai_messages(system, messages),
        )
        choice = response.choices[0]
        return Reply(
            text=(choice.message.content or "").strip(),
            usage=_openai_usage(response),
            model=getattr(response, "model", model),
        )

    def parse(self, *, model, system, messages, schema, max_tokens, effort) -> Reply:
        """Structured output through a forced tool call, then validation.

        There is no schema-constrained decoding here, so the model can return
        something that does not fit. Rather than accept a half-populated object,
        the failure is fed back once with the validation error — a near-miss
        usually recovers, and a second failure is reported rather than papered
        over with defaults.
        """
        tool = {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": (schema.__doc__ or "Record the structured result.").strip(),
                "parameters": schema.model_json_schema(),
            },
        }
        conversation = _to_openai_messages(system, messages)
        usage = Usage()
        last_error = ""

        for attempt in range(PARSE_ATTEMPTS):
            response = self.client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=conversation,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            )
            usage.add(_openai_usage(response))

            raw = _tool_arguments(response)
            if raw is not None:
                try:
                    return Reply(
                        parsed=schema.model_validate_json(raw),
                        usage=usage,
                        model=getattr(response, "model", model),
                    )
                except ValidationError as exc:
                    last_error = str(exc)
            else:
                last_error = "no tool call in the reply"

            if attempt + 1 < PARSE_ATTEMPTS:
                log.info("Structured reply did not validate; retrying once.")
                conversation = conversation + [
                    {"role": "user", "content":
                     f"That did not match the required schema:\n{last_error[:1500]}\n"
                     f"Call {TOOL_NAME} again with every required field present and "
                     "correctly typed."},
                ]

        raise BackendError(
            f"{model} did not return a valid {schema.__name__} after "
            f"{PARSE_ATTEMPTS} attempts. Last error:\n{last_error[:800]}\n\n"
            "Structured commands (digest, assess, hypothesis, review) need an "
            "endpoint with working tool calling. Prose commands (chat, draft, "
            "nativize, polish) do not and should still work."
        )


class BackendError(RuntimeError):
    pass


class BackendUnavailable(RuntimeError):
    pass


def _tool_arguments(response) -> str | None:
    message = response.choices[0].message
    calls = getattr(message, "tool_calls", None)
    if not calls:
        # Some gateways ignore tool_choice and answer in content instead. If that
        # content is JSON it is still usable, so try before giving up.
        content = (getattr(message, "content", "") or "").strip()
        return content if content.startswith("{") else None
    return calls[0].function.arguments


def _to_openai_messages(system, messages) -> list[dict[str, Any]]:
    """Flatten Anthropic-shaped system blocks into one system message."""
    if isinstance(system, str):
        text = system
    else:
        parts = []
        for block in system:
            parts.append(block["text"] if isinstance(block, dict) else str(block))
        text = "\n\n".join(p for p in parts if p)

    out: list[dict[str, Any]] = [{"role": "system", "content": text}] if text else []
    for message in messages:
        content = message["content"]
        if not isinstance(content, str):  # pragma: no cover - we only send strings
            content = json.dumps(content, ensure_ascii=False)
        out.append({"role": message["role"], "content": content})
    return out


def _openai_usage(response) -> Usage:
    """Map OpenAI-shaped usage onto ours.

    `prompt_tokens` counts cached and uncached input together, so the cached
    part is subtracted out — leaving it in would bill those tokens at the full
    input rate on top of the cache rate.
    """
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage(calls=1)

    prompt = getattr(raw, "prompt_tokens", 0) or 0
    cached = getattr(raw, "prompt_cache_hit_tokens", None)
    if cached is None:
        details = getattr(raw, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0

    return Usage(
        calls=1,
        input_tokens=max(prompt - cached, 0),
        output_tokens=getattr(raw, "completion_tokens", 0) or 0,
        cache_read_tokens=cached,
    )


# --------------------------------------------------------------- error reading


def api_error_types() -> tuple[type, ...]:
    """Whichever provider SDKs are installed, for the CLI's top-level handler."""
    types: list[type] = []
    for module_name, attribute in (("anthropic", "APIError"), ("openai", "APIError")):
        try:
            module = __import__(module_name)
        except ImportError:  # pragma: no cover - optional dependency
            continue
        error = getattr(module, attribute, None)
        if isinstance(error, type):
            types.append(error)
    return tuple(types) or (RuntimeError,)


def describe_api_error(exc: Exception) -> str:
    """Turn a provider error into something a researcher can act on.

    Found the hard way: a key running out of credit produced a forty-line
    traceback ending in a JSON blob. That tells someone who has never used a
    terminal nothing at all, and the fix is theirs to make, not ours.
    """
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)

    if "credit balance" in text or "insufficient" in text or "quota" in text:
        return (
            "余额不足，这次调用没有执行。\n"
            "  到 https://platform.claude.com 的 Plans & Billing 充值后重试。\n"
            "  已经完成的部分都已保存，重跑会从没做完的地方继续。"
        )
    if status == 401 or "authentication" in text or "invalid x-api-key" in text:
        return (
            "API key 无效或已被删除。\n"
            "  确认 ANTHROPIC_API_KEY 设对了（Windows 上 setx 之后要新开一个窗口），\n"
            "  或到 platform.claude.com 重新建一个。"
        )
    if status == 429 or "rate limit" in text:
        return "触发了服务端限流。等一两分钟再跑，已完成的部分不会丢。"
    if status is not None and 500 <= status < 600:
        return "服务端暂时出错，不是你的问题。稍后重试即可。"
    if "connection" in text or "timeout" in text:
        return (
            "连不上模型服务。检查网络或代理；如果在校园网内，可能被防火墙挡了。"
        )
    return f"调用模型时出错：{exc}"


# ------------------------------------------------------------------- selection


def build(cfg, client=None):
    """Construct the backend named by the config."""
    provider = (getattr(cfg, "provider", "") or "anthropic").lower()
    if provider == "anthropic":
        return AnthropicBackend(client=client)
    if provider in {"openai", "deepseek", "openai-compatible"}:
        import os

        return OpenAIBackend(
            api_key=os.environ.get("MRA_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=getattr(cfg, "base_url", "") or os.environ.get("MRA_BASE_URL") or None,
            client=client,
        )
    raise ValueError(
        f"Unknown provider {provider!r}. Use 'anthropic' or 'openai' "
        "(an OpenAI-compatible endpoint such as DeepSeek)."
    )
