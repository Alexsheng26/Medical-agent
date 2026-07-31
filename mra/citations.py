"""Citation integrity (QA-1).

The agent may only cite papers that are actually in the local knowledge base.
Every generated document is checked before it is written to disk, so a
hallucinated PMID surfaces as a hard warning rather than reaching a manuscript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import Store

CITATION_RE = re.compile(r"\[PMID:\s*(\d{4,9})\]", re.IGNORECASE)

# A bare 8-digit number next to citation-ish words is often a PMID the model
# wrote without the required bracket form; worth flagging separately.
LOOSE_PMID_RE = re.compile(r"(?<!\[PMID:)(?<!\d)(?:PMID|pmid)[:\s]+(\d{4,9})")


@dataclass
class CitationReport:
    cited: list[str]
    verified: list[str]
    unverified: list[str]
    malformed: list[str]

    @property
    def ok(self) -> bool:
        return not self.unverified and not self.malformed

    def summary(self) -> str:
        lines = [
            f"Citations: {len(self.cited)} referenced, {len(self.verified)} verified "
            f"against the knowledge base."
        ]
        if self.unverified:
            lines.append(
                "  ✗ NOT IN KNOWLEDGE BASE (do not trust these — they may be fabricated): "
                + ", ".join(self.unverified)
            )
        if self.malformed:
            lines.append(
                "  ! Loose PMID references not in [PMID:x] form: " + ", ".join(self.malformed)
            )
        if self.ok and self.cited:
            lines.append("  ✓ Every citation resolves to a stored record.")
        elif not self.cited:
            lines.append("  ! No citations found. A grounded document should carry them.")
        return "\n".join(lines)


def check(text: str, store: Store) -> CitationReport:
    cited = _dedupe(CITATION_RE.findall(text))
    loose = [p for p in _dedupe(LOOSE_PMID_RE.findall(text)) if p not in cited]

    verified, unverified = [], []
    for pmid in cited:
        (verified if store.has_article(pmid) else unverified).append(pmid)

    return CitationReport(
        cited=cited, verified=verified, unverified=unverified, malformed=loose
    )


def reference_list(text: str, store: Store) -> str:
    """Render a numbered reference list for every PMID cited in `text`."""
    pmids = _dedupe(CITATION_RE.findall(text))
    lines = []
    for index, pmid in enumerate(pmids, start=1):
        article = store.get_article(pmid)
        if article is None:
            lines.append(f"{index}. [PMID:{pmid}] — NOT IN KNOWLEDGE BASE, verify manually")
            continue
        authors = ", ".join(article.authors[:3])
        if len(article.authors) > 3:
            authors += ", et al"
        doi = f" doi:{article.doi}" if article.doi else ""
        lines.append(
            f"{index}. {authors}. {article.title} "
            f"{article.journal_abbrev or article.journal}. {article.year}. "
            f"PMID:{article.pmid}{doi}"
        )
    return "\n".join(lines)


def _dedupe(items: list[str]) -> list[str]:
    """Preserve first-appearance order, which is how references get numbered."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
