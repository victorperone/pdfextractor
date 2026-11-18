"""Regressões da RUN 008 (output/tesseract/008).

T3-P34: tabela raster escaneada com inclinação pequena e recortada no quadro
        da imagem (cantos cortados). A grade era encontrada e endireitada, mas
        seus cantos extrapolados saíam da imagem e a contenção a rejeitava
        (V9 p58, p66).
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from structured_pdf_text.document import TextToken
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.visual import detect_visual_table
from structured_pdf_text.tables.visual_alignment import point

_AMOUNTS = ("R$ 206,74", "R$ 1.045,56", "R$ 2.333,52", "R$ 7.607,34", "R$ 2.139,48")


def _table() -> tuple[Image.Image, list[TextToken]]:
    image = Image.new("RGB", (900, 360), "white")
    draw = ImageDraw.Draw(image)
    xs = [20, 140, 330, 520, 700, 880]
    ys = [20, 70, 125, 180, 235, 290, 340]
    for x in xs:
        draw.line((x, 20, x, 340), fill="black", width=3)
    for y in ys:
        draw.line((20, y, 880, y), fill="black", width=3)
    tokens = []
    rows = [("Item", "Descrição", "Valor", "Total", "Situação")] + [
        (f"00{r}", f"material {r}", amount, amount, "Regular") for r, amount in enumerate(_AMOUNTS, 1)
    ]
    for r, labels in enumerate(rows):
        for c, text in enumerate(labels):
            x, y = xs[c] + 10, ys[r] + 18
            draw.text((x, y), text, fill="black")
            tokens.append(TextToken(text, BBox(x, y, x + len(text) * 6, y + 12), [], .95, text,
                                    evidence_id=f"r{r}c{c}"))
    return image, tokens


def _tilted_and_cropped(degrees: float, crop: tuple[int, int, int, int]):
    cv2 = pytest.importorskip("cv2")
    image, tokens = _table()
    matrix = cv2.getRotationMatrix2D((450, 180), degrees, 1.0)
    tilted = cv2.warpAffine(np.asarray(image), matrix, (900, 360), borderValue=(255, 255, 255))
    left, top, right, bottom = crop
    cropped = np.ascontiguousarray(tilted[top:bottom, left:right])
    affine = (matrix[0, 0], matrix[0, 1], matrix[0, 2] - left, matrix[1, 0], matrix[1, 1], matrix[1, 2] - top)
    for token in tokens:
        corners = [point(affine, x, y) for x, y in ((token.bbox.x0, token.bbox.y0), (token.bbox.x1, token.bbox.y0),
                                                     (token.bbox.x1, token.bbox.y1), (token.bbox.x0, token.bbox.y1))]
        token.bbox = BBox(min(p[0] for p in corners), min(p[1] for p in corners),
                          max(p[0] for p in corners), max(p[1] for p in corners))
    return cropped, tokens


def _page(image) -> SimpleNamespace:
    height, width = image.shape[:2]
    bbox = BBox(0, 0, width, height)
    return SimpleNamespace(page_index=0, bbox=bbox, extracted_text="",
                           objects=SimpleNamespace(rotation=0, images=[SimpleNamespace(bbox=bbox)]))


def test_tilted_table_with_corners_cut_by_the_image_frame_is_detected():
    image, tokens = _tilted_and_cropped(3.5, (8, 14, 892, 346))

    table = detect_visual_table(_page(image), image, tokens=tokens)

    assert table is not None
    assert (table.row_count, table.column_count) == (6, 5)
    fragment = table.page_fragments[0].bbox
    assert all(fragment.x0 - 1e-6 <= cell.bbox.x0 and cell.bbox.x1 <= fragment.x1 + 1e-6
               and fragment.y0 - 1e-6 <= cell.bbox.y0 and cell.bbox.y1 <= fragment.y1 + 1e-6 for cell in table.cells)
    by_position = {(cell.row, cell.col): cell.text for cell in table.cells}
    assert [by_position[(r, 3)] for r in range(1, 6)] == list(_AMOUNTS)


def test_rules_outside_the_image_object_still_reject_the_grid():
    """Only the image frame changed: a network crossing the image object is not a raster table."""
    image, tokens = _tilted_and_cropped(3.5, (8, 14, 892, 346))
    page = _page(image)
    height, width = image.shape[:2]
    page.objects.images = [SimpleNamespace(bbox=BBox(0, 0, width * 0.6, height))]

    assert detect_visual_table(page, image, tokens=tokens) is None


def _tilted_table_block_and_cells(slope: float):
    from structured_pdf_text.document import TableMethod
    from structured_pdf_text.geometry import Point

    def warp(x, y):
        return x, y + slope * x

    cells = []
    for row, (y0, y1) in enumerate(((250.0, 300.0), (300.0, 350.0))):
        for col, (x0, x1) in enumerate(((50.0, 300.0), (300.0, 540.0))):
            corners = [Point(*warp(x, y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            bbox = BBox(min(p.x for p in corners), min(p.y for p in corners),
                        max(p.x for p in corners), max(p.y for p in corners))
            cells.append(SimpleNamespace(row=row, col=col, polygon=tuple(corners), bbox=bbox))
    table = SimpleNamespace(table_id="t", method=TableMethod.VISUAL_MODEL, cells=cells)
    box = BBox.union_all([cell.bbox for cell in cells])
    return table, box


def test_level_prose_below_a_tilted_raster_table_stays_after_it():
    """A wide level line below a -3.4° table must not be projected into its band (V9 p58)."""
    from structured_pdf_text.assemble.content import _place_visual_tables_between_prose
    from structured_pdf_text.document import ContentKind

    table, box = _tilted_table_block_and_cells(-0.06)
    above = SimpleNamespace(table_id=None, kind=ContentKind.TEXT, bbox=BBox(56, 150, 528, 175))
    below_first = SimpleNamespace(table_id=None, kind=ContentKind.TEXT, bbox=BBox(57, 362, 525, 384))
    below_second = SimpleNamespace(table_id=None, kind=ContentKind.TEXT, bbox=BBox(57, 430, 496, 452))
    shell = SimpleNamespace(table_id="t", kind=ContentKind.TABLE, bbox=box)

    ordered = _place_visual_tables_between_prose([above, below_first, shell, below_second], [table])

    assert ordered == [above, shell, below_first, below_second]


def test_quarter_turned_table_frame_is_kept():
    from structured_pdf_text.assemble.content import _quarter_turn_frame

    turned = lambda x, y: (y, -x)  # noqa: E731
    frame = _quarter_turn_frame(turned)
    assert frame(3.0, 5.0) == (5.0, -3.0)
    skewed = lambda x, y: (x + 0.06 * y, y - 0.06 * x)  # noqa: E731
    assert _quarter_turn_frame(skewed)(3.0, 5.0) == (3.0, 5.0)


def _ocr_line(y0: float, y1: float, text: str, x1: float = 190.0):
    from structured_pdf_text.document import Baseline, OcrToken, SourceKind, TextLine, WritingDirection

    token = OcrToken(text, BBox(57.0, y0, x1, y1), .9, "pt-BR", SourceKind.OCR_PAGE)
    return TextLine(tokens=[token], bbox=token.bbox, baseline=Baseline(y=y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None)


def test_glyph_box_jitter_does_not_split_an_ocr_paragraph():
    """T3-P20 (V10 p11): boxes 5.2–6.7 pt high, gaps 2.0–3.3 pt inside one paragraph."""
    from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs

    first = [(196.0, 202.5), (204.7, 210.2), (213.5, 220.0), (221.7, 228.0), (230.2, 236.7),
             (239.0, 245.7), (248.0, 254.5), (256.0, 262.7), (264.7, 271.2), (273.5, 280.0), (282.5, 288.7)]
    second = [(299.2, 305.7), (308.2, 313.5), (316.7, 323.2), (325.0, 331.5), (333.5, 340.0)]
    lines = [_ocr_line(y0, y1, f"linha {i} do primeiro parágrafo") for i, (y0, y1) in enumerate(first)]
    lines += [_ocr_line(y0, y1, f"linha {i} do segundo parágrafo") for i, (y0, y1) in enumerate(second)]

    paragraphs = segment_ocr_paragraphs(lines)

    assert [len(paragraph) for paragraph in paragraphs] == [len(first), len(second)]


def test_tight_paragraph_gap_still_separates_paragraphs():
    """T3-P20 (V4 p140): lines ~1 pt apart, paragraphs 3.5 pt apart, 8.2 pt lines."""
    from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs

    pitch, height = 9.2, 8.2
    first = [(89.7 + i * pitch, 89.7 + i * pitch + height) for i in range(10)]
    start = first[-1][1] + 3.5
    second = [(start + i * pitch, start + i * pitch + height) for i in range(10)]
    lines = [_ocr_line(y0, y1, f"linha {i} do primeiro parágrafo", x1=286.0) for i, (y0, y1) in enumerate(first)]
    lines += [_ocr_line(y0, y1, f"linha {i} do segundo parágrafo", x1=286.0) for i, (y0, y1) in enumerate(second)]

    paragraphs = segment_ocr_paragraphs(lines)

    assert [len(paragraph) for paragraph in paragraphs] == [10, 10]


def test_line_without_descenders_does_not_open_a_paragraph():
    """T3-P20 attempt 3 (V10 p32): a 4.7 pt box among 6.0 pt lines ends 1.3 pt early."""
    from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs

    lines, y = [], 100.0
    for index in range(8):
        height = 4.7 if index == 3 else 6.0
        lines.append(_ocr_line(y, y + height, f"linha {index} do mesmo parágrafo"))
        y += height + (3.7 if index == 3 else 2.2)

    assert [len(paragraph) for paragraph in segment_ocr_paragraphs(lines)] == [8]


def test_skewed_boxes_do_not_borrow_a_descender_across_a_real_gap():
    """T3-P20 attempt 5 (V9 p17): on a skewed scan a narrower line has a shorter box; it is not a missing descender."""
    from structured_pdf_text.ocr.reconstruct import segment_ocr_paragraphs

    header = _ocr_line(124.4, 138.7, "Processo: V9-2026-001452 Competência: 15/10/2026 Unidade: Núcleo de Patrimônio",
                       x1=57.0 + 309.0)
    body_text = "O levantamento considerou a competência de 15/10/2026, os registros do processo e a documentação"
    body = [_ocr_line(156.5 + 11.5 * index, 176.2 + 11.5 * index, body_text, x1=57.0 + 480.0) for index in range(3)]

    paragraphs = segment_ocr_paragraphs([header, *body])

    assert [len(paragraph) for paragraph in paragraphs] == [1, 3]
