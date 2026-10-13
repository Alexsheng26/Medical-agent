"""审课题 — reading a student's project before it starts.

Whether the judgement is good is for `mra eval`. These cover what must hold
whatever the model says: a criticism has to point at something the student
actually wrote, a paper it cites has to exist, a library with nothing on the
topic is said out loud, and the fatal problem comes first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mra import cli, critique
from mra.config import Config
from mra.pubmed import parse_efetch_xml
from mra.schemas import Critique, Novelty, ProposalIssue, SearchTerms, VERDICTS
from mra.store import Store

from test_documents import docx, p

CORPUS = Path(__file__).resolve().parent.parent / "mra" / "examples" / "demo_corpus.xml"

PROPOSAL = """题目：髓系 TREM2 通过 TGF-β1 促进 NASH 肝纤维化

一、立项依据
前期 20 例患者肝活检显示 TREM2 表达与纤维化分期正相关，说明 TREM2 驱动纤维化。

二、研究内容
（一） 构建 LysM-Cre Trem2 flox 小鼠，CDAHFD 喂养 12 周，每组 3 只。
（二） 在 RAW264.7 细胞中用 siRNA 敲低 Trem2，检测 TGF-β1 分泌。
"""


def issue(**kwargs) -> ProposalIssue:
    base = dict(
        where="说明 TREM2 驱动纤维化",
        problem="用相关性数据推出因果",
        consequence="立项依据撑不起「驱动」",
        fix="改成「相关」，因果留给敲除实验回答",
        severity="major",
    )
    base.update(kwargs)
    return ProposalIssue(**base)


def make(**kwargs) -> Critique:
    base = dict(
        understanding="TREM2+ 巨噬细胞是否通过 TGF-β1 推动 NASH 纤维化。",
        hypothesis="髓系 TREM2 缺失会减轻纤维化。",
        logic=[issue()],
        novelty=Novelty(level=2, kind="known mechanism, contested", judgement="已有相反结果",
                        prior=["[PMID:34556677]"]),
        rigor=[issue(where="每组 3 只", problem="每组 3 只没有效力", severity="major")],
        feasibility=[],
        first_steps=["先用现有组织切片做 TREM2/胶原共染，两周"],
        questions=["如果敲除后纤维化不减轻，第二部分研究什么？"],
        strengths=["临床问题明确"],
        verdict="revise",
        verdict_reason="前提和已发表的结果冲突，先回应它。",
    )
    base.update(kwargs)
    return Critique(**base)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "k.db") as store:
        store.add_articles(parse_efetch_xml(CORPUS.read_text(encoding="utf-8")))
        yield store


# ------------------------------------------------------------------ the quotes


class TestQuotesPointAtWhatTheStudentWrote:
    """A criticism of a sentence the student never wrote is the one they will
    point out — and then the rest of the review is discounted with it."""

    def test_a_real_quote_passes(self):
        assert critique.unquoted(make(), PROPOSAL) == []

    def test_an_invented_sentence_is_flagged(self):
        invented = make(logic=[issue(where="本课题首次发现 TREM2 是治疗靶点")])
        flagged = critique.unquoted(invented, PROPOSAL)
        assert len(flagged) == 1 and "逻辑链" in flagged[0]

    def test_full_and_half_width_punctuation_are_the_same_quote(self):
        """The model writes (一) where the document has （一）."""
        quoted = make(logic=[issue(where="(一) 构建 LysM-Cre Trem2 flox 小鼠")])
        assert critique.unquoted(quoted, PROPOSAL) == []

    def test_the_space_after_a_list_label_does_not_matter(self):
        """Word's list labels are written with a space after them; the student
        typed none, and the model may echo either."""
        quoted = make(logic=[issue(where="（二）在 RAW264.7 细胞中用 siRNA 敲低")])
        assert critique.unquoted(quoted, PROPOSAL) == []

    def test_quotation_marks_around_the_quote_are_not_part_of_it(self):
        quoted = make(logic=[issue(where="「说明 TREM2 驱动纤维化」")])
        assert critique.unquoted(quoted, PROPOSAL) == []

    def test_something_missing_altogether_needs_no_quote(self):
        absent = make(rigor=[issue(where="", problem="没有写盲法")])
        assert critique.unquoted(absent, PROPOSAL) == []


class TestCitations:
    def test_a_paper_not_in_the_library_is_flagged(self, store):
        made_up = make(novelty=Novelty(level=3, kind="new", judgement="x", prior=["[PMID:99999999]"]))
        assert critique.fabricated_citations(made_up, store) == ["99999999"]

    def test_a_real_one_passes(self, store):
        assert critique.fabricated_citations(make(), store) == []


# ------------------------------------------------------------------- the review


class Stub:
    """Answers the search-term helper and the review, and records the prompt."""

    def __init__(self, answer: Critique):
        self.answer = answer
        self.asked: list = []

    def parse(self, system, messages, schema, **kwargs):
        self.asked.append((schema, system))
        if schema is SearchTerms:
            return SearchTerms(terms=["TREM2", "macrophage", "fibrosis"])
        return self.answer


class TestReview:
    @pytest.fixture
    def cfg(self, tmp_path):
        return Config(workspace=tmp_path / ".mra")

    def test_the_fatal_problem_is_listed_first(self, cfg, store):
        answer = make(logic=[issue(severity="minor"), issue(severity="fatal"), issue(severity="major")])
        result, _ = critique.review(cfg, store, Stub(answer), PROPOSAL)
        assert [i.severity for i in result.logic] == ["fatal", "major", "minor"]

    def test_a_chinese_proposal_still_finds_the_english_literature(self, cfg, store):
        stub = Stub(make())
        _, warning = critique.review(cfg, store, stub, PROPOSAL)
        prompt = "\n".join(stub.asked[-1][1])
        assert "[PMID:34556677]" in prompt
        assert warning == ""

    def test_the_notes_and_the_material_reach_the_model(self, cfg, store):
        stub = Stub(make())
        critique.review(cfg, store, stub, PROPOSAL, notes="硕士二年级，还剩一年半", focus="重点看实验设计")
        prompt = "\n".join(stub.asked[-1][1])
        assert "硕士二年级，还剩一年半" in prompt and "重点看实验设计" in prompt
        assert "每组 3 只" in prompt

    def test_an_empty_library_is_said_out_loud(self, cfg, tmp_path):
        with Store(tmp_path / "empty.db") as empty:
            _, warning = critique.review(cfg, empty, Stub(make()), PROPOSAL)
        assert "文献库是空的" in warning

    def test_a_library_with_nothing_on_the_topic_is_said_out_loud(self, cfg, store):
        class NoTerms(Stub):
            def parse(self, system, messages, schema, **kwargs):
                if schema is SearchTerms:
                    return SearchTerms(terms=[])
                return self.answer

        _, warning = critique.review(cfg, store, NoTerms(make()), "肺癌 免疫治疗 课题设计")
        assert "对得上的文献" in warning


class TestMaterial:
    def test_one_file_is_the_material_itself(self):
        text, cut = critique.combine([("开题.docx", "正文")])
        assert (text, cut) == ("正文", False)

    def test_several_files_are_labelled(self):
        text, _ = critique.combine([("开题.docx", "正文"), ("组会.pptx", "幻灯片")])
        assert "=== 开题.docx ===\n正文" in text and "=== 组会.pptx ===\n幻灯片" in text

    def test_a_thesis_length_document_is_cut_and_says_so(self):
        text, cut = critique.combine([("全文.docx", "段落。\n" * 30000)])
        assert cut and len(text) < 60000


# ------------------------------------------------------------------- rendering


class TestRendering:
    def test_the_verdict_comes_first(self):
        first = critique.format_critique(make()).splitlines()[3]
        assert first.startswith("结论：改完再开始")

    def test_a_fatal_problem_is_announced_before_the_details(self):
        text = critique.format_critique(make(logic=[issue(severity="fatal")]))
        assert text.index("1 处动摇课题本身") < text.index(critique.SECTIONS["logic"])

    def test_every_section_heading_appears_once_in_order(self):
        """The eval scopes its checks by these headings."""
        text = critique.format_critique(make(feasibility=[issue()]))
        positions = [text.index(heading) for heading in critique.SECTIONS.values()]
        assert positions == sorted(positions)
        assert all(text.count(heading) == 1 for heading in critique.SECTIONS.values())

    def test_the_quote_and_the_fix_are_shown(self):
        text = critique.format_critique(make())
        assert "原文：「说明 TREM2 驱动纤维化」" in text
        assert "怎么改：改成「相关」" in text

    def test_prior_work_is_named_by_title(self, store):
        text = critique.format_critique(make(), store)
        assert "[PMID:34556677] TREM2+ macrophages restrain rather than promote" in text

    def test_an_empty_section_says_so(self):
        assert "没发现断的地方" in critique.format_critique(make(logic=[]))

    def test_verdicts_all_have_labels(self):
        assert set(VERDICTS) == set(critique.VERDICT_LABELS)

    def test_markdown_for_sending_on(self, store):
        text = critique.to_markdown(make(), store)
        assert text.startswith("# 课题审阅")
        for heading in ("## 逻辑链", "## 新颖性：2/5", "## 严谨性", "## 先做什么", "## 组会上可以问的问题"):
            assert heading in text

    def test_json_round_trips(self):
        assert json.loads(critique.to_json(make()))["verdict"] == "revise"


# ---------------------------------------------------------------------- the CLI


class TestCommandLine:
    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        workspace = tmp_path / ".mra"
        cli.main(["--workspace", str(workspace), "import", str(CORPUS)])
        calls = []

        def invoke(*args, answer=None):
            stub = Stub(answer or make())
            calls.append(stub)
            monkeypatch.setattr(cli, "_llm", lambda cfg: stub)
            return cli.main(["--workspace", str(workspace), *args])

        invoke.calls = calls
        return invoke

    def test_a_word_proposal_is_reviewed_and_saved(self, run, tmp_path, capsys):
        proposal = docx(tmp_path / "开题报告.docx", "".join(p(line) for line in PROPOSAL.splitlines() if line))
        out = tmp_path / "审阅意见.md"
        assert run("critique", str(proposal), "--notes", "硕士二年级", "-o", str(out)) == 0
        printed = capsys.readouterr().out
        assert "课题审阅" in printed and "结论：改完再开始" in printed
        assert out.read_text(encoding="utf-8").startswith("# 课题审阅")

    def test_a_criticism_of_words_the_student_never_wrote_is_flagged(self, run, tmp_path, capsys):
        proposal = tmp_path / "plan.md"
        proposal.write_text(PROPOSAL, encoding="utf-8")
        run("critique", str(proposal), answer=make(logic=[issue(where="本课题首次发现靶点")]))
        assert "在材料里找不到" in capsys.readouterr().out

    def test_an_unreadable_file_stops_before_anything_is_spent(self, run, tmp_path):
        old = tmp_path / "开题报告.doc"
        old.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(SystemExit, match="docx"):
            run("critique", str(old))
        assert run.calls[-1].asked == []


class TestWiring:
    def test_the_cli_exposes_it(self):
        from mra.cli import build_parser

        choices = next(a for a in build_parser()._actions if a.dest == "command").choices
        assert "critique" in choices

    def test_the_web_offers_it_marked_costly(self):
        from mra import webui

        assert "critique" in webui.COMMANDS and "critique" in webui.COSTLY
        argv = webui.build_argv("critique", {"files": ["开题.docx", "组会.pptx"], "notes": "硕士",
                                             "output": "审阅意见.md"})
        assert argv == ["critique", "开题.docx", "组会.pptx", "--notes=硕士", "--output=审阅意见.md"]
        assert "审课题" in webui.read_index().decode("utf-8")

    def test_the_guide_names_it(self):
        assert "mra critique" in cli.GUIDE
