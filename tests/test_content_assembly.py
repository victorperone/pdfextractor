"""Tests for assemble/content.py — canonical page content assembly.

Uses only synthetic minimal data (INV-10: no reference PDF rules or coordinates).
Covers the invariants INV-01 through INV-08 and the unit cases from section 24.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from structured_pdf_text.assemble.content import (
    PageContentAssemblyResult,
    _table_has_renderable_content,
    assemble_page_content,
    prose_flow_lines_by_region,
)
from structured_pdf_text.assemble.page import assemble_page
from structured_pdf_text.document import (
    ComplexityReason,
    ContentKind,
    LayoutRegion,
    PageDiagnostics,
    PageStrategy,
    RegionKind,
    RegionDecision,
    RegionQuality,
    StructuredPage,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
    Baseline,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.reading_order import order_region_lines


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _token(text: str, bbox: BBox) -> TextToken:
    from structured_pdf_text.document import EvidenceRef, SourceKind
    return TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "char:0")],
        confidence=1.0,
        normalized_text=text,
    )


def _line(text: str, bbox: BBox | None = None) -> TextLine:
    b = bbox or _bbox()
    return TextLine(
        tokens=[_token(text, b)],
        bbox=b,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
    )


def _region(
    kind: RegionKind,
    bbox: BBox,
    lines: list[TextLine],
    region_id: str = "r1",
    heading_level: int | None = None,
) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=kind,
        bbox=bbox,
        layout_confidence=0.9,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
        heading_level=heading_level,
    )


def _cell(row: int, col: int, text: str, bbox: BBox | None = None) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=1, colspan=1,
        bbox=bbox,
        text=text,
        tokens=[],
        confidence=1.0,
    )


def _table(
    table_id: str,
    page_index: int,
    frag_bbox: BBox,
    cells: list[TableCell],
    col_count: int = 2,
    row_count: int = 1,
) -> StructuredTable:
    return StructuredTable(
        table_id=table_id,
        page_fragments=[TableFragment(page_index=page_index, bbox=frag_bbox, row_start=0, row_end=row_count - 1)],
        cells=cells,
        column_count=col_count,
        row_count=row_count,
        confidence=0.9,
        method=TableMethod.STRICT_GRID,
    )


def _page(
    regions: list[LayoutRegion],
    tables: list[StructuredTable],
    page_index: int = 0,
) -> StructuredPage:
    return StructuredPage(
        page_index=page_index,
        bbox=_bbox(0, 0, 595, 842),
        regions=regions,
        tables=tables,
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=page_index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )


# ---------------------------------------------------------------------------
# _table_has_renderable_content
# ---------------------------------------------------------------------------

def test_empty_cells_not_renderable() -> None:
    table = _table("t1", 0, _bbox(), cells=[], row_count=0, col_count=0)
    assert not _table_has_renderable_content(table)


def test_cells_all_blank_not_renderable() -> None:
    table = _table("t1", 0, _bbox(), cells=[_cell(0, 0, "   "), _cell(0, 1, "")])
    assert not _table_has_renderable_content(table)


def test_one_non_blank_cell_renderable() -> None:
    table = _table("t1", 0, _bbox(), cells=[_cell(0, 0, "A"), _cell(0, 1, "")])
    assert _table_has_renderable_content(table)


# ---------------------------------------------------------------------------
# INV-01: line outside valid table survives as text (section 24.1)
# ---------------------------------------------------------------------------

def test_inv01_line_outside_table_kept_as_text() -> None:
    """TABLE region wider than the table — external lines must survive."""
    page_bbox = _bbox(0, 0, 200, 300)
    table_bbox = _bbox(50, 100, 150, 200)   # centre of page
    line_above = _line("título", _bbox(0, 10, 200, 30))     # above table
    line_below = _line("nota",  _bbox(0, 220, 200, 240))    # below table
    line_inside = _line("célula", _bbox(60, 120, 140, 140)) # inside table

    cell_bbox = _bbox(60, 120, 140, 140)
    cells = [_cell(0, 0, "célula", cell_bbox), _cell(0, 1, "B", _bbox(142, 120, 148, 140))]
    table = _table("t1", 0, table_bbox, cells, col_count=2, row_count=1)

    region = _region(
        RegionKind.TABLE,
        page_bbox,  # TABLE region spans entire page
        [line_above, line_inside, line_below],
        region_id="big-region",
    )
    page = _page([region], [table])
    result = assemble_page_content(page)

    kinds = {b.kind for b in result.blocks}
    texts = " ".join(b.text for b in result.blocks if b.kind == ContentKind.TEXT)

    assert ContentKind.TEXT in kinds, "External lines must produce text blocks"
    assert "título" in texts or "nota" in texts, "External lines must survive"


# ---------------------------------------------------------------------------
# INV-02: line represented by table cannot appear twice (section 24.3)
# ---------------------------------------------------------------------------

def test_inv02_table_line_not_duplicated() -> None:
    """A line inside a valid table must not also appear as prose."""
    table_bbox = _bbox(0, 0, 100, 50)
    cell_bbox = _bbox(5, 5, 95, 45)
    cells = [_cell(0, 0, "dado", cell_bbox), _cell(0, 1, "valor", _bbox(50, 5, 95, 45))]
    table = _table("t1", 0, table_bbox, cells)

    line = _line("dado", _bbox(10, 10, 90, 40))
    region = _region(RegionKind.TABLE, table_bbox, [line])
    page = _page([region], [table])
    result = assemble_page_content(page)

    text_blocks = [b for b in result.blocks if b.kind == ContentKind.TEXT]
    table_blocks = [b for b in result.blocks if b.kind == ContentKind.TABLE]

    assert len(table_blocks) == 1, "Table must appear exactly once"
    for tb in text_blocks:
        assert "dado" not in tb.text, "Claimed line must not appear in prose"


# ---------------------------------------------------------------------------
# INV-03 & INV-04: physical table stays on physical page (section 24.4)
# ---------------------------------------------------------------------------

def test_inv03_physical_table_stays_on_physical_page() -> None:
    """Each page keeps only its own physical table fragment."""
    table_bbox_p0 = _bbox(0, 0, 100, 50)
    table_bbox_p1 = _bbox(0, 0, 100, 50)

    cells_p0 = [_cell(0, 0, "p0", table_bbox_p0)]
    cells_p1 = [_cell(0, 0, "p1", table_bbox_p1)]

    table_p0 = _table("t-page0", 0, table_bbox_p0, cells_p0)
    table_p1 = _table("t-page1", 1, table_bbox_p1, cells_p1)

    region0 = _region(RegionKind.TABLE, table_bbox_p0, [_line("p0", table_bbox_p0)])
    region1 = _region(RegionKind.TABLE, table_bbox_p1, [_line("p1", table_bbox_p1)])

    page0 = _page([region0], [table_p0], page_index=0)
    page1 = _page([region1], [table_p1], page_index=1)

    result0 = assemble_page_content(page0)
    result1 = assemble_page_content(page1)

    table_ids_p0 = {b.table_id for b in result0.blocks if b.kind == ContentKind.TABLE}
    table_ids_p1 = {b.table_id for b in result1.blocks if b.kind == ContentKind.TABLE}

    assert "t-page0" in table_ids_p0
    assert "t-page1" not in table_ids_p0
    assert "t-page1" in table_ids_p1
    assert "t-page0" not in table_ids_p1


# ---------------------------------------------------------------------------
# INV-05: invalid table must not cause text loss (section 24.2)
# ---------------------------------------------------------------------------

def test_inv05_empty_table_falls_back_to_text() -> None:
    """TABLE region with StructuredTable(cells=[]) must produce text fallback."""
    region_bbox = _bbox(0, 0, 200, 100)
    empty_table = _table("t-empty", 0, region_bbox, cells=[], col_count=0, row_count=0)
    line = _line("conteúdo importante", region_bbox)
    region = _region(RegionKind.TABLE, region_bbox, [line])
    page = _page([region], [empty_table])
    result = assemble_page_content(page)

    all_text = " ".join(b.text for b in result.blocks)
    table_blocks = [b for b in result.blocks if b.kind == ContentKind.TABLE]

    assert "conteúdo" in all_text, "Text must survive when table is empty"
    assert len(table_blocks) == 0, "Empty table must not produce a TABLE block"


# ---------------------------------------------------------------------------
# INV-08: each valid table appears at most once per page
# ---------------------------------------------------------------------------

def test_inv08_table_emitted_once() -> None:
    """A table intersecting multiple regions must only be emitted once."""
    table_bbox = _bbox(0, 50, 200, 150)
    cells = [_cell(0, 0, "X", _bbox(10, 60, 190, 140))]
    table = _table("t1", 0, table_bbox, cells)

    region1 = _region(RegionKind.TABLE, _bbox(0, 0, 200, 100), [], region_id="r1")
    region2 = _region(RegionKind.TABLE, _bbox(0, 80, 200, 200), [], region_id="r2")

    page = _page([region1, region2], [table])
    result = assemble_page_content(page)

    table_blocks = [b for b in result.blocks if b.kind == ContentKind.TABLE]
    assert len(table_blocks) == 1, "Table must appear exactly once even when two regions overlap it"


# ---------------------------------------------------------------------------
# INV-09: deterministic block order
# ---------------------------------------------------------------------------

def test_inv09_block_order_deterministic() -> None:
    """Same evidence must produce same block sequence on repeated calls."""
    lines = [
        _line("first",  _bbox(0, 0,  200, 20)),
        _line("second", _bbox(0, 25, 200, 45)),
    ]
    region = _region(RegionKind.TEXT, _bbox(0, 0, 200, 50), lines)
    page = _page([region], [])

    result1 = assemble_page_content(page)
    result2 = assemble_page_content(page)

    ids1 = [b.block_id for b in result1.blocks]
    ids2 = [b.block_id for b in result2.blocks]
    assert ids1 == ids2, "Block IDs must be deterministic"


# ---------------------------------------------------------------------------
# Section 24.5: title before table (order preserved)
# ---------------------------------------------------------------------------

def test_title_before_table_order() -> None:
    title_bbox  = _bbox(0,   0,  200,  30)
    table_bbox  = _bbox(0,  40,  200, 120)
    prose_bbox  = _bbox(0, 130,  200, 160)

    title_line = _line("Introdução", title_bbox)
    prose_line = _line("texto final", prose_bbox)

    cell = _cell(0, 0, "dado", _bbox(10, 50, 190, 110))
    table = _table("t1", 0, table_bbox, [cell])

    r_title = _region(RegionKind.TITLE, title_bbox, [title_line], region_id="r-title", heading_level=2)
    r_table = _region(RegionKind.TABLE, table_bbox, [], region_id="r-table")
    r_prose = _region(RegionKind.TEXT, prose_bbox, [prose_line], region_id="r-prose")

    page = _page([r_title, r_table, r_prose], [table])
    result = assemble_page_content(page)

    kinds_in_order = [b.kind for b in sorted(result.blocks, key=lambda b: b.order_index)]
    assert ContentKind.TITLE in kinds_in_order
    assert ContentKind.TABLE in kinds_in_order
    assert ContentKind.TEXT in kinds_in_order

    title_idx = next(i for i, k in enumerate(kinds_in_order) if k == ContentKind.TITLE)
    table_idx = next(i for i, k in enumerate(kinds_in_order) if k == ContentKind.TABLE)
    assert title_idx < table_idx, "TITLE must come before TABLE"


def test_orphan_table_is_anchored_between_title_and_prose() -> None:
    """A table outside all regions keeps its physical position in the stream."""
    title_bbox = _bbox(0, 0, 200, 30)
    table_bbox = _bbox(0, 50, 200, 110)
    prose_bbox = _bbox(0, 140, 200, 170)

    title = _region(
        RegionKind.TITLE,
        title_bbox,
        [_line("Título", title_bbox)],
        region_id="title",
        heading_level=1,
    )
    prose = _region(
        RegionKind.TEXT,
        prose_bbox,
        [_line("Texto depois", prose_bbox)],
        region_id="prose",
    )
    table = _table(
        "orphan-table",
        0,
        table_bbox,
        [_cell(0, 0, "Dado", _bbox(10, 60, 190, 100))],
        col_count=1,
    )

    result = assemble_page_content(_page([title, prose], [table]))
    kinds = [block.kind for block in result.blocks]

    assert kinds == [ContentKind.TITLE, ContentKind.TABLE, ContentKind.TEXT]
    assert result.orphan_tables == 1


# ---------------------------------------------------------------------------
# Section 24.6: TABLE region with no table at all → fallback
# ---------------------------------------------------------------------------

def test_table_region_no_physical_table_fallback() -> None:
    region_bbox = _bbox(0, 0, 200, 100)
    line = _line("orphan text", region_bbox)
    region = _region(RegionKind.TABLE, region_bbox, [line])
    page = _page([region], tables=[])
    result = assemble_page_content(page)

    all_text = " ".join(b.text for b in result.blocks)
    assert "orphan text" in all_text, "Text in TABLE region with no table must survive"
    assert result.table_fallbacks == 1


# ---------------------------------------------------------------------------
# Section 24.7: header/footer blocks are kept in content_blocks
# ---------------------------------------------------------------------------

def test_header_footer_blocks_preserved() -> None:
    header_line = _line("Cabeçalho", _bbox(0, 0, 200, 20))
    footer_line = _line("Rodapé",    _bbox(0, 820, 200, 840))

    r_header = _region(RegionKind.HEADER, _bbox(0, 0, 200, 20),   [header_line], region_id="r-hdr")
    r_footer = _region(RegionKind.FOOTER, _bbox(0, 820, 200, 840), [footer_line], region_id="r-ftr")

    page = _page([r_header, r_footer], [])
    result = assemble_page_content(page)

    kinds = {b.kind for b in result.blocks}
    assert ContentKind.HEADER in kinds, "HEADER block must be in content_blocks"
    assert ContentKind.FOOTER in kinds, "FOOTER block must be in content_blocks"


# ---------------------------------------------------------------------------
# Section 24.8 & 24.9: normalisation (NFC and control char removal)
# ---------------------------------------------------------------------------

def test_nfc_normalisation_applied() -> None:
    raw = "café"  # decomposed 'é'
    line = _line(raw, _bbox())
    region = _region(RegionKind.TEXT, _bbox(), [line])
    page = _page([region], [])
    result = assemble_page_content(page)

    text_blocks = [b for b in result.blocks if b.kind == ContentKind.TEXT]
    assert text_blocks, "Should produce at least one text block"
    composed = text_blocks[0].text
    assert "é" in composed or "café" in composed, "NFC should compose 'é'"


def test_control_char_removed() -> None:
    raw = "normal\x00text"
    line = _line(raw, _bbox())
    region = _region(RegionKind.TEXT, _bbox(), [line])
    page = _page([region], [])
    result = assemble_page_content(page)

    all_text = " ".join(b.text for b in result.blocks)
    assert "\x00" not in all_text, "Null control char must be removed"


# ---------------------------------------------------------------------------
# Diagnostics fields
# ---------------------------------------------------------------------------

def test_assembly_result_has_diagnostics() -> None:
    line = _line("text", _bbox())
    region = _region(RegionKind.TEXT, _bbox(), [line])
    page = _page([region], [])
    result = assemble_page_content(page)

    assert isinstance(result.claimed_table_lines, int)
    assert isinstance(result.table_fallbacks, int)
    assert isinstance(result.orphan_tables, int)
    assert result.assembly_ms >= 0.0


# ---------------------------------------------------------------------------
# FIGURE blocks with OCR text must survive in reading_text
# ---------------------------------------------------------------------------

def test_figure_with_ocr_text_appears_in_reading_text() -> None:
    """A FIGURE block carrying OCR text must not be filtered from reading_text.

    Without a semantic figure representation, block.text is the only way the
    OCR result survives. Filtering it silently loses content.
    """
    ocr_text = "Texto recuperado por OCR"
    figure_line = _line(ocr_text, _bbox(0, 50, 200, 80))
    figure_region = _region(RegionKind.FIGURE, _bbox(0, 50, 200, 80), [figure_line], region_id="fig1")
    text_line = _line("Parágrafo após figura", _bbox(0, 100, 200, 120))
    text_region = _region(RegionKind.TEXT, _bbox(0, 100, 200, 120), [text_line], region_id="txt1")

    page = _page([figure_region, text_region], [])
    result = assemble_page_content(page)

    assert ocr_text in result.reading_text, (
        f"OCR text from FIGURE block must appear in reading_text. Got: {result.reading_text!r}"
    )
    assert "Parágrafo após figura" in result.reading_text


def test_figure_without_text_not_in_reading_text() -> None:
    """A FIGURE block with empty text must not produce empty entries in reading_text."""
    empty_figure = _region(RegionKind.FIGURE, _bbox(0, 50, 200, 80), [], region_id="fig1")
    text_region = _region(RegionKind.TEXT, _bbox(0, 100, 200, 120), [_line("Texto", _bbox())], region_id="txt1")

    page = _page([empty_figure, text_region], [])
    result = assemble_page_content(page)

    assert result.reading_text.strip() == "Texto"


# ---------------------------------------------------------------------------
# Reading order preservation — _reindex_blocks must not sort globally
# ---------------------------------------------------------------------------

def test_reading_order_preserved_two_column_layout() -> None:
    """order_regions() canonical order must survive _reindex_blocks.

    Two side-by-side columns: left column starts at x0=0 (lines at y=10..30),
    right column starts at x0=300 (lines at y=10..30). A naive sort by (y0, x0)
    would interleave them as L1, R1, L2, R2. The reading order engine places the
    entire left column before the right column, so the correct order is:
    L1, L2, R1, R2.

    HEADER is placed before both columns regardless of geometry.
    """
    page_width = 595.0
    # Header spans full width — must come first.
    header_line = _line("Cabeçalho", _bbox(0, 0, page_width, 15))
    header_region = _region(
        RegionKind.HEADER,
        _bbox(0, 0, page_width, 15),
        [header_line],
        region_id="header",
    )

    # Left column: x0=0, two lines vertically stacked.
    left_line1 = _line("Esquerda-1", _bbox(0, 20, 280, 35))
    left_line2 = _line("Esquerda-2", _bbox(0, 40, 280, 55))
    left_region = _region(
        RegionKind.TEXT,
        _bbox(0, 20, 280, 55),
        [left_line1, left_line2],
        region_id="left",
    )

    # Right column: x0=300, same vertical band as left — a sort by (y0, x0)
    # would interleave left and right lines.
    right_line1 = _line("Direita-1", _bbox(300, 20, page_width, 35))
    right_line2 = _line("Direita-2", _bbox(300, 40, page_width, 55))
    right_region = _region(
        RegionKind.TEXT,
        _bbox(300, 20, page_width, 55),
        [right_line1, right_line2],
        region_id="right",
    )

    page = _page([header_region, left_region, right_region], [])
    result = assemble_page_content(page)

    texts = [b.text for b in result.blocks if b.text]

    # Header must appear before any body text.
    assert texts[0] == "Cabeçalho", f"Expected header first, got: {texts}"

    # Within body blocks, all left-column text must come before right-column text.
    left_indices = [i for i, t in enumerate(texts) if "Esquerda" in t]
    right_indices = [i for i, t in enumerate(texts) if "Direita" in t]
    assert left_indices, "Left column blocks missing"
    assert right_indices, "Right column blocks missing"
    assert max(left_indices) < min(right_indices), (
        f"Left column must precede right column entirely. "
        f"Got order: {texts}"
    )


def test_same_line_identity_in_overlapping_regions_is_rendered_once() -> None:
    shared_line = _line("ocorrência única", _bbox(0, 20, 100, 35))
    first = _region(
        RegionKind.TEXT,
        _bbox(0, 0, 120, 50),
        [shared_line],
        region_id="first",
    )
    second = _region(
        RegionKind.TEXT,
        _bbox(0, 0, 120, 50),
        [shared_line],
        region_id="second",
    )

    result = assemble_page_content(_page([first, second], []))

    assert result.reading_text.count("ocorrência única") == 1
    assert result.deduplicated_lines == 1
    assert result.reading_decision.deduplicated_lines == 1


def test_distinct_same_text_native_occurrences_are_preserved() -> None:
    first = replace(
        _line("texto repetido", _bbox(0, 20, 100, 35)),
        line_id="native:0:10:22",
    )
    second = replace(
        _line("texto repetido", _bbox(140, 20, 240, 35)),
        line_id="native:0:40:52",
    )
    region = _region(
        RegionKind.TEXT,
        _bbox(0, 0, 240, 50),
        [first, second],
    )

    result = assemble_page_content(_page([region], []))

    assert result.reading_text.count("texto repetido") == 2
    assert result.deduplicated_lines == 0


def test_assemble_page_keeps_column_diagnostics_without_table_filter() -> None:
    lines = [
        _line("A1", _bbox(0, 0, 80, 10)),
        _line("B1", _bbox(120, 0, 200, 10)),
        _line("A2", _bbox(0, 20, 80, 30)),
        _line("B2", _bbox(120, 20, 200, 30)),
        _line("A3", _bbox(0, 40, 80, 50)),
        _line("B3", _bbox(120, 40, 200, 50)),
    ]
    region = _region(RegionKind.TEXT, _bbox(0, 0, 200, 60), lines)
    diagnostics = PageDiagnostics(
        page_index=0,
        strategy=PageStrategy.NATIVE,
        reasons=[],
        native_chars=0,
        native_text_length=0,
    )

    page = assemble_page(0, _bbox(0, 0, 200, 60), [region], [], diagnostics, "")

    assert page.diagnostics.facts["reading_flow_mode"] == "MULTI_COLUMN"


def test_table_lines_do_not_drive_mixed_prose_to_form_flow() -> None:
    """Table-owned parallel lines must not determine the prose hypothesis."""
    prose_lines: list[TextLine] = []
    table_lines: list[TextLine] = []
    cells: list[TableCell] = []
    for index in range(6):
        prose_lines.extend(
            (
                _line(f"A{index}", _bbox(0, index * 30, 50, index * 30 + 10)),
                _line(f"B{index}", _bbox(90, index * 30, 135, index * 30 + 10)),
            )
        )
    for index in range(20):
        y0 = 180 + index * 8
        left_bbox = _bbox(0, y0, 25, y0 + 10)
        right_bbox = _bbox(35, y0, 60, y0 + 10)
        table_lines.extend((_line(f"K{index}", left_bbox), _line(f"V{index}", right_bbox)))
        cells.extend(
            (
                _cell(index, 0, "K", left_bbox),
                _cell(index, 1, "V", right_bbox),
            )
        )

    region = _region(
        RegionKind.TEXT,
        _bbox(0, 0, 220, 500),
        [*prose_lines, *table_lines],
        region_id="mixed",
    )
    table = _table(
        "mixed-table",
        0,
        _bbox(0, 180, 60, 340),
        cells,
        col_count=2,
        row_count=20,
    )
    flow_lines = prose_flow_lines_by_region([region], [table], 0)

    _, without_table_ownership = order_region_lines([region])
    _, with_table_ownership = order_region_lines([region], flow_lines)

    assert without_table_ownership.flow_mode == "FORM"
    assert with_table_ownership.flow_mode == "MULTI_COLUMN"
    assert with_table_ownership.line_preservation_ok
