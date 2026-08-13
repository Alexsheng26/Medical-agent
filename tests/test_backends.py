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


class TestCredentialCheck:
    """The provider decides which variable holds the key. Getting this wrong
    made every command except `doctor` refuse to start on a non-default
    provider — and doctor passed, which is the worst combination."""

    def test_openai_provider_accepts_the_openai_key(self, monkeypatch, tmp_path):
        from mra import cli

        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("MRA_API_KEY", "sk-test")
        monkeypatch.setattr(cli, "LLM", lambda cfg, ledger=None: "built")

        cfg = Config(workspace=tmp_path, provider="openai", model="deepseek-chat")
        assert cli._llm(cfg) == "built"

    def test_a_missing_key_names_the_right_variable(self, monkeypatch, tmp_path):
        from mra import cli

        for name in ("MRA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        cfg = Config(workspace=tmp_path, provider="openai")

        with pytest.raises(ValueError, match="MRA_API_KEY"):
            cli._llm(cfg)

    def test_anthropic_is_unaffected(self, monkeypatch, tmp_path):
        from mra import cli

        monkeypatch.delenv("MRA_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(cli, "LLM", lambda cfg, ledger=None: "built")
        assert cli._llm(Config(workspace=tmp_path)) == "built"


class TestSchemaInlining:
    """DeepSeek's tool calling does not resolve $ref: given a schema with $defs
    it returned an empty object twice, with no error. Flat schemas on the same
    endpoint worked, so the reference itself was the whole problem."""

    def test_a_flat_schema_is_untouched(self):
        schema = Card.model_json_schema()
        assert backends.inline_refs(schema) is schema

    def test_nested_models_are_expanded(self):
        from mra.schemas import FigureSet

        inlined = backends.inline_refs(FigureSet.model_json_schema())
        assert "$defs" not in inlined
        assert "$ref" not in json.dumps(inlined)

    def test_the_expanded_schema_still_describes_the_leaves(self):
        from mra.schemas import FigureSet

        inlined = backends.inline_refs(FigureSet.model_json_schema())
        panels = inlined["properties"]["figures"]["items"]["properties"]["panels"]
        assert "label" in panels["items"]["properties"]
        assert "claim" in panels["items"]["properties"]

    def test_a_field_keeps_its_own_description(self):
        """A $ref sits beside the field's description; dropping the siblings
        would lose the instruction the model needs."""
        from mra.schemas import FigureSet

        inlined = backends.inline_refs(FigureSet.model_json_schema())
        assert "reading order" in inlined["properties"]["figures"]["description"]

    def test_every_schema_we_send_survives_inlining(self):
        """A schema that still carries $ref reaches DeepSeek as an empty reply."""
        from mra import schemas as s

        models = [
            s.SearchTerms, s.LitCard, s.Hypothesis, s.JournalProfile,
            s.FitAssessment, s.JournalRecommendation, s.ReviewOutline,
            s.FigureSet, s.LocalArticleMeta, s.BriefImpact, s.WritingFingerprint,
        ]
        for model in models:
            inlined = backends.inline_refs(model.model_json_schema())
            assert "$ref" not in json.dumps(inlined), f"{model.__name__} still has a $ref"

    def test_the_tool_definition_carries_the_inlined_schema(self):
        from mra.schemas import FigureSet

        stub = StubOpenAI([reply(arguments='{"figures": [], "story": "s", '
                                 '"caption_overclaims": [], "better_as_table": [], '
                                 '"supplementary": []}')])
        openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], FigureSet)
        sent = json.dumps(stub.requests[0]["tools"][0]["function"]["parameters"])
        assert "$ref" not in sent and "$defs" not in sent


class TestFailedParseAccounting:
    def test_tokens_spent_on_a_failed_parse_are_recorded(self):
        """The run reported one call after making three. Under-reporting cost is
        the one accounting error that always flatters us."""
        from mra.usage import Ledger

        ledger = Ledger()
        stub = StubOpenAI([reply(arguments="{}", prompt=1500, completion=141)] * 2)
        llm = LLM(Config(provider="openai", model="deepseek-chat"), client=stub, ledger=ledger)

        with pytest.raises(backends.BackendError):
            llm.parse("sys", [{"role": "user", "content": "go"}], Card)

        assert ledger.session.calls == 2
        assert ledger.session.input_tokens == 3000


class TestJsonRecovery:
    """DeepSeek returned 20 kB of a good figure plan followed by trailing
    characters, and strict parsing threw all of it away over the tail."""

    def test_clean_json_is_unchanged(self):
        assert json.loads(backends.parse_json_object('{"a": 1}')) == {"a": 1}

    def test_trailing_characters_are_dropped(self):
        raw = '{"a": 1} — I hope this helps!'
        assert json.loads(backends.parse_json_object(raw)) == {"a": 1}

    def test_a_second_concatenated_object_is_ignored(self):
        assert json.loads(backends.parse_json_object('{"a": 1}{"a": 2}')) == {"a": 1}

    def test_a_markdown_fence_is_stripped(self):
        assert json.loads(backends.parse_json_object('```json\n{"a": 1}\n```')) == {"a": 1}

    def test_leading_prose_before_the_object(self):
        assert json.loads(backends.parse_json_object('Here you go: {"a": 1}')) == {"a": 1}

    def test_nothing_parseable_returns_none(self):
        assert backends.parse_json_object("I could not do that.") is None

    def test_a_truncated_object_is_not_salvaged(self):
        """An object cut off mid-write must fail, not be padded into shape."""
        assert backends.parse_json_object('{"a": 1, "b": [1, 2') is None

    def test_a_trailing_tail_no_longer_fails_the_command(self):
        stub = StubOpenAI([reply(arguments='{"title": "t", "score": 3} extra words')])
        assert openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card).score == 3

    def test_the_unparseable_case_says_how_much_came_back(self):
        stub = StubOpenAI([reply(arguments="not json at all")] * 2)
        with pytest.raises(backends.BackendError, match="no parseable JSON"):
            openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], Card)


class TestDoubleEncodedFields:
    """Tool calling sometimes JSON-stringifies a nested value instead of
    emitting it. Every item is present; only the encoding is wrong."""

    SCHEMA = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "string"}},
            "caption": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"inner": {"type": "array", "items": {"type": "string"}}},
            },
        },
    }

    def test_a_stringified_array_is_decoded(self):
        out = backends.coerce_json_strings({"items": '["a", "b"]'}, self.SCHEMA)
        assert out["items"] == ["a", "b"]

    def test_a_real_array_is_untouched(self):
        out = backends.coerce_json_strings({"items": ["a"]}, self.SCHEMA)
        assert out["items"] == ["a"]

    def test_a_string_field_stays_a_string(self):
        """A caption that happens to start with a bracket is still a caption."""
        out = backends.coerce_json_strings({"caption": '["not a list"]'}, self.SCHEMA)
        assert out["caption"] == '["not a list"]'

    def test_a_decoded_value_of_the_wrong_shape_is_left_alone(self):
        """Leaving it makes the validation error tell the truth."""
        out = backends.coerce_json_strings({"items": '{"a": 1}'}, self.SCHEMA)
        assert out["items"] == '{"a": 1}'

    def test_unparseable_strings_are_left_alone(self):
        out = backends.coerce_json_strings({"items": "just prose"}, self.SCHEMA)
        assert out["items"] == "just prose"

    def test_nested_objects_are_walked(self):
        out = backends.coerce_json_strings({"nested": {"inner": '["x"]'}}, self.SCHEMA)
        assert out["nested"]["inner"] == ["x"]

    def test_unknown_keys_survive(self):
        out = backends.coerce_json_strings({"extra": "kept"}, self.SCHEMA)
        assert out["extra"] == "kept"

    def test_the_real_failure_now_parses(self):
        """The exact shape deepseek-chat returned: one list double-encoded."""
        from mra.schemas import FigureSet

        payload = json.dumps({
            "figures": [{
                "number": 1, "handle": "h", "argument": "a",
                "panels": [{"label": "A", "claim": "c", "shows": "s",
                            "plot_type": "p", "source": "demo_data.csv", "caveats": []}],
                "caption": "cap", "missing": [],
            }],
            "story": "s",
            "caption_overclaims": [],
            "better_as_table": '["Cohort/demographic table — a table, not a figure."]',
            "supplementary": [],
        })
        stub = StubOpenAI([reply(arguments=payload)])
        result = openai_llm(stub).parse("sys", [{"role": "user", "content": "go"}], FigureSet)

        assert result.better_as_table == ["Cohort/demographic table — a table, not a figure."]
        assert len(stub.requests) == 1, "no retry needed for an encoding slip"
