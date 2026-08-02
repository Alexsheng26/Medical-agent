"""Tests for scoring and journal recommendation (Clause 4).

The thing worth protecting here is that novelty is judged against the
researcher's own corpus rather than the model's memory. That is a property of
the prompt the caller builds, so these tests read the prompt the stub LLM was
handed.
"""

import pytest

from mra import assess, prompts
from mra.config import Config
from mra.pubmed import Article
from mra.schemas import (
    DimensionScore,
    FitAssessment,
    JournalCandidate,
    JournalRecommendation,
)
from mra.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        s.add_articles(
            [
                Article(
                    pmid="31234567",
                    title="Macrophage TGF-beta1 drives portal fibrosis",
                    abstract="Myeloid Tgfb1 deletion reduced Sirius red area by 38%.",
                    journal_abbrev="Hepatology",
                    year="2019",
                ),
                Article(
                    pmid="28001122",
                    title="Stellate activation independent of Kupffer cells",
                    abstract="Clodronate depletion did not prevent activation.",
                    journal_abbrev="J Hepatol",
                    year="2016",
                ),
            ]
        )
        s.save_journal("hepatology", {"journal": "Hepatology", "scope_summary": "liver"})
        yield s


@pytest.fixture
def data_file(tmp_path):
    path = tmp_path / "counts.csv"
    rows = "\n".join(f"gene{i},{i * 1.5},0.0{i}" for i in range(200))
    path.write_text(f"gene,fold_change,pvalue\n{rows}\n", encoding="utf-8")
    return path


class StubLLM:
    """Captures the prompt, returns a fixed structured result."""

    def __init__(self, result):
        self.result = result
        self.system = None

    def parse(self, system, messages, schema, **kwargs):
        self.system = system
        return self.result

    def text(self, *a, **k):  # pragma: no cover
        raise AssertionError("assess should not call text()")


def _recommendation(**overrides):
    payload = dict(
        dimensions=[DimensionScore(dimension="novelty", score=9, justification="j", gaps=[])],
        strongest_claim="claim",
        overclaims=[],
        candidates=[
            JournalCandidate(journal="Hepatology", tier="reach", why="w", gaps=["g"], odds="low"),
            JournalCandidate(journal="Liver International", tier="safe", why="w", gaps=[], odds="fair"),
        ],
        suggested_experiments=["e"],
        reality_check="check",
    )
    payload.update(overrides)
    return JournalRecommendation(**payload)


def _assessment():
    return FitAssessment(
        target_journal="Hepatology",
        dimensions=[DimensionScore(dimension="novelty", score=0, justification="j", gaps=[])],
        overall_verdict="needs targeted additions",
        strongest_claim="claim",
        overclaims=[],
        suggested_experiments=["e"],
        alternative_journals=[],
    )


class TestRetrievalQuery:
    def test_notes_lead_the_query(self, store, data_file):
        blocks = assess._data_blocks([data_file], "portal fibrosis in human biopsies")
        query = assess.retrieval_query(store, blocks, "portal fibrosis in human biopsies")
        assert query.startswith("portal fibrosis in human biopsies")

    def test_hypothesis_is_folded_in(self, store, data_file):
        store.save_hypothesis(
            {
                "title": "Kupffer cells set the fibrotic set-point",
                "knowledge_gap": "no human septal data",
                "mechanism_chain": ["step one", "step two"],
            }
        )
        blocks = assess._data_blocks([data_file], "")
        query = assess.retrieval_query(store, blocks, "")
        assert "Kupffer cells set the fibrotic set-point" in query
        assert "no human septal data" in query
        assert "step one" in query

    def test_table_body_is_excluded(self, store, data_file):
        """Every token becomes an OR term; 200 gene IDs would swamp the ranking."""
        blocks = assess._data_blocks([data_file], "")
        query = assess.retrieval_query(store, blocks, "")
        assert "fold_change" in query, "column names are searchable"
        assert "gene150" not in query, "the table body is not"

    def test_prose_data_survives_intact(self, store, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("Sirius red area fell 38% in myeloid Tgfb1 knockouts.", encoding="utf-8")
        blocks = assess._data_blocks([path], "")
        assert "Sirius red" in assess.retrieval_query(store, blocks, "")

    def test_no_data_files_is_an_error(self, store):
        with pytest.raises(ValueError):
            assess._data_blocks([], "")


class TestGrounding:
    """Novelty judged from memory is an opinion; judged from the corpus it is a
    claim the researcher can check. Both paths must carry the corpus."""

    def test_recommend_puts_retrieved_papers_in_the_prompt(self, store, data_file):
        llm = StubLLM(_recommendation())
        assess.recommend(Config(), store, llm, [data_file], notes="macrophage portal fibrosis")
        prompt = "\n".join(llm.system)
        assert "[PMID:31234567]" in prompt
        assert "only papers you may cite" in prompt

    def test_assess_puts_retrieved_papers_in_the_prompt(self, store, data_file):
        llm = StubLLM(_assessment())
        assess.assess(Config(), store, llm, "Hepatology", [data_file],
                      notes="macrophage portal fibrosis")
        prompt = "\n".join(llm.system)
        assert "[PMID:31234567]" in prompt

    def test_empty_knowledge_base_is_declared_not_hidden(self, tmp_path, data_file):
        with Store(tmp_path / "empty.db") as empty:
            llm = StubLLM(_recommendation())
            assess.recommend(Config(), empty, llm, [data_file], notes="fibrosis")
            assert "knowledge base is empty" in "\n".join(llm.system)

    def test_every_placeholder_is_filled(self, store, data_file):
        llm = StubLLM(_recommendation())
        assess.recommend(Config(), store, llm, [data_file], notes="fibrosis")
        assert "{context}" not in "\n".join(llm.system)
        assert "{data}" not in "\n".join(llm.system)

    def test_evaluate_prompt_declares_a_context_slot(self):
        """Guards against the placeholder being dropped from the Markdown."""
        assert "{context}" in prompts.load("evaluate")


class TestScoreClamping:
    def test_recommend_clamps_out_of_range_scores(self, store, data_file):
        llm = StubLLM(_recommendation())
        result = assess.recommend(Config(), store, llm, [data_file])
        assert result.dimensions[0].score == 5

    def test_assess_clamps_out_of_range_scores(self, store, data_file):
        llm = StubLLM(_assessment())
        result = assess.assess(Config(), store, llm, "Hepatology", [data_file])
        assert result.dimensions[0].score == 1


class TestRecommendationRendering:
    def test_candidates_are_numbered_in_order(self, store):
        text = assess.format_recommendation(_recommendation(), store)
        assert text.index("1. Hepatology") < text.index("2. Liver International")

    def test_existing_profiles_are_marked(self, store):
        text = assess.format_recommendation(_recommendation(), store)
        assert "Hepatology   ▲ 冲刺  [profile on file]" in text
        assert "[no style profile yet]" in text

    def test_points_at_the_next_command(self, store):
        """A recommendation the researcher cannot act on is half a feature."""
        text = assess.format_recommendation(_recommendation(), store)
        assert 'mra journal add "Liver International"' in text

    def test_no_store_means_no_profile_markers(self):
        text = assess.format_recommendation(_recommendation())
        assert "profile on file" not in text
        assert "mra journal add" not in text

    def test_all_profiled_suppresses_the_hint(self, store):
        store.save_journal("liver international", {"journal": "Liver International"})
        text = assess.format_recommendation(_recommendation(), store)
        assert "mra journal add" not in text

    def test_unknown_tier_passes_through(self, store):
        recommendation = _recommendation(
            candidates=[
                JournalCandidate(journal="Gut", tier="moonshot", why="w", gaps=[], odds="slim")
            ]
        )
        assert "moonshot" in assess.format_recommendation(recommendation, store)

    def test_reality_check_is_shown(self, store):
        assert "check" in assess.format_recommendation(_recommendation(), store)
