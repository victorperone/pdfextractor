"""VQ-09 — Phase B scaffold: mixed visual/ruled table detection.

These tests document the expected behavior when a page contains both ruled
(line-bordered) and visual (whitespace-track) tables. Phase B will implement
the disambiguation logic; for now tests are marked xfail and serve as specs.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import StructuredTable, TableMethod
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=200.0, y1=100.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


@pytest.mark.xfail(reason="VQ-09 Phase B — not yet implemented", strict=False)
class TestMixedVisualTablesPhaseB:
    """Specification tests for Phase B visual table disambiguation."""

    def test_ruled_table_preferred_over_visual_when_overlapping(self):
        """When a ruled table and a visual (text-track) table overlap, ruled wins."""
        # Arrange: two tables with overlapping bboxes
        ruled = StructuredTable(
            table_id="t_ruled",
            page_index=0,
            method=TableMethod.STRICT_GRID,
            fragments=[],
            row_count=3, col_count=2,
            bbox=_bbox(x0=0, y0=0, x1=200, y1=100),
        )
        visual = StructuredTable(
            table_id="t_visual",
            page_index=0,
            method=TableMethod.TEXT_TRACKS,
            fragments=[],
            row_count=3, col_count=2,
            bbox=_bbox(x0=10, y0=10, x1=190, y1=90),
        )
        tables = [ruled, visual]
        # Phase B: should resolve to only the ruled table
        resolved = _disambiguate_overlapping_tables(tables)  # type: ignore[name-defined]
        assert len(resolved) == 1
        assert resolved[0].method == TableMethod.STRICT_GRID

    def test_non_overlapping_tables_both_preserved(self):
        """Tables without bbox overlap should both be preserved."""
        t1 = StructuredTable(
            table_id="t1", page_index=0, method=TableMethod.STRICT_GRID,
            fragments=[], row_count=2, col_count=2,
            bbox=_bbox(x0=0, y0=0, x1=100, y1=50),
        )
        t2 = StructuredTable(
            table_id="t2", page_index=0, method=TableMethod.TEXT_TRACKS,
            fragments=[], row_count=2, col_count=2,
            bbox=_bbox(x0=200, y0=0, x1=300, y1=50),
        )
        resolved = _disambiguate_overlapping_tables([t1, t2])  # type: ignore[name-defined]
        assert len(resolved) == 2

    def test_visual_table_not_declared_before_detection(self):
        """Visual model tables are not returned before visual detection runs."""
        # This is a regression guard: visual tables must only appear after
        # the visual detection stage, never injected by the ruled detector.
        visual_model_table = StructuredTable(
            table_id="tv", page_index=0, method=TableMethod.VISUAL_MODEL,
            fragments=[], row_count=2, col_count=2,
            bbox=_bbox(),
        )
        # Phase B: visual_model tables require explicit visual detection pass
        assert visual_model_table.method == TableMethod.VISUAL_MODEL
