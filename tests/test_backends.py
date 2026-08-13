"""The OpenAI-compatible backend, for DeepSeek and anything shaped like it.

The Anthropic path is covered in test_llm.py against a mock transport. This
covers the other one, whose hard part is that structured output is emulated: the
endpoint can return something that does not fit the schema, and what happens
then decides whether a bad reply becomes a half-filled object or an error.
"""

import json

import pytest
from pydantic import BaseModel

from mra import backends
from mra.config import Config
from mra.llm import LLM


class Card(BaseModel):
    """A tiny stand-in for LitCard."""

    title: str
    score: int


class StubOpenAI:
    """Records requests, replies from a queued script."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return outer.replies.pop(0)

        class Chat:
            completions = Completions()

        self.chat = Chat()


def reply(*, content=None, arguments=None, prompt=100, cached=0, completion=20):
    """Build an object shaped like an OpenAI chat completion."""
    call = None
    if arguments is not None:
        call = type("Call", (), {"function": type("F", (), {"arguments": arguments})()})()

    message = type("M", (), {"content": content, "tool_calls": [call] if call else None})()
    usage = type("U", (), {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_cache_hit_tokens": cached,
    })()
    return type("R", (), {
        "choices": [type("C", (), {"message": message})()],
        "usage": usage,
        "model": "deepseek-chat",
    })()


def openai_llm(client, **cfg):
    return LLM(Config(provider="openai", model="deepseek-chat", **cfg), client=client)


class TestProviderSelection:
    def test_anthropic_is_the_default(self):
        assert backends.build(Config()).name == "anthropic"

    def test_openai_selected_by_config(self):
        stub = StubOpenAI([])
        assert backends.build(Config(provider="openai"), client=stub).name == "openai"

    def test_deepseek_is_accepted_as_an_alias(self):
        stub = StubOpenAI([])
        assert backends.build(Config(provider="deepseek"), client=stub).name == "openai"

    def test_unknown_provider_names_the_valid_ones(self):
        with pytest.raises(ValueError, match="anthropic"):
            backends.build(Config(provider="llamafile"))

    def test_provider_reads_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MRA_PROVIDER", "openai")
        monkeypatch.setenv("MRA_BASE_URL", "https://api.deepseek.com")
        cfg = Config.load()
        assert cfg.provider == "openai"
        assert cfg.base_url == "https://api.deepseek.com"


class TestPromptTranslation:
    def test_system_blocks_become_one_system_message(self, tmp_path):
        stub = StubOpenAI([reply(content="prose")])
        openai_llm(stub).text(["core rules", "task rules"], [{"role": "user", "content": "go"}])

        sent = stub.requests[0]["messages"]
        assert sent[0]["role"] == "system"
        assert "core rules" in sent[0]["content"]
        assert "task rules" in sent[0]["content"]
        assert sent[1] == {"role": "user", "content": "go"}

    def test_cache_control_is_stripped(self):
        """A cache_control key on an endpoint that does not know it is at best
        ignored and at worst a 400."""
        stub = StubOpenAI([reply(content="prose")])
        openai_llm(stub).text(["a", "b"], [{"role": "user", "content": "go"}], cache_upto=0)

        assert "cache_control" not in json.dumps(stub.requests[0])

    def test_effort_is_not_sent(self):
        stub = StubOpenAI([reply(content="prose")])
        openai_llm(stub).text("sys", [{"role": "user", "content": "go"}], effort="high")
        assert "output_config" not in stub.requests[0]
        assert "effort" not in stub.requests[0]


class TestStructuredOutput:
    def test_a_forced_tool_call_carries_the_schema(self):
        stub = StubOpenAI([reply(arguments='{"title": "t", "score": 3}')])
        result = openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card)

        assert result == Card(title="t", score=3)
        request = stub.requests[0]
        assert request["tool_choice"]["function"]["name"] == backends.TOOL_NAME
        assert "title" in request["tools"][0]["function"]["parameters"]["properties"]

    def test_a_bad_reply_is_retried_with_the_error(self):
        stub = StubOpenAI([
            reply(arguments='{"title": "t"}'),           # score missing
            reply(arguments='{"title": "t", "score": 4}'),
        ])
        result = openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card)

        assert result.score == 4
        assert len(stub.requests) == 2
        correction = stub.requests[1]["messages"][-1]["content"]
        assert "did not match the required schema" in correction
        assert "score" in correction, "the model must be told which field was wrong"

    def test_two_failures_raise_rather_than_return_a_half_object(self):
        stub = StubOpenAI([reply(arguments='{"title": "t"}')] * 2)
        with pytest.raises(backends.BackendError, match="digest, assess"):
            openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card)

    def test_json_in_content_is_accepted_when_tool_calls_are_ignored(self):
        """Some gateways ignore tool_choice and answer in content instead."""
        stub = StubOpenAI([reply(content='{"title": "t", "score": 5}')])
        assert openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card).score == 5

    def test_prose_in_content_is_not_mistaken_for_a_result(self):
        stub = StubOpenAI([reply(content="Sure, here is the card!")] * 2)
        with pytest.raises(backends.BackendError):
            openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card)


class TestUsageMapping:
    def test_cached_input_is_not_billed_twice(self):
        """prompt_tokens counts cached and uncached together; leaving the cached
        part in would bill it at the full input rate on top of the cache rate."""
        stub = StubOpenAI([reply(content="x", prompt=1000, cached=400)])
        result = openai_llm(stub).text("sys", [{"role": "user", "content": "go"}])

        assert result.input_tokens == 600
        assert result.cache_read_tokens == 400

    def test_every_retry_is_counted(self):
        from mra.usage import Ledger

        ledger = Ledger()
        stub = StubOpenAI([
            reply(arguments='{"title": "t"}', prompt=100, completion=10),
            reply(arguments='{"title": "t", "score": 1}', prompt=120, completion=10),
        ])
        llm = LLM(Config(provider="openai", model="deepseek-chat"), client=stub, ledger=ledger)
        llm.parse("sys", [{"role": "user", "content": "go"}], Card)

        assert ledger.session.calls == 2, "a retry costs money and must be reported"
        assert ledger.session.input_tokens == 220

    def test_a_response_without_usage_does_not_crash(self):
        stub = StubOpenAI([type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {
                "content": "x", "tool_calls": None})()})()],
            "usage": None, "model": "m",
        })()])
        assert openai_llm(stub).text("sys", [{"role": "user", "content": "go"}]).text == "x"


class TestErrorMessages:
    """A billing failure used to surface as a forty-line traceback ending in a
    JSON blob. The fix is the researcher's to make; the message has to say so."""

    def _error(self, message, status=None):
        exc = RuntimeError(message)
        if status is not None:
            exc.status_code = status
        return exc

    def test_out_of_credit_names_the_page_and_says_work_is_kept(self):
        text = backends.describe_api_error(self._error(
            "Error code: 400 - your credit balance is too low to access the API"))
        assert "余额不足" in text
        assert "Plans & Billing" in text
        assert "继续" in text, "the user needs to know a re-run resumes"

    def test_a_bad_key_mentions_the_windows_trap(self):
        text = backends.describe_api_error(self._error("invalid x-api-key", status=401))
        assert "setx" in text

    def test_rate_limit_says_to_wait(self):
        assert "限流" in backends.describe_api_error(self._error("rate limit", status=429))

    def test_a_server_fault_is_not_blamed_on_the_user(self):
        assert "不是你的问题" in backends.describe_api_error(self._error("boom", status=503))

    def test_an_unrecognised_error_still_shows_what_happened(self):
        assert "something odd" in backends.describe_api_error(self._error("something odd"))

    def test_the_handler_covers_the_installed_sdks(self):
        import anthropic
        assert anthropic.APIError in backends.api_error_types()
