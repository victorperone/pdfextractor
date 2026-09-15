from __future__ import annotations

from structured_pdf_text.document import (
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    LayoutRegion,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    StructuredDocument,
    StructuredPage,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import (
    render_markdown,
)


PAGE_BBOX = BBox(
    0.0,
    0.0,
    600.0,
    800.0,
)


def _page_diagnostics(
    page_index: int,
) -> PageDiagnostics:
    return PageDiagnostics(
        page_index=page_index,
        strategy=PageStrategy.OCR_CANDIDATE,
        reasons=[],
        native_chars=0,
        native_text_length=0,
        ocr_tokens_added=1,
    )


def _document(
    pages: list[StructuredPage],
    tables: list[StructuredTable],
) -> StructuredDocument:
    return StructuredDocument(
        pages=pages,
        tables=tables,
        raw_text="",
        reading_text="",
        metadata=DocumentMetadata(
            source_path="test.pdf",
            page_count=len(pages),
            pdfium_version=None,
        ),
        diagnostics=DocumentDiagnostics(
            status=ExtractionStatus.SUCCESS,
            page_count=len(pages),
            native_pages=0,
            mixed_pages=0,
            ocr_pages=len(pages),
            warnings=[],
            facts={
                "preserve_headers_footers": True,
            },
        ),
    )


def _table(
    *,
    table_id: str,
    page_index: int,
    text: str,
    bbox: BBox | None = None,
) -> StructuredTable:
    table_bbox = bbox or BBox(
        50.0,
        100.0,
        550.0,
        300.0,
    )

    return StructuredTable(
        table_id=table_id,
        page_fragments=[
            TableFragment(
                page_index=page_index,
                bbox=table_bbox,
                row_start=0,
                row_end=0,
            )
        ],
        cells=[
            TableCell(
                row=0,
                col=0,
                rowspan=1,
                colspan=1,
                bbox=table_bbox,
                text=text,
                tokens=[],
                confidence=1.0,
            )
        ],
        column_count=1,
        row_count=1,
        confidence=1.0,
        method=TableMethod.VISUAL_MODEL,
    )


def _empty_table(
    *,
    table_id: str,
    page_index: int,
    bbox: BBox,
) -> StructuredTable:
    return StructuredTable(
        table_id=table_id,
        page_fragments=[
            TableFragment(
                page_index=page_index,
                bbox=bbox,
                row_start=0,
                row_end=0,
            )
        ],
        cells=[],
        column_count=1,
        row_count=0,
        confidence=0.5,
        method=TableMethod.VISUAL_MODEL,
    )


def _text_line(
    text: str,
    bbox: BBox,
) -> TextLine:
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[],
        confidence=1.0,
        normalized_text=text,
    )

    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


def _table_region(
    *,
    region_id: str,
    text: str,
    bbox: BBox,
) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=RegionKind.TABLE,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=[
            _text_line(
                text,
                bbox,
            )
        ],
        ocr_tokens=[],
        quality=RegionQuality(
            decision=RegionDecision.OCR_REGION,
            reasons=[],
            confidence=1.0,
        ),
    )


def test_cross_page_table_is_rendered_on_each_physical_page():
    page_1_table = _table(
        table_id="table-p1",
        page_index=0,
        text="conteudo-pagina-1",
    )

    page_2_table = _table(
        table_id="table-p2",
        page_index=1,
        text="conteudo-pagina-2",
    )

    page_1 = StructuredPage(
        page_index=0,
        bbox=PAGE_BBOX,
        regions=[],
        tables=[page_1_table],
        raw_text="",
        reading_text="",
        diagnostics=_page_diagnostics(0),
    )

    page_2 = StructuredPage(
        page_index=1,
        bbox=PAGE_BBOX,
        regions=[],
        tables=[page_2_table],
        raw_text="",
        reading_text="",
        diagnostics=_page_diagnostics(1),
    )

    # Simulate the logical table produced by cross-page merging.
    logical_table = StructuredTable(
        table_id="logical-table",
        page_fragments=[
            TableFragment(
                page_index=0,
                bbox=BBox(
                    50.0,
                    100.0,
                    550.0,
                    300.0,
                ),
                row_start=0,
                row_end=0,
            ),
            TableFragment(
                page_index=1,
                bbox=BBox(
                    50.0,
                    100.0,
                    550.0,
                    300.0,
                ),
                row_start=1,
                row_end=1,
            ),
        ],
        cells=[
            TableCell(
                row=0,
                col=0,
                rowspan=1,
                colspan=1,
                bbox=None,
                text="conteudo-pagina-1",
                tokens=[],
                confidence=1.0,
            ),
            TableCell(
                row=1,
                col=0,
                rowspan=1,
                colspan=1,
                bbox=None,
                text="conteudo-pagina-2",
                tokens=[],
                confidence=1.0,
            ),
        ],
        column_count=1,
        row_count=2,
        confidence=1.0,
        method=TableMethod.VISUAL_MODEL,
        continues_to_next_page=False,
    )

    document = _document(
        [page_1, page_2],
        [logical_table],
    )

    markdown = render_markdown(document)

    page_1_markdown = markdown.split(
        "## Página 2",
        1,
    )[0]

    page_2_markdown = markdown.split(
        "## Página 2",
        1,
    )[1]

    assert "conteudo-pagina-1" in page_1_markdown
    assert "conteudo-pagina-2" in page_2_markdown


def test_table_region_falls_back_to_ocr_text_when_table_is_empty():
    table_bbox = BBox(
        50.0,
        100.0,
        550.0,
        300.0,
    )

    region = _table_region(
        region_id="table-region",
        text="texto OCR recuperado",
        bbox=table_bbox,
    )

    table = _empty_table(
        table_id="empty-table",
        page_index=0,
        bbox=table_bbox,
    )

    page = StructuredPage(
        page_index=0,
        bbox=PAGE_BBOX,
        regions=[region],
        tables=[table],
        raw_text="texto OCR recuperado",
        reading_text="",
        diagnostics=_page_diagnostics(0),
    )

    document = _document(
        [page],
        [table],
    )

    markdown = render_markdown(document)

    assert "texto OCR recuperado" in markdown


def test_valid_table_does_not_duplicate_table_region_text():
    table_bbox = BBox(
        50.0,
        100.0,
        550.0,
        300.0,
    )

    region = _table_region(
        region_id="table-region",
        text="VALOR-DA-CELULA",
        bbox=table_bbox,
    )

    table = _table(
        table_id="valid-table",
        page_index=0,
        text="VALOR-DA-CELULA",
        bbox=table_bbox,
    )

    page = StructuredPage(
        page_index=0,
        bbox=PAGE_BBOX,
        regions=[region],
        tables=[table],
        raw_text="VALOR-DA-CELULA",
        reading_text="",
        diagnostics=_page_diagnostics(0),
    )

    document = _document(
        [page],
        [table],
    )

    markdown = render_markdown(document)

    assert markdown.count("VALOR-DA-CELULA") == 1
