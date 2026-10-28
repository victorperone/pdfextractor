from __future__ import annotations

from structured_pdf_text.document import (
    EvidenceRef,
    LayoutRegion,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.reading_order import (
    ReadingLane,
    _order_lane_segments,
    order_region_lines,
)


def _line(text: str, x: float, y: float, width: float) -> TextLine:
    bbox = BBox(x, y, x + width, y + 10.0)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"line:{text}:{y}")],
        confidence=1.0,
        normalized_text=text,
    )
    return TextLine([token], bbox, None, WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(kind: RegionKind, region_id: str, bbox: BBox, lines: list[TextLine]) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=kind,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def test_two_columns_are_lane_major() -> None:
    lines = [
        _line("A1", 0, 0, 80), _line("B1", 120, 0, 80),
        _line("A2", 0, 20, 80), _line("B2", 120, 20, 80),
        _line("A3", 0, 40, 80), _line("B3", 120, 40, 80),
    ]
    ordered, decision = order_region_lines([_region(RegionKind.TEXT, "body", BBox(0, 0, 200, 50), lines)])
    assert [line.text for line in ordered] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert decision.flow_mode == "MULTI_COLUMN"
    assert decision.lane_count == 2
    assert decision.line_preservation_ok


def test_three_columns_are_lane_major() -> None:
    lines = []
    for y, suffix in ((0, "1"), (20, "2"), (40, "3")):
        lines.extend((_line(f"A{suffix}", 0, y, 45), _line(f"B{suffix}", 75, y, 45), _line(f"C{suffix}", 150, y, 45)))
    ordered, decision = order_region_lines([_region(RegionKind.TEXT, "body", BBox(0, 0, 200, 50), lines)])
    assert [line.text for line in ordered] == ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "C3"]
    assert decision.lane_count == 3


def test_form_remains_row_major_when_local_gaps_look_like_pairs() -> None:
    lines = [
        _line("Nome:", 0, 0, 30), _line("João", 45, 0, 40),
        _line("Data:", 0, 20, 30), _line("01/01/2026", 45, 20, 60),
        _line("Setor:", 0, 40, 35), _line("Financeiro", 45, 40, 60),
    ]
    ordered, decision = order_region_lines([_region(RegionKind.TEXT, "form", BBox(0, 0, 200, 50), lines)])
    assert [line.text for line in ordered] == ["Nome:", "João", "Data:", "01/01/2026", "Setor:", "Financeiro"]
    assert decision.flow_mode == "FORM"


def test_spanning_line_stays_between_column_segments() -> None:
    lines = [
        _line("A1", 0, 0, 80), _line("B1", 120, 0, 80),
        _line("A2", 0, 20, 80), _line("B2", 120, 20, 80),
        _line("QUOTE", 0, 35, 200),
        _line("A3", 0, 55, 80), _line("B3", 120, 55, 80),
    ]
    ordered, decision = order_region_lines([_region(RegionKind.TEXT, "body", BBox(0, 0, 200, 70), lines)])
    assert [line.text for line in ordered] == ["A1", "A2", "B1", "B2", "QUOTE", "A3", "B3"]
    assert decision.spanning_band_count == 1


def test_wide_line_inside_asymmetric_lane_is_not_spanning() -> None:
    lines = [
        _line("A1", 0, 0, 30), _line("B1", 110, 0, 100),
        _line("A2", 0, 20, 30), _line("B2", 110, 20, 100),
        _line("A3", 0, 40, 30), _line("B3", 110, 40, 100),
    ]
    ordered, spanning_count, _ = _order_lane_segments(
        lines,
        [
            ReadingLane(0, 40, tuple(lines[::2])),
            ReadingLane(100, 220, tuple(lines[1::2])),
        ],
        BBox(0, 0, 220, 50),
    )

    assert [line.text for line in ordered] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert spanning_count == 0


def test_figure_caption_edges_are_geometric() -> None:
    figure = _region(RegionKind.FIGURE, "figure", BBox(10, 20, 190, 100), [_line("OCR", 20, 40, 40)])
    caption = _region(RegionKind.CAPTION, "caption", BBox(20, 104, 180, 118), [_line("Caption", 20, 104, 50)])
    _, decision = order_region_lines([caption, figure])
    assert decision.region_order == ("figure", "caption")
    assert decision.figure_caption_edges


def test_footnote_does_not_enter_prose_lane_splitter() -> None:
    lines = [_line("A1", 0, 0, 80), _line("B1", 120, 0, 80), _line("A2", 0, 20, 80), _line("B2", 120, 20, 80)]
    footnote = _region(RegionKind.FOOTNOTE, "note", BBox(0, 60, 200, 72), [_line("Nota", 0, 60, 40)])
    ordered, decision = order_region_lines([footnote, _region(RegionKind.TEXT, "body", BBox(0, 0, 200, 40), lines)])
    assert [line.text for line in ordered] == ["A1", "A2", "B1", "B2", "Nota"]
    assert decision.lane_count == 2

def _tall_token(text: str, x: float, y: float, width: float, height: float = 20.0) -> TextToken:
    """Create a token with custom height to simulate scale=2 rendering (OCR at 144 DPI)."""
    return TextToken(
        text=text,
        bbox=BBox(x, y, x + width, y + height),
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"tok:{text}:{x}:{y}")],
        confidence=1.0,
        normalized_text=text,
    )


def test_narrow_gutter_merged_line_splits_correctly() -> None:
    """Regression: _split_line_by_lanes used height*1.5 as gap threshold.

    With scale=2 lines (height=20 px) and a 20 px gutter, the old threshold was
    max(5, 20*1.5)=30 px — larger than the gutter — so merged cross-column lines
    were never split and ended up as spanning separators instead.  The new threshold
    uses min_gutter*0.6=12 px, which correctly detects the cross-column gap.
    """
    # 3-column page: col1 0-80, gutter 80-100, col2 100-180, gutter 180-200, col3 200-280
    # All lines have height=20 to simulate OCR at scale=2 (144 DPI).
    height = 20.0

    def col_line(text: str, col_x: float, y: float) -> TextLine:
        tok = _tall_token(text, col_x, y, 60.0, height)
        return TextLine([tok], BBox(col_x, y, col_x + 60, y + height), None, WritingDirection.LEFT_TO_RIGHT, None, None)

    # Clean lines per column
    a_lines = [col_line("A1", 0, 0), col_line("A2", 0, 25), col_line("A3", 0, 50)]
    b_lines = [col_line("B1", 100, 0), col_line("B2", 100, 25), col_line("B3", 100, 50)]
    c_lines = [col_line("C1", 200, 0), col_line("C2", 200, 25), col_line("C3", 200, 50)]

    # Merged line: Tesseract combined col1 and col2 text at the same Y level.
    # Token "A4" ends at x=78 (inside col1), "B4" starts at x=102 (inside col2).
    # Gap between tokens = 102-78 = 24 px — smaller than old threshold (30 px) but
    # larger than new threshold (12 px).
    tok_a4 = _tall_token("A4", 10, 75, 68.0, height)   # ends at x=78
    tok_b4 = _tall_token("B4", 102, 75, 68.0, height)  # starts at x=102
    merged_bbox = BBox(10, 75, 170, 75 + height)
    merged_line = TextLine([tok_a4, tok_b4], merged_bbox, None, WritingDirection.LEFT_TO_RIGHT, None, None)

    lanes = [
        ReadingLane(0, 80, tuple(a_lines + [merged_line])),
        ReadingLane(100, 180, tuple(b_lines)),
        ReadingLane(200, 280, tuple(c_lines)),
    ]
    all_lines = a_lines + b_lines + c_lines + [merged_line]
    ordered, spanning_count, _ = _order_lane_segments(all_lines, lanes, BBox(0, 0, 280, 100))

    texts = [line.text for line in ordered]
    # merged_line must be split: A4 stays in col1 segment, B4 stays in col2 segment
    assert "A4 B4" not in texts, "Merged cross-column line should be split, not kept as one"
    assert spanning_count == 0, "Merged line should be split into lanes, not treated as spanning"
    # col1 comes before col2 comes before col3
    assert texts.index("A1") < texts.index("B1") < texts.index("C1")
    assert "A4" in " ".join(texts[:texts.index("B1")]), "A4 should be in col1 segment"
