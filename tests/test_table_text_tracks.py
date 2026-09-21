from types import SimpleNamespace

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
from structured_pdf_text.tables.text_tracks import detect_borderless_table


def _cell_line(text: str, x: float, y: float) -> TextLine:
    token = TextToken(
        text=text,
        bbox=BBox(x, y, x + 30, y + 8),
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"native:{text}:{x}:{y}")],
        confidence=1.0,
        normalized_text=text,
    )
    return TextLine(
        tokens=[token],
        bbox=token.bbox,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


def test_borderless_detector_reconstructs_rows_from_one_line_per_cell():
    lines = [
        _cell_line(f"r{row}c{column}", column * 100, row * 20)
        for row in range(4)
        for column in range(3)
    ]
    region = LayoutRegion(
        region_id="region-1",
        kind=RegionKind.TEXT,
        bbox=BBox(0, 0, 330, 90),
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )

    table = detect_borderless_table(SimpleNamespace(page_index=0), region)

    assert table is not None
    assert (table.column_count, table.row_count, len(table.cells)) == (3, 4, 12)
