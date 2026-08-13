"""Review planning and writing.

The property worth protecting: a review is assembled from papers that are
actually in the knowledge base. A section tied to an id nobody has is the
failure mode that produces a plausible review with fabricated references.
"""

import pytest

from mra import review
from mra.config import Config
from mra.pubmed import Article
from mra.schemas import ReviewOutline, ReviewSection
from mra.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        s.add_articles(
            [
                Article(pmid="31234567", title="Macrophage TGF-beta1 drives portal fibrosis",
                        abstract="Myeloid Tgfb1 deletion reduced Sirius red area by 38%.",
                        journal_abbrev="Hepatology", year="2019"),
                Article(pmid="28001122", title="Stellate activation independent of Kupffer cells",
                        abstract="Clodronate depletion did not prevent activation.",
                        journal_abbrev="J Hepatol", year="2016"),
                Article(pmid="34556677", title="TREM2 macrophages restrain fibrogenesis",
                        abstract="Trem2 deletion raised fibrotic area 46%.",
                        journal_abbrev="J Hepatol", year="2022"),
            ]
        )
        yield s


def _outline(**overrides):
    payload = dict(
        title="One cell, two arms",
        scope="Focused on macrophage TGF-beta1 in fibrosis.",
        sections=[
            ReviewSection(heading="The problem", argument="Fibrosis kills.",
                          identifiers=["31234567"]),
            ReviewSection(heading="The conflict", argument="Two directions.",
                          identifiers=["28001122", "34556677"]),
        ],
        controversies=["31234567 and 34556677 point opposite ways"],
        gaps=["no human genetics"],
        unsupported=["that TREM2 is a drug target"],
    )
    payload.update(overrides)
    return ReviewOutline(**payload)


class PlanLLM:
    """Returns a fixed outline; records that retrieval reached the prompt."""

    def __init__(self, outline):
        self.outline = outline
        self.system = None

    def parse(self, system, messages, schema, **kwargs):
        self.system = system
        return self.outline

    def text(self, system, messages, **kwargs):
        from mra.llm import LLMResult
        self.system = system
        return LLMResult(text="Section prose citing [PMID:31234567].")


class TestPlanning:
    def test_invented_identifiers_are_dropped(self, store, tmp_path):
        """A section tied to a paper nobody has produces a fabricated reference."""
        llm = PlanLLM(_outline(sections=[
            ReviewSection(heading="S", argument="a", identifiers=["31234567", "99999999"]),
        ]))
        outline, _ = review.plan(Config(workspace=tmp_path), store, llm, "fibrosis macrophage")

        assert outline.sections[0].identifiers == ["31234567"]

    def test_a_section_with_no_real_papers_is_removed(self, store, tmp_path):
        llm = PlanLLM(_outline(sections=[
            ReviewSection(heading="Real", argument="a", identifiers=["31234567"]),
            ReviewSection(heading="Invented", argument="b", identifiers=["99999999"]),
        ]))
        outline, _ = review.plan(Config(workspace=tmp_path), store, llm, "fibrosis")

        assert [s.heading for s in outline.sections] == ["Real"]

    def test_an_outline_grounded_in_nothing_is_an_error(self, store, tmp_path):
        llm = PlanLLM(_outline(sections=[
            ReviewSection(heading="Invented", argument="b", identifiers=["99999999"]),
        ]))
        with pytest.raises(ValueError, match="not what this knowledge base covers"):
            review.plan(Config(workspace=tmp_path), store, llm, "quantum gravity")

    def test_empty_knowledge_base_says_so_before_spending(self, tmp_path):
        with Store(tmp_path / "empty.db") as empty:
            llm = PlanLLM(_outline())
            with pytest.raises(ValueError, match="empty"):
                review.plan(Config(workspace=tmp_path), empty, llm, "anything")
            assert llm.system is None, "no model call on an empty library"

    def test_the_corpus_reaches_the_planner(self, store, tmp_path):
        llm = PlanLLM(_outline())
        review.plan(Config(workspace=tmp_path), store, llm, "macrophage fibrosis")
        assert "[PMID:31234567]" in "\n".join(llm.system)


class TestWriting:
    def test_each_section_sees_only_its_own_papers(self, store, tmp_path):
        """A section written against the whole corpus drifts into generalities;
        this is what keeps a long review anchored."""
        seen = []

        class Recording(PlanLLM):
            def text(self, system, messages, **kwargs):
                seen.append("\n".join(system))
                from mra.llm import LLMResult
                return LLMResult(text="Prose [PMID:31234567].")

        review.write(Config(workspace=tmp_path), store, Recording(_outline()), _outline())

        assert "28001122" not in seen[0], "section 1 must not see section 2's papers"
        assert "28001122" in seen[1]

    def test_sections_are_assembled_in_order(self, store, tmp_path):
        text, _ = review.write(Config(workspace=tmp_path), store, PlanLLM(_outline()), _outline())
        assert text.index("## The problem") < text.index("## The conflict")
        assert text.startswith("# One cell, two arms")

    def test_citations_are_checked_against_the_store(self, store, tmp_path):
        _, report = review.write(Config(workspace=tmp_path), store, PlanLLM(_outline()), _outline())
        assert report.verified == ["31234567"]
        assert report.ok

    def test_position_tells_each_section_where_it_sits(self, store, tmp_path):
        seen = []

        class Recording(PlanLLM):
            def text(self, system, messages, **kwargs):
                seen.append("\n".join(system))
                from mra.llm import LLMResult
                return LLMResult(text="x")

        review.write(Config(workspace=tmp_path), store, Recording(_outline()), _outline())
        assert "It closes the review." in seen[-1]


class TestOutlineRendering:
    def test_controversies_are_shown(self, store):
        text = review.format_outline(_outline(), store, ["31234567", "28001122", "34556677"])
        assert "分歧" in text
        assert "31234567 and 34556677" in text

    def test_unused_papers_are_reported(self, store):
        """An unused paper is either off-topic or a retrieval miss, and only the
        researcher can tell which."""
        text = review.format_outline(
            _outline(), store, ["31234567", "28001122", "34556677", "30778899"]
        )
        assert "未用上" in text
        assert "30778899" in text

    def test_full_coverage_hides_the_unused_line(self, store):
        text = review.format_outline(_outline(), store, ["31234567", "28001122", "34556677"])
        assert "未用上" not in text
