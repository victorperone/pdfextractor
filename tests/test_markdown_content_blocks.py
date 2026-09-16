"""Tests for the Markdown renderer (INV-06: renderer contains no geometric logic).

All content decisions must already be encoded in content_blocks; the renderer
only formats text and tables.
"""
from __future__ import annotations

import inspect

import pytest

from structured_pdf_text.document import (
    ContentKind,
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    PageContentBlock,
    PageDiagnostics,
    PageStrategy,
    RegionKind,
    StructuredDocument,
    StructuredPage,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import render_markdown, _render_table


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(
    kind: ContentKind,
    text: str = "",
    order_index: int = 0,
    table_id: str | None = None,
    heading_level: int | None = None,
    page_index: int = 0,
) -> PageContentBlock:
    return PageContentBlock(
        block_id=f"page-{page_index}:block-{order_index}",
        page_index=page_index,
        kind=kind,
        bbox=_bbox(),
        order_index=order_index,
        text=text,
        table_id=table_id,
        heading_level=heading_level,
    )


def _cell(row: int, col: int, text: str) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=1, colspan=1,
        bbox=None, text=text, tokens=[], confidence=1.0,
    )


def _table(
    table_id: str,
    cells: list[TableCell],
    col_count: int = 2,
    row_count: int = 2,
    page_index: int = 0,
) -> StructuredTable:
    return StructuredTable(
        table_id=table_id,
        page_fragments=[TableFragment(page_index=page_index, bbox=_bbox(), row_start=0, row_end=row_count - 1)],
        cells=cells,
        column_count=col_count,
        row_count=row_count,
        confidence=0.9,
        method=TableMethod.STRICT_GRID,
    )


def _page(
    content_blocks: list[PageContentBlock],
    tables: list[StructuredTable] | None = None,
    page_index: int = 0,
) -> StructuredPage:
    return StructuredPage(
        page_index=page_index,
        bbox=_bbox(0, 0, 595, 842),
        regions=[],
        tables=tables or [],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=page_index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
        content_blocks=content_blocks,
    )


def _document(pages: list[StructuredPage]) -> StructuredDocument:
    return StructuredDocument(
        pages=pages,
        tables=[t for p in pages for t in p.tables],
        raw_text="",
        reading_text="",
        metadata=DocumentMetadata(
            source_path="test",
            page_count=len(pages),
            pdfium_version=None,
        ),
        diagnostics=DocumentDiagnostics(
            status=ExtractionStatus.SUCCESS,
            page_count=len(pages),
            native_pages=len(pages),
            mixed_pages=0,
            ocr_pages=0,
        ),
    )


# ---------------------------------------------------------------------------
# INV-06: renderer has no geometric logic
# ---------------------------------------------------------------------------

def test_inv06_renderer_canonical_path_has_no_geometry() -> None:
    """_render_content_block must not use geometric logic.

    The legacy _render_page_legacy path may retain overlap_ratio for backwards
    compatibility. INV-06 applies only to the canonical block-based path.
    """
    import structured_pdf_text.renderers.markdown as md_module
    source = inspect.getsource(md_module._render_content_block)
    assert "BBox" not in source, "_render_content_block must not reference BBox"
    assert "overlap_ratio" not in source, "_render_content_block must not do geometric operations"
    assert "bbox.area" not in source, "_render_content_block must not compute areas"


# ---------------------------------------------------------------------------
# TEXT block
# ---------------------------------------------------------------------------

def test_text_block_rendered_verbatim() -> None:
    page = _page([_block(ContentKind.TEXT, "Hello world", order_index=0)])
    doc = _document([page])
    md = render_markdown(doc)
    assert "Hello world" in md


# ---------------------------------------------------------------------------
# TITLE block
# ---------------------------------------------------------------------------

def test_title_block_uses_heading_prefix() -> None:
    page = _page([_block(ContentKind.TITLE, "Capítulo 1", order_index=0, heading_level=2)])
    doc = _document([page])
    md = render_markdown(doc)
    assert "## Capítulo 1" in md


def test_title_default_h1_when_no_level() -> None:
    page = _page([_block(ContentKind.TITLE, "Título", order_index=0, heading_level=None)])
    doc = _document([page])
    md = render_markdown(doc)
    assert "# Título" in md


def test_title_max_heading_capped_at_6() -> None:
    page = _page([_block(ContentKind.TITLE, "Deep", order_index=0, heading_level=9)])
    doc = _document([page])
    md = render_markdown(doc)
    assert "######" in md
    assert "#######" not in md


# ---------------------------------------------------------------------------
# TABLE block
# ---------------------------------------------------------------------------

def test_table_block_renders_markdown_table() -> None:
    cells = [_cell(0, 0, "A"), _cell(0, 1, "B"), _cell(1, 0, "C"), _cell(1, 1, "D")]
    table = _table("tbl-1", cells, col_count=2, row_count=2)
    block = _block(ContentKind.TABLE, order_index=0, table_id="tbl-1")
    page = _page([block], tables=[table])
    doc = _document([page])
    md = render_markdown(doc)

    assert "| A | B |" in md
    assert "| C | D |" in md
    assert "---" in md


def test_table_block_without_matching_table_renders_empty() -> None:
    block = _block(ContentKind.TABLE, order_index=0, table_id="ghost-id")
    page = _page([block], tables=[])
    doc = _document([page])
    md = render_markdown(doc)
    assert "ghost-id" not in md


def test_pipe_char_escaped_in_cell() -> None:
    cells = [_cell(0, 0, "A|B"), _cell(0, 1, "C")]
    table = _table("t1", cells, col_count=2, row_count=1)
    block = _block(ContentKind.TABLE, order_index=0, table_id="t1")
    page = _page([block], tables=[table])
    doc = _document([page])
    md = render_markdown(doc)
    assert r"A\|B" in md


# ---------------------------------------------------------------------------
# HEADER / FOOTER blocks
# ---------------------------------------------------------------------------

def test_header_footer_suppressed_when_preserve_false() -> None:
    page = _page([
        _block(ContentKind.HEADER, "Top header", order_index=0),
        _block(ContentKind.FOOTER, "Bottom footer", order_index=1),
    ])
    doc = _document([page])
    doc.diagnostics.facts["preserve_headers_footers"] = False
    md = render_markdown(doc)
    assert "Top header" not in md
    assert "Bottom footer" not in md


def test_header_footer_preserved_by_default() -> None:
    page = _page([
        _block(ContentKind.HEADER, "Top header", order_index=0),
        _block(ContentKind.FOOTER, "Bottom footer", order_index=1),
    ])
    doc = _document([page])
    md = render_markdown(doc)
    assert "Top header" in md
    assert "Bottom footer" in md


# ---------------------------------------------------------------------------
# FIGURE block → skipped
# ---------------------------------------------------------------------------

def test_figure_block_without_text_produces_no_output() -> None:
    page = _page([
        _block(ContentKind.FIGURE, "", order_index=0),
        _block(ContentKind.TEXT, "After figure", order_index=1),
    ])
    doc = _document([page])
    md = render_markdown(doc)
    assert "After figure" in md


def test_figure_block_with_ocr_text_is_preserved_in_markdown() -> None:
    """OCR text on a FIGURE block must survive into the Markdown output.

    Until figures have a dedicated semantic representation, block.text carries
    the result of OCR performed on the image region. Silently dropping it would
    lose content that the user explicitly extracted.
    """
    ocr_text = "Texto recuperado por OCR"
    page = _page([
        _block(ContentKind.FIGURE, ocr_text, order_index=0),
        _block(ContentKind.TEXT, "Após figura", order_index=1),
    ])
    doc = _document([page])
    md = render_markdown(doc)
    assert ocr_text in md, f"OCR text from FIGURE block must appear in Markdown output"
    assert "Após figura" in md


# ---------------------------------------------------------------------------
# Block ordering
# ---------------------------------------------------------------------------

def test_blocks_rendered_by_order_index() -> None:
    page = _page([
        _block(ContentKind.TEXT, "Second", order_index=1),
        _block(ContentKind.TEXT, "First",  order_index=0),
    ])
    doc = _document([page])
    md = render_markdown(doc)
    first_pos  = md.index("First")
    second_pos = md.index("Second")
    assert first_pos < second_pos, "Blocks must render in order_index order"


# ---------------------------------------------------------------------------
# Legacy fallback (page without content_blocks)
# ---------------------------------------------------------------------------

def test_legacy_fallback_when_no_content_blocks() -> None:
    page = _page([], tables=[])
    page = StructuredPage(
        page_index=0,
        bbox=_bbox(0, 0, 595, 842),
        regions=[],
        tables=[],
        raw_text="",
        reading_text="Legacy text",
        diagnostics=PageDiagnostics(
            page_index=0,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
        content_blocks=[],  # no blocks
    )
    doc = _document([page])
    md = render_markdown(doc)
    assert "Legacy text" in md


# ---------------------------------------------------------------------------
# _render_table edge cases
# ---------------------------------------------------------------------------

def test_render_table_empty_no_output() -> None:
    table = _table("t1", cells=[], col_count=0, row_count=0)
    assert _render_table(table) == ""


def test_render_table_single_row() -> None:
    cells = [_cell(0, 0, "Only"), _cell(0, 1, "Row")]
    table = _table("t1", cells, col_count=2, row_count=1)
    rendered = _render_table(table)
    assert "| Only | Row |" in rendered
    assert "---" in rendered
