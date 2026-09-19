"""Tests for layout/assign.py — line-to-region assignment with tie-breaking."""
from __future__ import annotations

import pytest

from structured_pdf_text.document import (
    LayoutRegion,
    RegionDecision,
    RegionKind,
    RegionQuality,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.assign import (
    _MIN_LINE_REGION_COVERAGE,
    assign_lines_to_regions,
)
from structured_pdf_text.layout.regions import _coalesce_nested_regions


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _line(text: str, bbox: BBox) -> TextLine:
    from structured_pdf_text.document import EvidenceRef, SourceKind
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "char:0")],
        confidence=1.0,
        normalized_text=text,
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
    )


def _region(
    kind: RegionKind,
    bbox: BBox,
    region_id: str = "r1",
    confidence: float = 0.9,
) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=kind,
        bbox=bbox,
        layout_confidence=confidence,
        native_lines=[],
        ocr_tokens=[],
        quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
    )


# ---------------------------------------------------------------------------
# Basic assignment
# ---------------------------------------------------------------------------

def test_line_assigned_to_overlapping_region() -> None:
    line = _line("text", _bbox(10, 10, 90, 30))
    region = _region(RegionKind.TEXT, _bbox(0, 0, 100, 50))
    result = assign_lines_to_regions([line], [region])
    assert line in result[0].native_lines


def test_line_with_no_coverage_not_assigned() -> None:
    line = _line("text", _bbox(200, 200, 300, 220))
    region = _region(RegionKind.TEXT, _bbox(0, 0, 100, 50))
    result = assign_lines_to_regions([line], [region])
    assert line not in result[0].native_lines


def test_line_below_minimum_coverage_not_assigned() -> None:
    # Line barely overlaps region — less than _MIN_LINE_REGION_COVERAGE
    line = _line("text", _bbox(0, 0, 100, 20))
    small_region = _region(RegionKind.TEXT, _bbox(95, 0, 200, 20))
    result = assign_lines_to_regions([line], [small_region])
    assert line not in result[0].native_lines


# ---------------------------------------------------------------------------
# Tie-breaking by specificity (smaller region wins)
# ---------------------------------------------------------------------------

def test_smaller_region_wins_on_equal_coverage() -> None:
    """Line fully inside both a big and a small region — small wins."""
    line = _line("text", _bbox(40, 40, 60, 60))
    big = _region(RegionKind.TEXT, _bbox(0, 0, 200, 200), region_id="big", confidence=0.7)
    small = _region(RegionKind.TABLE, _bbox(35, 35, 65, 65), region_id="small", confidence=0.7)
    result = assign_lines_to_regions([line], [big, small])
    assigned_ids = [r.region_id for r in result if line in r.native_lines]
    assert assigned_ids == ["small"], f"Expected smaller region, got {assigned_ids}"


def test_higher_coverage_beats_smaller_area() -> None:
    """Region with clearly better coverage wins even if it's bigger."""
    line = _line("text", _bbox(10, 10, 90, 30))
    big_good = _region(RegionKind.TEXT, _bbox(0, 0, 100, 40), region_id="big-good")
    small_bad = _region(RegionKind.TEXT, _bbox(80, 0, 100, 40), region_id="small-bad")
    result = assign_lines_to_regions([line], [big_good, small_bad])
    assigned_ids = [r.region_id for r in result if line in r.native_lines]
    assert assigned_ids == ["big-good"]


# ---------------------------------------------------------------------------
# Multiple lines across regions
# ---------------------------------------------------------------------------

def test_multiple_lines_distributed() -> None:
    top_line = _line("top", _bbox(0, 0, 100, 20))
    bot_line = _line("bottom", _bbox(0, 30, 100, 50))
    top_region = _region(RegionKind.TEXT, _bbox(0, 0, 100, 25), region_id="top")
    bot_region = _region(RegionKind.TEXT, _bbox(0, 25, 100, 55), region_id="bot")
    result = assign_lines_to_regions([top_line, bot_line], [top_region, bot_region])
    top_r = next(r for r in result if r.region_id == "top")
    bot_r = next(r for r in result if r.region_id == "bot")
    assert top_line in top_r.native_lines
    assert bot_line in bot_r.native_lines


# ---------------------------------------------------------------------------
# Idempotency — assign clears previous lines
# ---------------------------------------------------------------------------

def test_previous_lines_cleared_before_assignment() -> None:
    line = _line("text", _bbox(10, 10, 90, 30))
    region = _region(RegionKind.TEXT, _bbox(0, 0, 100, 50))
    region.native_lines.append(_line("stale", _bbox(0, 0, 50, 10)))
    assign_lines_to_regions([line], [region])
    assert not any(l.tokens[0].text == "stale" for l in region.native_lines)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_lines_returns_regions_unchanged_count() -> None:
    region = _region(RegionKind.TEXT, _bbox(0, 0, 100, 50))
    result = assign_lines_to_regions([], [region])
    assert len(result) == 1
    assert result[0].native_lines == []


def test_no_regions_returns_empty() -> None:
    line = _line("text", _bbox(10, 10, 90, 30))
    result = assign_lines_to_regions([line], [])
    assert result == []


def test_nested_same_kind_regions_are_coalesced_without_merging_semantic_roles() -> None:
    broad = _region(RegionKind.TITLE, _bbox(0, 0, 200, 40), region_id="broad")
    nested = _region(RegionKind.TITLE, _bbox(40, 10, 100, 30), region_id="nested")
    broad.native_lines.append(_line("broad", _bbox(0, 0, 50, 10)))
    nested.native_lines.append(_line("nested", _bbox(40, 10, 100, 20)))

    result = _coalesce_nested_regions([broad, nested])

    assert [region.region_id for region in result] == ["broad"]
    assert {line.text for line in result[0].native_lines} == {"broad", "nested"}

    nested.semantic_role = "semantic_status"
    result = _coalesce_nested_regions([broad, nested])
    assert [region.region_id for region in result] == ["broad", "nested"]
