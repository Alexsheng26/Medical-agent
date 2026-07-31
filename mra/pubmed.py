"""PubMed E-utilities client.

Pure HTTP + XML: no model calls happen in this module, so the parser can be
tested against fixtures offline.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, Iterator

import httpx

log = logging.getLogger(__name__)

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# NCBI allows 3 requests/second anonymously, 10/second with an API key.
RATE_NO_KEY = 3.0
RATE_WITH_KEY = 10.0

# efetch accepts large id batches; 200 keeps individual responses manageable.
FETCH_BATCH = 200


@dataclass
class Article:
    pmid: str
    title: str = ""
    abstract: str = ""
    journal: str = ""
    journal_abbrev: str = ""
    year: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    publication_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        first = self.authors[0] if self.authors else "Anon"
        et_al = " et al." if len(self.authors) > 1 else ""
        return f"{first}{et_al} {self.journal_abbrev or self.journal} {self.year}. PMID:{self.pmid}"

    @property
    def is_empty(self) -> bool:
        """Records with no abstract carry almost no signal for extraction."""
        return not self.abstract.strip()


class PubMedError(RuntimeError):
    pass


class PubMed:
    def __init__(
        self,
        email: str = "",
        api_key: str = "",
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ):
        self.email = email
        self.api_key = api_key
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "medical-research-agent/0.1 (+https://pubmed.ncbi.nlm.nih.gov)"},
        )
        self._min_interval = 1.0 / (RATE_WITH_KEY if api_key else RATE_NO_KEY)
        self._last_call = 0.0

    # ------------------------------------------------------------------ api

    def search(self, query: str, *, retmax: int = 50, sort: str = "relevance",
               mindate: str = "", maxdate: str = "") -> list[str]:
        """Return PMIDs for a PubMed query string (full PubMed syntax supported)."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": str(retmax),
            "retmode": "json",
            "sort": sort,
        }
        if mindate:
            params["mindate"] = mindate
            params["datetype"] = "pdat"
        if maxdate:
            params["maxdate"] = maxdate
            params["datetype"] = "pdat"

        data = self._get("esearch.fcgi", params).json()
        result = data.get("esearchresult", {})
        if "ERROR" in result:
            raise PubMedError(f"PubMed rejected the query: {result['ERROR']}")
        return list(result.get("idlist", []))

    def fetch(self, pmids: Iterable[str]) -> list[Article]:
        """Fetch full records for PMIDs, batching to stay within request limits."""
        pmids = [p for p in pmids if p]
        articles: list[Article] = []
        for batch in _chunks(pmids, FETCH_BATCH):
            response = self._get(
                "efetch.fcgi",
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            )
            articles.extend(parse_efetch_xml(response.text))
        return articles

    def search_and_fetch(self, query: str, **kwargs) -> list[Article]:
        pmids = self.search(query, **kwargs)
        log.info("PubMed returned %d PMIDs for %r", len(pmids), query)
        if not pmids:
            return []
        return self.fetch(pmids)

    # -------------------------------------------------------------- internals

    def _get(self, endpoint: str, params: dict[str, str]) -> httpx.Response:
        if self.email:
            params = dict(params, email=self.email, tool="medical-research-agent")
        if self.api_key:
            params = dict(params, api_key=self.api_key)

        self._throttle()
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.get(f"{BASE}/{endpoint}", params=params)
                # 429 and 5xx from NCBI are routinely transient.
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}", request=response.request, response=response
                    )
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_exc = exc
                backoff = 2**attempt
                log.warning("PubMed %s failed (%s); retrying in %ds", endpoint, exc, backoff)
                time.sleep(backoff)
                self._last_call = time.monotonic()

        raise PubMedError(f"PubMed {endpoint} failed after 4 attempts: {last_exc}")

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


# ---------------------------------------------------------------- XML parsing


def parse_efetch_xml(xml_text: str) -> list[Article]:
    """Parse an efetch PubmedArticleSet into Article records."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise PubMedError(f"Could not parse PubMed XML: {exc}") from exc

    articles = []
    for node in root.iter("PubmedArticle"):
        article = _parse_article(node)
        if article:
            articles.append(article)
    return articles


def _parse_article(node: ET.Element) -> Article | None:
    pmid = _text(node.find("./MedlineCitation/PMID"))
    if not pmid:
        return None

    citation = node.find("./MedlineCitation")
    art = node.find("./MedlineCitation/Article")
    if art is None or citation is None:
        return Article(pmid=pmid)

    journal = art.find("./Journal")
    return Article(
        pmid=pmid,
        title=_flatten(art.find("./ArticleTitle")),
        abstract=_parse_abstract(art.find("./Abstract")),
        journal=_text(journal.find("./Title")) if journal is not None else "",
        journal_abbrev=_text(journal.find("./ISOAbbreviation")) if journal is not None else "",
        year=_parse_year(node),
        authors=_parse_authors(art.find("./AuthorList")),
        doi=_parse_doi(node),
        publication_types=[
            _text(pt) for pt in art.findall("./PublicationTypeList/PublicationType") if _text(pt)
        ],
        mesh_terms=[
            _text(mh)
            for mh in citation.findall("./MeshHeadingList/MeshHeading/DescriptorName")
            if _text(mh)
        ],
    )


def _parse_abstract(abstract: ET.Element | None) -> str:
    """Join abstract sections, preserving structured labels when present."""
    if abstract is None:
        return ""
    parts = []
    for chunk in abstract.findall("./AbstractText"):
        body = _flatten(chunk)
        if not body:
            continue
        label = chunk.get("Label") or chunk.get("NlmCategory")
        parts.append(f"{label.strip().title()}: {body}" if label else body)
    return "\n".join(parts)


def _parse_authors(author_list: ET.Element | None) -> list[str]:
    if author_list is None:
        return []
    names = []
    for author in author_list.findall("./Author"):
        last = _text(author.find("./LastName"))
        initials = _text(author.find("./Initials"))
        collective = _text(author.find("./CollectiveName"))
        if last:
            names.append(f"{last} {initials}".strip())
        elif collective:
            names.append(collective)
    return names


def _parse_year(node: ET.Element) -> str:
    for path in (
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year",
        "./MedlineCitation/Article/ArticleDate/Year",
        "./PubmedData/History/PubMedPubDate/Year",
    ):
        year = _text(node.find(path))
        if year:
            return year
    # Some records only carry a MedlineDate string such as "2019 Nov-Dec".
    medline = _text(node.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate"))
    return medline.split()[0] if medline else ""


def _parse_doi(node: ET.Element) -> str:
    for path in ("./PubmedData/ArticleIdList/ArticleId", "./MedlineCitation/Article/ELocationID"):
        for elem in node.findall(path):
            id_type = elem.get("IdType") or elem.get("EIdType")
            if id_type == "doi" and _text(elem):
                return _text(elem)
    return ""


def _text(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


def _flatten(elem: ET.Element | None) -> str:
    """Collapse mixed content (italics, sub/superscripts) into plain text."""
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
