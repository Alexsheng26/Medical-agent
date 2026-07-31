from pathlib import Path

import pytest

from mra.pubmed import PubMedError, parse_efetch_xml

FIXTURE = Path(__file__).parent / "fixtures" / "efetch_sample.xml"


@pytest.fixture(scope="module")
def articles():
    return parse_efetch_xml(FIXTURE.read_text(encoding="utf-8"))


def test_parses_all_records(articles):
    assert [a.pmid for a in articles] == ["31234567", "28001122", "30009999"]


def test_structured_abstract_keeps_labels(articles):
    abstract = articles[0].abstract
    assert abstract.startswith("Background And Aims:")
    assert "Methods:" in abstract
    assert "38% (p = 0.002)" in abstract, "numeric results must survive parsing"


def test_title_flattens_inline_markup(articles):
    # The fixture wraps the beta symbol in <i> tags mid-title.
    title = articles[0].title
    assert title.startswith("Macrophage-derived TGF-")
    assert "<i>" not in title
    assert title.endswith("1 drives portal fibrosis in murine NASH.")


def test_authors_and_doi(articles):
    first = articles[0]
    assert first.authors == ["Chen W", "Okonkwo A", "Nilsson E"]
    assert first.doi == "10.1002/hep.30712"
    assert first.journal_abbrev == "Hepatology"
    assert first.year == "2019"


def test_mesh_terms(articles):
    assert "Liver Cirrhosis" in articles[0].mesh_terms
    assert "Transforming Growth Factor beta" in articles[0].mesh_terms


def test_medline_date_fallback(articles):
    # Second record has no <Year>, only "2016 Nov-Dec" as a MedlineDate.
    assert articles[1].year == "2016"


def test_collective_author_name(articles):
    assert articles[2].authors == ["The Liver Study Group"]


def test_missing_abstract_is_flagged(articles):
    assert articles[2].is_empty
    assert not articles[0].is_empty


def test_citation_format(articles):
    assert articles[0].citation == "Chen W et al. Hepatology 2019. PMID:31234567"
    # Single-author papers get no "et al."
    assert articles[1].citation == "Yamamoto K J Hepatol 2016. PMID:28001122"


def test_malformed_xml_raises():
    with pytest.raises(PubMedError):
        parse_efetch_xml("<PubmedArticleSet><broken>")


def test_empty_set_is_not_an_error():
    assert parse_efetch_xml("<PubmedArticleSet></PubmedArticleSet>") == []
