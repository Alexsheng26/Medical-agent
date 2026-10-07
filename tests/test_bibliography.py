"""Reference export for EndNote, Zotero and NoteExpress.

An export is only useful if a reference manager can format a bibliography from
it, which takes three things these pin: volume, issue and pages actually stored
(they were not, before); names split the way reference managers expect, with
group authors marked as groups; and files the importers parse. The last is
checked with independent parsers where they are installed, not only by reading
our own output back with our own code.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mra import bibliography, cli
from mra.pubmed import Article, parse_efetch_xml
from mra.store import SCHEMA, Store

XML = """<?xml version="1.0" ?><PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>40000001</PMID><Article>
<Journal><JournalIssue><Volume>70</Volume><Issue>3</Issue><PubDate><Year>2019</Year></PubDate></JournalIssue>
<Title>Hepatology (Baltimore, Md.)</Title><ISOAbbreviation>Hepatology</ISOAbbreviation></Journal>
<ArticleTitle>Macrophage TGF-β1 drives portal fibrosis &amp; scarring.</ArticleTitle>
<Pagination><MedlinePgn>1234-45</MedlinePgn></Pagination>
<ELocationID EIdType="doi">10.1002/hep.30712</ELocationID>
<Abstract><AbstractText Label="RESULTS">Fibrosis fell by 38%.</AbstractText></Abstract>
<AuthorList><Author><LastName>Chen</LastName><Initials>W</Initials></Author>
<Author><LastName>van der Berg</LastName><Initials>AJ</Initials></Author>
<Author><CollectiveName>NASH Clinical Research Network</CollectiveName></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>40000002</PMID><Article>
<Journal><JournalIssue><Volume>12</Volume><Issue>10</Issue><PubDate><Year>2017</Year></PubDate></JournalIssue>
<Title>PloS one</Title><ISOAbbreviation>PLoS One</ISOAbbreviation></Journal>
<ArticleTitle>Kupffer cell depletion_ a 100% online study.</ArticleTitle>
<ELocationID EIdType="pii">e0185809</ELocationID><ELocationID EIdType="doi">10.1371/journal.pone.0185809</ELocationID>
<Abstract><AbstractText>Activation was unchanged.</AbstractText></Abstract>
<AuthorList><Author><LastName>Chen</LastName><Initials>W</Initials></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


@pytest.fixture
def articles() -> list[Article]:
    return parse_efetch_xml(XML)


class TestParsing:
    def test_volume_issue_and_pages_are_kept(self, articles):
        first = articles[0]
        assert (first.volume, first.issue, first.pages) == ("70", "3", "1234-45")

    def test_an_online_only_journal_gives_its_article_number(self, articles):
        assert articles[1].pages == "e0185809"


class TestNames:
    @pytest.mark.parametrize("name, expected", [
        ("Chen W", ("Chen", "W.", True)),
        ("van der Berg AJ", ("van der Berg", "A. J.", True)),
        ("Smith, John", ("Smith", "John", True)),
        ("NASH Clinical Research Network", ("NASH Clinical Research Network", "", False)),
    ])
    def test_split(self, name, expected):
        assert bibliography.split_author(name) == expected

    @pytest.mark.parametrize("pages, expected", [
        ("1234-45", ("1234", "1245")),
        ("1234–1245", ("1234", "1245")),
        ("e0185809", ("e0185809", "")),
        ("S12-S19", ("S12", "S19")),
        ("", ("", "")),
    ])
    def test_page_ranges_are_expanded(self, pages, expected):
        assert bibliography.page_range(pages) == expected


class TestRis:
    def test_a_complete_record(self, articles):
        record = bibliography.to_ris(articles).split("\n\n")[0]
        for line in ("TY  - JOUR", "AU  - Chen, W.", "AU  - van der Berg, A. J.",
                     "VL  - 70", "IS  - 3", "SP  - 1234", "EP  - 1245",
                     "DO  - 10.1002/hep.30712", "AN  - 40000001", "T2  - Hepatology (Baltimore, Md.)"):
            assert line in record.splitlines(), line
        assert record.endswith("ER  - ")

    def test_a_group_author_is_marked_as_one(self, articles):
        """EndNote's convention; without it the group becomes Mr Network."""
        assert "AU  - NASH Clinical Research Network," in bibliography.to_ris(articles)

    def test_a_local_file_says_its_metadata_needs_checking(self):
        local = Article(pmid="local:abcd1234", title="My paper", authors=["Chen W"], year="2024")
        record = bibliography.to_ris([local])
        assert "AN  - " not in record and "local:abcd1234" in record

    def test_values_are_single_line(self):
        article = Article(pmid="1", title="A title\nthat wraps", abstract="Line one\nline two")
        assert "TI  - A title that wraps" in bibliography.to_ris([article])

    def test_an_independent_parser_reads_it(self, articles):
        rispy = pytest.importorskip("rispy")
        parsed = rispy.loads(bibliography.to_ris(articles))
        assert parsed[0]["title"].startswith("Macrophage TGF")
        assert (parsed[0]["volume"], parsed[0]["start_page"], parsed[0]["end_page"]) == ("70", "1234", "1245")
        assert parsed[1]["start_page"] == "e0185809"


class TestBibtex:
    def test_special_characters_are_escaped(self, articles):
        text = bibliography.to_bibtex(articles)
        assert r"fibrosis \& scarring" in text and r"depletion\_ a 100\% online" in text

    def test_group_authors_are_braced_so_they_are_never_split(self, articles):
        assert "{NASH Clinical Research Network}" in bibliography.to_bibtex(articles)

    def test_keys_are_unique(self):
        same = [Article(pmid=str(i), title="t", authors=["Chen W"], year="2019") for i in range(3)]
        text = bibliography.to_bibtex(same)
        assert "@article{Chen2019," in text and "@article{Chen2019a," in text and "@article{Chen2019b," in text

    def test_keys_are_plain_ascii(self):
        key = bibliography.cite_key(Article(pmid="1", authors=["Müller K"], year="2020"), set())
        assert key == "Muller2020"

    def test_an_independent_parser_reads_it(self, articles):
        bibtexparser = pytest.importorskip("bibtexparser")
        entries = bibtexparser.loads(bibliography.to_bibtex(articles)).entries
        assert {e["pages"] for e in entries} == {"1234--1245", "e0185809"}


class TestIncomplete:
    def test_records_without_volume_or_pages_are_named(self, articles):
        bare = Article(pmid="local:1", title="t")
        assert bibliography.incomplete(articles + [bare]) == [bare]


class TestOlderLibraries:
    """Libraries built before 0.9.1 have no volume, issue or pages columns."""

    def _old_library(self, path: Path) -> None:
        schema = SCHEMA
        for column in ("volume", "issue", "pages"):
            schema = schema.replace(f",\n    {column:<15} TEXT NOT NULL DEFAULT ''", "")
        assert "volume" not in schema.split("CREATE VIRTUAL TABLE")[0]
        conn = sqlite3.connect(path)
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO articles (pmid, title, abstract, authors, added_at) VALUES (?,?,?,?,?)",
            ("40000001", "Title as first stored", "text", json.dumps(["Chen W"]), "2026-01-01"),
        )
        conn.commit()
        conn.close()

    def test_an_older_library_opens(self, tmp_path):
        self._old_library(tmp_path / "k.db")
        with Store(tmp_path / "k.db") as store:
            assert store.get_article("40000001").volume == ""

    def test_importing_again_completes_it_and_overwrites_nothing(self, tmp_path, articles):
        self._old_library(tmp_path / "k.db")
        with Store(tmp_path / "k.db") as store:
            store.add_articles(articles)
            stored = store.get_article("40000001")
        assert (stored.volume, stored.issue, stored.pages) == ("70", "3", "1234-45")
        assert stored.title == "Title as first stored"

    def test_filling_never_replaces_a_value(self, tmp_path, articles):
        with Store(tmp_path / "k.db") as store:
            store.add_articles(articles)
            changed = Article(pmid="40000001", volume="99", pages="1-2")
            assert store.fill_missing(changed) == 0
            assert store.get_article("40000001").volume == "70"


class TestCommands:
    @pytest.fixture
    def workspace(self, tmp_path, articles):
        workspace = tmp_path / ".mra"
        workspace.mkdir()
        with Store(workspace / "knowledge.db") as store:
            store.add_articles(articles)
        return workspace

    def run(self, workspace, *args):
        return cli.main(["--workspace", str(workspace), *args])

    def test_refs_exports_only_what_verified_in_citation_order(self, workspace, tmp_path, capsys):
        draft = tmp_path / "draft.md"
        draft.write_text("B [PMID:40000002]. A [PMID:40000001]. Fake [PMID:99999999].", encoding="utf-8")
        out = tmp_path / "refs.ris"
        assert self.run(workspace, "refs", str(draft), "--export", str(out)) == 1  # the fake one
        records = out.read_text(encoding="utf-8").split("\n\n")
        assert [r.split("AN  - ")[1].split("\n")[0] for r in records] == ["40000002", "40000001"]
        assert "99999999" not in out.read_text(encoding="utf-8")
        assert "没有导出：99999999" in capsys.readouterr().out

    def test_export_by_suffix(self, workspace, tmp_path):
        self.run(workspace, "export", "-o", str(tmp_path / "lib.ris"))
        self.run(workspace, "export", "-o", str(tmp_path / "lib.bib"))
        assert (tmp_path / "lib.ris").read_text(encoding="utf-8").count("ER  - ") == 2
        assert (tmp_path / "lib.bib").read_text(encoding="utf-8").count("@article{") == 2

    def test_export_only_the_chosen_papers(self, workspace, tmp_path):
        self.run(workspace, "export", "[PMID:40000002]", "-o", str(tmp_path / "one.ris"))
        text = (tmp_path / "one.ris").read_text(encoding="utf-8")
        assert "40000002" in text and "40000001" not in text

    def test_an_unknown_identifier_is_an_error_not_a_shorter_file(self, workspace, tmp_path, capsys):
        assert self.run(workspace, "export", "12345", "-o", str(tmp_path / "x.ris")) == 1
        assert not (tmp_path / "x.ris").exists()

    def test_json_export_is_unchanged(self, workspace, tmp_path):
        self.run(workspace, "export", "-o", str(tmp_path / "all.json"))
        assert len(json.loads((tmp_path / "all.json").read_text(encoding="utf-8"))["articles"]) == 2

    def test_incomplete_records_are_reported_with_how_to_fix_them(self, workspace, tmp_path, capsys):
        with Store(workspace / "knowledge.db") as store:
            store.add_articles([Article(pmid="local:abcd1234", title="Mine", abstract="x")])
        self.run(workspace, "export", "-o", str(tmp_path / "lib.ris"))
        out = capsys.readouterr().out
        assert "缺卷号或页码" in out and "local:abcd1234" in out and "查找参考文献更新" in out


class TestReferenceList:
    def test_the_printed_list_carries_volume_issue_and_pages(self, tmp_path, articles):
        from mra import citations

        with Store(tmp_path / "k.db") as store:
            store.add_articles(articles)
            text = citations.reference_list("[PMID:40000001] [PMID:40000002]", store)
        assert "Hepatology. 2019;70(3):1234-45." in text
        assert "PLoS One. 2017;12(10):e0185809." in text


class TestWeb:
    def test_export_with_nothing_ticked_means_the_whole_library(self):
        from mra import webui

        assert webui.build_argv("export", {"output": "文献库.ris"}) == ["export", "--output=文献库.ris"]

    def test_other_repeated_positionals_still_require_one(self):
        from mra import webui

        with pytest.raises(ValueError):
            webui.build_argv("compare", {})

    def test_refs_can_export_from_the_page(self):
        from mra import webui

        argv = webui.build_argv("refs", {"file": "d.md", "export": "参考文献.ris"})
        assert "--export=参考文献.ris" in argv

    def test_the_page_has_the_export_panel(self):
        from mra import webui

        assert "导出文献" in webui.read_index().decode("utf-8")
