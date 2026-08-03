"""Assemble the evidence context handed to the model.

Every generation path grounds on this. If a paper is not rendered here, the
model has no legitimate way to cite it — which is what makes the citation check
in `citations.py` meaningful rather than decorative.
"""

from __future__ import annotations

import logging
import re

from .pubmed import Article
from .store import Store

log = logging.getLogger(__name__)

# Han, kana and Hangul. The trigger is deliberately the script, not a failed
# search: a question written in Chinese cannot match an English corpus, so
# spending a search to discover that first only wastes a round trip.
_NON_LATIN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def needs_translation(query: str) -> bool:
    return bool(_NON_LATIN_RE.search(query))


def english_terms(cfg, llm, query: str) -> str:
    """One cheap call turning a question into English keywords.

    Returns an empty string when the model finds no searchable subject, or when
    the call fails — retrieval must degrade, never break, over a helper.
    """
    from . import prompts
    from .schemas import SearchTerms

    try:
        result = llm.parse(
            [prompts.load("search_terms", question=query)],
            [{"role": "user", "content": "List the search terms."}],
            SearchTerms,
            max_tokens=1000,
            effort="low",
        )
    except Exception as exc:  # noqa: BLE001 - a failed helper must not lose the turn
        log.warning("Could not derive English search terms (%s); searching as typed.", exc)
        return ""
    return " ".join(result.terms)


def build_context(
    store: Store,
    query: str,
    k: int = 12,
    *,
    cfg=None,
    llm=None,
) -> tuple[str, list[str]]:
    """Retrieve the k most relevant papers and render them for the prompt.

    Returns the rendered context and the PMIDs it covers, so callers can tell
    the model exactly which citations are legitimate.

    BM25 matches characters, so a question asked in Chinese scores zero on every
    paper in an English corpus. Two layers handle that. Given `cfg` and `llm`, a
    non-Latin question first buys English keywords for about a cent — the real
    fix, since it restores actual relevance ranking. Without them, or if that
    call fails, an empty result falls back to the most recent papers, which is
    right for a corpus of five and honest noise for a corpus of five hundred.
    The header says which of the three happened, so the model knows whether
    relevance was established, guessed at, or merely recency.
    """
    search_query = query
    if llm is not None and cfg is not None and needs_translation(query):
        # Appended, not substituted: gene symbols and journal names the
        # researcher typed are usually better search terms than any paraphrase.
        if terms := english_terms(cfg, llm, query):
            search_query = f"{query} {terms}"

    hits = store.search(search_query, limit=k)

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
