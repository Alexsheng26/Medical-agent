"""Ingest the researcher's own papers — PDFs and plain text.

A PI's personal library is usually better curated than any PubMed query, and
until now it was the one input that could not reach the knowledge base.

The hard part is identity. Citation integrity (QA-1) rests on every citation
resolving to a stored record, and a PDF off a laptop has no PMID. So each local
document gets an id: a real PMID when one can be read off the text, otherwise a
content-derived `local:<hash>` that is stable across re-imports. Both forms are
verified the same way — the guarantee is unchanged, only the namespace widens.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .pubmed import Article

log = logging.getLogger(__name__)

PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
XML_SUFFIXES = {".xml", ".nbib"}

SUPPORTED_SUFFIXES = PDF_SUFFIXES | TEXT_SUFFIXES | XML_SUFFIXES

# Below this a PDF almost certainly has no text layer — a scan or an image-only
# export. Silently storing 40 characters of header would poison retrieval, so
# these are reported rather than accepted.
MIN_USABLE_CHARS = 500

# What gets sent to the model for metadata extraction. The front matter carries
# title, authors, journal and DOI; the rest is wasted tokens for this purpose.
METADATA_WINDOW = 3000

# For a PDF the window is the first page rather than the first N characters.
# Two-column publisher layouts emit body text ahead of the title block in the
# content stream, so the front matter is often not at the front of the extracted
# text — in one Springer paper the title landed at character 3751. The page is
# the unit that actually means "front matter"; a character count only guesses at
# it. These bounds cover a title page that is mostly whitespace (fall through to
# page 2) and one that is unusually dense (stop before the budget runs away).
FRONT_MIN_CHARS = 1200
FRONT_MAX_CHARS = 9000

_ID_RE = re.compile(r"\b(?:PMID|pmid)[:\s]*(\d{4,9})\b")
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")


@dataclass
class ExtractedDoc:
    """Text pulled off one file, with an honest account of how it went."""

    path: Path
    text: str
    kind: str  # "pdf" | "text"
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    front: str = ""  # the window that carries title, authors, journal, DOI

    @property
    def usable(self) -> bool:
        return len(self.text.strip()) >= MIN_USABLE_CHARS


class IngestError(RuntimeError):
    pass


# ------------------------------------------------------------------ extraction


def extract(path: Path) -> ExtractedDoc:
    """Pull text out of one file. Never raises on a bad document — an
    unreadable file is reported through `warnings` so a batch import can
    continue and still tell the researcher exactly what failed."""
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _extract_pdf(path)
    if suffix in TEXT_SUFFIXES:
        text = _clean(path.read_text(encoding="utf-8", errors="replace"))
        doc = ExtractedDoc(path=path, text=text, kind="text", front=text[:METADATA_WINDOW])
        if not doc.usable:
            doc.warnings.append(f"only {len(doc.text)} characters — too short to be a paper")
        return doc
    raise IngestError(f"Unsupported file type: {path.name}")


def _extract_pdf(path: Path) -> ExtractedDoc:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IngestError(
            "PDF support needs pypdf. Install it with `pip install pypdf`, or "
            "save the paper as .txt instead."
        ) from exc

    doc = ExtractedDoc(path=path, text="", kind="pdf")
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - malformed PDFs are common
        doc.warnings.append(f"could not be opened ({type(exc).__name__}); skipped")
        return doc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")  # many "encrypted" PDFs use an empty owner password
        except Exception:  # noqa: BLE001
            doc.warnings.append("password-protected; skipped")
            return doc

    chunks = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the file
            doc.warnings.append(f"page {number} could not be read ({type(exc).__name__})")

    doc.pages = len(reader.pages)
    doc.text = _clean("\n".join(chunks))
    doc.front = _front_matter(chunks)

    if not doc.usable:
        doc.warnings.append(
            f"only {len(doc.text)} characters across {doc.pages} pages — this looks "
            "like a scan with no text layer. It needs OCR before it can be used."
        )
    return doc


def _front_matter(pages: list[str]) -> str:
    """The pages that plausibly carry title, authors, journal and DOI.

    Page one, plus page two when page one is too sparse to be a real first page
    (a cover sheet, a rights stamp, an offprint banner). Bounded so a dense
    two-column opener does not spend the whole extraction budget.
    """
    window = ""
    for page in pages[:2]:
        window = _clean(f"{window}\n{page}")
        if len(window) >= FRONT_MIN_CHARS:
            break
    return window[:FRONT_MAX_CHARS]


def _clean(text: str) -> str:
    """Tidy extractor output without destroying structure.

    PDF extraction leaves hyphenated line breaks and ragged spacing. Paragraph
    breaks are kept because retrieval and the model both read better with them.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # rejoin words split across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -------------------------------------------------------------------- identity


def local_id(text: str) -> str:
    """A stable id derived from the document's content.

    Content-derived rather than filename-derived so that re-importing the same
    paper under a different name does not create a duplicate record.
    """
    normalised = re.sub(r"\s+", " ", text).strip().lower()
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return f"local:{digest[:8]}"


def sniff_identifiers(text: str) -> tuple[str, str]:
    """Look for a PMID and DOI in the raw text before spending a model call.

    Publisher PDFs very often print both on the first page, so this resolves a
    good fraction of documents for free. Callers normally pass `doc.front`,
    which is already scoped to that page; the cap here only bounds a caller that
    hands over the whole document.
    """
    window = text[:FRONT_MAX_CHARS]
    pmid_match = _ID_RE.search(window)
    doi_match = _DOI_RE.search(window)
    doi = doi_match.group(1).rstrip(".,;)") if doi_match else ""
    return (pmid_match.group(1) if pmid_match else "", doi)


def to_article(doc: ExtractedDoc, meta, *, pmid: str = "", doi: str = "") -> Article:
    """Build a stored record from an extracted document plus its metadata.

    The full text goes in `abstract` because that is the column retrieval
    indexes; storing only a summary there would throw away the reason for
    importing full text in the first place.
    """
    identifier = pmid or local_id(doc.text)
    return Article(
        pmid=identifier,
        title=(getattr(meta, "title", "") or doc.path.stem).strip(),
        abstract=doc.text,
        journal=(getattr(meta, "journal", "") or "").strip(),
        journal_abbrev=(getattr(meta, "journal", "") or "").strip(),
        year=(getattr(meta, "year", "") or "").strip(),
        authors=list(getattr(meta, "authors", []) or []),
        doi=doi or (getattr(meta, "doi", "") or "").strip(),
        publication_types=["Local full text"],
        mesh_terms=list(getattr(meta, "keywords", []) or []),
    )


def is_local(identifier: str) -> bool:
    return identifier.startswith("local:")


def marker(identifier: str) -> str:
    """The inline citation marker the model must use for this record."""
    if is_local(identifier):
        return f"[LOCAL:{identifier.split(':', 1)[1]}]"
    return f"[PMID:{identifier}]"
