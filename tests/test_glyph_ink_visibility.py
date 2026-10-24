"""P0-1: native text is published only when its glyphs leave visible ink.

Paint order decides visibility: a later opaque path or image, a same-color
background or an invisible render mode hides a glyph; text drawn over a fill
stays visible. Rejected glyphs must not reach raw text, reading text, tables
or Markdown. Modes without rendering keep their native contract.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from structured_pdf_text import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig
from structured_pdf_text.renderers.markdown import render_markdown

SECRET = "SEGREDO-OCULTO-777"
VISIBLE = "Conteudo visivel preservado"


def _extract(path: Path, mode: ExtractionMode = ExtractionMode.BALANCED):
    config = ExtractorConfig(mode=mode, enable_ocr=False, enable_experimental_occlusion_redaction=True)
    document = PdfTextExtractor(config).extract(path)
    return document, render_markdown(document)


def _pdf(tmp_path: Path, name: str, draw, *, rotation: int = 0) -> Path:
    path = tmp_path / f"{name}.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(595, 842), pageCompression=0)
    if rotation:
        pdf.setPageRotation(rotation)
    pdf.setFont("Helvetica", 12)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(60, 500, VISIBLE)
    draw(pdf)
    pdf.save()
    return path


def _assert_hidden(document, markdown: str, secret: str = SECRET) -> None:
    page = document.pages[0]
    for text in (page.raw_text, page.reading_text, markdown):
        assert secret not in text
        assert secret.split("-")[0] not in text
    assert VISIBLE in markdown
    assert page.diagnostics.facts["inkless_native_characters"] > 0


def _covered_by_gray_composite(pdf):
    pdf.drawString(60, 400, SECRET)
    # Composite cover: two overlapping mid-gray rectangles (no solid black/white).
    pdf.setFillColorRGB(0.45, 0.5, 0.55)
    pdf.rect(55, 394, 90, 20, fill=1, stroke=0)
    pdf.rect(140, 394, 70, 20, fill=1, stroke=0)


def _covered_by_image(pdf):
    pdf.drawString(60, 400, SECRET)
    stamp = Image.new("RGB", (300, 60), (210, 40, 40))
    buffer = io.BytesIO()
    stamp.save(buffer, format="PNG")
    pdf.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 55, 390, width=170, height=28)


def _white_on_white(pdf):
    pdf.setFillColorRGB(1, 1, 1)
    pdf.drawString(60, 400, SECRET)


def _invisible_render_mode(pdf):
    text = pdf.beginText(60, 400)
    text.setTextRenderMode(3)
    text.textLine(SECRET)
    pdf.drawText(text)


@pytest.mark.parametrize("draw", [_covered_by_gray_composite, _covered_by_image,
                                  _white_on_white, _invisible_render_mode])
def test_hidden_native_text_never_reaches_output(tmp_path, draw):
    document, markdown = _extract(_pdf(tmp_path, draw.__name__, draw))
    _assert_hidden(document, markdown)


def test_label_over_redaction_bar_is_kept_and_covered_text_is_not(tmp_path):
    def draw(pdf):
        pdf.drawString(60, 400, SECRET)
        pdf.setFillColorRGB(0.12, 0.12, 0.12)
        pdf.rect(55, 394, 140, 20, fill=1, stroke=0)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.drawString(90, 400, "DADO RESTRITO")

    document, markdown = _extract(_pdf(tmp_path, "label", draw))
    _assert_hidden(document, markdown)
    assert "DADO RESTRITO" in markdown


@pytest.mark.parametrize("background,ink", [((0.1, 0.2, 0.3), (1, 1, 1)), ((0.9, 0.85, 0.2), (0.2, 0.2, 0.2)),
                                            ((1, 1, 1), (0.8, 0.8, 0.8))])
def test_text_over_fills_and_light_text_remain_visible(tmp_path, background, ink):
    def draw(pdf):
        pdf.setFillColorRGB(*background)
        pdf.rect(50, 390, 300, 30, fill=1, stroke=0)
        pdf.setFillColorRGB(*ink)
        pdf.drawString(60, 400, "Faixa legivel 2026 i.l,;")

    document, markdown = _extract(_pdf(tmp_path, "fill", draw))
    assert "Faixa legivel 2026 i.l,;" in markdown
    assert document.pages[0].diagnostics.facts["inkless_native_characters"] == 0


def test_text_drawn_over_an_image_is_visible(tmp_path):
    def draw(pdf):
        photo = Image.new("RGB", (200, 80), (230, 230, 230))
        buffer = io.BytesIO()
        photo.save(buffer, format="PNG")
        pdf.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 50, 380, width=300, height=50)
        pdf.drawString(60, 400, "Legenda sobre imagem")

    _, markdown = _extract(_pdf(tmp_path, "over-image", draw))
    assert "Legenda sobre imagem" in markdown


def test_covered_table_cell_is_not_resurrected(tmp_path):
    def draw(pdf):
        pdf.setLineWidth(1)
        for row in range(4):
            pdf.line(60, 700 - 24 * row, 420, 700 - 24 * row)
        for column in (60, 180, 300, 420):
            pdf.line(column, 700, column, 628)
        for row, values in enumerate([("Item", "Valor", "Status"), ("A-1", "R$ 10,00", "OK"),
                                      ("A-2", "R$ 811 mil SIGILO", "OK")]):
            for column, value in enumerate(values):
                pdf.drawString(66 + 120 * column, 684 - 24 * row, value)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.rect(182, 630, 116, 22, fill=1, stroke=0)

    document, markdown = _extract(_pdf(tmp_path, "table", draw))
    assert "SIGILO" not in markdown and "811" not in markdown
    assert all("SIGILO" not in (cell.text or "") for table in document.tables for cell in table.cells)
    assert "R$ 10,00" in markdown


def test_rotated_page_uses_the_visual_frame(tmp_path):
    document, markdown = _extract(_pdf(tmp_path, "rotated", _covered_by_gray_composite, rotation=90))
    _assert_hidden(document, markdown)


def test_native_mode_keeps_its_contract_without_rendering(tmp_path):
    document, _ = _extract(_pdf(tmp_path, "native", _white_on_white), mode=ExtractionMode.NATIVE)
    facts = document.pages[0].diagnostics.facts
    assert facts["inkless_native_characters"] == 0
