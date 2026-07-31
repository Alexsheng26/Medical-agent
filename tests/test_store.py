from pathlib import Path

import pytest

from mra.pubmed import Article, parse_efetch_xml
from mra.store import Store, to_fts_query

FIXTURE = Path(__file__).parent / "fixtures" / "efetch_sample.xml"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "kb.db") as s:
        yield s


@pytest.fixture
def populated(store):
    store.add_articles(parse_efetch_xml(FIXTURE.read_text(encoding="utf-8")), topic="fibrosis")
    return store


def test_add_and_count(populated):
    assert populated.count_articles() == 3
    assert populated.has_article("31234567")
    assert not populated.has_article("99999999")


def test_add_is_idempotent(store):
    articles = parse_efetch_xml(FIXTURE.read_text(encoding="utf-8"))
    assert store.add_articles(articles) == 3
    assert store.add_articles(articles) == 0, "re-adding must not duplicate"
    assert store.count_articles() == 3


def test_roundtrip_preserves_fields(populated):
    article = populated.get_article("31234567")
    assert article is not None
    assert article.authors == ["Chen W", "Okonkwo A", "Nilsson E"]
    assert article.mesh_terms
    assert "38%" in article.abstract


def test_search_ranks_the_relevant_paper_first(populated):
    hits = populated.search("macrophage TGF-beta portal fibrosis")
    assert hits, "BM25 search should return something"
    assert hits[0][0].pmid == "31234567"


def test_search_finds_the_contradicting_paper(populated):
    hits = populated.search("Kupffer cell depletion stellate activation")
    assert hits[0][0].pmid == "28001122"


def test_search_handles_punctuation_without_fts_syntax_errors(populated):
    # These would be FTS5 syntax errors if passed through unquoted.
    for query in ['TGF-beta/Smad "signalling"', "NF-kB (p65) AND *", "a", "()"]:
        populated.search(query)  # must not raise


def test_fts_query_builder():
    assert to_fts_query("TGF-beta signalling") == '"TGF-beta" OR "signalling"'
    assert to_fts_query("!!! ()") == ""
    # Single characters are dropped as noise.
    assert to_fts_query("a macrophage") == '"macrophage"'


def test_cards(populated):
    assert "31234567" in populated.pmids_without_cards()
    # The abstract-free record must never be queued for extraction.
    assert "30009999" not in populated.pmids_without_cards()

    populated.save_card("31234567", {"pmid": "31234567", "evidence_strength": 4})
    assert populated.get_card("31234567")["evidence_strength"] == 4
    assert "31234567" not in populated.pmids_without_cards()
    assert populated.count_cards() == 1


def test_hypothesis_versioning(store):
    v1 = store.save_hypothesis({"title": "first"}, note="initial")
    v2 = store.save_hypothesis({"title": "second"}, note="revised")

    assert (v1, v2) == (1, 2)
    assert store.latest_hypothesis()[1]["title"] == "second"
    assert store.get_hypothesis(1)["title"] == "first", "old versions stay retrievable"
    assert len(store.list_hypotheses()) == 2


def test_journal_profiles_are_case_insensitive(store):
    store.save_journal("Hepatology", {"journal": "Hepatology"})
    assert store.get_journal("hepatology") is not None
    assert store.get_journal("HEPATOLOGY") is not None
    assert store.list_journals() == ["hepatology"]


def test_chat_history_is_chronological(store):
    store.append_chat("user", "first")
    store.append_chat("assistant", "reply")
    store.append_chat("user", "second")

    history = store.chat_history()
    assert [t["content"] for t in history] == ["first", "reply", "second"]

    store.clear_chat()
    assert store.chat_history() == []


def test_chat_history_limit_keeps_the_most_recent(store):
    for i in range(10):
        store.append_chat("user", f"msg{i}")
    history = store.chat_history(limit=3)
    assert [t["content"] for t in history] == ["msg7", "msg8", "msg9"]


def test_article_with_empty_abstract_still_stored(store):
    store.add_articles([Article(pmid="1", title="t")])
    assert store.count_articles() == 1
