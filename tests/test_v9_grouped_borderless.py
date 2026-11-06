"""Tests for Fix-F-V9-04: grouped adjacent short-region borderless detection.

The root cause of missed tables in V9: Tesseract assigns each table row its own
paragraph block → separate LayoutRegion with 1–2 native_lines → _candidates()
requires ≥ 3 lines → returns [] → no table detected.

Fix: _group_short_regions() in detector.py groups y-adjacent, x-overlapping
short regions, and Pass 2 runs detect_borderless_table() on the composite.

GSR-01: Short regions that are vertically adjacent and horizontally overlapping
        must be grouped together.

GSR-02: Short regions with a large vertical gap must be placed in separate groups.

GSR-03: Short regions with no horizontal overlap must NOT be grouped.

GSR-04: A single short region (no neighbours) must return no groups.

GSR-05: Regions with ≥ 3 lines must be excluded from grouping (they are already
        handled by Pass 1 of detect_tables_native).
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import (
    LayoutRegion,
    RegionDecision,
    RegionKind,
    RegionQuality,
    TextLine,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.detector import _group_short_regions


def _line(x0: float, y0: float, x1: float, y1: float) -> TextLine:
    # _group_short_regions only checks len(region.native_lines) and region.bbox,
    # so a TextLine with empty tokens is sufficient for these unit tests.
    return TextLine([], BBox(x0, y0, x1, y1), None, WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(
    region_id: str,
    bbox: BBox,
    lines: list[TextLine],
    kind: RegionKind = RegionKind.TEXT,
) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=kind,
        bbox=bbox,
        layout_confidence=0.9,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


# ---------------------------------------------------------------------------
# GSR-01: adjacent short regions must be grouped
# ---------------------------------------------------------------------------

def test_gsr01_adjacent_short_regions_grouped():
    # Three rows of a table, each with 1 line; vertical gap = 2 pt, x-overlap = full
    r1 = _region("r1", BBox(50, 100, 400, 115), [_line(50, 100, 400, 115)])
    r2 = _region("r2", BBox(50, 117, 400, 132), [_line(50, 117, 400, 132)])
    r3 = _region("r3", BBox(50, 134, 400, 149), [_line(50, 134, 400, 149)])

    groups = _group_short_regions([r1, r2, r3])

    assert len(groups) == 1
    assert len(groups[0]) == 3
    assert {r.region_id for r in groups[0]} == {"r1", "r2", "r3"}


# ---------------------------------------------------------------------------
# GSR-02: large vertical gap must split into separate groups
# ---------------------------------------------------------------------------

def test_gsr02_large_gap_splits_groups():
    r1 = _region("r1", BBox(50, 100, 400, 115), [_line(50, 100, 400, 115)])
    r2 = _region("r2", BBox(50, 117, 400, 132), [_line(50, 117, 400, 132)])
    # 100 pt gap — far exceeds default y_gap_threshold=20
    r3 = _region("r3", BBox(50, 232, 400, 247), [_line(50, 232, 400, 247)])
    r4 = _region("r4", BBox(50, 249, 400, 264), [_line(50, 249, 400, 264)])

    groups = _group_short_regions([r1, r2, r3, r4])

    assert len(groups) == 2
    assert {r.region_id for r in groups[0]} == {"r1", "r2"}
    assert {r.region_id for r in groups[1]} == {"r3", "r4"}


# ---------------------------------------------------------------------------
# GSR-03: no horizontal overlap must not be grouped
# ---------------------------------------------------------------------------

def test_gsr03_no_x_overlap_not_grouped():
    # Two short regions at the same vertical position but non-overlapping x ranges
    r1 = _region("r1", BBox(0, 100, 150, 115), [_line(0, 100, 150, 115)])
    r2 = _region("r2", BBox(300, 117, 500, 132), [_line(300, 117, 500, 132)])

    groups = _group_short_regions([r1, r2])

    assert len(groups) == 0


# ---------------------------------------------------------------------------
# GSR-04: single short region produces no groups
# ---------------------------------------------------------------------------

def test_gsr04_single_region_no_group():
    r1 = _region("r1", BBox(50, 100, 400, 115), [_line(50, 100, 400, 115)])

    groups = _group_short_regions([r1])

    assert len(groups) == 0


# ---------------------------------------------------------------------------
# GSR-05: regions with ≥ 3 lines are excluded (handled by Pass 1)
# ---------------------------------------------------------------------------

def test_gsr05_long_regions_excluded():
    lines_many = [
        _line(50, 100 + i * 15, 400, 113 + i * 15) for i in range(4)
    ]
    r_long = _region("r_long", BBox(50, 100, 400, 162), lines_many)
    r_short = _region("r_short", BBox(50, 165, 400, 180), [_line(50, 165, 400, 180)])

    groups = _group_short_regions([r_long, r_short])

    # r_long has 4 lines → excluded; r_short has 1 line but no neighbour → no groups
    assert len(groups) == 0


# ---------------------------------------------------------------------------
# GSR-06: mixed — long region between two short groups must not bridge them
# ---------------------------------------------------------------------------

def test_gsr06_long_region_breaks_grouping():
    r1 = _region("r1", BBox(50, 100, 400, 115), [_line(50, 100, 400, 115)])
    r2 = _region("r2", BBox(50, 117, 400, 132), [_line(50, 117, 400, 132)])
    # Long region in the middle (3 lines) — not short, so not grouped
    long_lines = [_line(50, 140 + i * 15, 400, 153 + i * 15) for i in range(3)]
    r_long = _region("r_long", BBox(50, 140, 400, 198), long_lines)
    r3 = _region("r3", BBox(50, 200, 400, 215), [_line(50, 200, 400, 215)])
    r4 = _region("r4", BBox(50, 217, 400, 232), [_line(50, 217, 400, 232)])

    groups = _group_short_regions([r1, r2, r_long, r3, r4])

    # r_long excluded; gap between r2(y1=132) and r3(y0=200) = 68 pt > threshold=20
    assert len(groups) == 2
    assert {r.region_id for r in groups[0]} == {"r1", "r2"}
    assert {r.region_id for r in groups[1]} == {"r3", "r4"}
