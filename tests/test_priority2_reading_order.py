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
