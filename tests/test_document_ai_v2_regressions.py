"""Deterministic synthetic replacement for the unavailable Document_AI_V2.pdf.

The upstream corpus is not redistributed with this repository. These compact
pages preserve the regression behaviors exercised here and are generated in
the test temp directory, so acceptance never skips based on a local file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig
from structured_pdf_text.document import StructuredTable, TableCell, TableFragment, TableMethod
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import render_markdown


@pytest.fixture(scope="module")
def synthetic_regression_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("synthetic-document-ai") / "regressions.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(420, 300), pageCompression=0)
    for page in range(1, 40):
        if page == 7:
            pdf.drawString(40, 230, "H2O x2")
        elif page == 9:
            pdf.drawString(40, 240, "HZ-901")
            pdf.drawString(40, 205, "HZ-902")
            pdf.drawString(40, 170, "HZ-903")
        elif page == 21:
            pdf.drawString(40, 200, "APRO")
            pdf.drawString(90, 200, "VADO")
        elif page == 23:
            pdf.drawString(40, 250, "Receber PDF")
            pdf.drawString(40, 210, "Tem texto nativo?")
            pdf.drawString(40, 170, "sim")
            pdf.drawString(40, 145, "Extrair nativamente")
            pdf.drawString(180, 170, "não")
            pdf.drawString(180, 145, "Executar OCR")
        elif page == 24:
            pdf.drawString(40, 250, "Dados e IA")
            pdf.drawString(40, 210, "Operações")
            pdf.drawString(40, 170, "Risco e Compliance")
        elif page == 38:
            pdf.drawString(40, 230, "internacionalizacao office affluent")
        elif page == 39:
            pdf.drawString(60, 200, "SEGREDO-ALFA-991")
            pdf.setFillColorRGB(0, 0, 0)
            pdf.rect(55, 190, 170, 24, fill=1, stroke=0)
        pdf.showPage()
    pdf.save()
    return path


def _extract(path: Path, page: int, *, redaction: bool = False):
    config = ExtractorConfig(
        mode=ExtractionMode.BALANCED if redaction else ExtractionMode.NATIVE,
        page_indices=(page - 1,),
        enable_experimental_occlusion_redaction=redaction,
    )
    return PdfTextExtractor(config).extract(path).pages[0]


def test_synthetic_redaction_hides_occluded_text(synthetic_regression_pdf: Path):
    page = _extract(synthetic_regression_pdf, 39, redaction=True)
    assert "SEGREDO-ALFA-991" not in page.reading_text
    assert "SEGREDO-ALFA-991" not in page.raw_text
    assert page.diagnostics.facts["experimental_occlusion_redaction_enabled"] is True
    assert page.diagnostics.facts["textpage_reconciliation_disabled_for_redaction"] is True


@pytest.mark.parametrize("band_width", [120, 600])
def test_redaction_preserves_visible_white_text_on_large_black_fill(
    tmp_path: Path,
    band_width: int,
):
    path = tmp_path / f"white-text-on-black-{band_width}.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(600, 400), pageCompression=0)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.rect(0, 100, band_width, 180, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(40, 150, "TEXTO VISIVEL")
    pdf.save()

    config = ExtractorConfig(
        mode=ExtractionMode.BALANCED,
        enable_ocr=False,
        enable_experimental_occlusion_redaction=True,
    )
    document = PdfTextExtractor(config).extract(path)
    page = document.pages[0]
    markdown = render_markdown(document)

    assert "TEXTO VISIVEL" in page.raw_text
    assert "TEXTO VISIVEL" in page.reading_text
    assert "TEXTO VISIVEL" in markdown
    assert page.diagnostics.facts["redacted_native_characters"] == 0


def test_synthetic_columns_and_scripts_keep_text(synthetic_regression_pdf: Path):
    columns = _extract(synthetic_regression_pdf, 9).reading_text
    assert columns.index("HZ-901") < columns.index("HZ-902") < columns.index("HZ-903")
    scripts = _extract(synthetic_regression_pdf, 7).reading_text
    assert "H2O" in scripts
    assert "x2" in scripts


def test_synthetic_diagram_and_organogram_order(synthetic_regression_pdf: Path):
    flowchart = _extract(synthetic_regression_pdf, 23).reading_text
    assert flowchart.index("Receber PDF") < flowchart.index("Tem texto nativo?")
    assert flowchart.index("sim") < flowchart.index("Extrair nativamente")
    assert flowchart.index("não") < flowchart.index("Executar OCR")
    organogram = _extract(synthetic_regression_pdf, 24).reading_text
    assert organogram.index("Dados e IA") < organogram.index("Operações") < organogram.index("Risco e Compliance")


def test_synthetic_stamp_fragments_follow_visual_order(synthetic_regression_pdf: Path):
    text = _extract(synthetic_regression_pdf, 21).reading_text
    assert text.index("APRO") < text.index("VADO")


def test_synthetic_text_and_merged_table_markdown_are_preserved(synthetic_regression_pdf: Path):
    text = _extract(synthetic_regression_pdf, 38).reading_text
    assert "internacionalizacao" in text
    assert "office" in text
    assert "affluent" in text
    table = StructuredTable(
        table_id="synthetic-merged",
        page_fragments=[TableFragment(page_index=0, bbox=BBox(10, 10, 200, 100), row_start=0, row_end=1)],
        row_count=2,
        column_count=3,
        cells=[
            TableCell(0, 0, 2, 1, BBox(10, 10, 70, 100), "vertical", [], 1.0),
            TableCell(0, 1, 1, 2, BBox(70, 10, 200, 55), "horizontal", [], 1.0),
            TableCell(1, 1, 1, 1, BBox(70, 55, 135, 100), "left", [], 1.0),
            TableCell(1, 2, 1, 1, BBox(135, 55, 200, 100), "right", [], 1.0),
        ],
        header_rows=(),
        method=TableMethod.STRICT_GRID,
        confidence=1.0,
    )
    # The renderer uses explicit HTML spans for merged table cells.
    from structured_pdf_text.renderers.markdown import _render_table
    markdown = _render_table(table)
    assert 'rowspan="2"' in markdown
    assert 'colspan="2"' in markdown
