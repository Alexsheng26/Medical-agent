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

    When lexical retrieval finds nothing but the knowledge base is not empty,
    the most recent papers are sent instead. BM25 matches characters, so a
    question asked in Chinese about an English corpus scores zero on every
    paper — the researcher would import two PDFs, ask about them, and be told
    the knowledge base is empty. The header says which of the two happened, so
    the model knows whether relevance has been established or merely recency.
    """
    hits = store.search(query, limit=k)

    if hits:
        articles = [article for article, _score in hits]
        header = (
            f"{len(articles)} papers retrieved from the local knowledge base. "
            "These are the only papers you may cite.\n"
        )
    else:
        articles = store.recent_articles(limit=k)
        if not articles:
            return ("(The knowledge base is empty — nothing has been imported yet.)", [])
        header = (
            f"Keyword retrieval matched nothing for this query, so here are the "
            f"{len(articles)} most recently added papers instead. Their relevance to "
            "the question has NOT been established — check it before leaning on any "
            "of them, and say so if none of them bear on what was asked. These are "
            "still the only papers you may cite.\n"
        )

    blocks = [render_article(a, store.get_card(a.pmid)) for a in articles]
    return header + "\n\n---\n\n".join(blocks), [a.pmid for a in articles]


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
