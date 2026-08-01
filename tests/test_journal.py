"""Journal style profiling — sample collection (Clause 3.2).

Profiling is the step that gates all drafting, and its input is whatever the
researcher has on disk. That is a folder of PDFs, not a folder of .txt files.
"""

import pytest

from mra import journal


def _pdf(path, lines):
    """A PDF carrying a real text layer, built without a binary fixture."""
    body = ["BT", "/F1 11 Tf", "40 750 Td"]
    for index, line in enumerate(lines):
        if index:
            body.append("0 -16 Td")
        body.append(f"({line}) Tj")
    body.append("ET")
    content = "\n".join(body).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
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


# Long enough to clear MIN_USABLE_CHARS, and shaped like a paper.
PAPER_LINES = [
    "Portal macrophage density predicts fibrosis progression in NASH",
    "Hepatology 2021;74:88-101",
    "",
    "We quantified CD68-positive portal macrophages in paired liver biopsies",
    "taken a median of 4.1 years apart from one hundred and twelve patients",
    "with biopsy-proven nonalcoholic steatohepatitis. Baseline portal, but not",
    "lobular, macrophage density independently predicted a one-stage increase",
    "in fibrosis after adjustment for baseline stage, body mass index and",
    "diabetes. The association was absent for lobular macrophages, supporting",
    "compartment specificity rather than a global inflammatory burden effect.",
    "These findings identify portal macrophage density as a prognostic marker",
    "that can be read from routine histology without additional tissue.",
]


class TestSampleCollection:
    def test_pdfs_are_read_directly(self, tmp_path):
        """Asking a researcher to copy-paste five papers into .txt files before
        profiling can start is what stops the tool being used."""
        _pdf(tmp_path / "paper1.pdf", PAPER_LINES)

        samples = journal.collect_file_samples(tmp_path)

        assert len(samples) == 1
        assert "paper1.pdf" in samples[0]
        assert "portal macrophages" in samples[0]

    def test_text_files_still_work(self, tmp_path):
        (tmp_path / "paper.txt").write_text("\n".join(PAPER_LINES), encoding="utf-8")
        assert len(journal.collect_file_samples(tmp_path)) == 1

    def test_pdfs_and_text_mix_in_one_directory(self, tmp_path):
        _pdf(tmp_path / "a.pdf", PAPER_LINES)
        (tmp_path / "b.md").write_text("\n".join(PAPER_LINES), encoding="utf-8")
        assert len(journal.collect_file_samples(tmp_path)) == 2

    def test_scanned_pdf_is_skipped_not_profiled_from(self, tmp_path):
        """A scan has no text layer. Profiling voice from its header would
        produce a confident profile built on nothing."""
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with open(tmp_path / "scan.pdf", "wb") as fh:
            writer.write(fh)
        _pdf(tmp_path / "good.pdf", PAPER_LINES)

        samples = journal.collect_file_samples(tmp_path)

        assert len(samples) == 1
        assert "good.pdf" in samples[0]

    def test_unrelated_files_are_ignored(self, tmp_path):
        _pdf(tmp_path / "paper.pdf", PAPER_LINES)
        (tmp_path / "notes.docx").write_bytes(b"not readable here")
        (tmp_path / "figure.png").write_bytes(b"\x89PNG")
        assert len(journal.collect_file_samples(tmp_path)) == 1

    def test_empty_directory_names_the_accepted_types(self, tmp_path):
        with pytest.raises(ValueError, match=r"\.pdf"):
            journal.collect_file_samples(tmp_path)

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            journal.collect_file_samples(tmp_path / "nope")

    def test_long_samples_are_trimmed_head_and_tail(self, tmp_path):
        """Openings and closings carry the most style; the middle is padding."""
        filler = ["Sentence about hepatic stellate cell activation." for _ in range(4000)]
        (tmp_path / "long.txt").write_text(
            "OPENING LINE\n" + "\n".join(filler) + "\nCLOSING LINE", encoding="utf-8"
        )

        sample = journal.collect_file_samples(tmp_path)[0]

        assert "OPENING LINE" in sample
        assert "CLOSING LINE" in sample
        assert "middle omitted" in sample
        assert len(sample) < journal.MAX_CHARS_PER_FULLTEXT + 500
