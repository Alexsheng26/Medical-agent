"""Assemble the evidence context handed to the model.

Every generation path grounds on this. If a paper is not rendered here, the
model has no legitimate way to cite it — which is what makes the citation check
in `citations.py` meaningful rather than decorative.
"""

from __future__ import annotations

from .pubmed import Article
from .store import Store


def build_context(store: Store, query: str, k: int = 12) -> tuple[str, list[str]]:
    """Retrieve the k most relevant papers and render them for the prompt.

    Returns the rendered context and the PMIDs it covers, so callers can tell
    the model exactly which citations are legitimate.
    """
    hits = store.search(query, limit=k)
    if not hits:
        return ("(The knowledge base returned no matching papers for this query.)", [])

    blocks = []
    pmids = []
    for article, _score in hits:
        pmids.append(article.pmid)
        blocks.append(render_article(article, store.get_card(article.pmid)))

    header = (
        f"{len(blocks)} papers retrieved from the local knowledge base. "
        "These are the only papers you may cite.\n"
    )
    return header + "\n\n---\n\n".join(blocks), pmids


def render_article(article: Article, card: dict | None = None) -> str:
    """Render one paper. The structured card, when present, replaces the raw
    abstract — it is denser and already filtered for what matters.

    The first line shows the exact citation marker the model must copy. Local
    full texts are flagged as such: their metadata came off a PDF rather than an
    indexing database, and the extraction may have mangled tables and legends.
    """
    from .ingest import is_local, marker

    header = f"{marker(article.pmid)} {article.title}"
    if is_local(article.pmid):
        header += "  [local full text — metadata extracted from the file itself]"

    lines = [
        header,
        f"{article.journal_abbrev or article.journal} {article.year}"
        + (f" · {article.publication_types[0]}" if article.publication_types else ""),
    ]

    if card:
        lines.append(f"Question: {card.get('scientific_question', '')}")
        for finding in card.get("key_findings", []):
            lines.append(f"  · {finding}")
        if card.get("methods"):
            lines.append(f"Methods: {'; '.join(card['methods'])}")
        if card.get("limitations"):
            lines.append(f"Limitations: {'; '.join(card['limitations'])}")
        strength = card.get("evidence_strength")
        if strength:
            lines.append(f"Evidence strength: {strength}/5")
    else:
        abstract = article.abstract or "(no abstract available)"
        lines.append(abstract[:1800])

    return "\n".join(lines)


def render_pmid_list(store: Store, pmids: list[str]) -> str:
    """Render specific papers by PMID, for when the caller has already chosen."""
    blocks = []
    for pmid in pmids:
        article = store.get_article(pmid)
        if article:
            blocks.append(render_article(article, store.get_card(pmid)))
    return "\n\n---\n\n".join(blocks)
