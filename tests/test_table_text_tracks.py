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


def _row_line(values: list[str], y: float) -> TextLine:
    tokens: list[TextToken] = []
    for column, value in enumerate(values):
        if not value:
            continue
        x = column * 100.0
        tokens.append(
            TextToken(
                text=value,
                bbox=BBox(x, y, x + max(30.0, len(value) * 5.0), y + 8.0),
                sources=[EvidenceRef(SourceKind.OCR_PAGE, 0, f"ocr:{column}:{y}")],
                confidence=1.0,
                normalized_text=value,
                provenance="baseline",
            )
        )
    return TextLine(
        tokens=tokens,
        bbox=BBox.union_all([token.bbox for token in tokens]),
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


def test_borderless_detector_keeps_wrapped_cell_lines_in_one_row():
    lines = [
        _row_line(["Campo", "Valor", "Status", "Nota"], 0),
        _row_line(["A", "123,45", "OK", "Fictício"], 20),
        _row_line(["", "mensal", "", "controle"], 27),
        _row_line(["B", "67,89", "OK", "Controle"], 45),
        _row_line(["", "estimado", "", "interno"], 52),
        _row_line(["C", "90,12", "REVISAR", "V1"], 70),
        _row_line(["", "revisado", "", "atenção"], 77),
    ]
    region = LayoutRegion(
        region_id="region-multiline",
        kind=RegionKind.TEXT,
        bbox=BBox(0, 0, 430, 100),
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )

    table = detect_borderless_table(SimpleNamespace(page_index=0), region)

    assert table is not None
    assert (table.column_count, table.row_count) == (4, 4)
    cells = {(cell.row, cell.col): cell.text for cell in table.cells}
    assert cells[(1, 1)] == "123,45 mensal"
    assert cells[(1, 3)] == "Fictício controle"
    assert cells[(2, 1)] == "67,89 estimado"
    assert cells[(3, 3)] == "V1 atenção"
