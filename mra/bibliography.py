"""Reference export for EndNote, Zotero and NoteExpress.

The tool checks every citation against the library, but a manuscript's
bibliography is formatted in a reference manager — EndNote for most of the
people this is for, NoteExpress for many, Zotero for some. All three import RIS;
BibTeX is here for anyone writing in LaTeX. Export is offline and free.

Two things decide whether an export is usable rather than merely valid:

- **Volume, issue and pages.** Without them a formatted bibliography reads
  "Hepatology. 2019." Records stored before 0.9.1 lack them; `incomplete()`
  names those so the researcher can complete them instead of discovering the
  gaps in a proof.
- **Names.** PubMed stores "Smith JA"; a reference manager needs surname and
  initials apart, and needs a group author ("NASH Clinical Research Network")
  marked as one, or it files it as Mr Network.
"""

from __future__ import annotations

import re
import unicodedata

from .pubmed import Article

_INITIALS = re.compile(r"[A-Z]{1,4}")
_PAGES = re.compile(r"\s*([A-Za-z]?\d+)\s*[-–—]\s*([A-Za-z]?\d+)\s*")


def is_local(article: Article) -> bool:
    return article.pmid.startswith("local:")


def split_author(name: str) -> tuple[str, str, bool]:
    """(surname, initials, is_person) from "Smith JA", "van der Berg A" or a group."""
    name = name.strip()
    if "," in name:
        surname, given = name.split(",", 1)
        return surname.strip(), given.strip(), True
    parts = name.split()
    if len(parts) >= 2 and _INITIALS.fullmatch(parts[-1]):
        return " ".join(parts[:-1]), " ".join(f"{letter}." for letter in parts[-1]), True
    return name, "", False


def page_range(pages: str) -> tuple[str, str]:
    """("1234", "1245") from MEDLINE's "1234-45"; an article number stays whole."""
    pages = (pages or "").strip()
    match = _PAGES.fullmatch(pages)
    if not match:
        return pages, ""
    start, end = match.groups()
    if start.isdigit() and end.isdigit() and len(end) < len(start):
        end = start[: len(start) - len(end)] + end
    return start, end


def incomplete(articles: list[Article]) -> list[Article]:
    """Records a formatted bibliography would show without volume or pages."""
    return [a for a in articles if not (a.volume and a.pages)]


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


# ----------------------------------------------------------------------- RIS


def to_ris(articles: list[Article]) -> str:
    """RIS records, one per article. Written with "\\n"; Python turns that into
    the CRLF reference managers expect on Windows when the file is written."""
    records = []
    for article in articles:
        tags: list[tuple[str, str]] = [("TY", "JOUR")]
        for name in article.authors:
            surname, initials, person = split_author(name)
            if person:
                tags.append(("AU", f"{surname}, {initials}" if initials else surname))
            else:
                tags.append(("AU", f"{surname},"))  # trailing comma: a group author
        start, end = page_range(article.pages)
        journal = article.journal or article.journal_abbrev
        tags += [
            ("TI", _one_line(article.title)),
            ("T2", journal),
            ("JO", article.journal_abbrev if article.journal and article.journal_abbrev else ""),
            ("PY", article.year),
            ("VL", article.volume),
            ("IS", article.issue),
            ("SP", start),
            ("EP", end),
            ("DO", article.doi),
        ]
        if is_local(article):
            tags.append(("N1", f"Imported from a local file ({article.pmid}); "
                               "metadata read off its first page — check it."))
        else:
            tags += [
                ("AN", article.pmid),
                ("UR", f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/"),
                ("DB", "PubMed"),
                ("AB", _one_line(article.abstract)),
            ]
        tags += [("KW", term) for term in article.mesh_terms]
        lines = [f"{tag}  - {value}" for tag, value in tags if value]
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records) + "\n" if records else ""


# -------------------------------------------------------------------- BibTeX

_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%",
    "$": r"\$", "#": r"\#", "_": r"\_", "^": r"\^{}", "~": r"\~{}",
}
_SPECIAL = re.compile("|".join(re.escape(c) for c in _ESCAPES))


def _escape(text: str) -> str:
    return _SPECIAL.sub(lambda m: _ESCAPES[m.group(0)], _one_line(text))


def _bib_name(name: str) -> str:
    surname, initials, person = split_author(name)
    if not person:
        return "{" + _escape(surname) + "}"  # braces: never split a group name
    return f"{_escape(surname)}, {_escape(initials)}" if initials else _escape(surname)


def _ascii(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", folded)


def cite_key(article: Article, taken: set[str]) -> str:
    """Surname and year, as reference managers do: Smith2019, then Smith2019a."""
    surname = split_author(article.authors[0])[0] if article.authors else "Anon"
    base = (_ascii(surname) or "Anon") + (_ascii(article.year) or "nd")
    key, suffix = base, 0
    while key in taken:
        key = base + "abcdefghijklmnopqrstuvwxyz"[suffix % 26] * (suffix // 26 + 1)
        suffix += 1
    taken.add(key)
    return key


def to_bibtex(articles: list[Article]) -> str:
    taken: set[str] = set()
    entries = []
    for article in articles:
        start, end = page_range(article.pages)
        fields = [
            ("author", " and ".join(_bib_name(n) for n in article.authors)),
            ("title", "{" + _escape(article.title) + "}"),  # keep its capitals
            ("journal", _escape(article.journal or article.journal_abbrev)),
            ("year", _escape(article.year)),
            ("volume", _escape(article.volume)),
            ("number", _escape(article.issue)),
            ("pages", f"{start}--{end}" if end else _escape(start)),
            ("doi", article.doi),
        ]
        if is_local(article):
            fields.append(("note", _escape(f"Local file {article.pmid}; check the metadata")))
        else:
            fields += [("pmid", article.pmid),
                       ("url", f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/")]
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
        entries.append(f"@article{{{cite_key(article, taken)},\n{body}\n}}")
    return "\n\n".join(entries) + "\n" if entries else ""


def export(articles: list[Article], suffix: str) -> str:
    """By file suffix: .bib → BibTeX, anything else → RIS."""
    return to_bibtex(articles) if suffix.lower() == ".bib" else to_ris(articles)


FORMATS = {".ris", ".bib"}

IMPORT_HINT = (
    "导入方法 —— EndNote：文件 → 导入 → 文件，导入选项选「Reference Manager (RIS)」，"
    "文本转换选「Unicode (UTF-8)」；Zotero：文件 → 导入；NoteExpress：导入题录，过滤器选 RIS。"
)
