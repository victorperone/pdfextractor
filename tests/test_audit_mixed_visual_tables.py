"""VQ-09 — Visual table detection must not be blocked by native tables.

Tests that _has_uncovered_image_area and the visual detection guard allow
hybrid pages (native-ruled + raster table) to produce two distinct tables.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.api import _has_uncovered_image_area
from structured_pdf_text.document import StructuredTable, TableCell, TableFragment, TableMethod
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=200.0, y1=100.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


class _FakeImage:
    """Stub representing a rendered page object."""
    def __init__(self, width: int = 595, height: int = 842):
        self.width = width
        self.height = height


class _FakeImageItem:
    def __init__(self, bbox: BBox):
        self.bbox = bbox


class _FakeObjects:
    def __init__(self, images: list):
        self.images = images
        self.rotation = 0


class _FakeNativePage:
    def __init__(self, page_bbox: BBox, image_bboxes: list[BBox]):
        self.bbox = page_bbox
        self.objects = _FakeObjects([_FakeImageItem(b) for b in image_bboxes])


def _native_table(page_index: int, bbox: BBox, table_id: str = "t0") -> StructuredTable:
    fragment = TableFragment(page_index=page_index, bbox=bbox, row_start=0, row_end=1)
    return StructuredTable(
        table_id=table_id,
        page_fragments=[fragment],
        cells=[TableCell(row=0, col=0, rowspan=1, colspan=1, bbox=bbox, text="X", tokens=[], confidence=1.0)],
        column_count=1,
        row_count=1,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
    )


class TestHasUncoveredImageArea:
    """VQ-09: _has_uncovered_image_area drives whether visual detection runs."""

    def test_no_images_returns_false(self):
        """Page without images should not trigger visual detection."""
        page = _FakeNativePage(_bbox(x1=595, y1=842), image_bboxes=[])
        assert not _has_uncovered_image_area(page, tables=[])

    def test_image_with_no_tables_returns_true(self):
        """Page with an image and no tables must trigger visual detection."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=400, x1=595, y1=842)],
        )
        assert _has_uncovered_image_area(page, tables=[])

    def test_image_not_covered_by_native_table_returns_true(self):
        """Image in bottom half, native table in top half — visual detection runs."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=450, x1=595, y1=842)],
        )
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert _has_uncovered_image_area(page, tables=[native])

    def test_image_fully_covered_by_native_table_returns_false(self):
        """Image almost entirely inside native table bbox — visual detection skips."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=50, y0=50, x1=545, y1=390)],
        )
        # Native table covers the same area
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert not _has_uncovered_image_area(page, tables=[native])

    def test_two_images_one_covered_one_not(self):
        """Two images: one inside native table, one outside — returns True."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[
                _bbox(x0=50, y0=50, x1=545, y1=390),   # inside native table
                _bbox(x0=0, y0=500, x1=595, y1=800),   # outside native table
            ],
        )
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert _has_uncovered_image_area(page, tables=[native])


class TestVQ09VisualDetectionGuard:
    """VQ-09: visual detection condition changed from 'not tables' to 'uncovered image'."""

    def test_condition_allows_hybrid_page(self):
        """On a hybrid page, _has_uncovered_image_area returns True even with tables."""
        # V5-P019 scenario: native table in top half, raster image in bottom half
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=450, x1=595, y1=820)],
        )
        native = _native_table(0, _bbox(x0=50, y0=50, x1=545, y1=400), table_id="native")
        result = _has_uncovered_image_area(page, tables=[native])
        assert result, "Hybrid page with native + raster image must trigger visual detection"

    def test_pure_native_page_no_image_skips_visual(self):
        """Page with native table but no images must not run visual detection."""
        page = _FakeNativePage(_bbox(x1=595, y1=842), image_bboxes=[])
        native = _native_table(0, _bbox(x0=50, y0=50, x1=545, y1=400))
        result = _has_uncovered_image_area(page, tables=[native])
        assert not result, "Page without images must not run visual detection"


@pytest.mark.xfail(reason="VQ-09 Phase B — full hybrid page integration requires visual engine", strict=False)
class TestMixedVisualTablesIntegration:
    """Integration tests requiring the real visual detection engine."""

    def test_v5_p019_produces_two_tables(self):
        """V5-P019: page must have native table + visual raster table as distinct objects."""
        # Full integration test requires the corpus PDF
        pytest.skip("Corpus PDF not available in CI")

    def test_visual_table_not_overlapping_native(self):
        """Detected visual grid that overlaps native table > 50% is discarded."""
        pytest.skip("Requires visual engine")
