"""Regressões da RUN 007 (output/tesseract/007).

T3-P35: a mesma calha entre colunas de texto, validada em pedaços separados por
        um vão (quebra de parágrafo, linha de títulos), virava faixas de colunas
        distintas; linhas de colunas diferentes caíam numa mesma região e a
        montagem embaralhava segmentos (V10 p52, páginas raster L04–L06).
"""
from __future__ import annotations

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.reconstruct import ocr_text_column_layout, text_column_bands

PAGE = BBox(0, 0, 595, 842)


def _words(x0: float, y0: float, count: int, line_id: str, prefix: str) -> list[OcrToken]:
    return [
        OcrToken(f"{prefix}{index}", BBox(x0 + index * 35.0, y0, x0 + index * 35.0 + 30.0, y0 + 10.0),
                 0.9, "pt-BR", SourceKind.OCR_PAGE, layout_line_id=line_id)
        for index in range(count)
    ]


def _two_columns_with_gap(full_width_line_in_gap: bool = False) -> list[OcrToken]:
    tokens = []
    ys = [100.0 + row * 14.0 for row in range(6)] + [270.0 + row * 14.0 for row in range(6)]
    for column, x0 in enumerate((40.0, 320.0)):
        for row, y in enumerate(ys):
            tokens += _words(x0, y, 5, f"call:1:{column + 1}:1:{row + 1}", f"c{column}r{row}w")
    if full_width_line_in_gap:
        tokens += _words(40.0, 220.0, 14, "call:1:9:1:1", "largura")
    return tokens


def test_pieces_of_one_gutter_split_by_a_gap_are_joined():
    gutters, _ = ocr_text_column_layout(_two_columns_with_gap(), PAGE)

    assert len(gutters) == 1
    mid, top, bottom = gutters[0]
    assert 200.0 < mid < 320.0
    assert top < 110.0 and bottom > 330.0
    assert len(text_column_bands(gutters)) == 1


def test_pieces_separated_by_a_full_width_line_stay_apart():
    gutters, _ = ocr_text_column_layout(_two_columns_with_gap(full_width_line_in_gap=True), PAGE)

    assert len(gutters) == 2
    assert all(not (top < 225.0 < bottom) for _, top, bottom in gutters)


def _tilted(tokens: list[OcrToken], slope: float) -> list[OcrToken]:
    from dataclasses import replace

    return [replace(t, bbox=BBox(t.bbox.x0, t.bbox.y0 + slope * t.bbox.cx, t.bbox.x1, t.bbox.y1 + slope * t.bbox.cx))
            for t in tokens]


def test_pieces_are_not_joined_on_tilted_text():
    """On deskewed scans the layout is fragile; the join is limited to level text (V10 p25)."""
    gutters, slope = ocr_text_column_layout(_tilted(_two_columns_with_gap(), 0.03), PAGE)

    assert abs(slope) > 0.02
    assert len(gutters) == 2
