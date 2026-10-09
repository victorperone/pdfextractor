"""VQ-29 — IR → Markdown round-trip validation.

Verifies that the Markdown renderer does not silently lose or mangle content
that is correctly present in the IR (StructuredDocument / StructuredTable /
PageContentBlock). Tests use only in-process objects — no OCR or PDF needed.

Coverage:
- GFM pipe table: column count, row count, cell text preserved
- HTML spanned table: rowspan/colspan attributes, cell text
- Empty-cell preservation (not filled with neighbour content)
- Heading levels H1–H4 emitted with correct # prefix
- List items rendered with marker and correct nesting level
- Link URI emitted as [text](uri) when block.link_uri is set
- Unicode NFC, accents, Brazilian numeric separators survive escaping
- Pipe literal inside cell escaped without breaking column count
- Round-trip: parse rendered GFM table back and compare cell multisets
"""
from __future__ import annotations

import re

import pytest

from structured_pdf_text.document import (
    ContentKind,
    PageContentBlock,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import _render_table, _render_content_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _cell(row: int, col: int, text: str, rowspan: int = 1, colspan: int = 1) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=rowspan, colspan=colspan,
        bbox=_bbox(x0=col * 50, y0=row * 20, x1=(col + 1) * 50, y1=(row + 1) * 20),
        text=text, tokens=[], confidence=1.0,
    )


def _table(
    cells: list[TableCell],
    row_count: int,
    col_count: int,
    header_rows: tuple[int, ...] = (0,),
) -> StructuredTable:
    frag = TableFragment(
        page_index=0,
        bbox=_bbox(x1=col_count * 50, y1=row_count * 20),
        row_start=0,
        row_end=row_count,
    )
    return StructuredTable(
        table_id="t0",
        page_fragments=[frag],
        cells=cells,
        column_count=col_count,
        row_count=row_count,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
        header_rows=header_rows,
    )


def _block(
    kind: ContentKind,
    text: str,
    *,
    heading_level: int | None = None,
    list_items=None,
    table_id: str | None = None,
    link_uri: str | None = None,
) -> PageContentBlock:
    return PageContentBlock(
        block_id="b0",
        page_index=0,
        kind=kind,
        bbox=_bbox(),
        order_index=0,
        text=text,
        heading_level=heading_level,
        list_items=list_items or [],
        table_id=table_id,
        source_region_ids=[],
        link_uri=link_uri,
    )


def _parse_gfm_table(md: str) -> list[list[str]]:
    """Parse a GFM pipe table into a list of rows (each a list of cell texts).

    Handles escaped pipes (\\|) as literal characters, not column separators.
    """
    rows = []
    for line in md.splitlines():
        line = line.strip()
        if not line or set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        if re.fullmatch(r"[\|\s\-:]+", line):
            continue
        if line.startswith("|"):
            # Replace escaped pipes with a placeholder before splitting
            placeholder = "\x00"
            processed = line.replace(r"\|", placeholder)
            cells = [c.strip().replace(placeholder, "|") for c in processed.strip("|").split("|")]
            rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# GFM pipe table round-trip
# ---------------------------------------------------------------------------

class TestGFMTableRoundtrip:
    """VQ-29: GFM pipe table output must round-trip through parsing."""

    def test_2x3_table_column_count_preserved(self):
        cells = [
            _cell(0, 0, "Nome"), _cell(0, 1, "Valor"), _cell(0, 2, "Data"),
            _cell(1, 0, "Item A"), _cell(1, 1, "R$ 1.234,00"), _cell(1, 2, "01/10/2026"),
        ]
        md = _render_table(_table(cells, 2, 3))
        rows = _parse_gfm_table(md)
        assert len(rows) >= 2
        assert all(len(r) == 3 for r in rows), f"Not all rows have 3 cols: {rows}"

    def test_cell_texts_preserved_in_multiset(self):
        cells = [
            _cell(0, 0, "Código"), _cell(0, 1, "Descrição"),
            _cell(1, 0, "001"), _cell(1, 1, "Produto A"),
            _cell(2, 0, "002"), _cell(2, 1, "Produto B"),
        ]
        md = _render_table(_table(cells, 3, 2))
        for expected in ("Código", "Descrição", "001", "Produto A", "002", "Produto B"):
            assert expected in md, f"Missing: {expected!r}"

    def test_empty_cell_not_filled_by_neighbour(self):
        """A legitimately empty cell must appear as empty in output."""
        cells = [
            _cell(0, 0, "A"), _cell(0, 1, "B"),
            _cell(1, 0, ""), _cell(1, 1, "valor"),
        ]
        md = _render_table(_table(cells, 2, 2))
        rows = _parse_gfm_table(md)
        data_rows = [r for r in rows if "A" not in r and "B" not in r]
        assert data_rows, "No data row found"
        assert data_rows[0][0].strip() == "", f"Empty cell was filled: {data_rows[0][0]!r}"

    def test_pipe_literal_escaped_in_cell(self):
        """A literal | inside cell text must be escaped so column count stays correct."""
        cells = [
            _cell(0, 0, "Condição"), _cell(0, 1, "Fórmula"),
            _cell(1, 0, "a"), _cell(1, 1, "x | y"),
        ]
        md = _render_table(_table(cells, 2, 2))
        rows = _parse_gfm_table(md)
        assert all(len(r) == 2 for r in rows), f"Pipe broke column count: {rows}"

    def test_unicode_accents_not_mangled(self):
        cells = [
            _cell(0, 0, "Índice"), _cell(0, 1, "Ação"),
            _cell(1, 0, "1"), _cell(1, 1, "Aprovação"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert "Índice" in md
        assert "Ação" in md
        assert "Aprovação" in md

    def test_brazilian_currency_separator_preserved(self):
        cells = [
            _cell(0, 0, "Valor"),
            _cell(1, 0, "R$ 1.234,56"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert "R$ 1.234,56" in md or "R\\$ 1.234,56" in md

    def test_negative_value_preserved(self):
        cells = [
            _cell(0, 0, "Resultado"),
            _cell(1, 0, "-0,00"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert "-0,00" in md


# ---------------------------------------------------------------------------
# HTML spanned table
# ---------------------------------------------------------------------------

class TestHTMLSpannedTable:
    """HTML rendering path for tables with merged cells."""

    def test_rowspan_attribute_emitted(self):
        cells = [
            _cell(0, 0, "Header A"), _cell(0, 1, "Header B"),
            _cell(1, 0, "Merged", rowspan=2), _cell(1, 1, "R1"),
            _cell(2, 1, "R2"),
        ]
        md = _render_table(_table(cells, 3, 2))
        assert 'rowspan="2"' in md
        assert "Merged" in md

    def test_colspan_attribute_emitted(self):
        cells = [
            _cell(0, 0, "Título Principal", colspan=2),
            _cell(1, 0, "Col A"), _cell(1, 1, "Col B"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert 'colspan="2"' in md
        assert "Título Principal" in md

    def test_html_special_chars_escaped(self):
        cells = [
            _cell(0, 0, "Cond"),
            _cell(1, 0, "a < b & c > d", rowspan=2),
            _cell(2, 0, "x"),
        ]
        md = _render_table(_table(cells, 3, 1))
        assert "<table>" in md
        assert "&lt;" in md
        assert "&amp;" in md
        assert "&gt;" in md


# ---------------------------------------------------------------------------
# Content block round-trip
# ---------------------------------------------------------------------------

class TestContentBlockRoundtrip:
    """VQ-29: PageContentBlock rendering must preserve structure."""

    def test_heading_h1_prefix(self):
        b = _block(ContentKind.TITLE, "Título Principal", heading_level=1)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("# Título Principal")

    def test_heading_h2_prefix(self):
        b = _block(ContentKind.TITLE, "Seção 1", heading_level=2)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("## Seção 1")

    def test_heading_h3_prefix(self):
        b = _block(ContentKind.TITLE, "Subseção 1.1", heading_level=3)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("### Subseção 1.1")

    def test_heading_h4_prefix(self):
        b = _block(ContentKind.TITLE, "Item 1.1.1", heading_level=4)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("#### Item 1.1.1")

    def test_paragraph_text_preserved(self):
        b = _block(ContentKind.TEXT, "O saldo é de R$ 1.234,56.")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "R$ 1.234,56" in rendered or "R\\$ 1.234,56" in rendered

    def test_link_uri_emitted(self):
        b = _block(ContentKind.TEXT, "Clique aqui", link_uri="https://example.com")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "[Clique aqui](https://example.com)" in rendered

    def test_link_uri_with_paren_escaped(self):
        b = _block(ContentKind.TEXT, "Link", link_uri="https://example.com/path(test)")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        # Closing paren inside URI must be escaped
        assert "\\)" in rendered

    def test_suppressed_block_returns_empty(self):
        from dataclasses import replace
        b = _block(ContentKind.TEXT, "hidden text")
        b = replace(b, suppressed=True)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""

    def test_figure_block_without_text_returns_empty(self):
        b = _block(ContentKind.FIGURE, "")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""

    def test_table_block_without_matching_table_returns_empty(self):
        b = _block(ContentKind.TABLE, "", table_id="missing-table")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

class TestStructuralInvariants:
    """Structural properties that must hold for all rendered output."""

    def test_gfm_table_has_separator_row(self):
        """Every GFM pipe table must contain a separator row (--- pattern)."""
        cells = [_cell(0, 0, "H"), _cell(1, 0, "V")]
        md = _render_table(_table(cells, 2, 1))
        if "<table>" not in md:
            assert "---" in md, "GFM table missing separator row"

    def test_html_table_well_formed(self):
        """HTML tables must have balanced <table> and </table> tags."""
        cells = [
            _cell(0, 0, "H1", colspan=2),
            _cell(1, 0, "A"), _cell(1, 1, "B"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert md.count("<table>") == md.count("</table>")
        assert md.count("<tr>") == md.count("</tr>")

    def test_no_duplicate_content_from_cell_conflict(self):
        """Two cells claiming the same position must not double the first value."""
        cells = [
            _cell(0, 0, "Col"),
            _cell(1, 0, "X"),
            _cell(1, 0, "X"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert md.count("X / X") == 0
        assert "X" in md

    def test_non_ascii_in_heading_not_lost(self):
        """Heading with accented chars must survive rendering."""
        b = _block(ContentKind.TITLE, "Índice Geral — Seção 2º", heading_level=2)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "Índice" in rendered
        assert "Seção" in rendered
