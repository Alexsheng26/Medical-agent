"""Where papers agree, where they differ, and whether a contradiction is real.

The second of the two things the first real user asked for: "比较上传的文章
之间的异同点". Placing two summaries side by side would satisfy the wording and
nothing else — the researcher can read abstracts. What they cannot get from
the abstracts is the judgement: which differences change how the results read,
and whether papers that seem to disagree actually do. Most published
contradictions dissolve once you notice the two groups used a different injury
model, or depleted a different population of cells.

The comparison is also read against the researcher's own frozen hypothesis
when there is one, because "these two papers disagree" is only interesting
for what it does to the thing you are trying to show.

Papers are labelled A, B, C in the prompt; three things the model cannot be
trusted on are checked afterwards — that every letter it uses was selected,
that every selected paper got a cell in every row, and that it cites nothing
outside the selection.
"""

from __future__ import annotations

import json

from . import citations, prompts, selection
from .config import Config
from .llm import LLM
from .schemas import Comparison
from .store import Store

MINIMUM = 2
MAXIMUM = 6



def compare(
    cfg: Config,
    store: Store,
    llm: LLM,
    papers: list[selection.Paper],
    *,
    focus: str = "",
) -> Comparison:
    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "compare",
            hypothesis=hypothesis_text(store),
            focus=focus.strip() or "（没有特别要求。按上面的维度全面比较。）",
            material=selection.material(papers),
        ),
    ]
    return llm.parse(
        system,
        [{"role": "user", "content": "Compare the papers."}],
        Comparison,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )


def hypothesis_text(store: Store) -> str:
    latest = store.latest_hypothesis()
    if latest is None:
        return "（这个课题还没有冻结的假说。for_you 就写给正在设计下一个实验的人。）"
    version, body = latest
    lines = [f"The researcher's current hypothesis (v{version}):", f"Title: {body.get('title', '')}",
             f"Statement: {body.get('statement', '')}"]
    if chain := body.get("mechanism_chain"):
        lines.append("Proposed causal chain: " + " → ".join(chain))
    if weak := body.get("open_weaknesses"):
        lines.append("Known weak points: " + "; ".join(weak))
    return "\n".join(lines)


# ------------------------------------------------------------------- the checks


def stray_letters(result: Comparison, papers: list[selection.Paper]) -> list[str]:
    """Letters used in a `paper` field that name no selected paper."""
    valid = {p.label for p in papers}
    used = [cell.paper.strip() for row in result.rows for cell in row.cells]
    used += [cell.paper.strip() for conflict in result.conflicts for cell in conflict.positions]
    return sorted({letter for letter in used if letter not in valid})


def missing_cells(result: Comparison, papers: list[selection.Paper]) -> list[str]:
    """Rows where a selected paper has no cell.

    A paper with no cell in "主要发现" has been dropped from the comparison of
    findings, and a table that silently drops a column reads as complete.
    """
    labels = [p.label for p in papers]
    gaps = []
    for row in result.rows:
        present = {cell.paper.strip() for cell in row.cells}
        for label in labels:
            if label not in present:
                gaps.append(f"{row.dimension}：{label}")
    return gaps


def foreign_citations(result: Comparison, papers: list[selection.Paper]) -> list[str]:
    """[PMID:x] / [LOCAL:x] markers for papers that were not selected."""
    selected = {p.identifier for p in papers}
    text = to_json(result)
    return [i for i in citations.find_citations(text) if i not in selected]


# ------------------------------------------------------------------ rendering


def _cells_in_order(cells, papers):
    by_letter = {cell.paper.strip(): cell.text for cell in cells}
    return [(p.label, by_letter.get(p.label)) for p in papers]


def format_comparison(result: Comparison, papers: list[selection.Paper], focus: str = "") -> str:
    lines = ["文献异同比较", "=" * 60, ""]
    lines += [f"  {paper.legend()}" for paper in papers]
    if focus.strip():
        lines += ["", f"  你的要求：{focus.strip()}"]
    lines.append("")

    if result.shared:
        lines += ["共同点", ""]
        lines += [f"  · {item}" for item in result.shared]
        lines.append("")

    lines += ["逐项对比", ""]
    for row in result.rows:
        lines.append("─" * 60)
        lines.append(f"{row.dimension}   [{'基本一致' if row.alike else '不同'}]")
        for label, text in _cells_in_order(row.cells, papers):
            lines.append(f"  {label}  {text if text is not None else '（这一项漏了这篇）'}")
        if row.meaning.strip() and not row.alike:
            lines.append(f"  → {row.meaning}")
        lines.append("")

    if result.conflicts:
        lines += ["═" * 60, "看起来互相矛盾的地方", ""]
        for conflict in result.conflicts:
            lines.append(f"  ✗ {conflict.point}")
            for label, text in _cells_in_order(conflict.positions, papers):
                if text is not None:
                    lines.append(f"     {label}：{text}")
            lines.append(f"     最可能的解释：{conflict.reconciliation}")
            lines.append(f"     什么能分出对错：{conflict.decider}")
            lines.append("")

    lines += ["═" * 60, "放在一起怎么看", "", f"  {result.synthesis}", ""]
    lines += ["对你的课题意味着什么", "", f"  {result.for_you}"]
    return "\n".join(lines)


def to_markdown(result: Comparison, papers: list[selection.Paper], focus: str = "") -> str:
    """For pasting into a report or a lab meeting slide: a real table."""
    def cell(text: str | None) -> str:
        # None is a paper the model left out of this row, which must not read
        # the same as a paper that has nothing to report on it.
        if text is None:
            return "（漏了这篇）"
        return (text or "—").replace("|", "\\|").replace("\n", " ")

    lines = ["# 文献异同比较", ""]
    lines += [f"- **{p.label}** {p.identifier} — {p.short_title}" + (f" ({p.article.year})" if p.article.year else "")
              for p in papers]
    if focus.strip():
        lines += ["", f"*要求：{focus.strip()}*"]

    if result.shared:
        lines += ["", "## 共同点", ""] + [f"- {item}" for item in result.shared]

    lines += ["", "## 逐项对比", "",
              "| 维度 | " + " | ".join(p.label for p in papers) + " | 差异意味着 |",
              "|---|" + "---|" * len(papers) + "---|"]
    for row in result.rows:
        cells = [cell(text) for _, text in _cells_in_order(row.cells, papers)]
        meaning = "基本一致" if row.alike else cell(row.meaning)
        lines.append(f"| {cell(row.dimension)} | " + " | ".join(cells) + f" | {meaning} |")

    if result.conflicts:
        lines += ["", "## 看起来互相矛盾的地方"]
        for conflict in result.conflicts:
            lines += ["", f"**{conflict.point}**", ""]
            lines += [f"- {label}：{text}" for label, text in _cells_in_order(conflict.positions, papers)
                      if text is not None]
            lines += [f"- 最可能的解释：{conflict.reconciliation}", f"- 什么能分出对错：{conflict.decider}"]

    lines += ["", "## 放在一起怎么看", "", result.synthesis,
              "", "## 对你的课题意味着什么", "", result.for_you]
    return "\n".join(lines) + "\n"


def to_json(result: Comparison) -> str:
    return json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
