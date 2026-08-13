"""The self-check.

Its whole reason for existing is that some questions can only be answered on
the researcher's machine — this environment cannot reach every provider — so
the important property is that a half-working endpoint is reported as half
working rather than as fine.
"""

import pytest

from mra import doctor
from mra.config import Config


class StubLLM:
    def __init__(self, *, text_ok=True, parse_ok=True):
        self.text_ok = text_ok
        self.parse_ok = parse_ok

    def text(self, *a, **k):
        if not self.text_ok:
            raise RuntimeError("Error code: 401 - invalid x-api-key")
        from mra.llm import LLMResult
        return LLMResult(text="ready")

    def parse(self, system, messages, schema, **k):
        if not self.parse_ok:
            from mra.backends import BackendError
            raise BackendError("no valid Ping after 2 attempts")
        return schema(ok=True, model_note="fine")


@pytest.fixture
def stubbed(monkeypatch):
    def use(llm):
        monkeypatch.setattr(doctor, "LLM", lambda cfg: llm)
    return use


class TestKeyDiscovery:
    def test_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        assert doctor.key_variable("anthropic") == ("ANTHROPIC_API_KEY", True)

    def test_anthropic_falls_back_to_the_token_variable(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
        assert doctor.key_variable("anthropic") == ("ANTHROPIC_AUTH_TOKEN", True)

    def test_openai_prefers_the_mra_variable(self, monkeypatch):
        monkeypatch.setenv("MRA_API_KEY", "x")
        monkeypatch.setenv("OPENAI_API_KEY", "y")
        assert doctor.key_variable("openai") == ("MRA_API_KEY", True)

    def test_a_missing_key_names_the_one_to_set(self, monkeypatch):
        for name in ("MRA_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        name, present = doctor.key_variable("openai")
        assert (name, present) == ("MRA_API_KEY", False)


class TestProbes:
    def test_no_key_stops_before_spending(self, monkeypatch, tmp_path):
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(doctor, "LLM", lambda cfg: pytest.fail("must not build a client"))

        report = doctor.run(Config(workspace=tmp_path))
        assert not report.prose_ok

    def test_a_working_endpoint_passes_both(self, monkeypatch, stubbed, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        stubbed(StubLLM())
        report = doctor.run(Config(workspace=tmp_path))
        assert report.prose_ok and report.structured_ok

    def test_no_tool_calling_is_reported_as_half_working(self, monkeypatch, stubbed, tmp_path):
        """The DeepSeek case worth catching: prose fine, structured commands not."""
        monkeypatch.setenv("MRA_API_KEY", "x")
        stubbed(StubLLM(parse_ok=False))
        cfg = Config(workspace=tmp_path, provider="openai")

        report = doctor.run(cfg)
        assert report.prose_ok
        assert not report.structured_ok

        text = doctor.format_report(cfg, report)
        assert "只有一半可用" in text
        assert "digest" in text and "chat" in text, "both lists must be named"

    def test_a_dead_endpoint_does_not_probe_structured_output(
        self, monkeypatch, stubbed, tmp_path
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        stubbed(StubLLM(text_ok=False))
        report = doctor.run(Config(workspace=tmp_path))

        assert not report.prose_ok
        assert not any(c.name == "structured" for c in report.checks), (
            "nothing structured can work if prose cannot; a second failing call "
            "is noise and costs money"
        )

    def test_the_failure_is_translated_not_dumped(self, monkeypatch, stubbed, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        stubbed(StubLLM(text_ok=False))
        cfg = Config(workspace=tmp_path)

        text = doctor.format_report(cfg, doctor.run(cfg))
        assert "setx" in text, "a bad key should point at the usual Windows cause"


class TestReport:
    def test_the_configuration_is_echoed(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MRA_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(workspace=tmp_path, provider="openai",
                     base_url="https://api.deepseek.com", model="deepseek-chat")

        text = doctor.format_report(cfg, doctor.run(cfg))
        assert "https://api.deepseek.com" in text
        assert "deepseek-chat" in text
        assert "MRA_API_KEY" in text
