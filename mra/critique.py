"""审课题 — a supervisor's read of a student's project before it starts.

What the first real user does most weeks is not write papers: it is read a
student's 开题报告 or 组会 slides and decide whether the logic holds, whether it
is new, whether the design will survive a reviewer — and what to ask the
student. `assess` answers a different question (can these data carry a paper)
and needs data; a project at this stage has none.

Three things decide whether this is worth more than asking a chat window:

- **Novelty is judged against the researcher's own library**, with citations
  that open, the same way `assess` does. The literature a supervisor has
  collected on a topic is better than a model's memory of it, and a student
  who learns from a reviewer that their premise was refuted two years ago has
  lost the year.
- **Every problem is located in the material**, by a quote that is checked
  against it. A critique of a sentence the student never wrote is worse than
  none — it is the one the student will point out.
- **The output is ordered for a decision**: the verdict, then what is fatal,
  then what to do first and what to ask at the meeting.
"""

from __future__ import annotations

import json
import re
import textwrap
import unicodedata

from . import citations, fulltext, prompts, retrieval
from .config import Config
from .llm import LLM
from .rebuttal import _normalize
from .schemas import Critique, ProposalIssue
from .store import Store

RETRIEVAL_K = 12

# The opening of a proposal — title, background, the gap — is what names its
# subject. The methods further down would pull in every paper that used flow.
QUERY_CHARS = 1500

# A 开题报告 runs 5-15k characters, a full 国自然 application 30-40k. Past this
# the literature and the answer start competing for the context window.
MAX_MATERIAL = 40000

# Chinese packs more into a character than English does, so the probe is
# shorter than the rebuttal's; an accidental 24-character match is still
# implausible.
QUOTE_PROBE = 24

VERDICT_LABELS = {"go": "可以开始", "revise": "改完再开始", "rethink": "需要重新想"}
SEVERITY_LABELS = {"fatal": "动摇课题", "major": "必须改", "minor": "顺手改"}
_SEVERITY_MARK = {"fatal": "✗", "major": "!", "minor": "·"}
_SEVERITY_RANK = {"fatal": 0, "major": 1, "minor": 2}

#: Section headings, distinctive enough to find in the output: the model's own
#: prose says 新颖性 and 可行性 often, and a heading has to be told apart.
SECTIONS = {
    "logic": "■ 逻辑链",
    "novelty": "■ 新颖性",
    "rigor": "■ 严谨性",
    "feasibility": "■ 可行性",
    "first_steps": "■ 先做什么",
    "questions": "■ 组会上可以问的问题",
    "strengths": "■ 站得住的地方",
}


def combine(named_texts: list[tuple[str, str]]) -> tuple[str, bool]:
    """One block of material from one or more files, and whether it was cut."""
    if len(named_texts) == 1:
        text = named_texts[0][1]
    else:
        text = "\n\n".join(f"=== {name} ===\n{body}" for name, body in named_texts)
    fitted = fulltext.fit(text, MAX_MATERIAL)
    return fitted, fitted != text


def review(
    cfg: Config,
    store: Store,
    llm: LLM,
    material: str,
    *,
    notes: str = "",
    focus: str = "",
) -> tuple[Critique, str]:
    """The critique, and a warning when the library had nothing to judge
    novelty against (empty when it did)."""
    query = f"{notes} {material[:QUERY_CHARS]}".strip()
    context, _ = retrieval.build_context(store, query, k=RETRIEVAL_K, cfg=cfg, llm=llm)

    system = [
        prompts.core(cfg.chat_language),
        prompts.load(
            "critique",
            notes=notes.strip() or "（没有补充。学生的年级、剩余时间、实验室条件都不知道——"
                                   "可行性里需要这些的地方，放进 questions。）",
            focus=focus.strip() or "（没有特别要求。按上面的顺序全面审。）",
            context=context,
            material=material,  # last: nothing after it can be mistaken for a placeholder
        ),
    ]
    critique = llm.parse(
        system,
        [{"role": "user", "content": "Review the project."}],
        Critique,
        effort=cfg.effort,
        max_tokens=cfg.max_tokens,
        cache_upto=0,
    )
    for issues in (critique.logic, critique.rigor, critique.feasibility):
        issues.sort(key=lambda issue: _SEVERITY_RANK.get(issue.severity, 3))
    return critique, _literature_warning(context)


def _literature_warning(context: str) -> str:
    if context.startswith(retrieval.EMPTY_LIBRARY):
        return ("文献库是空的，所以「新颖性」那一项没有依据，只是模型的看法。先用「检索 PubMed」"
                "把这个方向的文献收进来，再审一次。")
    if context.startswith(retrieval.NOTHING_MATCHED):
        return ("库里没有和这个课题对得上的文献，所以「新颖性」那一项基本没有依据。"
                "建议给这个学生的方向单独建一个课题，先「检索 PubMed」收文献，再审一次。")
    return ""


# ------------------------------------------------------------------- the checks


def _issues(critique: Critique):
    for key in ("logic", "rigor", "feasibility"):
        for issue in getattr(critique, key):
            yield SECTIONS[key].lstrip("■ "), issue


_QUOTE_MARKS = "「」『』“”\"'‘’《》"


def _squash(text: str) -> str:
    """Fold what differs between a document and a model quoting it.

    Full-width and half-width punctuation (（一） vs (一)), the space this tool
    puts after a list label, curly quotes, line wrapping. Not the words.
    """
    text = unicodedata.normalize("NFKC", _normalize(text))
    return re.sub(r"\s+", "", text)


def unquoted(critique: Critique, material: str) -> list[str]:
    """Problems whose quoted location is not in the material."""
    haystack = _squash(material)
    missing = []
    for section, issue in _issues(critique):
        probe = _squash(issue.where.strip().strip(_QUOTE_MARKS))[:QUOTE_PROBE]
        if probe and probe not in haystack:
            missing.append(f"{section}：「{issue.where[:40]}」")
    return missing


def fabricated_citations(critique: Critique, store: Store) -> list[str]:
    """Identifiers anywhere in the critique that are not in the library."""
    return list(citations.check(to_json(critique), store).unverified)


# ------------------------------------------------------------------- rendering


def format_critique(critique: Critique, store: Store | None = None) -> str:
    lines = ["课题审阅", "=" * 60, ""]
    verdict = VERDICT_LABELS.get(critique.verdict, critique.verdict)
    lines += [f"结论：{verdict} —— {critique.verdict_reason}", ""]
    if fatal := [i for _, i in _issues(critique) if i.severity == "fatal"]:
        lines.append(f"  其中 {len(fatal)} 处动摇课题本身，先看标 ✗ 的。")
        lines.append("")

    lines.append("我理解的课题（和学生想的不一样的话，先对齐这个）：")
    lines += _indent(critique.understanding)
    lines.append("假说：")
    lines += _indent(critique.hypothesis)

    lines += _issue_section(SECTIONS["logic"], critique.logic, "没发现断的地方。")

    novelty = critique.novelty
    lines += ["", "─" * 60, f"{SECTIONS['novelty']}  {novelty.level}/5 · {novelty.kind}", ""]
    lines += _indent(novelty.judgement)
    for marker in novelty.prior:
        lines.append(f"    已有的工作：{_describe(marker, store)}")

    lines += _issue_section(SECTIONS["rigor"], critique.rigor, "没发现明显的设计漏洞。")
    lines += _issue_section(SECTIONS["feasibility"], critique.feasibility, "按材料里写的条件，做得下来。")

    lines += ["", "─" * 60, SECTIONS["first_steps"], ""]
    lines += [f"  {n}. {step}" for n, step in enumerate(critique.first_steps, 1)]
    if critique.questions:
        lines += ["", "─" * 60, SECTIONS["questions"], ""]
        lines += [f"  ? {question}" for question in critique.questions]
    if critique.strengths:
        lines += ["", "─" * 60, SECTIONS["strengths"], ""]
        lines += [f"  ✓ {item}" for item in critique.strengths]
    return "\n".join(lines)


def _issue_section(heading: str, issues: list[ProposalIssue], none: str) -> list[str]:
    lines = ["", "─" * 60, f"{heading}  （{len(issues)} 处）" if issues else heading, ""]
    if not issues:
        return lines + [f"  {none}"]
    for n, issue in enumerate(issues):
        mark = _SEVERITY_MARK.get(issue.severity, "·")
        label = SEVERITY_LABELS.get(issue.severity, issue.severity)
        if n:
            lines.append("")
        lines.append(f"  {mark} [{label}] {issue.problem}")
        if issue.where.strip():
            lines.append(f"      原文：「{issue.where.strip().strip(_QUOTE_MARKS)}」")
        lines.append(f"      后果：{issue.consequence}")
        lines.append(f"      怎么改：{issue.fix}")
    return lines


def _describe(marker: str, store: Store | None) -> str:
    """The marker, plus the paper's title when the library has it."""
    if store is None:
        return marker
    found = citations.find_citations(marker)
    article = store.get_article(found[0]) if found else None
    if article is None:
        return marker
    source = " ".join(part for part in (article.journal_abbrev or article.journal, article.year) if part)
    return f"{marker} {article.title}" + (f"（{source}）" if source else "")


def _indent(text: str, width: int = 72) -> list[str]:
    return [f"    {line}" for line in (textwrap.wrap(text, width=width) or [""])]


def to_markdown(critique: Critique, store: Store | None = None) -> str:
    """The same review as a file — something a supervisor can send on."""
    verdict = VERDICT_LABELS.get(critique.verdict, critique.verdict)
    out = ["# 课题审阅", "", f"**结论：{verdict}** —— {critique.verdict_reason}", "",
           "**我理解的课题：**" + critique.understanding, "", "**假说：**" + critique.hypothesis, ""]

    def issues(title: str, items: list[ProposalIssue], none: str) -> None:
        out.extend([f"## {title}", ""])
        if not items:
            out.extend([none, ""])
        for issue in items:
            out.append(f"- **[{SEVERITY_LABELS.get(issue.severity, issue.severity)}]** {issue.problem}")
            if issue.where.strip():
                out.append(f"  - 原文：「{issue.where.strip().strip(_QUOTE_MARKS)}」")
            out.append(f"  - 后果：{issue.consequence}")
            out.append(f"  - 怎么改：{issue.fix}")
        out.append("")

    issues("逻辑链", critique.logic, "没发现断的地方。")
    novelty = critique.novelty
    out += [f"## 新颖性：{novelty.level}/5 · {novelty.kind}", "", novelty.judgement, ""]
    out += [f"- 已有的工作：{_describe(marker, store)}" for marker in novelty.prior]
    out.append("")
    issues("严谨性", critique.rigor, "没发现明显的设计漏洞。")
    issues("可行性", critique.feasibility, "按材料里写的条件，做得下来。")
    out += ["## 先做什么", ""] + [f"{n}. {s}" for n, s in enumerate(critique.first_steps, 1)] + [""]
    if critique.questions:
        out += ["## 组会上可以问的问题", ""] + [f"- {q}" for q in critique.questions] + [""]
    if critique.strengths:
        out += ["## 站得住的地方", ""] + [f"- {s}" for s in critique.strengths] + [""]
    return "\n".join(out).rstrip() + "\n"


def to_json(critique: Critique) -> str:
    return json.dumps(critique.model_dump(), ensure_ascii=False, indent=2)
