"""Responding to peer review.

Not whether the judgement is good — that needs a key, and `mra eval` is where
it gets measured. These cover the parts that must hold whatever the model says:
that a comment it answers was really in the letter, that a paper it cites
really exists, that promised work is surfaced before the letter is sent, and
that nothing addressed to the researcher rides along into the file that goes to
the editor.
"""

from __future__ import annotations

import json

import pytest

from mra import rebuttal
from mra.schemas import Rebuttal, ReviewerPoint, SEVERITIES, STANCES
from mra.store import Store

LETTER = """
Reviewer 1

1. The authors claim that TREM2+ macrophages drive fibrosis progression, but
   the data are cross-sectional and cannot establish direction.

2. Figure 3B shows n = 4 per group plotted as a bar with SEM. Show the points.

Reviewer 2

1. Why was sex not considered as a biological variable?
"""


def point(**kwargs) -> ReviewerPoint:
    base = dict(
        reviewer="Reviewer 1",
        number="1",
        quote="the data are cross-sectional and cannot establish direction",
        asks_for="证据不足以支持因果方向",
        stance="concede-and-limit",
        severity="major",
        response="We agree the design cannot establish direction and have "
        "rewritten the claim as an association.",
        manuscript_change="Discussion, para 2: 'drive' replaced with 'are associated with'.",
        new_work="",
        evidence=[],
        risk="",
    )
    base.update(kwargs)
    return ReviewerPoint(**base)


def make(points, **kwargs) -> Rebuttal:
    base = dict(points=points, overall="We thank the reviewers.", decisions=[], threats=[])
    base.update(kwargs)
    return Rebuttal(**base)


class TestQuoteChecking:
    """A response to a comment nobody made reads to an editor as though the
    authors answered a different paper's reviews."""

    def test_a_real_quote_passes(self):
        assert rebuttal.unquoted_points(make([point()]), LETTER) == []

    def test_an_invented_comment_is_flagged(self):
        invented = point(quote="The English requires extensive revision by a native speaker")
        flagged = rebuttal.unquoted_points(make([invented]), LETTER)
        assert len(flagged) == 1
        assert "Reviewer 1 #1" in flagged[0]

    def test_whitespace_and_line_wrapping_do_not_count_as_invention(self):
        """The letter wraps mid-sentence; the model echoes it unwrapped."""
        rewrapped = point(
            quote="but\n   the data are cross-sectional   and cannot establish direction"
        )
        assert rebuttal.unquoted_points(make([rewrapped]), LETTER) == []

    def test_curly_quotes_from_a_pasted_pdf_do_not_count_as_invention(self):
        letter = "1. The authors’ claim that “TREM2 drives fibrosis” is unsupported."
        echoed = point(quote="The authors' claim that \"TREM2 drives fibrosis\" is unsupported.")
        assert rebuttal.unquoted_points(make([echoed]), letter) == []

    def test_an_empty_quote_is_flagged_rather_than_passing(self):
        """An empty probe is in every string — it must not read as verified."""
        flagged = rebuttal.unquoted_points(make([point(quote="")]), LETTER)
        assert len(flagged) == 1
        assert "没有引用原文" in flagged[0]

    def test_a_paraphrase_that_keeps_the_opening_words_still_passes(self):
        """The probe is a prefix, so this is a known limit, recorded not hidden:
        it catches wholesale invention, not creative trimming further in."""
        trimmed = point(
            quote="the data are cross-sectional and cannot establish direction in any cohort"
        )
        assert rebuttal.unquoted_points(make([trimmed]), LETTER) == []


class TestCitationChecking:
    @pytest.fixture
    def store(self, tmp_path):
        with Store(tmp_path / "k.db") as store:
            yield store

    def test_a_citation_not_in_the_library_is_flagged(self, store):
        cited = point(response="This is established in larger cohorts [PMID:99999999].")
        assert rebuttal.fabricated_citations(make([cited]), store) == ["99999999"]

    def test_evidence_markers_are_checked_even_when_the_prose_has_none(self, store):
        """A marker can reach the letter through either field."""
        cited = point(response="Established elsewhere.", evidence=["[PMID:12345678]"])
        assert rebuttal.fabricated_citations(make([cited]), store) == ["12345678"]

    def test_a_clean_letter_flags_nothing(self, store):
        assert rebuttal.fabricated_citations(make([point()]), store) == []


class TestCommitments:
    """What the letter signs the authors up for, before it is sent."""

    def test_points_promising_work_are_collected(self):
        promised = point(number="2", new_work="Re-plot Fig 3B as individual points, n=4.")
        found = rebuttal.commitments(make([point(), promised]))
        assert [p.number for p in found] == ["2"]

    def test_whitespace_is_not_a_commitment(self):
        assert rebuttal.commitments(make([point(new_work="   ")])) == []

    def test_the_report_names_them_under_a_heading_that_says_what_it_means(self):
        promised = point(new_work="Re-plot Fig 3B as individual points.")
        text = rebuttal.format_rebuttal(make([promised]))
        assert "你就承诺了这些事" in text
        assert "Re-plot Fig 3B" in text


class TestTheLetter:
    """The file goes to the editor. Everything else must stay on screen."""

    def test_the_letter_carries_the_comment_and_the_response(self):
        text = rebuttal.letter(make([point()]))
        assert "cross-sectional" in text
        assert "rewritten the claim as an association" in text

    def test_severity_risk_and_decisions_never_reach_the_letter(self):
        loaded = point(
            severity="fatal",
            risk="They will ask for the longitudinal cohort.",
            new_work="Run the 24-week follow-up.",
        )
        text = rebuttal.letter(make([loaded], decisions=["要不要补做队列？"], threats=["主结论站不住"]))
        assert "fatal" not in text and "动摇主结论" not in text
        assert "They will ask for" not in text
        assert "要不要补做队列" not in text
        assert "主结论站不住" not in text

    def test_work_promised_does_not_leak_into_the_letter_on_its_own(self):
        """`new_work` is the researcher's decision; only an explicit
        manuscript_change is the editor's business."""
        text = rebuttal.letter(make([point(new_work="Run the 24-week follow-up.")]))
        assert "24-week" not in text

    def test_manuscript_changes_do_appear(self):
        assert "Discussion, para 2" in rebuttal.letter(make([point()]))

    def test_none_is_not_printed_as_a_change(self):
        text = rebuttal.letter(make([point(manuscript_change="none")]))
        assert "none" not in text.lower().replace("nonetheless", "")

    def test_each_reviewer_gets_one_heading(self):
        text = rebuttal.letter(make([
            point(reviewer="Reviewer 1", number="1"),
            point(reviewer="Reviewer 2", number="1"),
            point(reviewer="Reviewer 1", number="2"),
        ]))
        assert text.count("## Reviewer 1") == 1
        assert text.count("## Reviewer 2") == 1


class TestOrdering:
    def test_the_worst_comments_come_first(self):
        draft = make([
            point(number="1", severity="minor"),
            point(number="2", severity="fatal"),
            point(number="3", severity="major"),
        ])
        draft.points.sort(key=rebuttal._order)
        assert [p.number for p in draft.points] == ["2", "3", "1"]

    def test_comment_10_sorts_after_comment_2(self):
        assert rebuttal._numeric("2") < rebuttal._numeric("10")

    def test_a_sub_numbered_comment_sorts_within_its_parent(self):
        assert rebuttal._numeric("2.1") < rebuttal._numeric("2.10") < rebuttal._numeric("3")

    def test_an_unnumbered_comment_does_not_crash_the_sort(self):
        assert rebuttal._numeric("第三条") == (0,)


class TestReport:
    def test_threats_are_stated_before_anything_else(self):
        text = rebuttal.format_rebuttal(make([point()], threats=["横断面设计撑不起因果"]))
        assert text.index("横断面设计撑不起因果") < text.index("原文：")

    def test_an_empty_threat_list_prints_no_scary_heading(self):
        assert "绕不过去" not in rebuttal.format_rebuttal(make([point()]))

    def test_the_stance_is_shown_in_chinese_not_as_a_slug(self):
        text = rebuttal.format_rebuttal(make([point(stance="disagree")]))
        assert "不认" in text
        assert "disagree" not in text

    def test_every_stance_the_schema_allows_has_a_label(self):
        """An unlabelled stance would print as a bare English slug."""
        assert set(STANCES) == set(rebuttal.STANCE_LABELS)

    def test_every_severity_the_schema_allows_has_a_label(self):
        assert set(SEVERITIES) == set(rebuttal.SEVERITY_LABELS)

    def test_decisions_are_rendered_as_the_researchers_to_make(self):
        text = rebuttal.format_rebuttal(make([point()], decisions=["要不要补做队列？"]))
        assert "只有你能定" in text
        assert "要不要补做队列？" in text


class TestJson:
    def test_round_trips(self):
        restored = json.loads(rebuttal.to_json(make([point()])))
        assert restored["points"][0]["stance"] == "concede-and-limit"


class TestWiring:
    def test_the_cli_exposes_it(self):
        from mra.cli import build_parser

        choices = next(a for a in build_parser()._actions if a.dest == "command").choices
        assert "rebuttal" in choices

    def test_the_web_offers_it_and_marks_it_costly(self):
        from mra import webui

        assert "rebuttal" in webui.COMMANDS
        assert "rebuttal" in webui.COSTLY

    def test_the_web_builds_a_usable_command_line(self):
        from mra import webui

        argv = webui.build_argv(
            "rebuttal", {"reviews": "r.md", "manuscript": "ms.md", "output": "reply.md"}
        )
        assert argv == ["rebuttal", "r.md", "--manuscript=ms.md", "--output=reply.md"]

    def test_the_page_lists_it(self):
        from mra import webui

        assert "回复审稿人" in webui.read_index().decode("utf-8")

    def test_the_guide_names_it_in_the_workflow(self):
        from mra.cli import GUIDE

        assert "mra rebuttal" in GUIDE

    def test_the_prompt_exists_and_takes_every_placeholder_the_code_fills(self):
        from mra import prompts

        text = prompts._read("rebuttal")
        for field in ("reviews", "manuscript", "data", "profile", "context"):
            assert "{" + field + "}" in text, field

    def test_the_prompt_names_the_failure_mode_it_exists_to_prevent(self):
        """Without this instruction the model concedes every point."""
        from mra import prompts

        text = prompts._read("rebuttal")
        assert "agreeing with everything" in text
        for stance in STANCES:
            assert stance in text


class TestEvalCase:
    """The new command has to be measurable, or its quality is an opinion.

    These check the fixture is what the case claims, so a marker cannot pass by
    matching the input — the mistake that made "gut" score itself against "gut
    microbiome" the first time this suite ran for real.
    """

    def _case(self):
        from mra import evaluation

        return next(c for c in evaluation.load_cases() if c["id"] == "rebuttal-mixed-reviews")

    def _fixtures(self):
        from importlib import resources

        read = lambda name: (resources.files("mra") / "evals" / name).read_text(encoding="utf-8")
        return read("reviews_mixed.txt"), read("manuscript_under_review.md")

    def test_the_case_is_registered(self):
        assert self._case()["command"] == "rebuttal"

    def test_every_planted_comment_is_in_the_letter(self):
        reviews, _ = self._fixtures()
        assert "cross-sectional" in reviews          # right, and fatal
        assert "only 12 patients" in reviews         # wrong — the paper says 48
        assert "Sex was not considered" in reviews   # already in the manuscript
        assert "five years" in reviews               # not doable in a revision
        assert "BMI" in reviews                       # fair, and cheap to do

    def test_the_manuscript_contradicts_the_misreading(self):
        """`rejects-the-misread-n` is only answerable if the real n is there."""
        _, manuscript = self._fixtures()
        assert "n = 48" in manuscript
        assert "12 cells/mm2" in manuscript, "the number the reviewer misread as the cohort"

    def test_the_manuscript_already_answers_the_sex_comment(self):
        _, manuscript = self._fixtures()
        assert "covariate" in manuscript and "Table 1" in manuscript

    def test_the_title_contains_the_word_the_fatal_comment_attacks(self):
        _, manuscript = self._fixtures()
        assert manuscript.splitlines()[0].startswith("# TREM2+ macrophages drive")

    def test_no_marker_can_be_satisfied_by_quoting_a_comment(self):
        """The report prints each comment verbatim, so any marker that appears
        in the review letter scores itself — the model gets the point for
        echoing the objection rather than for answering it.

        The manuscript is a different matter: it is never echoed, so a marker
        found only there (the real n, the covariate already in Methods) can
        only reach the output by the model having gone and looked.
        """
        reviews, _ = self._fixtures()
        quoted = reviews.lower()
        for expectation in self._case()["expect"]:
            echoed = [m for m in expectation["any"] if m.lower() in quoted]
            assert not echoed, f"{expectation['id']}: {echoed} is the reviewer's own wording"

    def test_the_sanity_marker_is_not_itself_quotable(self):
        reviews, manuscript = self._fixtures()
        for marker in self._case()["sanity"]["any"]:
            assert marker.lower() not in (reviews + manuscript).lower()
