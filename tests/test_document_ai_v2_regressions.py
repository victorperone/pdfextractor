from __future__ import annotations

from pathlib import Path

import pytest

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig
from structured_pdf_text.renderers.markdown import render_markdown


CORPUS = Path(__file__).parents[1] / "corpus" / "Document_AI_V2.pdf"


def _extract(
    page: int,
    *,
    tables: bool = False,
    experimental_occlusion_redaction: bool = False,
):
    if not CORPUS.exists():
        pytest.skip("Document_AI_V2.pdf is not present")
    return PdfTextExtractor(
        ExtractorConfig(
            mode=ExtractionMode.NATIVE,
            page_indices=(page - 1,),
            enable_tables=tables,
            enable_experimental_occlusion_redaction=(
                experimental_occlusion_redaction
            ),
        )
    ).extract(CORPUS).pages[0]


def test_document_ai_v2_experimental_redaction_hides_occluded_text():
    page = _extract(
        39,
        experimental_occlusion_redaction=True,
    )

    assert "SEGREDO-ALFA-991" not in page.reading_text
    assert "000123-9" not in page.reading_text
    assert "SEGREDO-ALFA-991" not in page.raw_text
    assert page.diagnostics.facts[
        "experimental_occlusion_redaction_enabled"
    ] is True
    assert page.diagnostics.facts[
        "textpage_reconciliation_disabled_for_redaction"
    ] is True
    assert page.diagnostics.facts["redacted_native_characters"] > 0


def test_document_ai_v2_columns_and_scripts_keep_visual_order():
    columns = _extract(9)
    text = columns.reading_text
    assert text.index("HZ-901") < text.index("HZ-902") < text.index("HZ-903")

    scripts = _extract(7).reading_text
    assert "H₂O" in scripts
    assert "x²" in scripts


def test_document_ai_v2_diagram_and_organogram_order():
    flowchart = _extract(23).reading_text
    assert flowchart.index("Receber PDF") < flowchart.index("Tem texto nativo?")
    assert flowchart.index("sim") < flowchart.index("Extrair nativamente")
    assert flowchart.index("não") < flowchart.index("Executar OCR")

    organogram = _extract(24).reading_text
    assert organogram.index("Dados e IA") < organogram.index("Operações") < organogram.index("Risco e Compliance")


def test_document_ai_v2_stamp_fragments_follow_visual_order():
    page = _extract(21)
    assert page.reading_text.index("APRO") < page.reading_text.index("VADO")


def test_document_ai_v2_native_text_reconciles_hyphens_ligatures_and_tables():
    page = _extract(38)
    assert "internacionalização" in page.reading_text
    assert "office" in page.reading_text
    assert "affluent" in page.reading_text

    table_page = _extract(18, tables=True)
    table = table_page.tables[0]
    assert table.cells[0].rowspan == 2
    assert table.cells[1].colspan == 2
    markdown = render_markdown(
        PdfTextExtractor(
            ExtractorConfig(mode=ExtractionMode.NATIVE, page_indices=(17,), enable_tables=True)
        ).extract(CORPUS)
    )
    assert 'rowspan="2"' in markdown
    assert 'colspan="2"' in markdown
