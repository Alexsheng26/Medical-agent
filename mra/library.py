"""Reading the knowledge base back out.

Everything `digest` extracts — the question a paper set out to answer, its
findings, what its authors do not admit — went into SQLite and came out only
indirectly, through `chat` retrieving it or `export` dumping the whole store as
JSON. So importing a PDF, paying for a card, and then having no way to see what
was read was the expected experience rather than a bug report.

This module is the plain view: what is in the library, and what one card says.
Formatting only — the data comes from Store unchanged.
"""

from __future__ import annotations

from typing import Any

from .pubmed import Article
from .store import Store

TITLE_WIDTH = 58
NO_LINE_START = "，。、；：？！）》」』’”%,.;:?!)]}"
# Wording follows LitCard.evidence_strength, whose anchors are 1 = a single
# in-vitro observation, 3 = solid animal or observational human data, 5 =
# randomised or multi-cohort human evidence. 2 and 4 are the gaps between.
# "2 = 体外为主，或小样本" was wrong: the first paper this ran on was a
# cross-sectional study of 9,781 people that landed at 2 for design reasons,
# and the label called it small and in-vitro.
STRENGTH_WORDS = {
    1: "1 单一体外观察",
    2: "2 弱于扎实的观察性研究（设计、报告或验证上有硬伤）",
    3: "3 扎实的动物或观察性人群数据",
    4: "4 强于单项观察性研究，但不到随机对照",
    5: "5 随机对照或多队列人群证据",
}


def format_library(store: Store) -> str:
    """One line per stored document, newest identifiers last."""
    pmids = store.all_pmids()
    if not pmids:
        return (
            "文献库是空的。\n\n"
            "先导入：菜单里的「导入文献」，或者 `mra import 文件.pdf`。\n"
            "想直接试手感，用「试用示例」导入自带的 8 篇。"
        )

    without = set(store.pmids_without_cards())
    rows = []
    for pmid in pmids:
        article = store.get_article(pmid)
        if article is None:
            continue
        card = None if pmid in without else store.get_card(pmid)
        rows.append((pmid, article, card))

    lines = [f"文献库里有 {len(rows)} 篇。", ""]
    lines.append(_pad("编号", 14) + _pad("年份", 6) + _pad("提炼", 6) + "标题")
    lines.append("─" * 46)
    for pmid, article, card in rows:
        mark = "✓" if card else "—"
        strength = f"  [证据 {card['evidence_strength']}]" if card else ""
        lines.append(
            _pad(pmid, 14) + _pad(article.year or "?", 6) + _pad(mark, 6)
            + _clip(article.title) + strength
        )

    pending = len(without)
    lines.append("")
    if pending:
        lines.append(
            f"其中 {pending} 篇还没提炼。跑「提炼文献」后再回来看，"
            "「提炼」那一列会变成 ✓。"
        )
    lines.append("看某一篇的提炼结果：在下面的输入框里填它的编号，或者 `mra library <编号>`。")
    return "\n".join(lines)


def format_card(store: Store, identifier: str) -> str:
    """The full card for one document, or an explanation of why there is none."""
    pmid = identifier.strip()
    article = store.get_article(pmid)
    if article is None:
        return (
            f"库里没有编号 {pmid!r}。\n\n"
            "编号就是「文献列表」第一列里的那一串 —— PubMed 来的是数字，"
            "自己导入的 PDF 是 local: 开头的。"
        )

    card = store.get_card(pmid)
    head = [
        article.title or "(无标题)",
        "",
        f"  编号    {pmid}",
        f"  期刊    {article.journal or article.journal_abbrev or '—'}  {article.year}",
        f"  作者    {_authors(article)}",
    ]
    if article.doi:
        head.append(f"  DOI     {article.doi}")

    if card is None:
        head += [
            "",
            "这一篇还没有提炼。跑「提炼文献」之后，这里会显示：",
            "  它想回答什么问题 · 具体发现 · 用了什么方法 · 作者说什么是新的",
            "  · 局限（包括作者自己没承认的）· 临床相关性 · 证据强度",
            "",
            "摘要原文：",
            _wrap(article.abstract or "（这一篇没有摘要）"),
        ]
        return "\n".join(head)

    body = [
        "",
        "─" * 78,
        "",
        "科学问题",
        _wrap(card.get("scientific_question", "")),
        "",
        "关键发现",
        *_bullets(card.get("key_findings")),
        "",
        "方法",
        *_bullets(card.get("methods")),
        "",
        "作者主张的新意",
        _wrap(card.get("novelty_claim", "")),
        "",
        "局限（含作者未承认的）",
        *_bullets(card.get("limitations")),
        "",
        "临床相关性",
        _wrap(card.get("clinical_relevance", "")),
        "",
        "机制关键词",
        "  " + "、".join(card.get("mechanism_keywords") or []) or "  —",
        "",
        "证据强度",
        "  " + STRENGTH_WORDS.get(card.get("evidence_strength"), str(card.get("evidence_strength"))),
    ]
    return "\n".join(head + body)


def _columns(text: str) -> int:
    """Display width. A CJK character occupies two terminal columns."""
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """Pad to a column width, not a character count.

    `f"{'编号':<14}"` pads by characters, so a two-character Chinese heading
    lands four columns wide and every heading after it is off by two.
    """
    return text + " " * max(1, width - _columns(text))


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= TITLE_WIDTH else text[: TITLE_WIDTH - 1] + "…"


def _authors(article: Article) -> str:
    if not article.authors:
        return "—"
    if len(article.authors) <= 3:
        return ", ".join(article.authors)
    return f"{article.authors[0]} 等（共 {len(article.authors)} 位）"


def _bullets(items: Any) -> list[str]:
    if not items:
        return ["  —"]
    return [f"  · {_wrap(str(item), indent=4).lstrip()}" for item in items]


def _wrap(text: str, width: int = 74, indent: int = 2) -> str:
    """Wrap on width, counting CJK characters as two columns.

    A line of Chinese wrapped at 74 *characters* runs to about 148 columns and
    wraps again wherever the terminal happens to end, which breaks the layout.
    """
    text = " ".join((text or "").split())
    if not text:
        return " " * indent + "—"

    pad = " " * indent
    lines, current, columns = [], "", 0
    for char in text:
        size = 2 if ord(char) > 0x2E80 else 1
        # Chinese typesetting does not begin a line with closing punctuation.
        # Letting it overhang by one column is the standard fix and reads far
        # better than a line that opens with a comma.
        if columns + size > width and current and char not in NO_LINE_START:
            lines.append(pad + current)
            current, columns = "", 0
        current += char
        columns += size
    if current:
        lines.append(pad + current)
    return "\n".join(lines)
