"""Reading Word and PowerPoint files directly.

The fixtures are written as the XML Word and PowerPoint actually produce,
cut down to the elements under test, so that each test shows the markup it is
about. What is checked is what would mislead a reader of the text if it went
wrong: a lost list number, a deleted sentence read as if it were still there,
a μ read as an m, a picture that silently vanished.
"""

from __future__ import annotations

import zipfile

import pytest

from mra import assess, documents, ingest
from mra import cli

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = (
    f'xmlns:w="{W}" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def docx(path, body, *, numbering="", styles="", comments="", footnotes=""):
    parts = {"numbering": numbering, "styles": styles, "comments": comments, "footnotes": footnotes}
    rels = "".join(
        f'<Relationship Id="r{kind}" Type="{REL}/{kind}" Target="{kind}.xml"/>'
        for kind, xml in parts.items() if xml
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        archive.writestr("word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>")
        for kind, xml in parts.items():
            if xml:
                root = {"numbering": "numbering", "styles": "styles",
                        "comments": "comments", "footnotes": "footnotes"}[kind]
                archive.writestr(f"word/{kind}.xml", f"<w:{root} {NS}>{xml}</w:{root}>")
    return path


def p(text, *, num=None, level=0, style=""):
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if num is not None:
        props += f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num}"/></w:numPr>'
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def abstract(aid, *levels):
    body = "".join(
        f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text}"/></w:lvl>'
        for i, (fmt, text) in enumerate(levels)
    )
    return f'<w:abstractNum w:abstractNumId="{aid}">{body}</w:abstractNum>'


def num(nid, aid, restart=None):
    override = (
        f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="{restart}"/></w:lvlOverride>'
        if restart is not None else ""
    )
    return f'<w:num w:numId="{nid}"><w:abstractNumId w:val="{aid}"/>{override}</w:num>'


def read(path):
    return documents.read(path)


# ------------------------------------------------------------------- numbering


class TestNumbering:
    """A reviewer's comments are usually an automatic list. Without the
    numbers, "Reviewer 2, comment 3" refers to nothing."""

    def test_a_numbered_list_keeps_its_numbers(self, tmp_path):
        path = docx(
            tmp_path / "reviews.docx",
            p("The claim is causal.", num=1) + p("Show the points.", num=1),
            numbering=abstract(0, ("decimal", "%1.")) + num(1, 0),
        )
        assert read(path).text == "1. The claim is causal.\n\n2. Show the points."

    def test_sub_items_restart_under_each_parent(self, tmp_path):
        path = docx(
            tmp_path / "nested.docx",
            p("first", num=1) + p("a", num=1, level=1) + p("b", num=1, level=1)
            + p("second", num=1) + p("again", num=1, level=1),
            numbering=abstract(0, ("decimal", "%1."), ("lowerLetter", "%1.%2)")) + num(1, 0),
        )
        lines = read(path).text.split("\n\n")
        assert lines == ["1. first", "1.a) a", "1.b) b", "2. second", "2.a) again"]

    def test_chinese_numbering(self, tmp_path):
        """研究内容（一）（二）（三） — the labels a proposal refers back to."""
        path = docx(
            tmp_path / "proposal.docx",
            p("构建敲除小鼠", num=1) + p("单细胞测序", num=1),
            numbering=abstract(0, ("chineseCounting", "（%1）")) + num(1, 0),
        )
        assert read(path).text == "（一） 构建敲除小鼠\n\n（二） 单细胞测序"

    def test_a_second_list_on_the_same_definition_continues(self, tmp_path):
        path = docx(
            tmp_path / "continue.docx",
            p("one", num=1) + p("two", num=1) + p("three", num=2),
            numbering=abstract(0, ("decimal", "%1.")) + num(1, 0) + num(2, 0),
        )
        assert read(path).text.endswith("3. three")

    def test_a_start_override_restarts_it(self, tmp_path):
        """Reviewer 2's list starts again at 1 — Word does this with an override."""
        path = docx(
            tmp_path / "restart.docx",
            p("one", num=1) + p("two", num=1) + p("again", num=2),
            numbering=abstract(0, ("decimal", "%1.")) + num(1, 0) + num(2, 0, restart=1),
        )
        assert read(path).text.endswith("1. again")

    def test_numbering_carried_by_the_paragraph_style(self, tmp_path):
        """Word's built-in 「列表编号」 style puts the numbering on the style."""
        styles = (
            '<w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/>'
            '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        )
        path = docx(
            tmp_path / "styled.docx",
            p("first", style="ListNumber") + p("second", style="ListNumber"),
            numbering=abstract(0, ("decimal", "%1.")) + num(1, 0),
            styles=styles,
        )
        assert read(path).text == "1. first\n\n2. second"

    def test_numbering_switched_off_on_the_paragraph_wins(self, tmp_path):
        styles = (
            '<w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/>'
            '<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        )
        path = docx(
            tmp_path / "off.docx",
            p("not numbered", style="ListNumber", num=0),
            numbering=abstract(0, ("decimal", "%1.")) + num(1, 0),
            styles=styles,
        )
        assert read(path).text == "not numbered"

    def test_bullets_drawn_from_a_symbol_font_become_a_plain_bullet(self, tmp_path):
        path = docx(
            tmp_path / "bullets.docx",
            p("point", num=1),
            numbering=abstract(0, ("bullet", "")) + num(1, 0),
        )
        assert read(path).text == "• point"

    @pytest.mark.parametrize("fmt, n, expected", [
        ("upperRoman", 4, "IV"), ("lowerLetter", 27, "aa"), ("chineseCounting", 12, "十二"),
        ("chineseCounting", 20, "二十"), ("decimalEnclosedCircle", 3, "③"),
        ("ideographTraditional", 2, "乙"), ("decimalZero", 7, "07"),
    ])
    def test_number_formats(self, fmt, n, expected):
        assert documents._format_number(n, fmt) == expected


# ---------------------------------------------------------------------- content


class TestWhatTheTextSays:
    def test_headings_are_marked(self, tmp_path):
        styles = ('<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="2"><w:name w:val="heading 2"/>'
                  '<w:basedOn w:val="1"/></w:style>')
        path = docx(tmp_path / "h.docx", p("立项依据", style="1") + p("研究现状", style="2"),
                    styles=styles)
        assert read(path).text == "# 立项依据\n\n## 研究现状"

    def test_tracked_changes_read_as_accepted_and_said_so(self, tmp_path):
        """A revision in progress: the deleted sentence must not be read as
        though it were still in the manuscript."""
        body = (
            "<w:p><w:r><w:t>Macrophages </w:t></w:r>"
            "<w:del><w:r><w:delText>drive</w:delText></w:r></w:del>"
            "<w:ins><w:r><w:t>are associated with</w:t></w:r></w:ins>"
            "<w:r><w:t> fibrosis.</w:t></w:r></w:p>"
        )
        document = read(docx(tmp_path / "revised.docx", body))
        assert document.text == "Macrophages are associated with fibrosis."
        assert any("修订" in note for note in document.notes)

    def test_a_dose_typed_in_the_symbol_font_keeps_its_micro(self, tmp_path):
        """"m" in Symbol is μ. Read as an m, 10 μg/kg becomes 10 mg/kg."""
        body = (
            "<w:p><w:r><w:t>10 </w:t></w:r>"
            '<w:r><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:t>m</w:t></w:r>'
            "<w:r><w:t>g/kg TGF-</w:t></w:r>"
            '<w:r><w:sym w:font="Symbol" w:char="F062"/></w:r>'
            "<w:r><w:t>1</w:t></w:r></w:p>"
        )
        assert read(docx(tmp_path / "dose.docx", body)).text == "10 μg/kg TGF-β1"

    def test_tables_keep_their_rows(self, tmp_path):
        def row(*cells):
            return "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in cells) + "</w:tr>"

        body = f"<w:tbl>{row('组别', 'n', 'ALT')}{row('对照', '3', '38')}</w:tbl>"
        assert read(docx(tmp_path / "t.docx", body)).text == "组别 | n | ALT\n对照 | 3 | 38"

    def test_a_text_box_is_read_once(self, tmp_path):
        """Word stores a text box twice, once for older readers."""
        box = "<w:txbxContent>" + p("技术路线：造模 → 测序") + "</w:txbxContent>"
        body = (
            "<w:p><w:r><mc:AlternateContent>"
            f"<mc:Choice Requires=\"wps\"><w:drawing>{box}</w:drawing></mc:Choice>"
            f"<mc:Fallback><w:pict>{box}</w:pict></mc:Fallback>"
            "</mc:AlternateContent></w:r></w:p>"
        )
        document = read(docx(tmp_path / "box.docx", body))
        assert document.text == "技术路线：造模 → 测序"
        assert document.notes == []

    def test_a_picture_is_marked_where_it_was_and_reported(self, tmp_path):
        """Told nothing, a model criticises a proposal for lacking the
        roadmap that was drawn in the picture."""
        body = p("技术路线见下图：") + "<w:p><w:r><w:drawing/></w:r></w:p>"
        document = read(docx(tmp_path / "pic.docx", body))
        assert document.text == f"技术路线见下图：\n\n{documents.PICTURE}"
        assert any("1 张图片" in note for note in document.notes)

    def test_field_codes_are_not_text(self, tmp_path):
        """EndNote keeps its database record in the field code; the citation
        the reader sees is the field's result."""
        body = (
            '<w:p><w:r><w:t>Shown before </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            "<w:r><w:instrText> ADDIN EN.CITE &lt;EndNote&gt;&lt;Cite&gt;</w:instrText></w:r>"
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>[12]</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t>.</w:t></w:r></w:p>'
        )
        assert read(docx(tmp_path / "cite.docx", body)).text == "Shown before [12]."

    def test_comments_and_footnotes_are_reported_not_dropped_silently(self, tmp_path):
        comments = '<w:comment w:id="0">' + p("改一下") + '</w:comment><w:comment w:id="1"/>'
        footnotes = ('<w:footnote w:type="separator" w:id="-1"/>'
                     '<w:footnote w:type="continuationSeparator" w:id="0"/>'
                     '<w:footnote w:id="1">' + p("note") + "</w:footnote>")
        document = read(docx(tmp_path / "c.docx", p("正文"), comments=comments, footnotes=footnotes))
        assert document.text == "正文"
        assert any("2 条批注" in note for note in document.notes)
        assert any("1 条脚注" in note for note in document.notes)


# ------------------------------------------------------------------- refusals


class TestWhatCannotBeRead:
    def test_a_renamed_or_locked_file_says_what_to_try(self, tmp_path):
        broken = tmp_path / "实验记录.docx"
        broken.write_bytes(b"\xd0\xcf\x11\xe0 an old binary document")
        with pytest.raises(documents.DocumentError, match="密码"):
            read(broken)

    @pytest.mark.parametrize("suffix, hint", [
        (".doc", "Word 文档"), (".wps", ".docx"), (".ppt", ".pptx"),
        (".xlsx", "CSV UTF-8"), (".png", "图片"),
    ])
    def test_formats_that_cannot_be_read_name_the_save_as(self, tmp_path, suffix, hint):
        path = tmp_path / f"file{suffix}"
        path.write_bytes(b"\x00\x01")
        with pytest.raises(documents.DocumentError, match=hint):
            read(path)

    def test_an_empty_word_file_is_an_error_not_an_empty_reading(self, tmp_path):
        with pytest.raises(documents.DocumentError, match="没有读到文字"):
            read(docx(tmp_path / "empty.docx", "<w:p/>"))

    def test_plain_text_must_be_utf8(self, tmp_path):
        path = tmp_path / "draft.txt"
        path.write_bytes("研究结果显示".encode("gbk"))
        with pytest.raises(documents.DocumentError, match="not UTF-8"):
            read(path)

    def test_a_byte_order_mark_is_not_text(self, tmp_path):
        path = tmp_path / "draft.md"
        path.write_bytes("结果".encode("utf-8-sig"))
        assert read(path).text == "结果"

    def test_a_pdf_is_read_the_way_import_reads_it(self, tmp_path):
        from test_journal import PAPER_LINES, _pdf

        _pdf(tmp_path / "proposal.pdf", PAPER_LINES)
        assert PAPER_LINES[0] in read(tmp_path / "proposal.pdf").text

    def test_a_scanned_pdf_says_it_needs_ocr(self, tmp_path):
        from test_journal import _pdf

        _pdf(tmp_path / "scan.pdf", ["Page 1"])
        with pytest.raises(documents.DocumentError, match="OCR"):
            read(tmp_path / "scan.pdf")

    def test_a_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read(tmp_path / "nope.docx")


# ------------------------------------------------------------------ PowerPoint


A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
PNS = f'xmlns:a="{A}" xmlns:p="{P}" xmlns:r="{REL}"'
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def shape(*paragraphs):
    body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paragraphs)
    return f"<p:sp><p:txBody>{body}</p:txBody></p:sp>"


def slide(*shapes):
    return f"<p:sld {PNS}><p:cSld><p:spTree>{''.join(shapes)}</p:spTree></p:cSld></p:sld>"


def pptx(path, slides, *, order=None, notes=None):
    """`order` lists slide file numbers in presentation order."""
    order = order or list(range(1, len(slides) + 1))
    notes = notes or {}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                         f'Type="{REL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
        ids = "".join(f'<p:sldId id="{255 + i}" r:id="s{n}"/>' for i, n in enumerate(order, 1))
        archive.writestr("ppt/presentation.xml", f"<p:presentation {PNS}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        rels = "".join(f'<Relationship Id="s{n}" Type="{REL}/slide" Target="slides/slide{n}.xml"/>'
                       for n in range(1, len(slides) + 1))
        archive.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{PKG}">{rels}</Relationships>')
        for n, xml in enumerate(slides, 1):
            archive.writestr(f"ppt/slides/slide{n}.xml", xml)
            if n in notes:
                archive.writestr(f"ppt/slides/_rels/slide{n}.xml.rels",
                                 f'<Relationships xmlns="{PKG}"><Relationship Id="n1" Type="{REL}/notesSlide" '
                                 f'Target="../notesSlides/notesSlide{n}.xml"/></Relationships>')
                archive.writestr(
                    f"ppt/notesSlides/notesSlide{n}.xml",
                    f"<p:notes {PNS}><p:cSld><p:spTree>"
                    '<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr></p:sp>'
                    f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
                    f"<p:txBody><a:p><a:r><a:t>{notes[n]}</a:t></a:r></a:p></p:txBody></p:sp>"
                    "</p:spTree></p:cSld></p:notes>",
                )
    return path


class TestSlides:
    """A student's 开题汇报 is usually a deck."""

    def test_slides_in_presentation_order_with_speaker_notes(self, tmp_path):
        path = pptx(
            tmp_path / "开题.pptx",
            [slide(shape("预实验结果")), slide(shape("科学问题", "TREM2 是否驱动纤维化"))],
            order=[2, 1],  # the file named slide2 was moved to the front
            notes={2: "这里讲为什么选 CDAHFD"},
        )
        text = read(path).text
        assert text.index("科学问题") < text.index("预实验结果")
        assert "## 第 1 页\n科学问题\nTREM2 是否驱动纤维化\n讲稿：这里讲为什么选 CDAHFD" in text

    def test_pictures_on_slides_are_reported(self, tmp_path):
        path = pptx(tmp_path / "pic.pptx", [slide(shape("结果"), "<p:pic/>")])
        document = read(path)
        assert documents.PICTURE in document.text
        assert any("1 张图片" in note for note in document.notes)

    def test_a_deck_with_no_text_is_an_error(self, tmp_path):
        path = pptx(tmp_path / "pictures-only.pptx", [slide("<p:pic/>")])
        with pytest.raises(documents.DocumentError, match="没有读到文字"):
            read(path)


# ---------------------------------------------------------------- where it's used


class TestEveryEntryPointReadsWord:
    def test_a_manuscript_command_reads_word_and_says_what_it_skipped(self, tmp_path, capsys):
        path = docx(tmp_path / "稿子.docx", p("Results.") + "<w:p><w:r><w:drawing/></w:r></w:p>")
        assert cli.read_text(path).startswith("Results.")
        assert "1 张图片" in capsys.readouterr().out

    def test_an_unreadable_manuscript_stops_with_the_instruction(self, tmp_path):
        old = tmp_path / "稿子.doc"
        old.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(SystemExit, match="另存为"):
            cli.read_text(old)

    def test_an_experiment_record_in_word_is_data_description(self, tmp_path):
        path = docx(tmp_path / "实验记录.docx", p("n=12/组，两批独立队列"))
        described = assess.load_data_description(path)
        assert described == "FILE: 实验记录.docx\n\nn=12/组，两批独立队列"

    def test_excel_is_still_refused_as_data(self, tmp_path):
        book = tmp_path / "数据.xlsx"
        book.write_bytes(b"PK")
        with pytest.raises(ValueError, match="CSV UTF-8"):
            assess.load_data_description(book)

    def test_import_reads_a_word_paper(self, tmp_path):
        text = "Macrophage-derived TGF-β1 drives portal fibrosis. " * 20
        doc = ingest.extract(docx(tmp_path / "preprint.docx", p(text)))
        assert doc.kind == "docx" and doc.usable
        assert "docx" in "".join(ingest.SUPPORTED_SUFFIXES)

    def test_a_broken_word_file_does_not_stop_a_batch_import(self, tmp_path):
        broken = tmp_path / "broken.docx"
        broken.write_bytes(b"not a zip")
        doc = ingest.extract(broken)
        assert not doc.usable and "打不开" in doc.warnings[0]
