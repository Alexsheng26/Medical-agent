"""Literature acquisition: query planning, retrieval, structured extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import prompts
from .config import Config
from .llm import LLM
from .pubmed import PubMed, parse_efetch_xml
from .schemas import LitCard, QueryPlan
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    query_used: str
    plan: QueryPlan | None
    found: int
    added: int
    skipped_no_abstract: int


def plan_query(llm: LLM, topic: str, language: str = "zh") -> QueryPlan:
    """Turn a clinical question into a PubMed search strategy (Clause 1.2)."""
    return llm.parse(
        [prompts.core(language), prompts.load("query_plan")],
        [{"role": "user", "content": f"Topic / clinical question:\n\n{topic}"}],
        QueryPlan,
    )


def search(
    cfg: Config,
    store: Store,
    llm: LLM | None,
    topic: str,
    *,
    retmax: int = 50,
    raw_query: str | None = None,
) -> SearchResult:
    """Search PubMed and store the results.

    `raw_query` bypasses query planning entirely — for when the researcher has
    already built the query themselves in PubMed and wants it used verbatim.
    """
    plan = None
    if raw_query:
        query = raw_query
    else:
        if llm is None:
            raise ValueError("Query planning needs a model; pass raw_query instead.")
        plan = plan_query(llm, topic, cfg.chat_language)
        query = plan.pubmed_query
        log.info("Planned query: %s", query)

    pubmed = PubMed(email=cfg.ncbi_email, api_key=cfg.ncbi_api_key)
    articles = pubmed.search_and_fetch(query, retmax=retmax)

    usable = [a for a in articles if not a.is_empty]
    added = store.add_articles(usable, topic=topic)

    return SearchResult(
        query_used=query,
        plan=plan,
        found=len(articles),
        added=added,
        skipped_no_abstract=len(articles) - len(usable),
    )


def import_xml(store: Store, paths: list[Path], topic: str = "") -> SearchResult:
    """Load records from PubMed XML saved on disk.

    The escape hatch for when E-utilities is unreachable — a restricted network,
    an institutional firewall, or an offline machine. In PubMed's web interface:
    run the search, then *Send to → File → Format: XML*, and import the result.
    """
    found = added = skipped = 0
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"No such file: {path}")

        articles = parse_efetch_xml(path.read_text(encoding="utf-8", errors="replace"))
        usable = [a for a in articles if not a.is_empty]
        found += len(articles)
        skipped += len(articles) - len(usable)
        added += store.add_articles(usable, topic=topic)
        log.info("%s: %d records", path.name, len(articles))

    return SearchResult(
        query_used=f"import of {len(paths)} file(s)",
        plan=None,
        found=found,
        added=added,
        skipped_no_abstract=skipped,
    )


def digest(
    cfg: Config,
    store: Store,
    llm: LLM,
    *,
    limit: int | None = None,
    on_progress=None,
) -> tuple[int, int]:
    """Extract structured cards for stored articles that do not have one yet.

    Returns (succeeded, failed). Failures are logged and skipped rather than
    aborting the batch — one malformed abstract should not cost the whole run.
    """
    pending = store.pmids_without_cards()
    if limit:
        pending = pending[:limit]

    system = [prompts.core(cfg.chat_language), prompts.load("extract")]
    succeeded = failed = 0

    for index, pmid in enumerate(pending, start=1):
        article = store.get_article(pmid)
        if article is None:
            continue

        content = (
            f"PMID: {article.pmid}\n"
            f"Journal: {article.journal} ({article.year})\n"
            f"Publication types: {', '.join(article.publication_types) or 'unspecified'}\n"
            f"Title: {article.title}\n\n"
            f"Abstract:\n{article.abstract}"
        )
        try:
            card = llm.parse(system, [{"role": "user", "content": content}], LitCard)
            payload = card.model_dump()
            # Trust our own record over whatever the model echoed back.
            payload["pmid"] = pmid
            payload["evidence_strength"] = max(1, min(5, payload.get("evidence_strength", 3)))
            store.save_card(pmid, payload)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - batch job, keep going
            log.warning("Extraction failed for PMID:%s — %s", pmid, exc)
            failed += 1

        if on_progress:
            on_progress(index, len(pending), pmid)

    return succeeded, failed
