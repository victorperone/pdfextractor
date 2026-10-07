from __future__ import annotations

from structured_pdf_text.document import (
    LayoutRegion,
    NativeObjectEvidence,
    NativePageEvidence,
    RegionDecision,
    RegionKind,
    RegionQuality,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.detector import (
    _cell_text_preserving_vertical_lines,
    detect_tables_native,
)


def _numeric_lines() -> list[TextLine]:
    lines = []
    for index, label in enumerate(("Cafe", "Leite", "Arroz", "Feijao")):
        y = 30 + index * 30
        tokens = [
            TextToken(label, BBox(10, y, 40, y + 10), [], 1.0, label),
            TextToken(" ", BBox(40, y, 100, y + 10), [], 1.0, " "),
        ]
        for char, x0, x1, y0, y1 in (
            ("1", 101, 104, y, y + 8.44),
            ("2", 107, 113, y, y + 8.44),
            (",", 114, 115, y + 7.17, y + 10.19),
            ("5", 117, 123, y + 0.18, y + 8.66),
            ("0", 124, 130, y, y + 8.66),
        ):
            tokens.append(TextToken(char, BBox(x0, y0, x1, y1), [], 1.0, char))
        lines.append(
            TextLine(
                tokens,
                BBox(10, y, 130, y + 11),
                None,
                WritingDirection.LEFT_TO_RIGHT,
                index * 7,
                index * 7 + 6,
                line_id=f"native:0:{index * 7}:{index * 7 + 6}",
            )
        )
    return lines


def test_native_decimal_glyphs_keep_order_in_all_table_reconstruction_tiers() -> None:
    lines = _numeric_lines()
    page_bbox = BBox(0, 0, 200, 200)
    objects = NativeObjectEvidence((), (), (), None, page_bbox, page_bbox, 0)
    page = NativePageEvidence(
        0,
        page_bbox,
        (),
        objects,
        "Cafe 12,50\nLeite 12,50\nArroz 12,50\nFeijao 12,50",
    )

    text_region = LayoutRegion(
        "text-tracks",
        RegionKind.TEXT,
        page_bbox,
        1.0,
        lines,
        [],
        RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    text_tracks = detect_tables_native(page, [text_region])
    assert len(text_tracks) == 1
    assert text_tracks[0].method.value == "text_tracks"
    assert [cell.text for cell in text_tracks[0].cells if cell.col == 1] == ["12,50"] * 4

    relaxed_region = LayoutRegion(
        "relaxed",
        RegionKind.TABLE,
        page_bbox,
        1.0,
        lines,
        [],
        RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    relaxed = detect_tables_native(page, [relaxed_region])
    assert len(relaxed) == 1
    assert relaxed[0].method.value == "relaxed_grid"
    assert [cell.text for cell in relaxed[0].cells if cell.col == 1] == ["12,50"] * 4

    # The strict-grid path passes partial native lines to the same native-cell
    # joiner when a cell owns only the value glyphs.
    value_tokens = [token for token in lines[0].tokens if token.text.strip() not in {"Cafe"}]
    assert _cell_text_preserving_vertical_lines(lines[:1], value_tokens) == "12,50"
