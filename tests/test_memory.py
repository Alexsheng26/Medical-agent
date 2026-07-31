from pathlib import Path

import pytest

from mra.memory import Memory, measure_style
from mra.pubmed import parse_efetch_xml
from mra.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "efetch_sample.xml"

SAMPLE = """
We enrolled 48 patients. Liver biopsies were scored independently by two
pathologists who were blinded to the clinical data, and disagreements were
resolved by consensus review.

Portal macrophage density correlated with fibrosis stage. We could not exclude
residual confounding by diabetes duration.
"""


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        s.add_articles(parse_efetch_xml(FIXTURE.read_text(encoding="utf-8")))
        yield s


class TestMeasureStyle:
    def test_basic_counts(self):
        stats = measure_style(SAMPLE)
        assert stats["sentences"] == 4
        assert stats["paragraphs"] == 2
        assert stats["longest_sentence"] > stats["shortest_sentence"]

    def test_passive_voice_detected(self):
        stats = measure_style(SAMPLE)
        assert stats["passive_markers_per_100_sentences"] > 0

    def test_first_person_detected(self):
        stats = measure_style(SAMPLE)
        assert stats["first_person_rate"] > 0

    def test_empty_input_is_safe(self):
        stats = measure_style("")
        assert stats["sentences"] == 0.0
        assert stats["mean_sentence_length"] == 0.0


class TestTopicGraph:
    def test_builds_from_mesh_terms(self, store, tmp_path):
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)

        concepts = dict(memory.concepts)
        assert "liver cirrhosis" in concepts
        assert "macrophages" in concepts

    def test_generic_terms_excluded(self, store, tmp_path):
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        # "Humans" is a MeSH term on the fixture but says nothing about direction.
        assert "humans" not in memory.concepts

    def test_cooccurrence_edges(self, store, tmp_path):
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        assert ("liver cirrhosis", "macrophages") in memory.edges

    def test_extraction_keywords_feed_the_graph(self, store, tmp_path):
        store.save_card("31234567", {"mechanism_keywords": ["Smad3", "TGF-beta"]})
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        assert "smad3" in memory.concepts

    def test_hypothesis_trajectory_recorded(self, store, tmp_path):
        store.save_hypothesis({"title": "Macrophage TGF-b1 drives portal fibrosis"})
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        assert memory.hypothesis_titles == ["v1: Macrophage TGF-b1 drives portal fibrosis"]

    def test_refresh_is_not_cumulative(self, store, tmp_path):
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        first = memory.concepts["macrophages"]
        memory.refresh_topics(store)
        assert memory.concepts["macrophages"] == first, "refresh must rebuild, not accumulate"


class TestPersistence:
    def test_roundtrip(self, store, tmp_path):
        path = tmp_path / "memory.json"
        memory = Memory(path=path)
        memory.refresh_topics(store)
        memory.fingerprint = {"mean_sentence_length": 21.4}
        memory.save()

        reloaded = Memory.load(path)
        assert reloaded.concepts == memory.concepts
        # Edge keys are tuples; JSON cannot express that, so they round-trip
        # through an "a||b" encoding.
        assert reloaded.edges == memory.edges
        assert reloaded.fingerprint["mean_sentence_length"] == 21.4
        assert reloaded.updated_at

    def test_missing_file_yields_empty_memory(self, tmp_path):
        memory = Memory.load(tmp_path / "nope.json")
        assert memory.concepts == {}
        assert memory.fingerprint is None

    def test_summary_when_empty(self, tmp_path):
        assert "empty" in Memory.load(tmp_path / "nope.json").summary()

    def test_summary_when_populated(self, store, tmp_path):
        memory = Memory(path=tmp_path / "memory.json")
        memory.refresh_topics(store)
        summary = memory.summary()
        assert "Centre of gravity" in summary
        assert "macrophages" in summary
        assert "not built yet" in summary  # no fingerprint yet
