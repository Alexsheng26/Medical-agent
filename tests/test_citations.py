import pytest

from mra import citations
from mra.pubmed import Article
from mra.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        s.add_articles(
            [
                Article(
                    pmid="31234567",
                    title="Macrophage-derived TGF-beta1 drives portal fibrosis.",
                    abstract="...",
                    journal="Hepatology",
                    journal_abbrev="Hepatology",
                    year="2019",
                    authors=["Chen W", "Okonkwo A", "Nilsson E", "Silva R"],
                    doi="10.1002/hep.30712",
                )
            ]
        )
        yield s


def test_verified_citation_passes(store):
    report = citations.check("Macrophages drive fibrosis [PMID:31234567].", store)
    assert report.ok
    assert report.verified == ["31234567"]
    assert report.unverified == []


def test_fabricated_citation_is_caught(store):
    report = citations.check(
        "One study showed X [PMID:31234567] and another showed Y [PMID:99999999].", store
    )
    assert not report.ok
    assert report.unverified == ["99999999"]
    assert "NOT IN KNOWLEDGE BASE" in report.summary()


def test_duplicate_citations_counted_once(store):
    report = citations.check("[PMID:31234567] and again [PMID:31234567].", store)
    assert report.cited == ["31234567"]


def test_loose_pmid_reference_flagged(store):
    report = citations.check("As shown previously (PMID: 31234567).", store)
    assert report.malformed == ["31234567"]
    assert not report.ok


def test_no_citations_is_reported(store):
    report = citations.check("A claim with no support at all.", store)
    assert report.cited == []
    assert "No citations found" in report.summary()


def test_reference_list_formats_correctly(store):
    refs = citations.reference_list("See [PMID:31234567].", store)
    assert refs.startswith("1. Chen W, Okonkwo A, Nilsson E, et al.")
    assert "Hepatology. 2019" in refs
    assert "doi:10.1002/hep.30712" in refs


def test_reference_list_marks_unknown_pmids(store):
    refs = citations.reference_list("See [PMID:12345678].", store)
    assert "NOT IN KNOWLEDGE BASE" in refs


def test_reference_numbering_follows_first_appearance(store):
    store.add_articles([Article(pmid="28001122", title="Second", year="2016")])
    refs = citations.reference_list("[PMID:28001122] then [PMID:31234567].", store)
    assert refs.splitlines()[0].startswith("1.")
    assert "28001122" in refs.splitlines()[0]


def test_case_insensitive_marker(store):
    assert citations.check("[pmid:31234567]", store).verified == ["31234567"]


class TestLocalDocuments:
    """The researcher's own imported full texts cite as [LOCAL:hash].

    Widening the namespace must not weaken QA-1: both forms are verified the
    same way, and an unknown one of either kind is still caught.
    """

    @pytest.fixture
    def mixed_store(self, store):
        store.add_articles([
            Article(
                pmid="local:a1b2c3d4",
                title="A preprint from my own library",
                abstract="Full text.",
                journal="bioRxiv",
                year="2024",
                authors=["Nakamura T"],
                publication_types=["Local full text"],
            )
        ])
        return store

    def test_local_citation_verifies(self, mixed_store):
        report = citations.check("As shown previously [LOCAL:a1b2c3d4].", mixed_store)
        assert report.ok
        assert report.verified == ["local:a1b2c3d4"]

    def test_fabricated_local_citation_is_caught(self, mixed_store):
        report = citations.check("Claimed in [LOCAL:deadbeef].", mixed_store)
        assert not report.ok
        assert report.unverified == ["local:deadbeef"]

    def test_both_namespaces_in_one_document(self, mixed_store):
        report = citations.check(
            "Published work [PMID:31234567] and my own data [LOCAL:a1b2c3d4].",
            mixed_store,
        )
        assert report.ok
        assert set(report.verified) == {"31234567", "local:a1b2c3d4"}

    def test_mixed_document_still_catches_one_fake(self, mixed_store):
        report = citations.check(
            "Real [PMID:31234567], real [LOCAL:a1b2c3d4], fake [PMID:99999999].",
            mixed_store,
        )
        assert report.unverified == ["99999999"]

    def test_numbering_follows_first_appearance_across_namespaces(self, mixed_store):
        refs = citations.reference_list(
            "First [LOCAL:a1b2c3d4] then [PMID:31234567].", mixed_store
        )
        lines = refs.splitlines()
        assert lines[0].startswith("1.") and "a1b2c3d4" in lines[0]
        assert lines[1].startswith("2.") and "31234567" in lines[1]

    def test_local_reference_declares_its_provenance(self, mixed_store):
        refs = citations.reference_list("[LOCAL:a1b2c3d4]", mixed_store)
        assert "local full text" in refs, "the reader must see this was not indexed"
        assert "Nakamura T" in refs

    def test_case_insensitive_local_marker(self, mixed_store):
        assert citations.check("[local:A1B2C3D4]", mixed_store).verified == ["local:a1b2c3d4"]

    def test_find_citations_normalises_to_stored_form(self):
        assert citations.find_citations("[LOCAL:AABBCCDD] [PMID:123456]") == [
            "local:aabbccdd",
            "123456",
        ]

    def test_titles_are_terminated_consistently(self, mixed_store):
        # PubMed titles usually carry a period; PDF-derived ones often do not.
        refs = citations.reference_list("[LOCAL:a1b2c3d4]", mixed_store)
        assert "A preprint from my own library. bioRxiv" in refs
