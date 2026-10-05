"""Tests for table cell OCR infrastructure (§34, §35)."""
from __future__ import annotations

import numpy as np
import pytest

from structured_pdf_text.geometry import BBox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page_array(h: int = 200, w: int = 300, fill: int = 255) -> np.ndarray:
    return np.full((h, w, 3), fill, dtype=np.uint8)


def _make_cell(
    row: int = 0,
    col: int = 0,
    text: str = "",
    confidence: float = 0.5,
    bbox: "BBox | None" = None,
):
    from structured_pdf_text.document import TableCell, TextToken
    return TableCell(
        row=row, col=col, rowspan=1, colspan=1,
        bbox=bbox or BBox(10, 10, 50, 30),
        text=text,
        tokens=[],
        confidence=confidence,
    )


def _make_table(cells):
    from structured_pdf_text.document import StructuredTable, TableMethod
    return StructuredTable(
        table_id="t1",
        page_fragments=[],
        cells=cells,
        column_count=2,
        row_count=len(cells),
        confidence=0.8,
        method=TableMethod.STRICT_GRID,
    )


# ---------------------------------------------------------------------------
# crop_cell_image
# ---------------------------------------------------------------------------

class TestCropCellImage:
    """§34: crop_cell_image extracts correct region from page raster."""

    def test_basic_crop_returns_array(self):
        """Normal cell bbox within page → non-None array."""
        from structured_pdf_text.tables.cell_ocr import crop_cell_image

        page = _make_page_array(200, 300)
        page_bbox = BBox(0, 0, 300, 200)   # PDF page: (0,0)–(300,200) same scale
        cell_bbox = BBox(10, 10, 100, 50)

        crop = crop_cell_image(page, cell_bbox, page_bbox, pad_px=0)
        assert crop is not None
        assert crop.ndim == 3

    def test_crop_outside_page_returns_none(self):
        """Cell bbox completely outside the page → None (zero-sized crop clamped)."""
        from structured_pdf_text.tables.cell_ocr import crop_cell_image

        page = _make_page_array(50, 50)
        page_bbox = BBox(0, 0, 50, 50)
        # Cell is at (200,200)-(300,300) — far outside a 50×50 page
        cell_bbox = BBox(200, 200, 300, 300)

        crop = crop_cell_image(page, cell_bbox, page_bbox, pad_px=0)
        assert crop is None

    def test_crop_with_padding_within_bounds(self):
        """Padding stays within image bounds; does not raise."""
        from structured_pdf_text.tables.cell_ocr import crop_cell_image

        page = _make_page_array(100, 100)
        page_bbox = BBox(0, 0, 100, 100)
        cell_bbox = BBox(5, 5, 20, 20)

        crop = crop_cell_image(page, cell_bbox, page_bbox, pad_px=4)
        assert crop is not None

    def test_zero_size_page_returns_none(self):
        """Zero-sized page array → None."""
        from structured_pdf_text.tables.cell_ocr import crop_cell_image

        page = np.zeros((0, 0, 3), dtype=np.uint8)
        page_bbox = BBox(0, 0, 100, 100)
        cell_bbox = BBox(10, 10, 50, 50)

        crop = crop_cell_image(page, cell_bbox, page_bbox)
        assert crop is None


# ---------------------------------------------------------------------------
# _cell_needs_ocr
# ---------------------------------------------------------------------------

class TestCellNeedsOcr:
    """§34: _cell_needs_ocr detects low-confidence or empty cells."""

    def test_empty_text_always_needs_ocr(self):
        from structured_pdf_text.tables.cell_ocr import _cell_needs_ocr

        cell = _make_cell(text="", confidence=0.99)
        assert _cell_needs_ocr(cell) is True

    def test_high_confidence_text_does_not_need_ocr(self):
        from structured_pdf_text.tables.cell_ocr import _cell_needs_ocr

        cell = _make_cell(text="R$ 1.234,56", confidence=0.95)
        assert _cell_needs_ocr(cell) is False

    def test_low_confidence_non_empty_needs_ocr(self):
        from structured_pdf_text.tables.cell_ocr import _cell_needs_ocr

        cell = _make_cell(text="123", confidence=0.30)
        assert _cell_needs_ocr(cell) is True


# ---------------------------------------------------------------------------
# ocr_table_cells
# ---------------------------------------------------------------------------

class TestOcrTableCells:
    """§34: ocr_table_cells updates low-confidence cells using provided ocr_fn."""

    def test_empty_cell_gets_ocr_text(self):
        """An empty cell receives text from the OCR function."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cell = _make_cell(text="", confidence=0.0)
        table = _make_table([cell])
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        def fake_ocr(crop):
            return "R$ 1.234,56"

        updated = ocr_table_cells(table, page, page_bbox, fake_ocr)
        assert updated.cells[0].text == "R$ 1.234,56"

    def test_high_confidence_cell_not_touched(self):
        """A cell with text and high confidence is not sent to OCR."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cell = _make_cell(text="existing text", confidence=0.99)
        table = _make_table([cell])
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        call_count = 0
        def fake_ocr(crop):
            nonlocal call_count
            call_count += 1
            return "should not be called"

        ocr_table_cells(table, page, page_bbox, fake_ocr)
        assert call_count == 0

    def test_cell_without_bbox_not_processed(self):
        """Cell with bbox=None is skipped."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cell = _make_cell(text="", confidence=0.0, bbox=None)
        # Override bbox explicitly to None
        import dataclasses
        cell_no_bbox = dataclasses.replace(cell, bbox=None)
        table = _make_table([cell_no_bbox])
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        updated = ocr_table_cells(table, page, page_bbox, lambda c: "text")
        assert updated.cells[0].text == ""

    def test_returns_new_table_not_mutated(self):
        """Input table is not mutated; a new StructuredTable is returned."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cell = _make_cell(text="", confidence=0.0)
        table = _make_table([cell])
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        updated = ocr_table_cells(table, page, page_bbox, lambda c: "new")
        assert updated is not table

    def test_ocr_fn_failure_preserves_original_cell(self):
        """When ocr_fn raises, the original cell is kept."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cell = _make_cell(text="", confidence=0.0)
        table = _make_table([cell])
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        def bad_ocr(crop):
            raise RuntimeError("OCR failed")

        updated = ocr_table_cells(table, page, page_bbox, bad_ocr)
        assert updated.cells[0].text == ""

    def test_max_cells_limit(self):
        """When max_cells=1, only the first qualifying cell is processed."""
        from structured_pdf_text.tables.cell_ocr import ocr_table_cells

        cells = [_make_cell(text="", confidence=0.0) for _ in range(5)]
        table = _make_table(cells)
        page = _make_page_array()
        page_bbox = BBox(0, 0, 300, 200)

        processed = 0
        def counting_ocr(crop):
            nonlocal processed
            processed += 1
            return f"cell{processed}"

        ocr_table_cells(table, page, page_bbox, counting_ocr, max_cells=1)
        assert processed == 1
