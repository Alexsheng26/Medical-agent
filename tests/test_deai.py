import pytest

from mra import deai

# Deliberately written in the register the lint is meant to catch: stock phrases,
# even sentence lengths, a connective in front of every sentence.
AI_SLOP = """
In the realm of hepatic fibrosis research, it is worth noting that macrophages
play a crucial role in disease progression. Furthermore, a growing body of
evidence has begun to shed light on the intricate interplay between immune cells
and stellate cells. Moreover, these findings underscore the importance of
understanding the multifaceted nature of the fibrotic microenvironment.

Additionally, recent studies have delved into the myriad mechanisms that govern
this process. Notably, the potential to revolutionize treatment paradigms cannot
be overstated in this context. Importantly, such work paves the way for novel
therapeutic strategies in the years ahead.

Collectively, these observations demonstrate the potential of targeting
macrophage populations. Overall, further research is warranted to fully unlock
the potential of this promising approach. Taken together, this represents a
testament to the value of comprehensive understanding in the field.
"""

# Same subject matter, written the way a paper actually reads: numbers, uneven
# sentence lengths, no scaffolding.
HUMAN_PROSE = """
Portal fibrosis predicts decompensation in NASH, yet the source of the
profibrotic signal has stayed unclear. Two candidate populations dominate the
literature: resident Kupffer cells and monocyte-derived macrophages recruited
during injury.

We deleted Tgfb1 in the myeloid compartment. Sirius red-positive area fell by
38% (p = 0.002) after twelve weeks on CDAHFD, and portal collagen I deposition
dropped in parallel. The effect was confined to the portal tract; pericentral
fibrosis was unchanged.

Human data supported this. Across 48 biopsies, portal macrophage density tracked
fibrosis stage (r = 0.61, p < 0.001). The correlation held after adjusting for
BMI and diabetes duration.

One result complicates the picture. Yamamoto and colleagues found that stellate
cell activation proceeds normally after clodronate depletion, which argues
against an obligate macrophage requirement. Their depletion was systemic and
acute, ours was lineage-restricted and chronic, so the two need not conflict.
"""


def test_slop_scores_far_above_human_prose():
    slop = deai.analyze(AI_SLOP)
    human = deai.analyze(HUMAN_PROSE)
    assert slop.score > human.score * 2, (
        f"slop={slop.score:.1f} human={human.score:.1f} — the lint must separate these"
    )


def test_human_prose_lands_in_the_clean_band():
    report = deai.analyze(HUMAN_PROSE)
    assert report.score < 35, f"false positive on real prose: {report.score:.1f}"


def test_slop_is_flagged_clearly():
    report = deai.analyze(AI_SLOP)
    assert report.score > 50
    assert report.verdict in {"clearly patterned", "heavily patterned"}


def test_stock_phrases_are_identified():
    report = deai.analyze(AI_SLOP)
    details = " ".join(f.detail for f in report.findings if f.kind == "stock phrase")
    assert "delve" in details
    assert "realm of" in details


def test_formulaic_openers_flagged():
    report = deai.analyze(AI_SLOP)
    kinds = {f.kind for f in report.findings}
    assert "formulaic openers" in kinds


def test_human_prose_has_higher_burstiness():
    assert (
        deai.analyze(HUMAN_PROSE).metrics["burstiness"]
        > deai.analyze(AI_SLOP).metrics["burstiness"]
    )


def test_lexical_score_is_density_normalised():
    """The same tells diluted into more text must score lower, not the same.

    Note this is deliberately not tested by repeating a document: duplicating
    paragraphs genuinely does introduce repeated openings and uniform paragraph
    lengths, which the lint is correct to flag.
    """
    tell = "In the realm of hepatic research, it is worth noting that this cannot be overstated. "
    first_paragraph = HUMAN_PROSE.strip().split("\n\n")[0]

    concentrated = deai.analyze(tell + first_paragraph)
    diluted = deai.analyze(tell + HUMAN_PROSE)

    assert concentrated.score > diluted.score
    # Same lexical findings in both; only the surrounding volume differs.
    assert {f.kind for f in concentrated.findings} == {f.kind for f in diluted.findings}


def test_repetition_is_itself_flagged():
    """A document assembled by repeating a block should be caught as patterned."""
    report = deai.analyze(HUMAN_PROSE * 3)
    kinds = {f.kind for f in report.findings}
    assert "repeated openings" in kinds or "uniform paragraphs" in kinds


def test_empty_text_does_not_crash():
    report = deai.analyze("")
    assert report.score == 0.0
    assert report.metrics["sentences"] == 0.0


def test_summary_is_renderable():
    text = deai.analyze(AI_SLOP).summary()
    assert "AI-tell lint score" in text
    assert "heuristic" in text, "the summary must not overstate what this measures"


class TestAnnotationsAreNotProse:
    """`[DATA NEEDED: ...]` is a question for the researcher, not text to score.

    Scoring it punished the behaviour we want — a draft that flags every number
    it cannot support — and fed `polish` findings telling it to recast the very
    markers `writing.py` warns about losing.
    """

    def test_data_needed_blocks_do_not_create_repeated_openings(self):
        body = (
            "Septal density rose across fibrosis stage in every biopsy examined. "
            "Control livers carried far fewer of these cells. "
            "Overlap between adjacent stages was substantial. "
            "Representative fields appear in the first figure panel. "
            "Counts were made inside fibrous septa only. "
            "Parenchymal fields were not acquired in the same sections."
        )
        annotated = body + " " + " ".join(
            f"[DATA NEEDED: per-arm n, effect size, and the test used for panel {i}]"
            for i in range(6)
        )

        kinds = {f.kind for f in deai.analyze(annotated).findings}
        assert "repeated openings" not in kinds

    def test_lists_inside_annotations_are_not_triadic_findings(self):
        text = (
            "We quantified septal macrophage density in every biopsy. "
            "[DATA NEEDED: BMI, diabetes status, and platelet count for both groups] "
            "[DATA NEEDED: age, sex, and disease duration for the control group] "
            "[DATA NEEDED: cohort size, stage, and follow-up interval]"
        )
        assert "triadic lists" not in {f.kind for f in deai.analyze(text).findings}

    def test_annotations_do_not_inflate_sentence_length(self):
        prose = "Density rose with stage. The gradient was monotonic."
        annotated = (
            "Density rose with stage. [DATA NEEDED: post hoc pairwise p values for "
            "every stage comparison, with the correction method used] "
            "The gradient was monotonic."
        )
        assert deai.analyze(annotated).metrics["mean_sentence_len"] == pytest.approx(
            deai.analyze(prose).metrics["mean_sentence_len"], abs=0.6
        )

    def test_citation_markers_are_not_words(self):
        with_marker = "Septal density rose with fibrosis stage [PMID:31234567]."
        without = "Septal density rose with fibrosis stage."
        assert deai.strip_annotations(with_marker).split() == without.split()

    def test_markdown_headings_are_not_sentence_openings(self):
        """`4 sentences begin with '###'` is a fact about the file format."""
        text = "\n\n".join(
            f"### Finding {i} in the septal compartment\n\nDensity rose with stage."
            for i in range(4)
        )
        detail = " ".join(
            f.detail for f in deai.analyze(text).findings if f.kind == "repeated openings"
        )
        assert "###" not in detail

    def test_heading_words_still_count(self):
        """Strip the marker, keep the voice — title grammar is real style."""
        assert "Septal" in deai.strip_annotations("### Septal density rises with stage")

    def test_bold_markers_are_not_part_of_the_word(self):
        assert deai.strip_annotations("**Density** rose.") == "Density rose."

    def test_list_markers_are_dropped(self):
        stripped = deai.strip_annotations("- first item\n- second item\n1. third item")
        assert stripped.splitlines() == ["first item", "second item", "third item"]

    def test_real_prose_still_scores(self):
        """The strip must not become a way to launder actual tells."""
        text = (
            "It is worth noting that macrophages delve into the intricate interplay "
            "of fibrosis. [DATA NEEDED: n per group] This underscores the importance "
            "of a comprehensive understanding of the mechanism."
        )
        assert "stock phrase" in {f.kind for f in deai.analyze(text).findings}


class TestSentenceSplitting:
    def test_abbreviations_do_not_split(self):
        text = "We used CDAHFD (Surwit et al. 2019) for 12 wk. Fibrosis increased."
        assert len(deai.split_sentences(text)) == 2

    def test_eg_and_ie_do_not_split(self):
        text = "Several models, e.g. CCl4 and CDAHFD, were compared. Both worked."
        assert len(deai.split_sentences(text)) == 2

    def test_decimals_do_not_split(self):
        # "0.002" contains a period but must not end a sentence.
        assert len(deai.split_sentences("The effect held (p = 0.002) throughout.")) == 1

    def test_initials_do_not_split(self):
        assert len(deai.split_sentences("Reported by Chen W. and colleagues later.")) == 1

    def test_normal_sentences_split(self):
        assert len(deai.split_sentences("One thing. Two things. Three things.")) == 3
