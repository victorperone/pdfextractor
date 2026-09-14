from pathlib import Path

from reportlab.pdfgen import canvas

from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource


def _sample_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=(200, 200))
    c.drawString(20, 150, "Olá Justiça 123")
    c.save()
    return path


def test_pdfium_native_source_extracts_chars_and_coordinates(tmp_path):
    pdf = _sample_pdf(tmp_path / "sample.pdf")
    with PdfiumNativeEvidenceSource(pdf) as source:
        context = source.open()
        page = source.extract_page(0)
    assert context.page_count == 1
    assert "Olá Justiça" in page.extracted_text
    assert len(page.characters) >= len("Olá Justiça 123")
    first = page.characters[0]
    assert first.text == "O"
    assert first.unicode_codepoint == ord("O")
    assert first.bbox.x0 > 0
    assert first.bbox.y0 < 60  # converted to top-left coordinates
