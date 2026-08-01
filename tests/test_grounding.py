"""Tests for retrieval grounding, batch resilience, and the pure helpers.

Retrieval is what makes the citation check meaningful: if a paper is not
rendered into the prompt, the model has no legitimate way to cite it.
"""

import pytest

from mra import assess, dialogue, retrieval
from mra.config import Config
from mra.pipeline import digest
from mra.pubmed import Article
from mra.schemas import LitCard
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
                    publication_types=["Journal Article"],
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
        yield s


class TestRetrieval:
    def test_returns_citable_pmids(self, store):
        context, pmids = retrieval.build_context(store, "macrophage fibrosis TGF")
        assert "31234567" in pmids
        assert "[PMID:31234567]" in context

    def test_states_the_citation_boundary(self, store):
        context, _ = retrieval.build_context(store, "fibrosis")
        assert "only papers you may cite" in context

    def test_empty_knowledge_base_says_so(self, tmp_path):
        with Store(tmp_path / "empty.db") as empty:
            context, pmids = retrieval.build_context(empty, "anything")
            assert pmids == []
            assert "no matching papers" in context

    def test_card_replaces_the_raw_abstract(self, store):
        store.save_card(
            "31234567",
            {
                "scientific_question": "What drives portal fibrosis?",
                "key_findings": ["Myeloid Tgfb1 deletion cut fibrotic area 38%"],
                "methods": ["CDAHFD mouse model"],
                "limitations": ["Single dietary model"],
                "evidence_strength": 4,
            },
        )
        rendered = retrieval.render_article(store.get_article("31234567"),
                                            store.get_card("31234567"))
        assert "What drives portal fibrosis?" in rendered
        assert "Evidence strength: 4/5" in rendered
        assert "Limitations: Single dietary model" in rendered

    def test_falls_back_to_the_abstract_without_a_card(self, store):
        rendered = retrieval.render_article(store.get_article("31234567"), None)
        assert "Sirius red area by 38%" in rendered

    def test_respects_the_retrieval_limit(self, store):
        _, pmids = retrieval.build_context(store, "fibrosis cells liver", k=1)
        assert len(pmids) == 1

    def test_render_pmid_list_skips_unknown(self, store):
        rendered = retrieval.render_pmid_list(store, ["31234567", "99999999"])
        assert "31234567" in rendered
        assert "99999999" not in rendered


class TestDigestResilience:
    """One bad abstract must not abort a 200-paper extraction run."""

    class FlakyLLM:
        def __init__(self, fail_on):
            self.fail_on = fail_on
            self.seen = []

        def parse(self, system, messages, schema, **kwargs):
            content = messages[0]["content"]
            pmid = content.split("PMID: ")[1].split("\n")[0]
            self.seen.append(pmid)
            if pmid in self.fail_on:
                raise RuntimeError("model returned nothing parseable")
            return LitCard(
                pmid=pmid,
                scientific_question="q",
                key_findings=["f"],
                methods=["m"],
                novelty_claim="n",
                limitations=["l"],
                mechanism_keywords=["TGF-beta"],
                clinical_relevance="c",
                evidence_strength=9,  # deliberately out of range
            )

        def text(self, *a, **k):  # pragma: no cover
            raise AssertionError("digest should not call text()")

    def test_failures_are_skipped_not_fatal(self, store, tmp_path):
        cfg = Config(workspace=tmp_path / ".mra")
        llm = self.FlakyLLM(fail_on={"31234567"})

        ok, failed = digest(cfg, store, llm)

        assert (ok, failed) == (1, 1)
        assert len(llm.seen) == 2, "the batch must continue past the failure"
        assert store.get_card("28001122") is not None
        assert store.get_card("31234567") is None

    def test_evidence_strength_is_clamped(self, store, tmp_path):
        cfg = Config(workspace=tmp_path / ".mra")
        digest(cfg, store, llm=self.FlakyLLM(fail_on=set()))
        assert store.get_card("28001122")["evidence_strength"] == 5

    def test_pmid_comes_from_our_record_not_the_model(self, store, tmp_path):
        cfg = Config(workspace=tmp_path / ".mra")
        digest(cfg, store, llm=self.FlakyLLM(fail_on=set()))
        assert store.get_card("28001122")["pmid"] == "28001122"


class TestHypothesisDiff:
    def test_reports_scalar_changes(self, store):
        store.save_hypothesis({"title": "old title", "novelty_level": 2})
        store.save_hypothesis({"title": "new title", "novelty_level": 4})

        text = dialogue.diff_hypotheses(store, 1, 2)
        assert "- old title" in text
        assert "+ new title" in text

    def test_reports_list_additions_and_removals(self, store):
        store.save_hypothesis({"mechanism_chain": ["step A", "step B"]})
        store.save_hypothesis({"mechanism_chain": ["step A", "step C"]})

        text = dialogue.diff_hypotheses(store, 1, 2)
        assert "+ step C" in text
        assert "- step B" in text
        assert "step A" not in text, "unchanged entries are noise"

    def test_identical_versions_report_no_change(self, store):
        store.save_hypothesis({"title": "same"})
        store.save_hypothesis({"title": "same"})
        assert "(identical)" in dialogue.diff_hypotheses(store, 1, 2)

    def test_missing_version_raises(self, store):
        store.save_hypothesis({"title": "only one"})
        with pytest.raises(ValueError):
            dialogue.diff_hypotheses(store, 1, 99)


class TestDataLoading:
    def test_csv_is_summarised_not_dumped(self, tmp_path):
        path = tmp_path / "big.csv"
        rows = "\n".join(f"gene{i},{i * 1.5},0.0{i}" for i in range(500))
        path.write_text(f"gene,fold_change,pvalue\n{rows}\n", encoding="utf-8")

        described = assess.load_data_description(path)

        assert "COLUMNS (3): gene,fold_change,pvalue" in described
        assert "ROWS: 500" in described
        assert "gene0,0.0,0.00" in described
        # Only a sample of rows is included, not the whole matrix.
        assert "gene499" not in described

    def test_tsv_uses_tab_delimiter(self, tmp_path):
        path = tmp_path / "d.tsv"
        path.write_text("a\tb\n1\t2\n", encoding="utf-8")
        assert "COLUMNS (2): a\tb" in assess.load_data_description(path)

    def test_text_passes_through(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("n = 12 per group, two independent cohorts.", encoding="utf-8")
        assert "n = 12 per group" in assess.load_data_description(path)

    def test_empty_csv_is_handled(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        assert "is empty" in assess.load_data_description(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            assess.load_data_description(tmp_path / "nope.csv")


class TestLocalDocumentRendering:
    """The model can only cite what the context tells it how to cite."""

    def test_local_document_shows_the_local_marker(self, store):
        store.add_articles([Article(pmid="local:a1b2c3d4", title="My PDF",
                                    abstract="Full text of my own paper.")])
        rendered = retrieval.render_article(store.get_article("local:a1b2c3d4"))
        assert rendered.startswith("[LOCAL:a1b2c3d4]")

    def test_local_document_declares_its_provenance(self, store):
        store.add_articles([Article(pmid="local:a1b2c3d4", title="My PDF",
                                    abstract="Full text.")])
        rendered = retrieval.render_article(store.get_article("local:a1b2c3d4"))
        assert "local full text" in rendered

    def test_pubmed_records_are_unchanged(self, store):
        rendered = retrieval.render_article(store.get_article("31234567"))
        assert rendered.startswith("[PMID:31234567]")
        assert "local full text" not in rendered

    def test_mixed_context_carries_both_marker_types(self, store):
        store.add_articles([Article(pmid="local:beef1234", title="My macrophage PDF",
                                    abstract="Macrophage fibrosis portal findings.")])
        context, ids = retrieval.build_context(store, "macrophage fibrosis portal")
        assert "local:beef1234" in ids
        assert "[LOCAL:beef1234]" in context
        assert "[PMID:31234567]" in context
