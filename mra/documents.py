"""Read the files researchers actually have: Word, PowerPoint, PDF, plain text.

Every command that takes a manuscript, a set of reviews or a student's proposal
used to say "save it as .txt first". In a Chinese lab everything arrives as a
.docx — the draft, the decision letter pasted out of the journal's email, the
student's 开题报告 — and a step that has to be done by hand every single time
is the step that stops a tool from being used.

Both formats are ZIP archives of XML, so they are read with the standard
library. What matters is what comes out, and four things in a Word file decide
whether the text a model reads is the text the researcher wrote:

- **Automatic numbering.** "1." in a numbered list is not in the text — Word
  draws it from numbering.xml. Lose it, and "Reviewer 2, comment 3" has no 3,
  and a proposal's 研究内容（一）（二）（三） have no labels to refer to.
- **Tracked changes.** A revision in progress holds deleted and inserted text
  side by side. It is read as if every change were accepted, and the reader
  is told so.
- **The Symbol font.** Older documents type μ as "m" in Symbol, or insert it as
  a symbol character. Read naively, 10 μg becomes 10 mg — a thousandfold error
  in a dose, in a file whose purpose is to be checked for errors.
- **What cannot be read.** Pictures and comments are reported, not dropped in
  silence: a model that is not told a 技术路线图 was there will criticise the
  proposal for not having one.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

WORD_SUFFIXES = {".docx", ".docm", ".dotx"}
SLIDE_SUFFIXES = {".pptx", ".pptm"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}

#: Placed where a picture was, so whoever reads the text knows something is
#: missing there rather than concluding nothing was.
PICTURE = "[图片，内容未读取]"

_EXCEL = "Excel 文件读不了。在 Excel 里 文件 → 另存为 → 选「CSV UTF-8（逗号分隔）」，再选那个 .csv。"
_STATS = "统计软件的文件读不了。在软件里导出为 CSV（编码选 UTF-8）。"
_OLD_WORD = ("这是老版 Word 格式（.doc）。在 Word 里 文件 → 另存为 → 文件类型选"
             "「Word 文档 (*.docx)」，再选新存的那个文件。")
_OTHER_WORD = "在 Word 或 WPS 里 文件 → 另存为 → 选「Word 文档 (*.docx)」，再选新存的那个文件。"
_OLD_SLIDES = ("这是老版 PowerPoint 格式。在 PowerPoint 或 WPS 里 文件 → 另存为 → 选"
               "「PowerPoint 演示文稿 (*.pptx)」，再选新存的那个文件。")
_PICTURE = "这是图片。现在只能读文字，读不了图片里的内容。"

#: Formats that cannot be read, each with the one step that makes them readable.
REFUSED = {
    ".doc": _OLD_WORD,
    ".wps": "这是 WPS 自己的格式（.wps）。" + _OTHER_WORD,
    ".rtf": "这是 RTF 格式。" + _OTHER_WORD,
    ".odt": "这是 OpenDocument 格式。" + _OTHER_WORD,
    ".pages": "这是 Pages 格式。在 Pages 里 文件 → 导出为 → Word，再选导出的 .docx。",
    ".ppt": _OLD_SLIDES, ".dps": _OLD_SLIDES,
    ".xlsx": _EXCEL, ".xls": _EXCEL, ".xlsm": _EXCEL, ".et": _EXCEL,
    ".sav": _STATS, ".dta": _STATS, ".sas7bdat": _STATS, ".rds": _STATS, ".rdata": _STATS,
    ".zip": "压缩包读不了，先解压，再选里面的文件。",
    ".rar": "压缩包读不了，先解压，再选里面的文件。",
    ".7z": "压缩包读不了，先解压，再选里面的文件。",
    ".png": _PICTURE, ".jpg": _PICTURE, ".jpeg": _PICTURE, ".tif": _PICTURE,
    ".tiff": _PICTURE, ".bmp": _PICTURE, ".gif": _PICTURE,
}


class DocumentError(ValueError):
    """A file that cannot be read, with what to do about it."""


@dataclass
class Document:
    text: str
    #: What the reader should know was not read, or was read in a particular
    #: way — shown to the researcher, never mixed into the text.
    notes: list[str] = field(default_factory=list)


def read(path: Path) -> Document:
    """The text of a document the researcher wrote or was sent."""
    if not path.exists():
        raise FileNotFoundError(f"找不到这个文件：{path}")
    suffix = path.suffix.lower()
    if suffix in REFUSED:
        raise DocumentError(f"{path.name}：{REFUSED[suffix]}")
    if suffix in WORD_SUFFIXES:
        return read_docx(path)
    if suffix in SLIDE_SUFFIXES:
        return read_pptx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    return Document(decode_utf8(path))


def decode_utf8(path: Path) -> str:
    """Plain text that has to be exactly what was written.

    No fallback to GB18030 here, unlike for data tables: a manuscript is about
    to be rewritten and submitted, and an English file saved as Windows-1252
    also "decodes" as GB18030 — every curly quote silently becomes a Chinese
    character and swallows the letter after it. An instruction is better.
    """
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise DocumentError(
            f"{path.name} 不是文本文件，像是某个软件自己的格式。"
            "Word 文件请存成 .docx 再选；表格请存成 CSV（编码选 UTF-8）。"
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError(
            f"{path} is not UTF-8 ({exc.reason} at byte {exc.start}).\n"
            "Re-save it as UTF-8 — in Notepad, 文件 → 另存为 → 编码选 UTF-8; "
            "in VS Code, click the encoding in the status bar → Save with Encoding. "
            "Word 文件不用转，直接选 .docx 就行。"
        ) from exc


def _read_pdf(path: Path) -> Document:
    from . import ingest  # ingest reads Word files through this module

    extracted = ingest.extract(path)
    if not extracted.usable:
        problem = "；".join(extracted.warnings) or "读不出文字"
        raise DocumentError(
            f"{path.name}：{problem}。如果是扫描件，要先做文字识别（OCR）；"
            "手上有 Word 原件的话，直接选 .docx 更好。"
        )
    return Document(extracted.text, [f"{path.name}：{w}" for w in extracted.warnings])


# --------------------------------------------------------------------- the ZIP


_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _open(path: Path, kind: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentError(
            f"{path.name} 打不开。三种可能：设了打开密码（先在{kind}里去掉密码）；"
            f"其实是老版格式、只是改了扩展名（在{kind}里重新另存一次）；或者文件损坏了。"
        ) from exc


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """{id: (type suffix, part name)} for one part's .rels file."""
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, "_rels", f"{name}.rels")
    try:
        root = ET.fromstring(archive.read(rels))
    except (KeyError, ET.ParseError):
        return {}
    found = {}
    for rel in root.iter(_REL):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(folder, target))
        found[rel.get("Id", "")] = (rel.get("Type", "").rsplit("/", 1)[-1], resolved)
    return found


def _main_part(archive: zipfile.ZipFile, default: str) -> str:
    for kind, target in _relationships(archive, "").values():
        if kind == "officeDocument":
            return target
    return default


def _xml(archive: zipfile.ZipFile, part: str | None) -> ET.Element | None:
    if not part:
        return None
    try:
        return ET.fromstring(archive.read(part))
    except (KeyError, ET.ParseError):
        return None


def _namespace(root: ET.Element) -> str:
    return root.tag.split("}", 1)[0] + "}" if root.tag.startswith("{") else ""


# ------------------------------------------------------------------------ Word


def read_docx(path: Path) -> Document:
    with _open(path, "Word") as archive:
        main = _main_part(archive, "word/document.xml")
        root = _xml(archive, main)
        if root is None:
            raise DocumentError(f"{path.name} 不像是 Word 文档（里面没有正文）。在 Word 里另存一次 .docx 再试。")
        parts = {kind: target for kind, target in _relationships(archive, main).values()}
        w = _namespace(root)
        reader = _WordReader(
            w,
            numbering=_xml(archive, parts.get("numbering")),
            styles=_xml(archive, parts.get("styles")),
        )
        body = root.find(f"{w}body")
        blocks = reader.blocks(body) if body is not None else []
        comments = _xml(archive, parts.get("comments"))
        footnotes = sum(
            _count_notes(_xml(archive, parts.get(kind)), w) for kind in ("footnotes", "endnotes")
        )

    text = "\n\n".join(block for block in blocks if block.strip())
    notes = []
    if reader.revisions:
        notes.append(f"{path.name} 里有没接受的修订，按「全部接受」之后的样子读的（删掉的字不算）。")
    if comments is not None and (n := len(comments.findall(f"{w}comment"))):
        notes.append(f"{path.name} 里有 {n} 条批注，没有读进来，只读了正文。")
    if footnotes:
        notes.append(f"{path.name} 里有 {footnotes} 条脚注或尾注，没有读进来。")
    if reader.pictures:
        notes.append(f"{path.name} 里有 {reader.pictures} 张图片，读不了图，只读了文字"
                     f"（图片的位置标成了「{PICTURE}」）。")
    if not text.strip():
        raise DocumentError(f"{path.name} 里没有读到文字。如果内容都在图片里，读不了。")
    return Document(text, notes)


def _count_notes(root: ET.Element | None, w: str) -> int:
    if root is None:
        return 0
    # Every notes part carries two built-in separator entries; they are not notes.
    return sum(1 for note in root if note.get(f"{w}type") in (None, "normal"))


class _WordReader:
    def __init__(self, w: str, *, numbering: ET.Element | None, styles: ET.Element | None):
        self.w = w
        self.numbering = _Numbering(w, numbering)
        self.styles = _Styles(w, styles)
        self.revisions = False
        self.pictures = 0

    def blocks(self, container: ET.Element) -> list[str]:
        w = self.w
        out: list[str] = []
        for node in container:
            if node.tag == f"{w}p":
                out.extend(self.paragraph(node))
            elif node.tag == f"{w}tbl":
                out.append(self.table(node))
            elif node.tag in (f"{w}sdt", f"{w}customXml", f"{w}ins", f"{w}moveTo"):
                if node.tag in (f"{w}ins", f"{w}moveTo"):
                    self.revisions = True
                inner = node.find(f"{w}sdtContent") if node.tag == f"{w}sdt" else node
                if inner is not None:
                    out.extend(self.blocks(inner))
            elif node.tag in (f"{w}del", f"{w}moveFrom"):
                self.revisions = True
        return out

    def table(self, table: ET.Element) -> str:
        w = self.w
        rows = []
        for row in table.findall(f"{w}tr"):  # direct rows only: a nested table is cell text
            cells = [" ".join(" ".join(self.blocks(cell)).split()) for cell in row.findall(f"{w}tc")]
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    def paragraph(self, p: ET.Element) -> list[str]:
        """The paragraph's line, then any text boxes anchored in it."""
        w = self.w
        parts: list[str] = []
        boxes: list[str] = []
        self._inline(p, parts, boxes, symbol=False)
        text = "".join(parts).strip()

        props = p.find(f"{w}pPr")
        style = ""
        direct = None
        if props is not None:
            style_node = props.find(f"{w}pStyle")
            style = style_node.get(f"{w}val", "") if style_node is not None else ""
            direct = props.find(f"{w}numPr")

        # Numbered before the emptiness check: Word counts an empty list item.
        label = self.numbering.label(direct, self.styles.numbering(style), style)
        if not text:
            return boxes
        if label:
            text = f"{label} {text}"
        if heading := self.styles.heading_level(style):
            text = f"{'#' * heading} {text}"
        return [text] + boxes

    def _inline(self, node: ET.Element, parts: list[str], boxes: list[str], *, symbol: bool) -> None:
        w = self.w
        for child in node:
            tag = child.tag
            if tag in (f"{w}pPr", f"{w}rPr"):
                continue
            if tag in (f"{w}del", f"{w}moveFrom"):
                self.revisions = True
                continue
            if tag in (f"{w}ins", f"{w}moveTo"):
                self.revisions = True
            if tag == f"{_MC}Fallback":
                continue  # the same content again, for older readers
            if tag == f"{w}r":
                self._inline(child, parts, boxes, symbol=self._is_symbol_run(child))
            elif tag in (f"{w}t", f"{_MATH}t"):
                text = child.text or ""
                parts.append(_from_symbol_font(text) if symbol else text)
            elif tag == f"{w}tab":
                parts.append("\t")
            elif tag in (f"{w}br", f"{w}cr"):
                parts.append("\n")
            elif tag == f"{w}noBreakHyphen":
                parts.append("-")
            elif tag == f"{w}sym":
                parts.append(_symbol_character(child.get(f"{w}font", ""), child.get(f"{w}char", "")))
            elif tag == f"{w}txbxContent":
                boxes.extend(self.blocks(child))
            elif tag in (f"{w}drawing", f"{w}pict", f"{w}object"):
                before = len(boxes)
                self._inline(child, parts, boxes, symbol=symbol)
                if len(boxes) == before:  # a text box is text, not a picture
                    self.pictures += 1
                    parts.append(PICTURE)
            else:
                self._inline(child, parts, boxes, symbol=symbol)

    def _is_symbol_run(self, run: ET.Element) -> bool:
        w = self.w
        fonts = run.find(f"{w}rPr/{w}rFonts")
        if fonts is None:
            return False
        return any(fonts.get(f"{w}{attr}", "") == "Symbol" for attr in ("ascii", "hAnsi"))


class _Styles:
    """Paragraph styles: which are headings, and which carry numbering."""

    def __init__(self, w: str, root: ET.Element | None):
        self.w = w
        self.based_on: dict[str, str] = {}
        self.num: dict[str, tuple[str, str | None]] = {}
        self.names: dict[str, str] = {}
        if root is None:
            return
        for style in root.findall(f"{w}style"):
            if style.get(f"{w}type") not in (None, "paragraph"):
                continue
            sid = style.get(f"{w}styleId", "")
            name = style.find(f"{w}name")
            self.names[sid] = (name.get(f"{w}val", "") if name is not None else "").lower()
            based = style.find(f"{w}basedOn")
            if based is not None:
                self.based_on[sid] = based.get(f"{w}val", "")
            numpr = style.find(f"{w}pPr/{w}numPr")
            if numpr is not None:
                num_id = numpr.find(f"{w}numId")
                level = numpr.find(f"{w}ilvl")
                self.num[sid] = (
                    num_id.get(f"{w}val", "") if num_id is not None else "",
                    level.get(f"{w}val") if level is not None else None,
                )

    def _chain(self, style: str):
        seen = set()
        while style and style not in seen:
            seen.add(style)
            yield style
            style = self.based_on.get(style, "")

    def numbering(self, style: str) -> tuple[str, str | None] | None:
        for sid in self._chain(style):
            if sid in self.num:
                return self.num[sid]
        return None

    def heading_level(self, style: str) -> int:
        """Built-in names are English in the XML whatever the interface
        language — 标题 1 is stored as "heading 1"."""
        for sid in self._chain(style):
            name = self.names.get(sid, "")
            if match := re.fullmatch(r"heading (\d)", name):
                return min(int(match.group(1)), 6)
            if name == "title":
                return 1
        return 0


@dataclass
class _Level:
    fmt: str = "decimal"
    text: str = ""
    start: int = 1
    style: str = ""


class _Numbering:
    """Turns numPr references into the labels Word would draw.

    Counters are kept per abstract definition: separate lists that share one
    definition continue each other's numbering in Word unless a list restarts
    with a start override, and that is what is reproduced here.
    """

    def __init__(self, w: str, root: ET.Element | None):
        self.w = w
        self.abstract: dict[str, dict[int, _Level]] = {}
        self.nums: dict[str, tuple[str, dict[int, int]]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.restarted: set[tuple[str, int]] = set()
        if root is None:
            return
        for abstract in root.findall(f"{w}abstractNum"):
            levels = {}
            for level in abstract.findall(f"{w}lvl"):
                levels[_int(level.get(f"{w}ilvl"), 0)] = _Level(
                    fmt=_val(level, f"{w}numFmt", w) or "decimal",
                    text=_val(level, f"{w}lvlText", w) or "",
                    start=_int(_val(level, f"{w}start", w), 1),
                    style=_val(level, f"{w}pStyle", w) or "",
                )
            self.abstract[abstract.get(f"{w}abstractNumId", "")] = levels
        for num in root.findall(f"{w}num"):
            overrides = {}
            for override in num.findall(f"{w}lvlOverride"):
                start = override.find(f"{w}startOverride")
                if start is not None:
                    overrides[_int(override.get(f"{w}ilvl"), 0)] = _int(start.get(f"{w}val"), 1)
            self.nums[num.get(f"{w}numId", "")] = (_val(num, f"{w}abstractNumId", w) or "", overrides)

    def label(self, direct: ET.Element | None, from_style, style: str) -> str:
        w = self.w
        num_id, level = None, None
        if from_style:
            num_id, level = from_style
        if direct is not None:
            if (node := direct.find(f"{w}numId")) is not None:
                num_id = node.get(f"{w}val")
            if (node := direct.find(f"{w}ilvl")) is not None:
                level = node.get(f"{w}val")
        if not num_id or num_id == "0" or num_id not in self.nums:
            return ""

        abstract_id, overrides = self.nums[num_id]
        levels = self.abstract.get(abstract_id, {})
        if level is None:
            # A style-linked list names its level through the style, not the paragraph.
            level = next((str(i) for i, lv in levels.items() if lv.style and lv.style == style), "0")
        ilvl = _int(level, 0)

        counters = self.counters.setdefault(abstract_id, {})
        if ilvl in overrides and (num_id, ilvl) not in self.restarted:
            self.restarted.add((num_id, ilvl))
            counters[ilvl] = overrides[ilvl] - 1
        current = levels.get(ilvl, _Level(text=f"%{ilvl + 1}."))
        counters[ilvl] = counters.get(ilvl, current.start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]

        if current.fmt == "bullet":
            return _bullet(current.text)
        if current.fmt == "none":
            return ""

        def fill(match: re.Match) -> str:
            k = int(match.group(1)) - 1
            lv = levels.get(k, _Level())
            return _format_number(counters.get(k, lv.start), lv.fmt)

        return re.sub(r"%(\d)", fill, current.text or f"%{ilvl + 1}.").strip()


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _val(node: ET.Element, path: str, w: str) -> str | None:
    found = node.find(path)
    return found.get(f"{w}val") if found is not None else None


def _bullet(glyph: str) -> str:
    # Bullets are usually drawn from Symbol or Wingdings, stored as private-use
    # code points that mean nothing outside that font.
    if not glyph or any("" <= ch <= "" for ch in glyph):
        return "•"
    return glyph


_CHINESE_DIGITS = "〇一二三四五六七八九"
_HEAVENLY_STEMS = "甲乙丙丁戊己庚辛壬癸"
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _format_number(n: int, fmt: str) -> str:
    if fmt in ("upperRoman", "lowerRoman"):
        roman = _roman(n)
        return roman if fmt == "upperRoman" else roman.lower()
    if fmt in ("upperLetter", "lowerLetter"):
        # Word repeats the letter past Z: Y, Z, AA, BB.
        letter = chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else str(n)
        return letter if fmt == "upperLetter" else letter.lower()
    if fmt in ("chineseCounting", "chineseCountingThousand", "chineseLegalSimplified",
               "japaneseCounting", "taiwaneseCounting", "ideographDigital"):
        return _chinese(n)
    if fmt == "ideographTraditional" and 1 <= n <= 10:
        return _HEAVENLY_STEMS[n - 1]
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese") and 1 <= n <= 20:
        return _CIRCLED[n - 1]
    if fmt == "decimalEnclosedParen":
        return f"({n})"
    if fmt == "decimalEnclosedFullstop":
        return f"{n}."
    if fmt in ("decimalFullWidth", "decimalFullWidth2"):
        return str(n).translate({ord(d): ord(d) + 0xFEE0 for d in "0123456789"})
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


def _roman(n: int) -> str:
    if n <= 0:
        return str(n)
    out = ""
    for value, numeral in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
                           (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
                           (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += numeral
            n -= value
    return out


def _chinese(n: int) -> str:
    if not 0 < n < 100:
        return str(n)
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _CHINESE_DIGITS[ones]
    head = "" if tens == 1 else _CHINESE_DIGITS[tens]
    return f"{head}十{_CHINESE_DIGITS[ones] if ones else ''}"


# Adobe Symbol encoding, for the characters a biomedical text actually uses.
# Unmapped characters in that font (digits, brackets, most punctuation) are the
# same as ASCII.
_SYMBOL_FONT = dict(zip(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖαβχδεφγηιϕκλμνοπθρστυϖωξψζ",
))
_SYMBOL_FONT.update({
    "\x22": "∀", "\x24": "∃", "\x2d": "−", "\x40": "≅", "\x7e": "∼",
    "\xa2": "′", "\xa3": "≤", "\xa5": "∞", "\xab": "↔", "\xac": "←", "\xad": "↑",
    "\xae": "→", "\xaf": "↓", "\xb0": "°", "\xb1": "±", "\xb2": "″", "\xb3": "≥",
    "\xb4": "×", "\xb5": "∝", "\xb6": "∂", "\xb7": "•", "\xb8": "÷", "\xb9": "≠",
    "\xba": "≡", "\xbb": "≈", "\xbc": "…", "\xc6": "∅", "\xc7": "∩", "\xc8": "∪",
    "\xce": "∈", "\xcf": "∉", "\xd1": "∇", "\xd6": "√", "\xd7": "⋅", "\xd8": "¬",
    "\xdb": "⇔", "\xde": "⇒", "\xe5": "∑", "\xf2": "∫",
})
_WINGDINGS = {0xFC: "✓", 0xFB: "✗", 0xFE: "☑", 0xFD: "☒", 0xA8: "☐", 0x9F: "•", 0x6C: "●"}


def _from_symbol_font(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0xF000 <= code <= 0xF0FF:
            code -= 0xF000
        out.append(_SYMBOL_FONT.get(chr(code), chr(code)))
    return "".join(out)


def _symbol_character(font: str, char: str) -> str:
    try:
        code = int(char, 16)
    except ValueError:
        return ""
    if 0xF000 <= code <= 0xF0FF:
        code -= 0xF000
    if font.lower() == "symbol":
        return _SYMBOL_FONT.get(chr(code), chr(code) if 0x20 <= code < 0x7F else "")
    if font.lower().startswith("wingdings"):
        return _WINGDINGS.get(code, "")
    return chr(code) if 0x20 <= code < 0x7F else ""


# ------------------------------------------------------------------ PowerPoint


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_pptx(path: Path) -> Document:
    """Each slide's text in presentation order, then its speaker notes.

    A student's 开题汇报 is usually a deck, and the argument lives as much in
    what they say over a slide as on it — so notes are read too.
    """
    with _open(path, "PowerPoint") as archive:
        main = _main_part(archive, "ppt/presentation.xml")
        presentation = _xml(archive, main)
        rels = _relationships(archive, main)
        order = []
        if presentation is not None:
            for slide in presentation.iter(f"{_P}sldId"):
                target = rels.get(slide.get(f"{_R}id", ""), ("", ""))[1]
                if target:
                    order.append(target)
        if not order:  # no presentation part we can read: fall back to file names
            order = sorted(
                (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
            )

        slides = []
        pictures = 0
        has_text = False
        for number, part in enumerate(order, 1):
            root = _xml(archive, part)
            if root is None:
                continue
            lines, found = _slide_text(root)
            pictures += found
            notes_part = next(
                (t for kind, t in _relationships(archive, part).values() if kind == "notesSlide"), None
            )
            spoken = _notes_text(_xml(archive, notes_part))
            has_text = has_text or bool(spoken) or any(line != PICTURE for line in lines)
            block = [f"## 第 {number} 页"] + lines
            if spoken:
                block.append("讲稿：" + " ".join(spoken))
            slides.append("\n".join(block))

    if not has_text:
        raise DocumentError(f"{path.name} 里没有读到文字。如果内容都在图片里，读不了。")
    notes = []
    if pictures:
        notes.append(f"{path.name} 里有 {pictures} 张图片，读不了图，只读了文字"
                     f"（图片的位置标成了「{PICTURE}」）。")
    return Document("\n\n".join(slides), notes)


def _slide_text(root: ET.Element) -> tuple[list[str], int]:
    lines: list[str] = []
    pictures = 0
    tree = root.find(f"{_P}cSld/{_P}spTree")
    if tree is None:
        return lines, 0

    def walk(node: ET.Element) -> None:
        nonlocal pictures
        for child in node:
            if child.tag == f"{_P}pic":
                pictures += 1
                lines.append(PICTURE)
            elif child.tag == f"{_A}tbl":
                for row in child.iter(f"{_A}tr"):
                    cells = [" ".join(_paragraphs(cell)) for cell in row.findall(f"{_A}tc")]
                    if any(cells):
                        lines.append(" | ".join(cells))
            elif child.tag == f"{_A}txBody" or child.tag == f"{_P}txBody":
                lines.extend(_paragraphs(child))
            elif child.tag == f"{_MC}Fallback":
                continue
            else:
                walk(child)

    walk(tree)
    return lines, pictures


def _paragraphs(body: ET.Element) -> list[str]:
    out = []
    for paragraph in body.iter(f"{_A}p"):
        text = "".join(
            (node.text or "") if node.tag == f"{_A}t" else "\n"
            for node in paragraph.iter()
            if node.tag in (f"{_A}t", f"{_A}br")
        ).strip()
        if text:
            out.append(text)
    return out


def _notes_text(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    spoken = []
    for shape in root.iter(f"{_P}sp"):
        placeholder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        # Only the body placeholder holds what was written; the others repeat
        # the slide image and its number.
        if placeholder is None or placeholder.get("type") != "body":
            continue
        body = shape.find(f"{_P}txBody")
        if body is not None:
            spoken.extend(_paragraphs(body))
    return spoken
