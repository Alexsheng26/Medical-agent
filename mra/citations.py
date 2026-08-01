"""Citation integrity (QA-1).

The agent may only cite documents that are actually in the local knowledge base.
Every generated document is checked before it is written to disk, so a
hallucinated reference surfaces as a hard warning rather than reaching a
manuscript.

Two namespaces, one rule. PubMed records are cited `[PMID:12345]`; the
researcher's own imported full texts are cited `[LOCAL:a1b2c3d4]` because they
have no PMID. Both must resolve to a stored record — widening the namespace
does not weaken the guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import Store

PMID_RE = re.compile(r"\[PMID:\s*(\d{4,9})\]", re.IGNORECASE)
LOCAL_RE = re.compile(r"\[LOCAL:\s*([0-9a-f]{6,16})\]", re.IGNORECASE)

# Kept as the module's public name: callers (writing.py's fidelity guard) use it
# to detect citations dropped during a rewrite, and it must see both namespaces.
CITATION_RE = re.compile(
    r"\[(?:PMID:\s*(?P<pmid>\d{4,9})|LOCAL:\s*(?P<local>[0-9a-f]{6,16}))\]",
    re.IGNORECASE,
)

# A bare PMID next to citation-ish words is often one the model wrote without
# the required bracket form; worth flagging separately.
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


def find_citations(text: str) -> list[str]:
    """Every cited identifier, in first-appearance order (that is how references
    get numbered). Local hashes are returned in their stored `local:` form."""
    found = []
    for match in CITATION_RE.finditer(text):
        if match.group("pmid"):
            found.append(match.group("pmid"))
        else:
            found.append(f"local:{match.group('local').lower()}")
    return _dedupe(found)


def check(text: str, store: Store) -> CitationReport:
    cited = find_citations(text)
    loose = [p for p in _dedupe(LOOSE_PMID_RE.findall(text)) if p not in cited]

    verified, unverified = [], []
    for identifier in cited:
        (verified if store.has_article(identifier) else unverified).append(identifier)

    return CitationReport(
        cited=cited, verified=verified, unverified=unverified, malformed=loose
    )


def reference_list(text: str, store: Store) -> str:
    """Render a numbered reference list for every document cited in `text`."""
    lines = []
    for index, identifier in enumerate(find_citations(text), start=1):
        article = store.get_article(identifier)
        if article is None:
            lines.append(
                f"{index}. [{_marker(identifier)}] — NOT IN KNOWLEDGE BASE, verify manually"
            )
            continue

        authors = ", ".join(article.authors[:3])
        if len(article.authors) > 3:
            authors += ", et al"
        doi = f" doi:{article.doi}" if article.doi else ""

        title = _terminate(article.title)
        if identifier.startswith("local:"):
            # Flag provenance: this came off the researcher's disk, not from an
            # indexed database, so the usual metadata guarantees do not apply.
            lines.append(
                f"{index}. {authors or '[author not extracted]'}. {title} "
                f"{article.journal or ''} {article.year}{doi} "
                f"({_marker(identifier)}, local full text)".replace("  ", " ")
            )
        else:
            lines.append(
                f"{index}. {authors}. {title} "
                f"{article.journal_abbrev or article.journal}. {article.year}. "
                f"PMID:{article.pmid}{doi}"
            )
    return "\n".join(lines)


def _terminate(title: str) -> str:
    """Titles from PubMed usually end in a period; ones read off a PDF often do
    not. Normalise so the reference list reads consistently."""
    title = title.strip()
    return title if title.endswith((".", "?", "!")) else f"{title}."


def _marker(identifier: str) -> str:
    if identifier.startswith("local:"):
        return f"LOCAL:{identifier.split(':', 1)[1]}"
    return f"PMID:{identifier}"


def _dedupe(items: list[str]) -> list[str]:
    """Preserve first-appearance order, which is how references get numbered."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
