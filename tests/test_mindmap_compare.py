"""Mind maps and comparisons — the two things the first real user asked for.

The model's judgement is measured by `mra eval`. These cover what must hold no
matter what the model says: the word budget is kept and never silently, a
malformed tree is repaired rather than dropped, a comparison cannot quietly
lose a paper or borrow one nobody selected, and choosing papers works the way
a researcher would try it — by identifier in any pasted form, or by file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mra import cli, compare, mindmap, selection
from mra.config import Config
from mra.pubmed import parse_efetch_xml
from mra.schemas import Comparison, CompareCell, CompareRow, Conflict, MindMap, MindNode
from mra.store import Store

CORPUS = Path(__file__).resolve().parent.parent / "mra" / "examples" / "demo_corpus.xml"


@pytest.fixture
def cfg(tmp_path):
    return Config(workspace=tmp_path / ".mra")


@pytest.fixture
def store(cfg):
    cfg.ensure_workspace()
    with Store(cfg.db_path) as store:
        store.add_articles(parse_efetch_xml(CORPUS.read_text(encoding="utf-8")))
        yield store


def node(id, parent, text, source=""):
    return MindNode(id=id, parent=parent, text=text, source=source)


class Stub:
    """Hands back prepared answers in order and records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple[list, list]] = []

    def parse(self, system, messages, schema, **kwargs):
        self.asked.append((system, messages))
        return self.answers.pop(0)


# ---------------------------------------------------------------- selection


class TestSelection:
    def test_identifiers_in_every_pasted_form(self, cfg, store):
        papers = selection.resolve(cfg, store, ["[PMID:31234567]", " 28001122 "])
        assert [p.identifier for p in papers] == ["31234567", "28001122"]
        assert [p.label for p in papers] == ["A", "B"]

    def test_local_markers_normalise_to_the_stored_form(self):
        assert selection.normalise_identifier("[LOCAL:ABCD1234]") == "local:abcd1234"
        assert selection.normalise_identifier("LOCAL:abcd1234") == "local:abcd1234"
        assert selection.normalise_identifier("local:abcd1234") == "local:abcd1234"

    def test_an_unknown_identifier_says_where_the_identifiers_are(self, cfg, store):
        with pytest.raises(ValueError, match="文献列表"):
            selection.resolve(cfg, store, ["31234567", "99999999"])

    def test_a_file_is_imported_and_used(self, cfg, store, tmp_path):
        paper = tmp_path / "my_paper.txt"
        paper.write_text("Hepatic macrophages and fibrosis. " * 40, encoding="utf-8")
        papers = selection.resolve(cfg, store, [str(paper), "31234567"])
        assert papers[0].identifier.startswith("local:")
        assert store.has_article(papers[0].identifier)

    def test_a_file_already_in_the_library_is_found_not_refused(self, cfg, store, tmp_path):
        """'already in the knowledge base' is a reason to use it, not an error."""
        paper = tmp_path / "again.txt"
        paper.write_text("Kupffer cells and stellate activation. " * 40, encoding="utf-8")
        first = selection.resolve(cfg, store, [str(paper)])
        second = selection.resolve(cfg, store, [str(paper)])
        assert first[0].identifier == second[0].identifier

    def test_the_same_paper_twice_counts_once(self, cfg, store):
        papers = selection.resolve(cfg, store, ["31234567", "[PMID:31234567]", "28001122"])
        assert len(papers) == 2

    def test_too_few_and_too_many_are_refused_with_the_count(self, cfg, store):
        with pytest.raises(ValueError, match="至少要选 2 篇"):
            selection.resolve(cfg, store, ["31234567"], minimum=2)
        with pytest.raises(ValueError, match="一次最多 2 篇"):
            selection.resolve(cfg, store, store.all_pmids()[:3], maximum=2)

    def test_material_carries_the_reading_card_when_there_is_one(self, cfg, store):
        store.save_card("31234567", {"scientific_question": "SOURCE-OF-TGF", "key_findings": [],
                                     "methods": [], "limitations": ["UNADMITTED"],
                                     "evidence_strength": 3})
        text = selection.material(selection.resolve(cfg, store, ["31234567", "28001122"]))
        assert "SOURCE-OF-TGF" in text and "UNADMITTED" in text
        assert "=== Paper A ===" in text and "=== Paper B ===" in text

    def test_long_texts_share_the_budget(self):
        trimmed = selection.trim("x" * 100_000, 10_000)
        assert len(trimmed) < 10_200
        assert "omitted from the middle" in trimmed


class TestImportReportsIdentifiers:
    def test_xml_reports_every_record(self, cfg, tmp_path):
        from mra import pipeline

        cfg.ensure_workspace()
        with Store(cfg.db_path) as fresh:
            result = pipeline.import_files(cfg, fresh, [CORPUS])
        assert len(result.identifiers[str(CORPUS)]) == 8


# ------------------------------------------------------------------ counting


class TestCounting:
    @pytest.mark.parametrize("text, expected", [
        ("纤维化", 3),
        ("Sirius red", 2),
        ("TGF-β1 敲除 → 纤维化 ↓38%", 7),
        ("n = 48", 2),
        ("，。、！", 0),
        ("CCl4 小鼠", 3),
    ])
    def test_chinese_characters_and_english_words_count_one_each(self, text, expected):
        assert mindmap.count(text) == expected


# ---------------------------------------------------------------------- tree


class TestTreeRepair:
    def test_an_orphan_is_hung_off_the_root_not_dropped(self):
        fixed, root = mindmap.normalise([node("r", "", "根"), node("x", "ghost", "孤儿")])
        assert {n.id: n.parent for n in fixed}["x"] == root

    def test_a_cycle_is_broken(self):
        fixed, root = mindmap.normalise([node("r", "", "根"), node("a", "b", "甲"), node("b", "a", "乙")])
        parents = {n.id: n.parent for n in fixed}
        assert root in (parents["a"], parents["b"])

    def test_a_second_root_becomes_a_branch(self):
        fixed, root = mindmap.normalise([node("r", "", "根"), node("s", "", "另一个根")])
        assert root == "r"
        assert {n.id: n.parent for n in fixed}["s"] == "r"

    def test_duplicate_ids_keep_the_first(self):
        fixed, _ = mindmap.normalise([node("r", "", "根"), node("1", "r", "第一"), node("1", "r", "第二")])
        assert [n.text for n in fixed if n.id == "1"] == ["第一"]

    def test_an_empty_answer_is_an_error_not_an_empty_map(self):
        with pytest.raises(ValueError):
            mindmap.normalise([])


class TestPruning:
    def _tree(self):
        return [node("r", "", "根节点"), node("1", "r", "一级分支"),
                node("1.1", "1", "二级细节甲"), node("1.2", "1", "二级细节乙"),
                node("2", "r", "另一个一级")]

    def test_cuts_the_deepest_last_leaf_first(self):
        nodes, root = mindmap.normalise(self._tree())
        kept, cut = mindmap.prune(nodes, root, mindmap.total(nodes) - 1)
        assert cut == ["二级细节乙"]

    def test_never_cuts_the_root(self):
        nodes, root = mindmap.normalise(self._tree())
        kept, _ = mindmap.prune(nodes, root, 1)
        assert [n.id for n in kept] == ["r"]

    def test_reports_every_cut(self):
        nodes, root = mindmap.normalise(self._tree())
        kept, cut = mindmap.prune(nodes, root, 9)
        assert mindmap.total(kept) <= 9
        assert len(cut) == len(nodes) - len(kept)


# ------------------------------------------------------------------- drawing


def _papers(cfg, store, *ids):
    return selection.resolve(cfg, store, list(ids))


def _over(budget: int) -> MindMap:
    return MindMap(nodes=[node("r", "", "根"), *[
        node(str(i), "r", "很长的一个分支说明文字" * 3) for i in range(budget // 10)
    ]])


class TestDraw:
    def test_within_budget_is_one_call(self, cfg, store):
        llm = Stub(MindMap(nodes=[node("r", "", "门脉纤维化"), node("1", "r", "方法")]))
        drawn = mindmap.draw(cfg, llm, _papers(cfg, store, "31234567"), limit=300)
        assert len(llm.asked) == 1 and not drawn.redrawn and not drawn.pruned

    def test_over_budget_is_redrawn_once_with_the_overrun_named(self, cfg, store):
        small = MindMap(nodes=[node("r", "", "门脉纤维化"), node("1", "r", "方法")])
        llm = Stub(_over(300), small)
        drawn = mindmap.draw(cfg, llm, _papers(cfg, store, "31234567"), limit=50)
        retry = llm.asked[1][1][-1]["content"]
        assert "上限 50 字" in retry and "超了" in retry
        assert drawn.redrawn and drawn.chars <= 50 and not drawn.pruned

    def test_still_over_after_the_redraw_is_cut_and_the_cuts_listed(self, cfg, store):
        llm = Stub(_over(300), _over(300))
        drawn = mindmap.draw(cfg, llm, _papers(cfg, store, "31234567"), limit=40)
        assert drawn.chars <= 40
        assert drawn.pruned
        assert "删掉了这几个末端分支" in mindmap.format_map(drawn, _papers(cfg, store, "31234567"))

    def test_no_limit_means_no_redraw(self, cfg, store):
        llm = Stub(_over(300))
        drawn = mindmap.draw(cfg, llm, _papers(cfg, store, "31234567"), limit=0)
        assert len(llm.asked) == 1 and drawn.chars > 300

    def test_the_researchers_request_reaches_the_prompt(self, cfg, store):
        llm = Stub(MindMap(nodes=[node("r", "", "根")]))
        mindmap.draw(cfg, llm, _papers(cfg, store, "31234567"), focus="给本科生讲，侧重方法")
        assert "给本科生讲，侧重方法" in "\n".join(llm.asked[0][0])


class TestMapOutput:
    def _drawn(self):
        nodes, root = mindmap.normalise([
            node("r", "", "中心"), node("1", "r", "方法", "A"),
            node("1.1", "1", "敲除小鼠", "A"), node("2", "r", "A↔B 方向相反")])
        return mindmap.Drawn(nodes, root, 300)

    def test_terminal_tree_uses_branches(self, cfg, store):
        text = mindmap.plain_tree(self._drawn())
        assert "├─ 方法" in text and "│  └─ 敲除小鼠" in text and "└─ A↔B" in text

    def test_source_letters_only_when_there_are_several_papers(self, cfg, store):
        one = mindmap.format_map(self._drawn(), _papers(cfg, store, "31234567"))
        two = mindmap.format_map(self._drawn(), _papers(cfg, store, "31234567", "28001122"))
        assert "[A]" not in one and "[A]" in two

    def test_markdown_nests(self, cfg, store):
        text = mindmap.to_markdown(self._drawn(), _papers(cfg, store, "31234567"))
        assert text.startswith("# 中心") and "\n- 方法\n  - 敲除小鼠" in text

    def test_opml_is_valid_xml_and_escapes(self, cfg, store):
        import xml.dom.minidom

        nodes, root = mindmap.normalise([node("r", "", 'p<0.05 & "显著"'), node("1", "r", "子")])
        text = mindmap.to_opml(mindmap.Drawn(nodes, root, 0), [])
        assert xml.dom.minidom.parseString(text.encode("utf-8"))

    def test_the_web_line_carries_the_whole_tree(self, cfg, store):
        line = mindmap.web_line(self._drawn(), _papers(cfg, store, "31234567"))
        assert line.startswith(mindmap.WEB_PREFIX)
        payload = json.loads(line[len(mindmap.WEB_PREFIX):])
        assert payload["root"] == "r" and len(payload["nodes"]) == 4 and payload["limit"] == 300


# ------------------------------------------------------------------- compare


def _comparison(**changes) -> Comparison:
    base = dict(
        shared=["(A, B) 都是小鼠实验"],
        rows=[CompareRow(dimension="研究设计", alike=False, meaning="干预的细胞群不同",
                         cells=[CompareCell(paper="A", text="髓系敲除"),
                                CompareCell(paper="B", text="氯膦酸清除")])],
        conflicts=[],
        synthesis="不矛盾。",
        for_you="先区分常驻和募集的巨噬细胞。",
    )
    base.update(changes)
    return Comparison(**base)


class TestCompareChecks:
    def test_a_clean_comparison_flags_nothing(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        result = _comparison()
        assert not compare.stray_letters(result, papers)
        assert not compare.missing_cells(result, papers)
        assert not compare.foreign_citations(result, papers)

    def test_a_letter_nobody_selected_is_flagged(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        result = _comparison(conflicts=[Conflict(
            point="必要性", reconciliation="模型不同", decider="同一模型里重做",
            positions=[CompareCell(paper="A", text="x"), CompareCell(paper="D", text="y")])])
        assert compare.stray_letters(result, papers) == ["D"]

    def test_a_paper_dropped_from_a_row_is_flagged_and_shown(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        result = _comparison(rows=[CompareRow(dimension="局限", alike=False, meaning="",
                                              cells=[CompareCell(paper="A", text="只有相关")])])
        assert compare.missing_cells(result, papers) == ["局限：B"]
        assert "漏了这篇" in compare.format_comparison(result, papers)
        assert "漏了这篇" in compare.to_markdown(result, papers)

    def test_a_citation_outside_the_selection_is_flagged(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        result = _comparison(synthesis="见 [PMID:30778899]。")
        assert compare.foreign_citations(result, papers) == ["30778899"]


class TestCompareOutput:
    def test_conflict_heading_only_when_there_is_a_conflict(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        assert "看起来互相矛盾" not in compare.format_comparison(_comparison(), papers)

    def test_markdown_is_a_real_table(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        text = compare.to_markdown(_comparison(), papers)
        assert "| 维度 | A | B | 差异意味着 |" in text
        assert "| 研究设计 | 髓系敲除 | 氯膦酸清除 | 干预的细胞群不同 |" in text

    def test_a_pipe_in_a_cell_does_not_break_the_table(self, cfg, store):
        papers = _papers(cfg, store, "31234567", "28001122")
        result = _comparison(rows=[CompareRow(dimension="发现", alike=True, meaning="",
                                              cells=[CompareCell(paper="A", text="x | y"),
                                                     CompareCell(paper="B", text="z")])])
        assert "x \\| y" in compare.to_markdown(result, papers)

    def test_the_frozen_hypothesis_reaches_the_prompt(self, cfg, store):
        store.save_hypothesis({"title": "MY-HYPOTHESIS", "statement": "TREM2 restrains fibrosis",
                               "mechanism_chain": ["step one", "step two"]})
        llm = Stub(_comparison())
        compare.compare(cfg, store, llm, _papers(cfg, store, "31234567", "28001122"))
        system = "\n".join(llm.asked[0][0])
        assert "MY-HYPOTHESIS" in system and "step one → step two" in system

    def test_without_a_hypothesis_it_says_so_rather_than_inventing_one(self, store):
        assert "还没有冻结的假说" in compare.hypothesis_text(store)


# ----------------------------------------------------------------------- cli


class TestCommandLine:
    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        workspace = tmp_path / ".mra"
        cli.main(["--workspace", str(workspace), "import", str(CORPUS)])

        def invoke(*args, answers=()):
            stub = Stub(*answers)
            monkeypatch.setattr(cli, "_llm", lambda cfg: stub)
            return cli.main(["--workspace", str(workspace), *args])
        return invoke

    def _small_map(self):
        return MindMap(nodes=[node("r", "", "中心"), node("1", "r", "方法")])

    def test_the_browser_payload_is_printed_only_for_the_browser(self, run, capsys, monkeypatch):
        monkeypatch.delenv("MRA_WEB", raising=False)
        run("mindmap", "31234567", answers=[self._small_map()])
        assert mindmap.WEB_PREFIX not in capsys.readouterr().out

        monkeypatch.setenv("MRA_WEB", "1")
        run("mindmap", "31234567", answers=[self._small_map()])
        assert mindmap.WEB_PREFIX in capsys.readouterr().out

    def test_a_tiny_limit_is_refused_before_any_call(self, run, capsys):
        assert run("mindmap", "31234567", "--limit", "10") == 1
        assert "字数上限至少" in capsys.readouterr().err

    def test_compare_writes_markdown_by_default_and_json_on_request(self, run, tmp_path):
        run("compare", "31234567", "28001122", "-o", str(tmp_path / "c.md"), answers=[_comparison()])
        assert (tmp_path / "c.md").read_text(encoding="utf-8").startswith("# 文献异同比较")
        run("compare", "31234567", "28001122", "-o", str(tmp_path / "c.json"), answers=[_comparison()])
        assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["rows"]

    def test_compare_needs_two(self, run, capsys):
        assert run("compare", "31234567") == 1
        assert "至少要选 2 篇" in capsys.readouterr().err


# ------------------------------------------------------------------- wiring


class TestWiring:
    def test_both_are_on_the_page_and_marked_costly(self):
        from mra import webui

        page = webui.read_index().decode("utf-8")
        assert "思维导图" in page and "异同比较" in page
        assert {"mindmap", "compare"} <= webui.COSTLY

    def test_a_typed_zero_reaches_the_command(self):
        """`0 in (None, "", False)` is True in Python — "no limit" was being
        dropped on its way from the browser and the default of 300 applied."""
        from mra import webui

        assert "--limit=0" in webui.build_argv("mindmap", {"items": ["1"], "limit": 0})

    def test_the_guide_names_both(self):
        assert "mra mindmap" in cli.GUIDE and "mra compare" in cli.GUIDE

    def test_both_prompts_take_every_placeholder_the_code_fills(self):
        from mra import prompts

        for name, fields in (("mindmap", ("limit", "focus", "material")),
                             ("compare", ("hypothesis", "focus", "material"))):
            text = prompts._read(name)
            for field in fields:
                assert "{" + field + "}" in text, f"{name}: {field}"


class TestEvalCase:
    """The comparison has to be measurable, or its quality is an opinion."""

    def _case(self):
        from mra import evaluation

        return next(c for c in evaluation.load_cases() if c["id"] == "compare-planted-contradiction")

    def _inputs(self) -> str:
        """Everything the model is shown, and everything the report echoes:
        the two papers' titles and abstracts."""
        articles = {a.pmid: a for a in parse_efetch_xml(CORPUS.read_text(encoding="utf-8"))}
        return " ".join(f"{articles[i].title} {articles[i].abstract}"
                        for i in self._case()["args"]).lower()

    def test_both_papers_are_in_the_corpus_the_eval_seeds(self):
        ids = {a.pmid for a in parse_efetch_xml(CORPUS.read_text(encoding="utf-8"))}
        assert set(self._case()["args"]) <= ids

    def test_the_planted_reconciliation_is_really_there(self):
        """Diet model vs toxin model, all myeloid cells vs resident Kupffer cells."""
        text = self._inputs()
        assert "choline-deficient high-fat diet" in text and "ccl4" in text
        assert "myeloid-specific" in text and "kupffer cell depletion" in text
        assert "correlated" in text  # A's human arm is a correlation

    def test_no_marker_can_score_by_echoing_a_paper(self):
        """The report restates each paper in its cells, so a marker found in
        either abstract would score whether or not the point was made."""
        inputs = self._inputs()
        for expectation in self._case()["expect"]:
            echoed = [m for m in expectation["any"] if m.lower() in inputs]
            assert not echoed, f"{expectation['id']}: {echoed} is in the papers themselves"
