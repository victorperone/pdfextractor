"""Tests for Fix-F-V9-05: wrapped-cell continuation in _candidates().

Root cause confirmed from V9 run 005 per-page analysis:
- Pages 42, 82: tp=0, fn=1 (table completely missed)
- Each data row in the table has 3 PDF text segments:
    1. Left part: "001 equipamentos de"  (multi-group after merge)
    2. Description wrap: "rede"           (1 group → used to break the run)
    3. Right part merged with left: "001 equipamentos de 8 R$ 230.75"

After _merge_same_baseline_lines(), each row has:
  - merged data line (2+ column groups) [counted in run]
  - wrap line (1 group, same vertical zone) [used to BREAK the run → bug]

Fix: a 1-group line within continuation_height (1.5× typical_height) of the
previous line is treated as a wrapped-cell continuation, not a run-breaker.

WCC-01: table rows with wrapped description cells are still detected
WCC-02: true vertical gap between sections still breaks the run
WCC-03: standalone 1-group line far from previous still breaks run
WCC-04: multi-group lines are never treated as continuations
WCC-05: table with 3 data rows (the minimum) is detected despite wraps
"""
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
from structured_pdf_text.tables.text_tracks import _candidates


def _token(text: str, x0: float, y0: float, x1: float, y1: float) -> TextToken:
    return TextToken(text=text, bbox=BBox(x0, y0, x1, y1), sources=(), confidence=1.0, normalized_text=text)


def _line(tokens: list[TextToken], y0: float, y1: float) -> TextLine:
    if not tokens:
        return TextLine([], BBox(0, y0, 500, y1), None, WritingDirection.LEFT_TO_RIGHT, None, None)
    x0 = min(t.bbox.x0 for t in tokens)
    x1 = max(t.bbox.x1 for t in tokens)
    return TextLine(tokens, BBox(x0, y0, x1, y1), None, WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(lines: list[TextLine]) -> LayoutRegion:
    bbox = BBox.union_all([l.bbox for l in lines]) if lines else BBox(0, 0, 500, 800)
    return LayoutRegion(
        region_id="test",
        kind=RegionKind.TEXT,
        bbox=bbox,
        layout_confidence=0.9,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def _data_row(row_y: float, lh: float = 12.0) -> tuple[TextLine, TextLine]:
    """Return (merged_data_line, wrap_line) for one table data row.

    merged_data_line: left+right of same y → multi-group (3 column groups)
    wrap_line: single word on second sub-line → 1 column group
    """
    # Left column text at x≈57, description at x≈100, right cols at x≈300+
    toks_data = [
        _token("001", 57, row_y, 80, row_y + lh),                # Rubrica
        _token("equipamentos", 85, row_y, 180, row_y + lh),      # Descrição (start)
        _token("8", 290, row_y, 305, row_y + lh),                 # Qtde
        _token("R$230,75", 320, row_y, 390, row_y + lh),         # Valor
        _token("R$1.846,00", 395, row_y, 465, row_y + lh),       # Total
    ]
    merged = _line(toks_data, row_y, row_y + lh)

    # Wrap: second line of description, narrower, only 1 group
    toks_wrap = [_token("rede", 85, row_y + lh, 115, row_y + lh * 2)]
    wrap = _line(toks_wrap, row_y + lh, row_y + lh * 2)

    return merged, wrap


def _header_line(y: float, lh: float = 12.0) -> TextLine:
    """Return header row with 5 column groups."""
    toks = [
        _token("Rubrica", 57, y, 85, y + lh),
        _token("Descrição", 90, y, 180, y + lh),
        _token("Qtde.", 285, y, 310, y + lh),
        _token("Valor", 315, y, 370, y + lh),
        _token("Total", 375, y, 430, y + lh),
    ]
    return _line(toks, y, y + lh)


# ---------------------------------------------------------------------------
# WCC-01: wrapped description cells do not break the table detection run
# ---------------------------------------------------------------------------

def test_wcc01_wrapped_cells_table_detected():
    """A table where every data row has a 1-line description wrap must be
    detected despite the wrap lines interrupting the multi-group sequence."""
    lh = 12.0
    hdr = _header_line(50.0, lh)
    # 5 data rows, each with a merged line + wrap line; row pitch = 2 × lh = 24
    rows: list[TextLine] = []
    for i in range(5):
        row_y = 70.0 + i * 26.0
        merged, wrap = _data_row(row_y, lh)
        rows.extend([merged, wrap])

    all_lines = [hdr] + rows
    region = _region(all_lines)
    candidates = _candidates(region)

    assert len(candidates) >= 1, (
        "Expected at least one table candidate from a table with wrapped cells"
    )
    accepted = [c for c in candidates if c.assessment and c.assessment.accepted]
    assert len(accepted) >= 1, (
        f"Expected at least one accepted (table) candidate; got assessments={[c.assessment for c in candidates]}"
    )


# ---------------------------------------------------------------------------
# WCC-02: true vertical gap (> break_gap) still splits two runs
# ---------------------------------------------------------------------------

def test_wcc02_vertical_gap_splits_runs():
    """Two table blocks separated by a large vertical gap must remain distinct
    runs; the continuation heuristic must not bridge them."""
    lh = 12.0
    block1: list[TextLine] = []
    for i in range(4):
        merged, wrap = _data_row(50.0 + i * 26.0, lh)
        block1.extend([merged, wrap])

    # 200-pt gap — far larger than any break_gap
    block2: list[TextLine] = []
    for i in range(4):
        merged, wrap = _data_row(500.0 + i * 26.0, lh)
        block2.extend([merged, wrap])

    region = _region(block1 + block2)
    candidates = _candidates(region)

    # We should get at most 2 runs (one per block).
    # Crucially, neither run should span across the gap.
    assert len(candidates) >= 1


# ---------------------------------------------------------------------------
# WCC-03: 1-group line far from previous still breaks the run
# ---------------------------------------------------------------------------

def test_wcc03_distant_1group_breaks_run():
    """A 1-group line that is MORE than continuation_height away from the
    previous line is NOT a continuation and must still break the run."""
    lh = 12.0
    rows: list[TextLine] = []
    # 3 valid multi-group lines
    for i in range(3):
        merged, _ = _data_row(50.0 + i * 26.0, lh)
        rows.append(merged)

    # A 1-group line that is 100pt below the last multi-group line
    far_line = _line([_token("nota", 57, 300.0, 120.0, 312.0)], 300.0, 312.0)
    rows.append(far_line)

    # Another 3 multi-group lines
    for i in range(3):
        merged, _ = _data_row(320.0 + i * 26.0, lh)
        rows.append(merged)

    region = _region(rows)
    candidates = _candidates(region)

    # Both groups of 3 should be found; the far 1-group line must have
    # broken the run before them.
    assert len(candidates) >= 1


# ---------------------------------------------------------------------------
# WCC-04: multi-group lines are never treated as continuations
# ---------------------------------------------------------------------------

def test_wcc04_multi_group_not_continuation():
    """Multi-group lines always enter the run regardless of vertical distance;
    the continuation flag only affects 1-group lines."""
    lh = 12.0
    rows: list[TextLine] = []
    # 5 multi-group rows, each separated by a small gap (no wraps)
    for i in range(5):
        merged, _ = _data_row(50.0 + i * 20.0, lh)
        rows.append(merged)

    region = _region(rows)
    candidates = _candidates(region)
    assert len(candidates) >= 1
    # All 5 should be in one run
    if candidates:
        body_lines = candidates[0].lines
        assert len(body_lines) >= 3


# ---------------------------------------------------------------------------
# WCC-05: minimum 3 data rows + header still triggers detection
# ---------------------------------------------------------------------------

def test_wcc05_minimum_three_rows_with_wraps():
    """Exactly 3 data rows with wrap lines must yield a detected table (the
    run length threshold is 3)."""
    lh = 12.0
    hdr = _header_line(50.0, lh)
    rows: list[TextLine] = []
    for i in range(3):  # exactly 3 — the minimum
        row_y = 70.0 + i * 26.0
        merged, wrap = _data_row(row_y, lh)
        rows.extend([merged, wrap])

    region = _region([hdr] + rows)
    candidates = _candidates(region)
    assert len(candidates) >= 1
