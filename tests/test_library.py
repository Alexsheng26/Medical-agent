"""Reading the knowledge base back out."""

from __future__ import annotations

import pytest

from mra import library
from mra.pubmed import Article
from mra.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "k.db") as opened:
        opened.add_articles([
            Article(pmid="31234567", title="Macrophage-derived TGF-beta1 drives portal fibrosis",
                    abstract="Portal fibrosis in NASH.", journal="Hepatology", year="2019",
                    authors=["Chen W", "Okonkwo A", "Nilsson E", "A Fourth"], doi="10.1002/hep.1"),
            Article(pmid="local:a1b2c3d4", title="A PDF the researcher imported",
                    abstract="Local full text.", year="2024"),
        ])
        yield opened


CARD = {
    "pmid": "31234567",
    "scientific_question": "门脉巨噬细胞来源的 TGF-β1 是驱动因素还是伴随现象？",
    "key_findings": ["门脉胶原减少 47%（p=0.003），窦周无差异"],
    "methods": ["LysM-Cre 小鼠"],
    "novelty_claim": "把来源定位到单核来源巨噬细胞",
    "limitations": ["作者未承认：LysM-Cre 并非巨噬细胞特异"],
    "mechanism_keywords": ["TGF-β1", "Smad3"],
    "clinical_relevance": "给药区室可能才是问题",
    "evidence_strength": 3,
}


class TestLibraryList:
    def test_empty_store_says_what_to_do_next(self, tmp_path):
        with Store(tmp_path / "empty.db") as empty:
            text = library.format_library(empty)
        assert "文献库是空的" in text
        assert "导入文献" in text

    def test_lists_every_stored_document(self, store):
        text = library.format_library(store)
        assert "31234567" in text and "local:a1b2c3d4" in text
        assert "文献库里有 2 篇" in text

    def test_marks_which_ones_have_been_digested(self, store):
        def row(text: str) -> str:
            return next(line for line in text.splitlines() if line.startswith("31234567"))

        assert "✓" not in row(library.format_library(store))
        store.save_card("31234567", CARD)
        marked = library.format_library(store)
        assert "✓" in row(marked)
        assert "[证据 3]" in row(marked)

    def test_says_how_many_are_still_pending(self, store):
        store.save_card("31234567", CARD)
        assert "其中 1 篇还没提炼" in library.format_library(store)

    def test_pending_notice_disappears_once_everything_is_digested(self, store):
        store.save_card("31234567", CARD)
        store.save_card("local:a1b2c3d4", {**CARD, "pmid": "local:a1b2c3d4"})
        assert "还没提炼" not in library.format_library(store)

    def test_columns_line_up_despite_chinese_headings(self, store):
        """Padding by character count puts every column after 编号 off by two."""
        lines = library.format_library(store).splitlines()
        header = next(line for line in lines if "编号" in line)
        assert library._columns(header[: header.index("年份")]) == 14


class TestCard:
    def test_unknown_identifier_explains_where_to_find_one(self, store):
        text = library.format_card(store, "99999999")
        assert "库里没有编号" in text
        assert "第一列" in text

    def test_undigested_document_shows_the_abstract_and_what_is_missing(self, store):
        text = library.format_card(store, "31234567")
        assert "这一篇还没有提炼" in text
        assert "Portal fibrosis in NASH." in text

    def test_a_card_renders_every_field(self, store):
        store.save_card("31234567", CARD)
        text = library.format_card(store, "31234567")
        for heading in ("科学问题", "关键发现", "方法", "作者主张的新意",
                        "局限（含作者未承认的）", "临床相关性", "机制关键词", "证据强度"):
            assert heading in text, heading
        assert "3 扎实的动物或观察性人群数据" in text

    def test_identifier_is_stripped_so_a_pasted_value_still_resolves(self, store):
        assert "库里没有编号" not in library.format_card(store, "  31234567\n")

    def test_long_author_lists_are_summarised(self, store):
        assert "共 4 位" in library.format_card(store, "31234567")


class TestStrengthWording:
    def test_every_level_on_the_scale_has_wording(self):
        from mra.schemas import LitCard

        assert set(library.STRENGTH_WORDS) == {1, 2, 3, 4, 5}

    def test_level_two_does_not_claim_in_vitro_or_small(self):
        """The first real paper to score 2 had n=9,781 — it scored low on design,
        not on size, and the label said the opposite."""
        assert "体外" not in library.STRENGTH_WORDS[2]
        assert "小样本" not in library.STRENGTH_WORDS[2]

    def test_wording_starts_with_its_own_number(self):
        for level, words in library.STRENGTH_WORDS.items():
            assert words.startswith(str(level))


class TestWrapping:
    def test_chinese_counts_as_two_columns(self):
        wrapped = library._wrap("中" * 60, width=40)
        assert all(library._columns(line) <= 42 for line in wrapped.splitlines())

    def test_a_line_never_opens_with_closing_punctuation(self):
        """Chinese typesetting forbids it, and it reads as a typo."""
        text = ("数据显示" + "很长" * 18 + "，后半句")
        for line in library._wrap(text, width=40).splitlines():
            assert line.strip()[0] not in library.NO_LINE_START

    def test_empty_text_becomes_a_dash_rather_than_nothing(self):
        assert library._wrap("").strip() == "—"
