"""VQ-10 / VQ-19 / VQ-20 — Markdown renderer correctness.

VQ-10: Duplicate-cell conflict must be preserved as "A / B", not silently overwritten.
VQ-19: Merged cells (rowspan/colspan) must render as HTML table when present.
VQ-20: Table with only a header row renders without crash.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import (
    StructuredTable, TableCell, TableFragment, TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import _render_table


def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _cell(row: int, col: int, text: str, rowspan: int = 1, colspan: int = 1) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=rowspan, colspan=colspan,
        bbox=_bbox(x0=col * 50, y0=row * 20, x1=(col + 1) * 50, y1=(row + 1) * 20),
        text=text, tokens=[], confidence=1.0,
    )


def _table(cells: list[TableCell], row_count: int, col_count: int, header_rows: tuple[int, ...] = (0,)) -> StructuredTable:
    fragment = TableFragment(page_index=0, bbox=_bbox(x1=col_count * 50, y1=row_count * 20), row_start=0, row_end=row_count)
    return StructuredTable(
        table_id="t0",
        page_fragments=[fragment],
        cells=cells,
        column_count=col_count,
        row_count=row_count,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
        header_rows=header_rows,
    )


class TestVQ10CellConflict:
    """VQ-10: when two sources claim the same cell, preserve both values."""

    def test_conflict_rendered_as_slash_join(self):
        """Two cells at same (row, col) must appear as 'A / B'."""
        # Build two cells that map to (1, 0)
        cells = [
            _cell(0, 0, "Header"),
            _cell(0, 1, "Value"),
            _cell(1, 0, "001"),
            _cell(1, 0, "001-duplicate"),  # second cell at same position
            _cell(1, 1, "R$ 1.234,00"),
        ]
        table = _table(cells, row_count=2, col_count=2)
        md = _render_table(table)
        # Either keep both or at minimum keep the first
        assert "001" in md
        assert "R$ 1.234,00" in md

    def test_conflict_appends_second_value(self):
        """Second unique value at same cell position must be appended with ' / '."""
        cells = [
            _cell(0, 0, "Col"),
            _cell(1, 0, "Valor A"),
            _cell(1, 0, "Valor B"),
        ]
        table = _table(cells, row_count=2, col_count=1)
        md = _render_table(table)
        assert "Valor A / Valor B" in md

    def test_duplicate_same_value_not_doubled(self):
        """Two cells with identical text at same position must not produce 'X / X'."""
        cells = [
            _cell(0, 0, "Col"),
            _cell(1, 0, "X"),
            _cell(1, 0, "X"),  # exact duplicate
        ]
        table = _table(cells, row_count=2, col_count=1)
        md = _render_table(table)
        assert "X / X" not in md
        assert "X" in md


class TestVQ19MergedCells:
    """VQ-19: tables with merged cells must use HTML rendering path."""

    def test_colspan_triggers_html(self):
        """colspan > 1 triggers HTML table output."""
        cells = [
            _cell(0, 0, "Título Principal", rowspan=1, colspan=2),
            _cell(1, 0, "Col A"),
            _cell(1, 1, "Col B"),
            _cell(2, 0, "1"),
            _cell(2, 1, "2"),
        ]
        table = _table(cells, row_count=3, col_count=2)
        md = _render_table(table)
        assert "<table>" in md
        assert "Título Principal" in md

    def test_rowspan_triggers_html(self):
        """rowspan > 1 triggers HTML table output."""
        cells = [
            _cell(0, 0, "Header"),
            _cell(0, 1, "Header2"),
            _cell(1, 0, "Merged", rowspan=2, colspan=1),
            _cell(1, 1, "Row 1"),
            _cell(2, 1, "Row 2"),
        ]
        table = _table(cells, row_count=3, col_count=2)
        md = _render_table(table)
        assert "<table>" in md

    def test_simple_table_uses_pipe_syntax(self):
        """Tables without merged cells use GFM pipe syntax."""
        cells = [
            _cell(0, 0, "A"),
            _cell(0, 1, "B"),
            _cell(1, 0, "1"),
            _cell(1, 1, "2"),
        ]
        table = _table(cells, row_count=2, col_count=2)
        md = _render_table(table)
        assert "|" in md
        assert "<table>" not in md


class TestVQ20SingleRowHeader:
    """VQ-20: header-only tables must not crash the renderer."""

    def test_single_row_renders_without_crash(self):
        cells = [_cell(0, 0, "Coluna 1"), _cell(0, 1, "Coluna 2")]
        table = _table(cells, row_count=1, col_count=2)
        md = _render_table(table)
        assert "Coluna 1" in md

    def test_empty_table_graceful(self):
        table = _table(cells=[], row_count=0, col_count=0)
        md = _render_table(table)
        assert isinstance(md, str)
        assert md == ""

    def test_header_only_no_body_rows(self):
        """A table with only a header row renders with separator but no data rows."""
        cells = [_cell(0, 0, "H1"), _cell(0, 1, "H2")]
        table = _table(cells, row_count=1, col_count=2)
        md = _render_table(table)
        # Must contain header values and separator line
        assert "H1" in md
        assert "---" in md
