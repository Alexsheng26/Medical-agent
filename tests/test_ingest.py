"""Local document ingest: PDF/text extraction and identity.

The PDF fixtures are generated at test time with pypdf, so the suite stays
offline and there is no binary blob in the repository.
"""

import pytest

from mra import ingest


PAPER_TEXT = """Macrophage-derived TGF-beta1 drives portal fibrosis in murine NASH

Chen W, Okonkwo A, Nilsson E
Hepatology 2019;70(4):1122-1135
doi: 10.1002/hep.30712
PMID: 31234567

Abstract
Portal fibrosis predicts outcome in nonalcoholic steatohepatitis, but the
cellular source of profibrotic signals is unresolved. We used myeloid-specific
Tgfb1 knockout mice fed a choline-deficient high-fat diet for twelve weeks and
validated the findings in forty-eight human liver biopsies. Myeloid Tgfb1
deletion reduced Sirius red-positive area by 38 percent.
"""


class TestTextExtraction:
    def test_plain_text_file(self, tmp_path):
        path = tmp_path / "paper.txt"
        path.write_text(PAPER_TEXT, encoding="utf-8")

        doc = ingest.extract(path)
        assert doc.kind == "text"
        assert doc.usable
        assert "Sirius red" in doc.text
        assert doc.warnings == []

    def test_short_text_is_flagged_not_silently_stored(self, tmp_path):
        path = tmp_path / "note.txt"
        path.write_text("Just a note.", encoding="utf-8")

        doc = ingest.extract(path)
        assert not doc.usable
        assert any("too short" in w for w in doc.warnings)

    def test_unsupported_suffix_raises(self, tmp_path):
        path = tmp_path / "data.xlsx"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(ingest.IngestError, match="Unsupported"):
            ingest.extract(path)

    def test_markdown_is_treated_as_text(self, tmp_path):
        path = tmp_path / "paper.md"
        path.write_text(PAPER_TEXT, encoding="utf-8")
        assert ingest.extract(path).kind == "text"


class TestPdfExtraction:
    def test_pdf_without_a_text_layer_is_reported_as_needing_ocr(self, tmp_path):
        """A scan must never be stored silently — 40 characters of header would
        poison retrieval while looking like a successful import."""
        pypdf = pytest.importorskip("pypdf")
        path = tmp_path / "scan.pdf"
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as fh:
            writer.write(fh)

        doc = ingest.extract(path)
        assert doc.kind == "pdf"
        assert not doc.usable
        assert doc.pages == 1
        assert any("OCR" in w for w in doc.warnings)

    def test_corrupt_pdf_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4 this is not really a pdf")

        doc = ingest.extract(path)
        assert not doc.usable
        assert doc.warnings, "a corrupt file must explain itself"


class TestCleaning:
    def test_hyphenated_line_breaks_are_rejoined(self):
        assert "fibrosis" in ingest._clean("fibro-\nsis was measured")

    def test_paragraph_structure_survives(self):
        cleaned = ingest._clean("Para one.\n\n\n\nPara two.")
        assert cleaned == "Para one.\n\nPara two."

    def test_runs_of_spaces_collapse(self):
        assert ingest._clean("a     b") == "a b"


class TestIdentity:
    def test_local_id_is_stable_for_the_same_content(self):
        assert ingest.local_id(PAPER_TEXT) == ingest.local_id(PAPER_TEXT)

    def test_local_id_ignores_whitespace_differences(self):
        """Re-exporting the same PDF must not mint a second record."""
        assert ingest.local_id(PAPER_TEXT) == ingest.local_id(
            PAPER_TEXT.replace("\n", "  \n ")
        )

    def test_local_id_differs_for_different_content(self):
        assert ingest.local_id(PAPER_TEXT) != ingest.local_id(PAPER_TEXT + " extra finding")

    def test_local_id_shape(self):
        identifier = ingest.local_id(PAPER_TEXT)
        assert identifier.startswith("local:")
        assert len(identifier.split(":")[1]) == 8

    def test_sniff_finds_printed_pmid_and_doi(self):
        pmid, doi = ingest.sniff_identifiers(PAPER_TEXT)
        assert pmid == "31234567"
        assert doi == "10.1002/hep.30712"

    def test_sniff_returns_blanks_when_absent(self):
        assert ingest.sniff_identifiers("A paper with no identifiers.") == ("", "")

    def test_sniff_strips_trailing_punctuation_from_doi(self):
        _, doi = ingest.sniff_identifiers("Available at doi: 10.1002/hep.30712.")
        assert doi == "10.1002/hep.30712"


class TestArticleConstruction:
    def test_printed_pmid_wins_over_a_local_id(self, tmp_path):
        path = tmp_path / "p.txt"
        path.write_text(PAPER_TEXT, encoding="utf-8")
        doc = ingest.extract(path)

        article = ingest.to_article(doc, None, pmid="31234567")
        assert article.pmid == "31234567"
        assert not ingest.is_local(article.pmid)

    def test_falls_back_to_local_id(self, tmp_path):
        path = tmp_path / "p.txt"
        path.write_text(PAPER_TEXT, encoding="utf-8")
        doc = ingest.extract(path)

        article = ingest.to_article(doc, None)
        assert ingest.is_local(article.pmid)
        # Filename stands in when no metadata was extracted.
        assert article.title == "p"
        assert article.publication_types == ["Local full text"]

    def test_full_text_lands_in_the_indexed_column(self, tmp_path):
        path = tmp_path / "p.txt"
        path.write_text(PAPER_TEXT, encoding="utf-8")
        article = ingest.to_article(ingest.extract(path), None)
        # `abstract` is what FTS indexes; storing only a summary there would
        # throw away the reason for importing full text.
        assert "Sirius red" in article.abstract

    def test_metadata_object_populates_fields(self, tmp_path):
        from mra.schemas import LocalArticleMeta

        path = tmp_path / "p.txt"
        path.write_text(PAPER_TEXT, encoding="utf-8")
        meta = LocalArticleMeta(
            title="Macrophage TGF-beta1 drives portal fibrosis",
            authors=["Chen W", "Okonkwo A"],
            journal="Hepatology",
            year="2019",
            doi="10.1002/hep.30712",
            pmid="",
            keywords=["fibrosis"],
            document_type="research article",
        )
        article = ingest.to_article(ingest.extract(path), meta)
        assert article.title.startswith("Macrophage TGF")
        assert article.authors == ["Chen W", "Okonkwo A"]
        assert article.year == "2019"
        assert article.mesh_terms == ["fibrosis"]


class TestMarkers:
    def test_local_marker(self):
        assert ingest.marker("local:a1b2c3d4") == "[LOCAL:a1b2c3d4]"

    def test_pmid_marker(self):
        assert ingest.marker("31234567") == "[PMID:31234567]"


class TestFrontMatterWindow:
    """Two-column publisher PDFs emit body text ahead of the title block, so the
    front matter is often not at the front of the extracted text."""

    def test_front_is_the_first_page_not_the_first_n_characters(self, tmp_path):
        pypdf = pytest.importorskip("pypdf")
        from pypdf import PdfWriter

        # Page 1 long enough to exceed FRONT_MIN_CHARS; page 2 must not be pulled in.
        page1 = "Title line about hepatic macrophages. " * 60
        page2 = "Discussion text that belongs to page two only. " * 60
        path = _two_page_pdf(tmp_path / "paper.pdf", page1, page2)

        doc = ingest.extract(path)

        assert "page two only" not in doc.front
        assert len(doc.front) <= ingest.FRONT_MAX_CHARS

    def test_sparse_first_page_falls_through_to_the_second(self, tmp_path):
        pytest.importorskip("pypdf")
        path = _two_page_pdf(tmp_path / "cover.pdf", "Offprint.", "Real title. " * 200)

        doc = ingest.extract(path)

        assert "Real title" in doc.front, "a cover sheet must not be the whole window"

    def test_text_files_keep_the_character_window(self, tmp_path):
        path = tmp_path / "paper.txt"
        path.write_text("A" * 10_000, encoding="utf-8")
        assert len(ingest.extract(path).front) == ingest.METADATA_WINDOW


def _two_page_pdf(path, page1_text, page2_text):
    """Two pages carrying real text layers, built without a binary fixture."""
    def stream(text):
        lines = [text[i:i + 90] for i in range(0, len(text), 90)][:45]
        body = ["BT", "/F1 10 Tf", "40 750 Td"]
        for index, line in enumerate(lines):
            if index:
                body.append("0 -16 Td")
            body.append(f"({line}) Tj")
        body.append("ET")
        return "\n".join(body).encode("latin-1")

    contents = [stream(page1_text), stream(page2_text)]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R "
        b"/Resources << /Font << /F1 7 0 R >> >> >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 6 0 R "
        b"/Resources << /Font << /F1 7 0 R >> >> >>",
        b"<< /Length " + str(len(contents[0])).encode() + b" >>\nstream\n" + contents[0] + b"\nendstream",
        b"<< /Length " + str(len(contents[1])).encode() + b" >>\nstream\n" + contents[1] + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref).encode() + b"\n%%EOF\n"
    )
    path.write_bytes(bytes(out))
    return path
