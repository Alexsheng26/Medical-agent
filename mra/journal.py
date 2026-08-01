"""Journal style profiling (Clause 3).

Two sources of samples, and the difference matters:

- **PubMed abstracts** are free and instant, but only support title and abstract
  conventions plus a rough sense of scope. Structural claims about Introductions
  and Discussions inferred from abstracts alone are weak.
- **Full-text files** the researcher supplies produce a profile that can
  actually drive drafting.

Use PubMed to bootstrap, then add three to five full texts of papers close to
the researcher's own work.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import prompts
from .config import Config
from .llm import LLM
from .pubmed import PubMed
from .schemas import JournalProfile
from .store import Store

log = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}

# Full texts are long; this keeps a five-paper sample inside a sane prompt while
# preserving the parts that carry style (opening and closing of each section).
MAX_CHARS_PER_FULLTEXT = 24000


def collect_pubmed_samples(cfg: Config, journal: str, count: int = 20, years: int = 5) -> list[str]:
    """Pull recent abstracts from the journal to bootstrap a profile."""
    pubmed = PubMed(email=cfg.ncbi_email, api_key=cfg.ncbi_api_key)
    query = f'"{journal}"[Journal] AND hasabstract'
    articles = pubmed.search_and_fetch(
        query, retmax=count, sort="date", mindate=str(_year_ago(years))
    )

    samples = []
    for article in articles:
        if article.is_empty:
            continue
        samples.append(
            f"TITLE: {article.title}\n"
            f"TYPE: {', '.join(article.publication_types) or 'unspecified'}\n"
            f"YEAR: {article.year}\n"
            f"ABSTRACT:\n{article.abstract}"
        )
    return samples


def collect_file_samples(directory: Path) -> list[str]:
    """Read full-text samples the researcher has saved as plain text."""
    if not directory.exists():
        raise FileNotFoundError(f"No such directory: {directory}")

    samples = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 500:
            log.warning("Skipping %s — too short to profile from.", path.name)
            continue
        samples.append(f"FULL TEXT ({path.name}):\n{_trim(text, MAX_CHARS_PER_FULLTEXT)}")

    if not samples:
        raise ValueError(
            f"No usable .txt/.md files in {directory}. Save each paper's text as a "
            "separate .txt file (copy-paste from the PDF is fine)."
        )
    return samples


def build_profile(
    cfg: Config,
    store: Store,
    llm: LLM,
    journal: str,
    *,
    sample_dir: Path | None = None,
    pubmed_count: int = 20,
    years: int = 5,
) -> JournalProfile:
    """Build and store a style profile for one journal."""
    samples: list[str] = []
    if sample_dir:
        samples.extend(collect_file_samples(sample_dir))
    if pubmed_count:
        try:
            samples.extend(collect_pubmed_samples(cfg, journal, pubmed_count, years))
        except Exception as exc:  # noqa: BLE001 - full texts alone are still usable
            if not samples:
                raise
            log.warning("PubMed sampling failed (%s); profiling from local files only.", exc)

    if not samples:
        raise ValueError(f"No samples collected for {journal}.")

    log.info("Profiling %s from %d samples.", journal, len(samples))

    system = [
        prompts.core(cfg.chat_language),
        prompts.load("journal_profile", journal=journal, samples="\n\n===\n\n".join(samples)),
    ]
    profile = llm.parse(
        system,
        [{"role": "user", "content": f"Build the style profile for {journal}."}],
        JournalProfile,
        effort=cfg.effort,
        max_tokens=16000,
        cache_upto=0,
    )

    payload = profile.model_dump()
    payload["_meta"] = {
        "sample_count": len(samples),
        "used_full_text": bool(sample_dir),
    }
    store.save_journal(journal, payload)
    return profile


def profile_text(store: Store, journal: str) -> str:
    """Render a stored profile for injection into a drafting prompt."""
    payload = store.get_journal(journal)
    if payload is None:
        known = ", ".join(store.list_journals()) or "none yet"
        raise ValueError(
            f"No profile for {journal!r}. Build one with `mra journal add {journal}`. "
            f"Profiles on file: {known}"
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _trim(text: str, limit: int) -> str:
    """Keep the head and tail — openings and closings carry the most style."""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.6)]
    tail = text[-int(limit * 0.4) :]
    return f"{head}\n\n[... middle omitted ...]\n\n{tail}"


def _year_ago(years: int) -> int:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).year - years
