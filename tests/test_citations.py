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
