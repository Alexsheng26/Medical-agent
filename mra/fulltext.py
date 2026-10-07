"""How a long paper's reading budget is spent.

A full-text PDF runs to 40-60k characters, and one model call reads 24k of it
when a paper is digested — less when several are compared at once. The budget
used to be spent by position: the first 55% and the last 45%. Measured on real
journal PDFs that was the wrong trade twice over:

- **Tables sat in the discarded middle.** In an Elsevier paper four of its five
  tables were never shown to the model; in a Springer paper one of two. Tables
  are where a paper's numbers are, and the numbers are what a judgement of
  whether a study holds up has to rest on.
- **The kept ending was mostly the reference list** — 45-80% of it in all four
  papers measured. A list of other people's papers tells the model nothing
  about this one.

So the budget is now spent by value. When a paper does not fit, the reference
list goes first; then the middle goes, except for tables found in it, which are
kept up to a share of the budget.

The text was never the problem: pypdf already extracts journal tables with one
row per line, labels and numbers intact. What it cannot do reliably is order
the cells of a header that wraps over several lines, and the model is told so.
A table-detection library was tried and rejected: on the same papers it
reported plot gridlines as tables and missed the three-line tables (三线表)
most journals print.

Known gap: a table whose cells are sentences (a list of operations and what
each does) reads exactly like body text and is not recognised. Medical tables
are mostly numbers, symbols and short labels, which are.

Nothing here touches what is stored. The library keeps the full text; this only
decides what one model call reads.
"""

from __future__ import annotations

import re

HEAD_SHARE = 0.55
# Tables may take up to this much of the budget, taken from the head and tail
# in proportion. More, and the abstract and discussion start to suffer.
TABLE_SHARE = 0.35

MAX_TABLE_LINES = 80
MAX_FOOTNOTE_LINES = 6

# A reference list shorter than this is a heading somewhere else, not a list.
MIN_REFERENCE_LIST = 300

_REFERENCES = re.compile(
    r"(?m)^[ \t]*(?:\d+\.?[ \t]*)?(?:References|REFERENCES|Reference List|Literature Cited|"
    r"LITERATURE CITED|Bibliography|BIBLIOGRAPHY|Works Cited|参考文献)[ \t]*[:：]?[ \t]*$"
)
# Author manuscripts put figure legends and tables *after* the references.
# Those are worth keeping; only the list itself goes.
_AFTER_REFERENCES = re.compile(
    r"(?m)^[ \t]*(?:Table\s+S?\d+|TABLE\s+S?\d+|表\s*\d+|Figure [Ll]egends?|FIGURE LEGENDS?|"
    r"Appendix|APPENDIX|Supplementary|SUPPLEMENTARY)"
)
_CAPTION = re.compile(r"^\s*(?P<word>Table|TABLE|表)\s*(?P<num>S?\d+|[IVX]+)(?P<rest>.*)$")
_FIGURE = re.compile(r"^\s*(?:Fig\.?|Figure|FIGURE|图)\s*\d")
# A section heading after a table ends it, even in a footnote run of short lines:
# "V. CONCLUSION", "4. Results", "3.4 Storage and visualization", "Discussion".
_HEADING = re.compile(
    r"^\s*(?:(?:[IVX]+|\d+(?:\.\d+)*)\.?\s+[A-Z][A-Za-z ]{2,40}$|[A-Z][A-Z ]{4,30}$|"
    r"(?:Abstract|Introduction|Methods?|Materials and [Mm]ethods|Results|Discussion|"
    r"Conclusions?|Acknowledge?ments?|讨论|结果|结论|方法)\s*$)"
)
_DIGIT = re.compile(r"\d")
_CJK = re.compile(r"[㐀-鿿]")
_PLACEHOLDERS = {"na", "ns", "nr", "nd", "n/a", "—", "–", "-", "NA", "NS", "NR", "ND", "N/A"}
# Lowercase words, including the ﬁ/ﬂ ligatures pypdf emits ("signiﬁcance").
_LOWER = "a-z\u00df-\u00ff\ufb00-\ufb06"
_WORD = re.compile(rf"^[{_LOWER}][{_LOWER}'’-]+[,.;:)]*$")
_FOOTNOTE_WORDS = re.compile(
    r"\b(?:indicates?|denotes?|represents?|refers? to|expressed as|shown as|"
    r"mean\s*±|abbreviations?)\b", re.IGNORECASE
)
MAX_TITLE_LINES = 3
_FOOTNOTE_START = re.compile(
    r"^\s*(?:[*†‡§¶#]|[a-f]\s|\d\s+[A-Z]|ns\b|na\b|NS\b|NA\b|Notes?\b|Abbreviations?\b|Data are\b|"
    r"Values are\b|Results are\b|For\s|[Pp]\s*[<=≤]|注[:：])"
)
_ABBREVIATION = re.compile(r"\b[A-Z][A-Za-z0-9]{0,6}[,:=]\s")


# --------------------------------------------------------------- the pieces


def without_references(text: str) -> tuple[str, int]:
    """The text with its reference list removed, and how much was removed.

    Takes the last references heading in the latter part of the paper — an
    early one is a table of contents. Anything after the list that is a table
    or figure legend is kept.
    """
    headings = [m for m in _REFERENCES.finditer(text) if m.start() >= len(text) * 0.4]
    if not headings:
        return text, 0
    start = headings[-1].start()
    after = _AFTER_REFERENCES.search(text, headings[-1].end())
    end = after.start() if after else len(text)
    if end - start < MIN_REFERENCE_LIST:
        return text, 0
    rest = text[end:].strip()
    return text[:start].rstrip() + (f"\n\n{rest}" if rest else ""), end - start


def is_caption(line: str) -> bool:
    """A table's own caption, as opposed to prose that mentions one.

    A wrapped sentence often starts a line with "Table 3 for all deciles" or
    "Table 2 gives". A caption is the label alone, or the label followed by
    punctuation or a capitalised title: "Table 3", "Table 1: Current layout
    models", "TABLE 2 Baseline characteristics".
    """
    match = _CAPTION.match(line)
    if not match:
        return False
    rest = match.group("rest")
    if match.group("word") == "表":
        # "表1 两组患者基线资料" is a caption; "表1显示…" is a sentence.
        return not rest or not _CJK.match(rest[:1])
    if rest[:1].isalnum():  # "Table Info" is not Table I; "Table 1a" is not handled
        return False
    rest = rest.strip()
    if not rest:
        return True
    if rest[0] in ".:|—–-":
        return True
    if match.group("word") == "TABLE":
        return True
    return rest[0].isupper()


def numeric_share(line: str) -> float:
    """How much of a line is numbers — high for a table row, low for prose."""
    tokens = line.split()
    if not tokens:
        return 0.0
    numeric = sum(1 for t in tokens if _DIGIT.search(t) or t in _PLACEHOLDERS)
    return numeric / len(tokens)


def word_share(line: str) -> float:
    """How much of a line is ordinary lowercase words — high for prose.

    The test that separates a sentence from a table row. Numbers alone do not:
    a results sentence is full of them, and a table of significance marks
    ("P,TP", "*", "ns") has none.
    """
    tokens = line.split()
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if _WORD.match(t)) / len(tokens)


def _is_prose(line: str) -> bool:
    if len(_CJK.findall(line)) > 25:
        return True
    if len(line) <= 40:
        return False
    # A row's last cell is usually a value; "Strength of grouping by color
    # similarity 0.03" is a parameter table, not a sentence.
    if _DIGIT.search(line.split()[-1]):
        return word_share(line) >= 0.7
    return word_share(line) >= 0.45


def _is_footnote(line: str) -> bool:
    return (
        bool(_FOOTNOTE_START.match(line))
        or len(_ABBREVIATION.findall(line)) >= 2
        or bool(_FOOTNOTE_WORDS.search(line))
    )


def table_blocks(text: str) -> list[tuple[int, int]]:
    """Character spans of the tables in extracted text: caption, rows, notes.

    Starts at a caption and takes lines while they look like a table — short,
    or mostly numbers — then up to a few footnotes, and stops at the first
    line of prose. The first line after the caption is always taken, because
    a table's title is often a long line with no numbers in it.
    """
    lines = text.splitlines(keepends=True)
    starts, position = [], 0
    for line in lines:
        starts.append(position)
        position += len(line)

    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not is_caption(lines[index]):
            index += 1
            continue

        first = index
        last = index
        rows = short = footnotes = 0
        cursor = index + 1
        while cursor < len(lines) and cursor - first < MAX_TABLE_LINES:
            line = lines[cursor].strip()
            if not line:
                cursor += 1
                continue
            if is_caption(line) or _FIGURE.match(line):
                break
            tabular = rows or short
            if tabular and _HEADING.match(line):
                break
            if not tabular and cursor - first <= MAX_TITLE_LINES:
                # The title, which wraps and reads like prose.
                if not _is_prose(line):
                    short += 1
                last = cursor
            elif footnotes:
                continued = _is_footnote(line) or len(line) <= 45
                if not continued or footnotes >= MAX_FOOTNOTE_LINES:
                    break
                footnotes += 1
                last = cursor
            elif _is_prose(line):
                if tabular and _is_footnote(line):
                    footnotes = 1
                    last = cursor
                else:
                    break
            else:
                if numeric_share(line) >= 0.3:
                    rows += 1
                else:
                    short += 1
                last = cursor
            cursor += 1

        # A caption with nothing tabular after it is a label whose table the
        # extractor put somewhere else; keeping it alone adds nothing.
        if rows or short >= 3:
            spans.append((starts[first], starts[last] + len(lines[last])))
        index = max(last + 1, index + 1)
    return spans


# ------------------------------------------------------------------- fitting


TABLE_NOTE = (
    "Tables from the omitted part, as extracted from the PDF text. Rows are "
    "reliable; column headers that wrap over several lines are often out of order."
)


def fit(text: str, limit: int) -> str:
    """At most about `limit` characters of a paper, chosen by value.

    Untouched when it fits. Otherwise the reference list goes first, then the
    middle — keeping any tables found there, up to TABLE_SHARE of the budget.
    """
    if len(text) <= limit:
        return text

    body, reference_chars = without_references(text)
    references_note = (
        f"; the reference list ({reference_chars} characters) was left out" if reference_chars else ""
    )
    if len(body) <= limit:
        return f"{body}\n\n[... the reference list ({reference_chars} characters) was left out ...]"

    blocks = table_blocks(body)
    head, tail, middle = _plan(body, limit, blocks, reserve=0)
    wanted = sum(end - start for start, end in middle)
    reserve = min(wanted, int(limit * TABLE_SHARE))
    if reserve:
        head, tail, middle = _plan(body, limit, blocks, reserve)

    kept: list[str] = []
    used = 0
    for start, end in middle:
        piece = body[start:end].strip()
        if used + len(piece) > reserve:
            room = reserve - used
            if room > 400:
                kept.append(piece[:room].rsplit("\n", 1)[0] + "\n(table cut short here)")
            break
        kept.append(piece)
        used += len(piece) + 2

    head_end = _line_boundary(body, head, backwards=True)
    tail_start = _line_boundary(body, len(body) - tail, backwards=False)
    omitted = tail_start - head_end
    tables = f"{TABLE_NOTE}\n\n" + "\n\n".join(kept) + "\n\n" if kept else ""
    return (
        f"{body[:head_end].rstrip()}\n\n"
        f"[... {omitted} characters omitted from the middle of the paper{references_note} ...]\n\n"
        f"{tables}{body[tail_start:].lstrip()}"
    )


def _plan(body: str, limit: int, blocks, reserve: int):
    head = int((limit - reserve) * HEAD_SHARE)
    tail = limit - reserve - head
    low, high = head, len(body) - tail
    middle = [(s, e) for s, e in blocks if e > low and s < high]
    return head, tail, middle


def _line_boundary(text: str, position: int, *, backwards: bool) -> int:
    """Move a cut to the nearest line break, if one is close; never past budget."""
    window = 300
    if backwards:
        found = text.rfind("\n", max(0, position - window), position)
        return found if found > 0 else position
    found = text.find("\n", position, position + window)
    return found + 1 if found >= 0 else position
