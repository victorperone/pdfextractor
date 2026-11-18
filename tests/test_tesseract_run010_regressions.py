"""Regressões tratadas depois da RUN 010 (output/tesseract/010).

T3-P38: a tentativa 2 de T3-P20 alargou o limite de vão entre parágrafos para
        ``m + 0.2·h``. Em OCR denso de coluna única o vão real entre
        parágrafos fica abaixo desse limite e parágrafos independentes eram
        fundidos (V4 p83, p129, p137, p138 …).
"""
from __future__ import annotations

from structured_pdf_text.geometry import BBox

# Geometria medida na extração real da V4 p129 (OCR de texto, coluna única,
# photocopy_threshold): altura de cada caixa de linha e vão até a seguinte,
# em pt, como chegam a ``segment_ocr_paragraphs``. Nove parágrafos de cinco
# linhas; os vãos entre parágrafos são 3.0–4.75 pt, entre linhas 0–1.75 pt.
_P129_HEIGHTS = [10.25, 7.25, 8.0, 10.25, 8.0, 8.0, 7.25, 8.0, 8.0, 8.0, 10.0, 7.5, 8.0, 10.0, 8.0, 8.0,
                 7.5, 8.0, 8.0, 8.0, 8.0, 7.5, 8.0, 8.0, 7.75, 9.75, 7.25, 7.75, 9.75, 7.75, 8.0, 7.25,
                 8.0, 8.0, 8.0, 10.25, 7.5, 8.0, 10.25, 8.0, 10.0, 7.5, 8.0, 10.25, 7.75]
_P129_GAPS = [0.0, 1.5, 0.0, 0.5, 4.0, 1.0, 1.75, 1.0, 1.0, 3.5, 0.0, 1.5, 0.0, 0.0, 4.0, 1.5, 1.5, 1.0,
              1.0, 4.0, 1.0, 1.5, 1.0, 1.75, 3.25, 0.25, 1.75, 0.25, 0.25, 4.75, 1.0, 1.75, 1.0, 1.0,
              3.0, 0.25, 1.5, 0.0, -0.25, 3.0, 0.0, 1.5, 0.0, 0.5]
_P129_PARAGRAPH_STARTS = [0, 5, 10, 15, 20, 25, 30, 35, 40]


def _ocr_line(y0: float, y1: float, text: str):
    from structured_pdf_text.document import Baseline, OcrToken, SourceKind, TextLine, WritingDirection

    token = OcrToken(text, BBox(57.0, y0, 57.0 + 4.0 * len(text), y1), .9, "pt-BR", SourceKind.OCR_PAGE)
    return TextLine(tokens=[token], bbox=token.bbox, baseline=Baseline(y=y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None)


def test_dense_single_column_paragraphs_are_not_merged():
    """V4 p129: vãos de 3.0 pt entre parágrafos (linhas a 1.5 pt) não podem fundir parágrafos."""
    from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs

    lines, y = [], 100.0
    for index, height in enumerate(_P129_HEIGHTS):
        label = "Parágrafo 129.%02d inicia aqui" % (index // 5 + 1) if index in _P129_PARAGRAPH_STARTS \
            else "linha %02d do mesmo parágrafo denso" % index
        lines.append(_ocr_line(y, y + height, label.ljust(40, ".")))
        if index < len(_P129_GAPS):
            y += height + _P129_GAPS[index]

    paragraphs = segment_ocr_paragraphs(lines)

    first_lines = {id(paragraph[0]) for paragraph in paragraphs}
    assert all(id(lines[start]) in first_lines for start in _P129_PARAGRAPH_STARTS)
    assert sum(len(paragraph) for paragraph in paragraphs) == len(lines)
