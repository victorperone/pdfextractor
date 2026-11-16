"""T3-P22: ordem de leitura de páginas nativas multicoluna (componente compartilhado).

As linhas nativas chegam separadas por coluna; a ordem é decidida em
``text/reading_order.py``. Estes testes protegem o comportamento existente e
cobrem as correções de T3-P22.
"""
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
import structured_pdf_text.text.reading_order as reading_order
from structured_pdf_text.text.reading_order import _detect_persistent_gutters, order_lines_in_region


def _line(text: str, x: float, y: float, width: float, height: float = 10.0) -> TextLine:
    bbox = BBox(x, y, x + width, y + height)
    token = TextToken(text=text, bbox=bbox, sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"{text}:{y}")],
                      confidence=1.0, normalized_text=text)
    return TextLine([token], bbox, None, WritingDirection.LEFT_TO_RIGHT, None, None)


def _region(lines: list[TextLine]) -> LayoutRegion:
    return LayoutRegion(
        region_id="text", kind=RegionKind.TEXT,
        bbox=BBox.union_all([line.bbox for line in lines]), layout_confidence=1.0,
        native_lines=lines, ocr_tokens=[], quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def _bbox(lines: list[TextLine]) -> BBox:
    return BBox.union_all([line.bbox for line in lines])


# Left-aligned columns; the middle one ends at very different x on each line.
_RAGGED_MIDDLE = (110.0, 150.0, 115.0, 145.0, 120.0, 150.0, 112.0, 148.0)


def _three_ragged_columns() -> list[TextLine]:
    lines = []
    for row, middle in enumerate(_RAGGED_MIDDLE):
        y = row * 12.0
        lines.append(_line(f"A{row}", 0.0, y, 145.0 + (row % 2) * 5.0))
        lines.append(_line(f"B{row}", 170.0, y, middle))
        lines.append(_line(f"C{row}", 340.0, y, 150.0))
    return lines


def test_gutter_beside_a_ragged_right_column_is_detected():
    lines = _three_ragged_columns()

    gutters = _detect_persistent_gutters(sorted(lines, key=lambda l: (l.bbox.y0, l.bbox.x0)), _bbox(lines))

    assert len(gutters) == 2
    assert gutters[0][1] <= 170.0 and gutters[1][0] >= 320.0 and gutters[1][1] <= 340.0


def test_three_ragged_columns_are_read_column_by_column():
    ordered, _ = order_lines_in_region(_region(_three_ragged_columns()))

    assert [line.text for line in ordered] == (
        [f"A{row}" for row in range(8)] + [f"B{row}" for row in range(8)] + [f"C{row}" for row in range(8)]
    )


def test_gutters_found_by_centre_clustering_are_unchanged(monkeypatch):
    lines = []
    for row in range(8):
        lines += [_line(f"A{row}", 0.0, row * 12.0, 150.0), _line(f"B{row}", 170.0, row * 12.0, 150.0)]
    ordered = sorted(lines, key=lambda l: (l.bbox.y0, l.bbox.x0))

    with_extension = _detect_persistent_gutters(ordered, _bbox(lines))
    monkeypatch.setattr(reading_order, "_ragged_column_gutters", lambda *args, **kwargs: [])
    without_extension = _detect_persistent_gutters(ordered, _bbox(lines))

    assert with_extension == without_extension == [(150.0, 170.0)]


def test_single_column_prose_has_no_gutter():
    lines = [_line(f"L{row}", 0.0, row * 12.0, 300.0 - (row % 3) * 40.0) for row in range(10)]

    assert _detect_persistent_gutters(lines, _bbox(lines)) == []


def _three_over_two_columns() -> list[TextLine]:
    """Three columns above, two below, with the band headings in their own regions."""
    lines = [_line("Publicação: lote e competência", 0.0, 0.0, 400.0)]
    for row in range(8):
        y = 40.0 + row * 12.0
        lines += [_line(f"A{row}", 0.0, y, 150.0), _line(f"B{row}", 170.0, y, 150.0), _line(f"C{row}", 340.0, y, 150.0)]
    for row in range(6):
        y = 170.0 + row * 12.0
        lines += [_line(f"D{row}", 0.0, y, 230.0), _line(f"E{row}", 260.0, y, 230.0)]
    return lines


def test_slabs_with_different_column_counts_are_read_column_by_column():
    from structured_pdf_text.text.reading_order import _order_horizontal_slabs

    lines = sorted(_three_over_two_columns(), key=lambda l: (l.bbox.y0, l.bbox.x0))
    ordered, segments = _order_horizontal_slabs(lines)

    assert [line.text for line in ordered] == (
        ["Publicação: lote e competência"]
        + [f"A{row}" for row in range(8)] + [f"B{row}" for row in range(8)] + [f"C{row}" for row in range(8)]
        + [f"D{row}" for row in range(6)] + [f"E{row}" for row in range(6)]
    )
    assert segments >= 3


def test_slabs_are_not_used_without_a_multi_column_slab():
    from structured_pdf_text.text.reading_order import _order_horizontal_slabs

    lines = []
    for paragraph in range(3):
        lines += [_line(f"P{paragraph}L{row}", 0.0, paragraph * 90.0 + row * 12.0, 300.0) for row in range(4)]

    assert _order_horizontal_slabs(lines) is None


def test_single_column_with_paragraph_gaps_keeps_its_order():
    lines = []
    y = 0.0
    for paragraph in range(3):
        for row in range(4):
            lines.append(_line(f"P{paragraph}L{row}", 0.0, y, 300.0 - row * 30.0))
            y += 12.0
        y += 30.0
    ordered, _ = order_lines_in_region(_region(lines))

    assert [line.text for line in ordered] == [line.text for line in lines]


def test_slabs_do_not_change_a_region_already_decided():
    from structured_pdf_text.text.reading_order import _order_prose_lines_with_decision

    lines = []
    for row in range(5):
        lines += [_line(f"Rótulo {row}:", 0.0, row * 14.0, 80.0), _line(f"valor {row}", 200.0, row * 14.0, 90.0)]
    for row in range(5):
        y = 120.0 + row * 14.0
        lines += [_line(f"Campo {row}:", 0.0, y, 80.0), _line(f"dado {row}", 200.0, y, 90.0)]
    box = _bbox(lines)

    with_slabs = _order_prose_lines_with_decision(lines, box.x0, box.width, box)
    without_slabs = _order_prose_lines_with_decision(lines, box.x0, box.width, box, allow_slabs=False)

    assert with_slabs.lines == without_slabs.lines
    assert "horizontal_slabs" not in with_slabs.decision.reasons


def test_one_multi_column_slab_is_not_enough_evidence():
    """Prose rows over key/value pairs: only the pairs slab looks multi-column."""
    from structured_pdf_text.text.reading_order import _order_horizontal_slabs

    lines = [_line(f"Texto {row}", 0.0, row * 30.0, 300.0) for row in range(4)]
    for row in range(10):
        y = 150.0 + row * 12.0
        lines += [_line(f"Chave {row}", 0.0, y, 40.0), _line(f"Valor {row}", 60.0, y, 40.0)]

    assert _order_horizontal_slabs(sorted(lines, key=lambda l: (l.bbox.y0, l.bbox.x0))) is None
