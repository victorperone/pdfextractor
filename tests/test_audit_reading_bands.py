"""VQ-17 — Phase C scaffold: reading-band detection for multi-column layouts.

Documents expected behavior for detecting and ordering reading bands in
multi-column documents. Phase C will implement the full band algorithm;
tests are xfail as specs.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import LayoutRegion, RegionKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.reading_order import _order_regions_by_reading_band


def _region(kind: RegionKind, x0: float, y0: float, x1: float, y1: float) -> LayoutRegion:
    return LayoutRegion(
        region_id=f"r_{int(x0)}_{int(y0)}",
        kind=kind,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        layout_confidence=1.0,
        native_lines=[],
        ocr_tokens=[],
        quality=None,
    )


class TestReadingBandsUnit:
    """Basic layout assertions that run today."""

    def test_region_bbox_valid(self):
        r = _region(RegionKind.TEXT, 0, 0, 100, 50)
        assert r.bbox.x1 > r.bbox.x0
        assert r.bbox.y1 > r.bbox.y0

    def test_two_column_regions_separated_horizontally(self):
        col1 = _region(RegionKind.TEXT, 0, 0, 250, 700)
        col2 = _region(RegionKind.TEXT, 300, 0, 550, 700)
        assert col2.bbox.x0 > col1.bbox.x1 - 50  # gap >= -50 (they don't heavily overlap)


class TestReadingBandsPhasec:
    """Specification tests for multi-column reading-band ordering."""

    def test_two_column_left_before_right(self):
        """In a two-column layout, left column lines come before right column lines."""
        col1_regions = [
            _region(RegionKind.TEXT, 0, 0, 250, 350),
            _region(RegionKind.TEXT, 0, 360, 250, 700),
        ]
        col2_regions = [
            _region(RegionKind.TEXT, 300, 0, 550, 350),
            _region(RegionKind.TEXT, 300, 360, 550, 700),
        ]
        all_regions = col1_regions + col2_regions
        ordered = _order_regions_by_reading_band(all_regions)
        col1_indices = [ordered.index(r) for r in col1_regions]
        col2_indices = [ordered.index(r) for r in col2_regions]
        assert max(col1_indices) < min(col2_indices), "All col1 before all col2"

    def test_spanning_header_comes_first(self):
        """A full-width header region precedes both columns."""
        header = _region(RegionKind.TITLE, 0, 0, 550, 60)
        col1 = _region(RegionKind.TEXT, 0, 80, 250, 700)
        col2 = _region(RegionKind.TEXT, 300, 80, 550, 700)
        ordered = _order_regions_by_reading_band([col1, col2, header])
        assert ordered.index(header) == 0

    def test_footer_comes_last(self):
        """Footer/page-number regions come after all content."""
        footer = _region(RegionKind.FOOTER, 0, 680, 550, 700)
        body = _region(RegionKind.TEXT, 0, 0, 550, 650)
        ordered = _order_regions_by_reading_band([footer, body])
        assert ordered[-1] == footer
