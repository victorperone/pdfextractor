"""Tests for tables/geometry.py and the geometry/detector separation contract.

Invariants verified
-------------------
GEO-01: A decorative horizontal rule that spans the full page width but has no
        companion vertical segments must NOT produce a TableGeometryCandidate.
        (Decorative lines must not contaminate region candidates.)

DET-01: _detect_strict_grid() must accept a table that has complete horizontal
        rules but only partial vertical separators. This case passes the detector
        but would fail geometry.py's stricter H+V requirement, which is the
        documented reason the two modules remain independent.
"""
from __future__ import annotations

from structured_pdf_text.document import (
    AnnotationEvidence,
    NativeCharacter,
    NativeObjectEvidence,
    NativePageEvidence,
    PathEvidence,
    StructureTreeEvidence,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.geometry import detect_path_table_candidates
from structured_pdf_text.tables.detector import _detect_strict_grid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _page_bbox(w: float = 595.0, h: float = 842.0) -> BBox:
    return BBox(x0=0, y0=0, x1=w, y1=h)


def _path(x0: float, y0: float, x1: float, y1: float, page_index: int = 0) -> PathEvidence:
    return PathEvidence(
        page_index=page_index,
        object_index=0,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
    )


def _page(paths: list[PathEvidence], page_bbox: BBox | None = None) -> NativePageEvidence:
    pb = page_bbox or _page_bbox()
    objects = NativeObjectEvidence(
        images=(),
        paths=tuple(paths),
        annotations=(),
        structure_tree=StructureTreeEvidence(available=False),
        page_bbox=pb,
        crop_bbox=pb,
        rotation=0,
    )
    return NativePageEvidence(
        page_index=0,
        bbox=pb,
        characters=(),
        objects=objects,
        extracted_text="",
    )


# ---------------------------------------------------------------------------
# GEO-01: decorative full-width horizontal rule must not become a candidate
# ---------------------------------------------------------------------------

def test_geo01_decorative_horizontal_rule_not_a_candidate() -> None:
    """A single full-width horizontal line has no vertical companions.

    geometry.py requires ≥2 H segments AND ≥2 V segments in the same
    connected component. A decorative rule must be discarded, not promoted
    to a TableGeometryCandidate that would incorrectly mark a region as TABLE.
    """
    page = _page([
        # One full-width horizontal rule (height≈0, width≈page width).
        _path(0, 100, 595, 101),
    ])
    candidates = detect_path_table_candidates(page)
    assert candidates == [], (
        "A single horizontal decorative rule must not produce a table candidate"
    )


def test_geo01_two_decorative_rules_without_verticals_not_candidates() -> None:
    """Two horizontal rules but no vertical segments → still no candidate."""
    page = _page([
        _path(0, 100, 595, 101),
        _path(0, 200, 595, 201),
    ])
    candidates = detect_path_table_candidates(page)
    assert candidates == [], (
        "Horizontal rules without companion verticals must not produce a candidate"
    )


# ---------------------------------------------------------------------------
# DET-01: _detect_strict_grid accepts tables with partial vertical separators
# ---------------------------------------------------------------------------

def test_det01_strict_grid_accepts_partial_verticals() -> None:
    """A table with 3 horizontal rules but only 2 of the 3 expected vertical
    separators must still be detected by _detect_strict_grid().

    This is the documented justification for keeping detector.py independent
    from geometry.py: the stricter H+V connected-component check in geometry.py
    might reject such a table at the candidate-localisation stage, but the
    detector should accept it at the grid-reconstruction stage.
    """
    page_b = _page_bbox(200, 200)

    # 3 horizontal rules spanning the full table width.
    h1 = _path(10, 20, 190, 21)   # top border
    h2 = _path(10, 70, 190, 71)   # middle row separator
    h3 = _path(10, 120, 190, 121) # bottom border

    # Only 2 vertical separators (left and right borders) — middle column
    # separator is absent or incomplete.
    v1 = _path(10,  20, 11,  121)  # left border
    v2 = _path(190, 20, 191, 121)  # right border

    page = _page([h1, h2, h3, v1, v2], page_bbox=page_b)

    # The search region is the table bounding box.
    region_bbox = BBox(x0=10, y0=20, x1=191, y1=121)
    grid = _detect_strict_grid(page, region_bbox)

    assert grid is not None, (
        "_detect_strict_grid must detect a table with partial vertical separators"
    )
    # With 3 H edges (y≈20, y≈70, y≈120) there should be 2 rows.
    assert len(grid.y_edges) >= 3, (
        f"Expected ≥3 y-edges (top, mid, bottom), got {grid.y_edges}"
    )


def test_det02_strict_grid_accepts_repeated_cell_rectangles() -> None:
    """Cell rectangles must provide a grid even without thin line paths."""
    page_b = _page_bbox(240, 180)
    paths = [
        _path(x0, y0, x1, y1)
        for y0, y1 in ((20, 60), (60, 100))
        for x0, x1 in ((20, 100), (100, 220))
    ]
    page = _page(paths, page_bbox=page_b)

    grid = _detect_strict_grid(page, page_b)

    assert grid is not None
    assert len(grid.x_edges) == 3
    assert len(grid.y_edges) == 3
