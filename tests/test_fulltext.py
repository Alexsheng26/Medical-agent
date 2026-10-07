"""Spending a long paper's reading budget on what carries the evidence.

Measured on real journal PDFs before this existed: reading a paper kept its
first 55% and last 45% by position, which dropped four of an Elsevier paper's
five tables, and 45-80% of the kept ending was the reference list. These pin
the behaviour that fixed it, on synthetic text shaped like what pypdf produced
from those papers — tables one row per line, captions on their own line,
footnotes under the rows, in-text mentions that start a wrapped line.
"""

from __future__ import annotations

import pytest

from mra import fulltext, pipeline, selection

PROSE = (
    "Patients with advanced fibrosis had higher transaminase levels than those\n"
    "without, and the difference persisted after adjustment for body mass index\n"
    "and diabetes in every model that was fitted to the cohort data we analysed.\n"
)

BASELINE = """Table 1
Baseline characteristics of the study population according to fibrosis stage
Characteristic F0-F1 (n = 265) F2-F4 (n = 244) P value
Age, years 54.2 ± 11.3 56.1 ± 10.8 0.21
Female sex, n (%) 120 (45.3) 98 (40.2) 0.32
BMI, kg/m2 27.4 ± 4.1 29.8 ± 4.6 <0.001
Diabetes, n (%) 45 (17.0) 82 (33.6) <0.001
ALT, U/L 38 (25-61) 57 (38-89) <0.001
Data are mean ± SD, n (%) or median (IQR).
BMI, body mass index; ALT, alanine aminotransferase.
"""

SIGNIFICANCE = """Table 2
Significance of the time and treatment terms across all sites in the analysis
Site Week
4 8 12 16
Cohort A PP ,* PPP , P, P,
Cohort B P,TP ,T ,* *,* P,TP ,* P,* P,*
Cohort C P,TP ,TP ,TP ,T TTT na na
P indicates the treatment term was significant at the 5% level, and na denotes too few data points.
"""

CHINESE = """表1 两组患者基线资料比较
项目 对照组(n=60) 观察组(n=60) P值
年龄（岁） 54.2±11.3 56.1±10.8 0.21
男性[例(%)] 32(53.3) 35(58.3) 0.58
病程（月） 14.2±5.6 13.8±6.1 0.71
"""

REFERENCES = "References\n" + "".join(
    f"{n}. Author A, Author B. A study of hepatic fibrosis number {n}. Hepatology. 2019;70:{n}-{n + 9}.\n"
    for n in range(1, 40)
)


def paper(*, middle: str, after_references: str = "", padding: int = 40) -> str:
    head = "Abstract\nBackground and aims.\n" + PROSE * padding
    tail = "Discussion\n" + PROSE * padding + "Conclusions\nThe end of the paper.\n"
    return head + PROSE * padding + middle + PROSE * padding + tail + REFERENCES + after_references


class TestCaptions:
    @pytest.mark.parametrize("line", [
        "Table 1", "Table 1: Current layout detection models", "Table 2. Primers used",
        "TABLE 2 Baseline characteristics", "Table S1. Antibodies", "Table II: Parameters used",
        "Table 3 Clinical characteristics of the cohort", "表1 两组患者基线资料比较", "表 2",
    ])
    def test_a_caption_is_recognised(self, line):
        assert fulltext.is_caption(line)

    @pytest.mark.parametrize("line", [
        "Table 3 for all deciles, where solutions were found.",
        "Table 2 gives the full breakdown",
        "Table 1 ). These results could be scaled",
        "Table 5 shows that total and low flow reductions",
        "表1显示两组患者基线资料差异无统计学意义",
        "Tables 1 and 2 summarise the cohort",
        "Table Info is not a caption",
        "(Table 1).",
    ])
    def test_prose_that_mentions_a_table_is_not_a_caption(self, line):
        assert not fulltext.is_caption(line)


class TestLines:
    @pytest.mark.parametrize("line", [
        "Age, years 54.2 ± 11.3 56.1 ± 10.8 0.21",
        "Female sex, n (%) 120 (45.3) 98 (40.2) 0.32",
        "Cohort B P,TP ,T ,* *,* P,TP ,* P,* P,*",
        "γc Strength of grouping by colour similarity 0.03",
        "年龄（岁） 54.2±11.3 56.1±10.8 0.21",
    ])
    def test_table_rows_are_not_prose(self, line):
        assert not fulltext._is_prose(line)

    @pytest.mark.parametrize("line", [
        "Patients with advanced fibrosis had higher transaminase levels than those",
        "the significance of the rainfall and time terms is given in the table below",
        "两组患者在年龄、性别构成方面差异均无统计学意义，具有可比性，可以进行后续分析。",
    ])
    def test_sentences_are_prose(self, line):
        assert fulltext._is_prose(line)


class TestBlocks:
    def _blocks(self, text):
        return [text[s:e] for s, e in fulltext.table_blocks(text)]

    def test_a_baseline_table_is_taken_whole_with_its_footnotes(self):
        blocks = self._blocks(PROSE + BASELINE + PROSE)
        assert len(blocks) == 1
        block = blocks[0]
        assert block.startswith("Table 1") and "ALT, U/L 38 (25-61)" in block
        assert "Data are mean ± SD" in block and "BMI, body mass index" in block
        assert "Patients with advanced fibrosis" not in block

    def test_a_table_of_significance_marks_is_a_table(self):
        """No digits in its cells — a numbers-only test called every row prose."""
        block = self._blocks(PROSE + SIGNIFICANCE + PROSE)[0]
        assert "Cohort C P,TP" in block and "P indicates the treatment term" in block

    def test_a_title_that_wraps_does_not_end_the_table(self):
        table = ("Table 5\nPublished flow reductions from paired catchment analyses compared\n"
                 "to the reductions estimated in this study for every site\n"
                 "Site Year Reduction (%)\nSite A 21 50\nSite B 18 60\nSite C 13 41\n")
        block = self._blocks(PROSE + table + PROSE)[0]
        assert "Site C 13 41" in block

    def test_a_chinese_three_line_table(self):
        block = self._blocks(PROSE + CHINESE + PROSE)[0]
        assert block.startswith("表1") and "病程（月） 14.2±5.6" in block

    def test_a_mention_in_prose_is_not_a_table(self):
        text = PROSE + "Table 2 shows that the reduction was larger in\n" + PROSE
        assert self._blocks(text) == []

    def test_a_caption_whose_rows_landed_elsewhere_is_not_kept_alone(self):
        assert self._blocks(PROSE + "Table 4\n" + PROSE) == []

    def test_a_section_heading_ends_the_table(self):
        block = self._blocks(SIGNIFICANCE + "V. CONCLUSION\nShort line\n" + PROSE)[0]
        assert "CONCLUSION" not in block


class TestReferences:
    def test_the_list_is_removed_and_measured(self):
        body, removed = fulltext.without_references(paper(middle=""))
        assert "A study of hepatic fibrosis number 7" not in body
        assert removed > 1000
        assert body.rstrip().endswith("The end of the paper.")

    def test_tables_after_the_references_are_kept(self):
        """Author manuscripts put the tables after the reference list."""
        body, _ = fulltext.without_references(paper(middle="", after_references=BASELINE))
        assert "ALT, U/L 38 (25-61)" in body
        assert "A study of hepatic fibrosis number 7" not in body

    def test_an_early_references_heading_is_a_contents_page_not_the_list(self):
        text = "References\n" + "x" * 50 + "\n" + PROSE * 80
        assert fulltext.without_references(text)[1] == 0

    def test_a_heading_with_almost_nothing_under_it_is_left_alone(self):
        text = PROSE * 80 + "References\nNone.\n"
        assert fulltext.without_references(text)[1] == 0


class TestFit:
    LIMIT = 12000

    def test_a_paper_that_fits_is_untouched(self):
        assert fulltext.fit("short abstract", self.LIMIT) == "short abstract"

    def test_tables_in_the_middle_reach_the_model(self):
        """The regression this module exists for: by position alone, a table
        in the middle of a long paper never reached the model."""
        text = paper(middle=BASELINE + SIGNIFICANCE, padding=60)
        by_position = text[: int(self.LIMIT * 0.55)] + text[-(self.LIMIT - int(self.LIMIT * 0.55)):]
        assert "ALT, U/L 38 (25-61)" not in by_position

        fitted = fulltext.fit(text, self.LIMIT)
        assert "ALT, U/L 38 (25-61)" in fitted and "Cohort C P,TP" in fitted
        assert fulltext.TABLE_NOTE in fitted

    def test_the_reference_list_is_never_what_gets_read(self):
        fitted = fulltext.fit(paper(middle="", padding=60), self.LIMIT)
        assert "A study of hepatic fibrosis number" not in fitted
        assert "reference list" in fitted
        assert fitted.rstrip().endswith("The end of the paper.")

    def test_dropping_the_list_alone_can_be_enough(self):
        text = paper(middle="", padding=4)
        assert len(text) > 2000
        fitted = fulltext.fit(text, len(text) - 1000)
        assert "omitted from the middle" not in fitted
        assert "Background and aims" in fitted and "The end of the paper." in fitted

    def test_the_budget_holds_with_tables(self):
        text = paper(middle=(BASELINE + SIGNIFICANCE + PROSE * 3) * 12, padding=80)
        fitted = fulltext.fit(text, self.LIMIT)
        assert len(fitted) <= self.LIMIT + 400  # the bracketed notes
        tables = fitted.split(fulltext.TABLE_NOTE)[1]
        assert len(tables) <= self.LIMIT * fulltext.TABLE_SHARE + len(PROSE) * 120

    def test_without_tables_it_still_keeps_both_ends(self):
        fitted = fulltext.fit("HEAD\n" + PROSE * 400 + "TAIL", self.LIMIT)
        assert fitted.startswith("HEAD") and fitted.endswith("TAIL")
        assert fulltext.TABLE_NOTE not in fitted


class TestCallers:
    def test_reading_uses_it(self):
        text = paper(middle=BASELINE, padding=200)
        assert "ALT, U/L 38 (25-61)" in pipeline._trim_for_digest(text)

    def test_mind_maps_and_comparisons_use_it(self):
        text = paper(middle=BASELINE, padding=200)
        assert "ALT, U/L 38 (25-61)" in selection.trim(text, 10000)


def test_pypdf_internals_do_not_reach_the_screen():
    """57 lines of font dictionaries printed over one real import."""
    import logging

    from mra import ingest  # noqa: F401 - importing it is what sets the level

    assert logging.getLogger("pypdf").getEffectiveLevel() >= logging.ERROR
