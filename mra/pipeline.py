"""Literature acquisition: query planning, retrieval, extraction, and sync."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import ingest, prompts
from .config import Config
from .llm import LLM
from .pubmed import PubMed, PubMedError, parse_efetch_xml
from .schemas import LitCard, LocalArticleMeta, QueryPlan
from .store import Store

log = logging.getLogger(__name__)

# A full-text import can run to 60k characters. Extraction reads the whole thing
# only up to this point — past it the marginal value drops sharply while the
# token cost keeps climbing.
DIGEST_TEXT_LIMIT = 24000


@dataclass
class SearchResult:
    query_used: str
    plan: QueryPlan | None
    found: int
    added: int
    skipped_no_abstract: int
    warnings: list[str] = field(default_factory=list)


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


# ----------------------------------------------------------------- importing


def import_files(
    cfg: Config,
    store: Store,
    paths: list[Path],
    *,
    llm: LLM | None = None,
    topic: str = "",
    on_file=None,
) -> SearchResult:
    """Load documents from disk into the knowledge base.

    Three kinds, dispatched on suffix:

    - **PubMed XML** — from the browser's *Send to → File → Format: XML*. Fully
      structured; the escape hatch when E-utilities is unreachable.
    - **PDF** — the researcher's own library. Text is extracted locally and
      metadata read off the front matter with one model call per file.
    - **Plain text / Markdown** — same as PDF without the extraction step.
    """
    found = added = skipped = 0
    warnings: list[str] = []

    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"No such file: {path}")

        suffix = path.suffix.lower()
        if suffix in ingest.XML_SUFFIXES:
            articles = parse_efetch_xml(path.read_text(encoding="utf-8", errors="replace"))
            usable = [a for a in articles if not a.is_empty]
            found += len(articles)
            skipped += len(articles) - len(usable)
            added += store.add_articles(usable, topic=topic)
            if on_file:
                on_file(path, len(usable), [])
            continue

        if suffix not in ingest.SUPPORTED_SUFFIXES:
            warnings.append(f"{path.name}: unsupported file type, skipped")
            continue

        doc = ingest.extract(path)
        found += 1

        if not doc.usable:
            skipped += 1
            for warning in doc.warnings:
                warnings.append(f"{path.name}: {warning}")
            if on_file:
                on_file(path, 0, doc.warnings)
            continue

        article = _local_article(cfg, doc, llm)
        if store.has_article(article.pmid):
            skipped += 1
            warnings.append(f"{path.name}: already in the knowledge base as {article.pmid}")
        else:
            added += store.add_articles([article], topic=topic)

        for warning in doc.warnings:
            warnings.append(f"{path.name}: {warning}")
        if on_file:
            on_file(path, 1, doc.warnings)

    return SearchResult(
        query_used=f"import of {len(paths)} file(s)",
        plan=None,
        found=found,
        added=added,
        skipped_no_abstract=skipped,
        warnings=warnings,
    )


def _local_article(cfg: Config, doc: ingest.ExtractedDoc, llm: LLM | None):
    """Build a stored record for a local document, resolving its identity.

    A printed PMID or DOI is used when present — it is free and exact. Only when
    neither is found does this spend a model call on the front matter.
    """
    pmid, doi = ingest.sniff_identifiers(doc.text)

    meta = None
    if llm is not None:
        try:
            meta = llm.parse(
                [
                    prompts.core(cfg.chat_language),
                    prompts.load("local_meta", text=doc.text[: ingest.METADATA_WINDOW]),
                ],
                [{"role": "user", "content": f"Extract metadata for {doc.path.name}."}],
                LocalArticleMeta,
                max_tokens=2000,
                cache_upto=0,
            )
            pmid = pmid or (meta.pmid or "").strip()
            doi = doi or (meta.doi or "").strip()
        except Exception as exc:  # noqa: BLE001 - metadata is a nicety, text is the point
            log.warning("Metadata extraction failed for %s — %s", doc.path.name, exc)
            doc.warnings.append(
                f"metadata could not be extracted ({type(exc).__name__}); "
                "stored with the filename as title"
            )

    return ingest.to_article(doc, meta, pmid=pmid, doi=doi)


# ----------------------------------------------------------------- extraction


def digest(
    cfg: Config,
    store: Store,
    llm: LLM,
    *,
    limit: int | None = None,
    only: list[str] | None = None,
    on_progress=None,
    stop_check=None,
) -> tuple[int, int]:
    """Extract structured cards for stored articles that do not have one yet.

    Returns (succeeded, failed). Failures are logged and skipped rather than
    aborting the batch — one malformed abstract should not cost the whole run.

    `stop_check` is consulted before each call and ends the batch when it returns
    true. That is what lets an unattended run stop at a spend ceiling instead of
    working through a thousand papers unsupervised.
    """
    pending = store.pmids_without_cards()
    if only is not None:
        wanted = set(only)
        pending = [p for p in pending if p in wanted]
    if limit:
        pending = pending[:limit]

    system = [prompts.core(cfg.chat_language), prompts.load("extract")]
    succeeded = failed = 0

    for index, pmid in enumerate(pending, start=1):
        if stop_check is not None and stop_check():
            log.info("Stopping extraction early at %d/%d", index - 1, len(pending))
            break

        article = store.get_article(pmid)
        if article is None:
            continue

        body = article.abstract
        truncated = len(body) > DIGEST_TEXT_LIMIT
        if truncated:
            body = body[:DIGEST_TEXT_LIMIT]

        content = (
            f"PMID: {article.pmid}\n"
            f"Journal: {article.journal} ({article.year})\n"
            f"Publication types: {', '.join(article.publication_types) or 'unspecified'}\n"
            f"Title: {article.title}\n\n"
            f"{'Full text (truncated)' if truncated else 'Abstract'}:\n{body}"
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
            log.warning("Extraction failed for %s — %s", pmid, exc)
            failed += 1

        if on_progress:
            on_progress(index, len(pending), pmid)

    return succeeded, failed


# ----------------------------------------------------------------------- sync


@dataclass
class SyncResult:
    watches_run: int = 0
    found: int = 0
    new_ids: list[str] = field(default_factory=list)
    digested: int = 0
    digest_failed: int = 0
    stopped_on_budget: bool = False
    errors: list[str] = field(default_factory=list)
    brief_path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and not self.stopped_on_budget


def sync(
    cfg: Config,
    store: Store,
    llm: LLM,
    *,
    max_cost: float | None = None,
    do_digest: bool = True,
    on_event=None,
) -> SyncResult:
    """Replay every saved watch, extract what is new, and report.

    Written to be safe on a cron: idempotent (already-stored records are
    skipped), bounded (a spend ceiling ends it cleanly), and tolerant (one
    unreachable watch does not abort the others).
    """
    result = SyncResult()
    watches = store.list_watches()
    if not watches:
        result.errors.append("No watches configured. Add one with `mra watch add`.")
        return result

    def emit(message: str) -> None:
        log.info(message)
        if on_event:
            on_event(message)

    before = set(store.all_pmids())
    pubmed = PubMed(email=cfg.ncbi_email, api_key=cfg.ncbi_api_key)

    for watch in watches:
        name = watch["name"]
        try:
            articles = pubmed.search_and_fetch(watch["query"], retmax=watch["retmax"])
            usable = [a for a in articles if not a.is_empty]
            added = store.add_articles(usable, topic=watch["topic"])
            store.mark_watch_run(name, added)
            result.watches_run += 1
            result.found += len(articles)
            emit(f"watch '{name}': {len(articles)} found, {added} new")
        except PubMedError as exc:
            # One dead watch must not cost the whole run — the others may be fine.
            result.errors.append(f"watch '{name}' failed: {exc}")
            emit(f"watch '{name}': FAILED ({exc})")

    result.new_ids = sorted(set(store.all_pmids()) - before)

    if do_digest and result.new_ids:
        ledger = llm.ledger

        def over_budget() -> bool:
            if max_cost is None or ledger is None:
                return False
            spent = ledger.session.cost(cfg.model)
            return spent is not None and spent >= max_cost

        emit(f"extracting {len(result.new_ids)} new record(s)")
        result.digested, result.digest_failed = digest(
            cfg, store, llm, only=result.new_ids, stop_check=over_budget
        )
        result.stopped_on_budget = over_budget()
        if result.stopped_on_budget:
            emit(f"stopped: spend ceiling ${max_cost:.2f} reached")

    return result
