"""Which papers a command is about, and what the model is shown of each.

`mindmap` and `compare` both start from "these papers": ones already in the
library, named by the identifier `mra library` prints, or files the researcher
has just picked. A file is imported on the way in, so "pick two PDFs and compare
them" is one step rather than import, look up the identifiers, then compare.

Each paper is shown to the model under a letter — A, B, C — rather than its
identifier. Letters are short, impossible to mistype into a real-looking PMID,
and checkable: a letter outside the set is a paper nobody selected.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from pathlib import Path

from . import fulltext, pipeline
from .config import Config
from .llm import LLM
from .pubmed import Article
from .store import Store

# Shared across all selected papers. Large enough for three full-text PDFs at
# a useful depth, small enough that six abstracts and their cards are cheap.
TEXT_BUDGET = 60000
PER_PAPER_CEILING = pipeline.DIGEST_TEXT_LIMIT

LABELS = string.ascii_uppercase

_BRACKETED = re.compile(r"^\[?\s*(PMID|LOCAL)\s*:\s*([^\]\s]+)\s*\]?$", re.IGNORECASE)


@dataclass
class Paper:
    label: str
    identifier: str
    article: Article
    card: dict | None

    @property
    def short_title(self) -> str:
        title = (self.article.title or "(无标题)").strip()
        return title if len(title) <= 70 else title[:69] + "…"

    def legend(self) -> str:
        year = f" ({self.article.year})" if self.article.year else ""
        return f"{self.label}  {self.identifier}  {self.short_title}{year}"


def normalise_identifier(item: str) -> str:
    """Accept the forms people paste: `[PMID:123]`, `LOCAL:abcd`, `local:abcd`."""
    item = item.strip()
    match = _BRACKETED.match(item)
    if match:
        kind, value = match.group(1).lower(), match.group(2)
        return f"local:{value.lower()}" if kind == "local" else value
    if item.lower().startswith("local:"):
        return item.lower()
    return item


def resolve(
    cfg: Config,
    store: Store,
    items: list[str],
    *,
    llm: LLM | None = None,
    minimum: int = 1,
    maximum: int = len(LABELS),
) -> list[Paper]:
    """Turn what the researcher picked into labelled papers.

    An item is a file if it exists on disk, otherwise an identifier. Files are
    imported first; an unknown identifier is an error that says where the
    identifiers are, rather than a quietly shorter comparison.
    """
    identifiers: list[str] = []
    files = [Path(item) for item in items if Path(item).is_file()]

    if files:
        result = pipeline.import_files(cfg, store, files, llm=llm)
        for path in files:
            found = result.identifiers.get(str(path), [])
            if not found:
                reasons = [w for w in result.warnings if w.startswith(path.name)]
                raise ValueError(
                    f"{path.name} 读不出可用的内容。"
                    + (f"\n  {reasons[0]}" if reasons else "")
                )
            identifiers.extend(found)

    unknown = []
    for item in items:
        if Path(item).is_file():
            continue
        identifier = normalise_identifier(item)
        if store.has_article(identifier):
            identifiers.append(identifier)
        else:
            unknown.append(item)

    if unknown:
        raise ValueError(
            f"库里没有：{', '.join(unknown)}\n"
            "编号在「文献列表」（mra library）第一列。也可以直接给 PDF 文件，会先导入再处理。"
        )

    # Same paper picked twice — as a file and by its identifier, say — is one paper.
    unique = list(dict.fromkeys(identifiers))
    if len(unique) < minimum:
        raise ValueError(f"至少要选 {minimum} 篇，现在是 {len(unique)} 篇。")
    if len(unique) > maximum:
        raise ValueError(
            f"一次最多 {maximum} 篇，现在是 {len(unique)} 篇。"
            "篇数再多，每篇能给模型看的内容就太少了，比较会流于表面。"
        )

    papers = []
    for label, identifier in zip(LABELS, unique):
        article = store.get_article(identifier)
        if article is None:  # pragma: no cover - checked above
            continue
        papers.append(Paper(label, identifier, article, store.get_card(identifier)))
    return papers


def material(papers: list[Paper]) -> str:
    """What the model reads: each paper's reading card if it has one, and its text.

    The card is what `digest` already paid for — the question, the findings,
    the limitations the authors did not admit — so it is reused rather than
    re-derived. The text is still sent, because a card is a summary, and a
    comparison turns on details a summary drops: the exact model, the n, which
    cells were targeted.
    """
    share = min(PER_PAPER_CEILING, TEXT_BUDGET // max(1, len(papers)))
    blocks = []
    for paper in papers:
        article = paper.article
        lines = [
            f"=== Paper {paper.label} ===",
            f"Identifier: {paper.identifier}",
            f"Title: {article.title}",
            f"Journal: {article.journal or article.journal_abbrev or 'unknown'} ({article.year or 'n.d.'})",
            f"Publication types: {', '.join(article.publication_types) or 'unspecified'}",
        ]
        if paper.card:
            lines += ["", "Reading card (from an earlier structured reading):", _card_text(paper.card)]
        lines += ["", "Text:", trim(article.abstract or "(no text stored)", share)]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _card_text(card: dict) -> str:
    def bullets(values) -> str:
        return "\n".join(f"  - {v}" for v in (values or [])) or "  -"

    return "\n".join([
        f"Question: {card.get('scientific_question', '')}",
        "Key findings:", bullets(card.get("key_findings")),
        "Methods:", bullets(card.get("methods")),
        f"Claimed novelty: {card.get('novelty_claim', '')}",
        "Limitations:", bullets(card.get("limitations")),
        f"Evidence strength (1-5): {card.get('evidence_strength', '?')}",
    ])


def trim(text: str, limit: int) -> str:
    """The same budgeting `digest` uses: references first, tables kept."""
    return fulltext.fit(text, limit)


def by_label(papers: list[Paper]) -> dict[str, Paper]:
    return {paper.label: paper for paper in papers}
