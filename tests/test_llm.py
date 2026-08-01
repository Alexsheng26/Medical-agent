"""Wire-format tests for the model wrapper.

These run against a mock HTTP transport, so they verify the exact request the
SDK puts on the wire — model id, thinking mode, effort, cache breakpoints,
fallback beta — without needing an API key or spending a call.
"""

import json

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from mra.config import Config
from mra.llm import LLM, RefusalError, system_blocks


def make_llm(handler, **cfg_kwargs):
    """Build an LLM whose HTTP calls are answered by `handler`."""
    client = anthropic.Anthropic(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    cfg = Config(**cfg_kwargs)
    return LLM(cfg, client=client)


def message_response(text="ok", stop_reason="end_turn", stop_details=None):
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
        },
    }
    if stop_details is not None:
        body["stop_details"] = stop_details
    return httpx.Response(200, json=body)


def sse_response(text="streamed ok"):
    """A minimal but complete Messages streaming event sequence."""
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-opus-5", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 0,
                      "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 45}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    payload = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(
        200, text=payload, headers={"content-type": "text/event-stream"}
    )


class Recorder:
    """Captures each request body and replies with a canned response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else message_response()
        return response() if callable(response) else response

    @property
    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


class TestTextRequestShape:
    def test_sends_expected_parameters(self):
        recorder = Recorder(message_response("hello"))
        llm = make_llm(recorder, max_tokens=4000)

        result = llm.text("You are helpful.", [{"role": "user", "content": "hi"}])

        assert result.text == "hello"
        assert result.cache_read_tokens == 100

        body = recorder.bodies[0]
        assert body["model"] == "claude-opus-5"
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "high"}
        assert body["max_tokens"] == 4000

    def test_system_prompt_carries_a_cache_breakpoint(self):
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=4000)

        llm.text(["stable rules", "volatile context"], [{"role": "user", "content": "hi"}])

        system = recorder.bodies[0]["system"]
        assert [b["text"] for b in system] == ["stable rules", "volatile context"]
        # The breakpoint belongs on the last block, which caches everything before it.
        assert "cache_control" not in system[0]
        assert system[1]["cache_control"] == {"type": "ephemeral"}

    def test_cache_upto_keeps_volatile_blocks_outside_the_cached_prefix(self):
        """When a later system block embeds per-request material, caching it is
        worse than not caching: the entry is never reused and the 1.25x write
        premium is paid on tokens that will never be read again."""
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=4000)

        llm.text(
            ["stable core rules", "retrieval context for THIS request only"],
            [{"role": "user", "content": "hi"}],
            cache_upto=0,
        )

        system = recorder.bodies[0]["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in system[1]

    def test_default_still_caches_the_whole_system_prompt(self):
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=4000)
        llm.text(["a", "b"], [{"role": "user", "content": "hi"}])

        system = recorder.bodies[0]["system"]
        assert "cache_control" not in system[0]
        assert system[1]["cache_control"] == {"type": "ephemeral"}

    def test_effort_override(self):
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=4000)
        llm.text("s", [{"role": "user", "content": "hi"}], effort="low")
        assert recorder.bodies[0]["output_config"]["effort"] == "low"

    def test_large_output_budget_streams(self):
        recorder = Recorder(sse_response("long answer"))
        llm = make_llm(recorder, max_tokens=32000)

        result = llm.text("s", [{"role": "user", "content": "hi"}])

        assert result.text == "long answer"
        assert recorder.bodies[0]["stream"] is True

    def test_small_output_budget_does_not_stream(self):
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=2000)
        llm.text("s", [{"role": "user", "content": "hi"}])
        assert "stream" not in recorder.bodies[0]


class TestFallbacks:
    def test_fallbacks_requested_by_default(self):
        recorder = Recorder(message_response())
        llm = make_llm(recorder, max_tokens=4000)
        llm.text("s", [{"role": "user", "content": "hi"}])

        assert recorder.bodies[0]["fallbacks"] == "default"
        assert "server-side-fallback" in recorder.requests[0].headers["anthropic-beta"]

    def test_degrades_when_the_endpoint_rejects_the_beta(self):
        rejection = httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error",
                                             "message": "unsupported beta: server-side-fallback"}},
        )
        recorder = Recorder(rejection, message_response("recovered"))
        llm = make_llm(recorder, max_tokens=4000)

        result = llm.text("s", [{"role": "user", "content": "hi"}])

        assert result.text == "recovered"
        assert "fallbacks" not in recorder.bodies[1]

    def test_degradation_happens_only_once(self):
        rejection = httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error",
                                             "message": "unsupported beta"}},
        )
        recorder = Recorder(rejection, message_response(), message_response())
        llm = make_llm(recorder, max_tokens=4000)

        llm.text("s", [{"role": "user", "content": "a"}])
        llm.text("s", [{"role": "user", "content": "b"}])

        # 1 rejected + 1 retry + 1 direct = 3, not 4: the second call must not
        # re-attempt the unsupported beta.
        assert len(recorder.requests) == 3
        assert "fallbacks" not in recorder.bodies[2]

    def test_unrelated_bad_request_is_not_swallowed(self):
        rejection = httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error",
                                             "message": "max_tokens must be positive"}},
        )
        llm = make_llm(Recorder(rejection), max_tokens=4000)

        with pytest.raises(anthropic.BadRequestError):
            llm.text("s", [{"role": "user", "content": "hi"}])


class TestRefusal:
    def test_refusal_raises_with_category(self):
        response = message_response(
            text="", stop_reason="refusal",
            stop_details={"type": "refusal", "category": "bio",
                          "explanation": "declined"},
        )
        llm = make_llm(Recorder(response), max_tokens=4000)

        with pytest.raises(RefusalError) as exc:
            llm.text("s", [{"role": "user", "content": "hi"}])

        assert exc.value.category == "bio"
        assert "bio" in str(exc.value)

    def test_normal_response_does_not_raise(self):
        llm = make_llm(Recorder(message_response()), max_tokens=4000)
        llm.text("s", [{"role": "user", "content": "hi"}])  # must not raise


class TestParse:
    def test_structured_output_request(self):
        class Card(BaseModel):
            finding: str
            strength: int

        payload = json.dumps({"finding": "fibrosis fell", "strength": 4})
        recorder = Recorder(message_response(payload))
        llm = make_llm(recorder, max_tokens=4000)

        card = llm.parse("s", [{"role": "user", "content": "x"}], Card)

        assert card.finding == "fibrosis fell"
        assert card.strength == 4

        body = recorder.bodies[0]
        assert body["output_config"]["format"]["type"] == "json_schema"
        # Extraction runs at its own effort setting, not the conversational one.
        assert body["output_config"]["effort"] == "medium"


class TestSystemBlockPlacement:
    def test_index_is_clamped_to_the_available_blocks(self):
        blocks = system_blocks("a", "b", cache_upto=99)
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[0]

    def test_negative_index_counts_from_the_end(self):
        blocks = system_blocks("a", "b", "c", cache_upto=-2)
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_empty_parts_do_not_shift_the_index(self):
        # "" is dropped, so index 0 must still land on "core".
        blocks = system_blocks("core", "", "volatile", cache_upto=0)
        assert blocks[0]["text"] == "core"
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert len(blocks) == 2


def test_system_blocks_ignores_empty_parts():
    blocks = system_blocks("first", "", "   ", "last")
    assert [b["text"] for b in blocks] == ["first", "last"]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


def test_system_blocks_on_empty_input():
    assert system_blocks() == []
    assert system_blocks("", "  ") == []
