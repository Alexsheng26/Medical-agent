"""Tests for the drafting/polish layer, using a stub model.

The point is to pin down the loop's control flow and its content-fidelity
guards without spending API calls or depending on model behaviour.
"""

import pytest

from mra import writing
from mra.config import Config
from mra.llm import LLMResult

SLOPPY = """
In the realm of fibrosis, it is worth noting that macrophages play a crucial role.
Furthermore, this intricate interplay cannot be overstated in this context here.
Moreover, these findings underscore the importance of a comprehensive understanding.
Additionally, such work paves the way for novel strategies in the years ahead now.
Notably, the potential to revolutionize treatment paradigms is a testament to it.
"""

CLEAN = """
Macrophage-derived TGF-beta1 drives portal fibrosis. Deleting Tgfb1 in the
myeloid compartment cut Sirius red-positive area by 38% (p = 0.002) after twelve
weeks on CDAHFD.

Human biopsies agreed. Portal macrophage density tracked fibrosis stage across
48 samples (r = 0.61, p < 0.001), and the association survived adjustment for
BMI and diabetes duration.
"""


class StubLLM:
    """Returns queued responses in order; records the prompts it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def text(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        reply = self.responses.pop(0) if self.responses else "(exhausted)"
        return LLMResult(text=reply)

    def parse(self, system, messages, schema, **kwargs):  # pragma: no cover
        raise AssertionError("parse should not be called in these tests")


@pytest.fixture
def cfg(tmp_path):
    config = Config(workspace=tmp_path / ".mra")
    config.ensure_workspace()
    return config


class TestPolishLoop:
    def test_stops_once_the_target_is_reached(self, cfg):
        llm = StubLLM([CLEAN])
        result = writing.polish(cfg, llm, SLOPPY, target=35.0, max_rounds=3)

        assert result.rounds == 1
        assert result.final.score < result.initial.score
        assert result.text.strip() == CLEAN.strip()

    def test_clean_text_needs_no_rounds(self, cfg):
        llm = StubLLM([])
        result = writing.polish(cfg, llm, CLEAN, target=35.0)

        assert result.rounds == 0
        assert llm.calls == [], "no model call when the text already passes"
        assert result.text == CLEAN

    def test_a_worse_rewrite_is_discarded(self, cfg):
        # The stub "improves" the text into something even sloppier.
        llm = StubLLM([SLOPPY + SLOPPY])
        result = writing.polish(cfg, llm, SLOPPY, target=1.0, max_rounds=3)

        assert result.rounds == 1, "loop must stop rather than keep degrading"
        assert result.text == SLOPPY, "the previous text stands"

    def test_respects_the_round_cap(self, cfg):
        # Each response is slightly better, so the loop would otherwise continue.
        responses = [SLOPPY.replace("Furthermore, ", "", i) for i in range(1, 6)]
        llm = StubLLM(responses)
        result = writing.polish(cfg, llm, SLOPPY, target=0.0, max_rounds=2)
        assert result.rounds <= 2

    def test_lint_report_is_passed_to_the_model(self, cfg):
        llm = StubLLM([CLEAN])
        writing.polish(cfg, llm, SLOPPY, target=35.0)

        system_text = " ".join(
            block["text"] if isinstance(block, dict) else str(block)
            for block in llm.calls[0]["system"]
        )
        assert "AI-tell lint score" in system_text, "the rewrite must be lint-driven"
        assert "stock phrase" in system_text

    def test_progress_callback_fires(self, cfg):
        seen = []
        llm = StubLLM([CLEAN])
        writing.polish(
            cfg, llm, SLOPPY, target=35.0,
            on_round=lambda i, before, after: seen.append((i, before, after)),
        )
        assert len(seen) == 1
        assert seen[0][0] == 1


class TestFidelityGuards:
    def test_dropped_citation_is_warned(self):
        warnings = writing._fidelity_warnings(
            "Fibrosis rose [PMID:31234567].", "Fibrosis rose."
        )
        assert any("dropped citations" in w for w in warnings)
        assert any("31234567" in w for w in warnings)

    def test_invented_citation_is_warned(self):
        warnings = writing._fidelity_warnings(
            "Fibrosis rose.", "Fibrosis rose [PMID:99999999]."
        )
        assert any("introduced citations" in w for w in warnings)

    def test_lost_numbers_are_warned(self):
        warnings = writing._fidelity_warnings(
            "Area fell by 38% (p = 0.002).", "Area fell substantially."
        )
        assert any("Numbers present" in w for w in warnings)

    def test_removed_data_markers_are_warned(self):
        warnings = writing._fidelity_warnings(
            "We saw an effect [DATA NEEDED: n per group].", "We saw an effect."
        )
        assert any("DATA NEEDED" in w for w in warnings)

    def test_faithful_rewrite_produces_no_warnings(self):
        before = "Sirius red area fell 38% (p = 0.002) [PMID:31234567]."
        after = "Fibrotic area dropped by 38% (p = 0.002) [PMID:31234567]."
        assert writing._fidelity_warnings(before, after) == []

    def test_warnings_surface_in_the_polish_result(self, cfg):
        llm = StubLLM([CLEAN.replace("(p = 0.002)", "")])
        result = writing.polish(cfg, llm, SLOPPY + " Effect size 0.002 held.", target=35.0)
        assert result.warnings


class TestFenceStripping:
    def test_strips_wrapping_fence(self):
        assert writing._strip_fences("```markdown\n# Title\n\nBody\n```") == "# Title\n\nBody"

    def test_leaves_unfenced_text_alone(self):
        assert writing._strip_fences("# Title\n\nBody") == "# Title\n\nBody"

    def test_leaves_internal_fences_alone(self):
        text = "Text\n\n```python\ncode\n```\n\nMore"
        assert writing._strip_fences(text) == text


def test_polish_summary_reports_both_scores(cfg):
    llm = StubLLM([CLEAN])
    result = writing.polish(cfg, llm, SLOPPY, target=35.0)
    summary = result.summary()
    assert "De-AI pass" in summary
    assert "→" in summary
