"""Regressões da RUN 005 (output/tesseract/005).

T3-P18: páginas digitalizadas com inclinação de 1°–10° são relidas endireitadas
        pelo backend Tesseract; as caixas voltam às coordenadas originais.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from structured_pdf_text.ocr.backends.tesseract import (
    TesseractBackend,
    _parse_tsv,
    _tsv_line_slope,
    _unrotate_tsv,
)

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tilted_tsv(slope: float, lines: int = 5, words: int = 8, confidence: float = 90.0,
                jitter: tuple[float, ...] = ()) -> str:
    rows = [HEADER]
    for line in range(lines):
        line_slope = slope + (jitter[line] if line < len(jitter) else 0.0)
        for word in range(words):
            left = 100.0 + word * 120.0
            top = 200.0 + line * 60.0 + line_slope * (left + 50.0)
            rows.append(f"5\t1\t1\t1\t{line + 1}\t{word + 1}\t{left}\t{top}\t100\t30\t{confidence}\tpalavra")
    return "\n".join(rows)


def test_line_slope_is_measured_from_tesseract_lines():
    assert abs(_tsv_line_slope(_tilted_tsv(0.08)) - 0.08) < 1e-6
    assert abs(_tsv_line_slope(_tilted_tsv(-0.05)) + 0.05) < 1e-6


def test_line_slope_requires_agreeing_lines():
    assert _tsv_line_slope(_tilted_tsv(0.08, lines=2)) is None
    assert _tsv_line_slope(_tilted_tsv(0.0, jitter=(0.08, -0.08, 0.06, -0.07, 0.09))) is None


def test_boxes_read_on_the_deskewed_image_map_back_to_the_original():
    image = Image.new("L", (800, 600), 255)
    ImageDraw.Draw(image).rectangle((600, 100, 620, 120), fill=0)
    for angle in (4.5, -3.0):
        rotated = image.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC)
        pixels = rotated.load()
        dark = [(x, y) for x in range(rotated.width) for y in range(rotated.height) if pixels[x, y] < 128]
        cx = sum(x for x, _ in dark) / len(dark)
        cy = sum(y for _, y in dark) / len(dark)
        tsv = f"{HEADER}\n5\t1\t3\t1\t2\t1\t{cx}\t{cy}\t0\t0\t90\tpalavra"
        (row,) = _parse_tsv(_unrotate_tsv(tsv, angle, rotated.size, image.size))
        assert abs(float(row["left"]) - 610.0) < 1.0
        assert abs(float(row["top"]) - 110.0) < 1.0
        assert (row["block_num"], row["line_num"], row["text"]) == ("3", "2", "palavra")


def test_unrotated_box_is_the_bound_of_its_turned_corners():
    tsv = f"{HEADER}\n5\t1\t1\t1\t1\t1\t100\t100\t200\t40\t90\tpalavra"
    (row,) = _parse_tsv(_unrotate_tsv(tsv, 5.0, (1000, 1000), (1000, 1000)))
    expected_height = 40 * math.cos(math.radians(5)) + 200 * math.sin(math.radians(5))
    assert abs(float(row["height"]) - expected_height) < 0.05


def _backend(readings: list[str]):
    backend = TesseractBackend.__new__(TesseractBackend)
    calls = []

    def read(pil, dpi):
        calls.append(pil.size)
        return readings[len(calls) - 1]

    backend._read_tsv = read
    return backend, calls


def test_skewed_page_is_reread_deskewed_when_the_reading_improves():
    upright = _tilted_tsv(0.08, words=4)
    deskewed = _tilted_tsv(0.0, words=8, confidence=95.0)
    backend, calls = _backend([deskewed])

    result = backend._deskew_page(Image.new("RGB", (1200, 900), "white"), 300, upright)

    assert len(calls) == 1 and calls[0] != (1200, 900)  # imagem girada e ampliada
    assert len(_parse_tsv(result)) == len(_parse_tsv(deskewed))


def test_deskewed_reading_is_discarded_when_it_is_worse():
    image = Image.new("RGB", (1200, 900), "white")
    upright = _tilted_tsv(0.08, words=8, confidence=95.0)
    backend, _ = _backend([_tilted_tsv(0.0, words=4, confidence=96.0)])  # perde caracteres
    assert backend._deskew_page(image, 300, upright) == upright
    backend, _ = _backend([_tilted_tsv(0.0, words=8, confidence=92.0)])  # perde confiança
    assert backend._deskew_page(image, 300, upright) == upright


def test_fragmented_skewed_reading_does_not_block_the_deskewed_one():
    """V10 p25: a leitura inclinada fragmenta palavras e infla a contagem."""
    upright = _tilted_tsv(0.08, words=10, confidence=90.0).replace("\tpalavra", "\tpala")
    deskewed = _tilted_tsv(0.0, words=7, confidence=96.0)
    backend, _ = _backend([deskewed])
    result = backend._deskew_page(Image.new("RGB", (1200, 900), "white"), 300, upright)
    assert len(_parse_tsv(result)) == len(_parse_tsv(deskewed))


def test_upright_or_barely_tilted_page_is_not_reread():
    backend, calls = _backend([])
    image = Image.new("RGB", (1200, 900), "white")

    assert backend._deskew_page(image, 300, _tilted_tsv(0.0)) == _tilted_tsv(0.0)
    assert backend._deskew_page(image, 300, _tilted_tsv(0.01)) == _tilted_tsv(0.01)  # 0,57°
    assert calls == []


# --- T3-P18: ordem das colunas em página inclinada (coordenadas originais) ----

def _skewed_columns(slope: float, starts=(40.0, 320.0), rows: int = 8, words: int = 6):
    from structured_pdf_text.document import OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox

    tokens = []
    for column, x0 in enumerate(starts):
        for row in range(rows):
            for word in range(words):
                x = x0 + word * 35.0
                y = 100.0 + row * 14.0 + slope * x
                tokens.append(OcrToken(f"c{column}r{row}w{word}", BBox(x, y, x + 30.0, y + 10.0), 0.9, "pt-BR",
                                       SourceKind.OCR_PAGE, layout_line_id=f"call:1:{column + 1}:1:{row + 1}"))
    return tokens


def test_columns_of_a_skewed_scan_are_ordered_column_by_column():
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.layout.ocr_aware import _order_ocr_lines_by_text_gutters
    from structured_pdf_text.ocr.reconstruct import ocr_text_column_layout, reconstruct_ocr_lines

    page = BBox(0, 0, 595, 842)
    tokens = _skewed_columns(0.08)  # ~4,6°: a calha se desloca ~11 pt ao longo das 8 linhas
    lines = reconstruct_ocr_lines(tokens, 0, page)
    gutters, slope = ocr_text_column_layout(tokens, page)

    assert abs(slope - 0.08) < 0.01 and gutters
    assert all(line.text.split()[0][:2] == line.text.split()[-1][:2] for line in lines)  # nenhuma linha cruza colunas
    ordered = [line.text.split()[0][:4] for line in _order_ocr_lines_by_text_gutters(lines, gutters, slope)]
    assert ordered == [f"c0r{r}" for r in range(8)] + [f"c1r{r}" for r in range(8)]


def test_text_gutter_check_for_tables_follows_the_tilt():
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.tables.ocr_tracks import _crosses_text_column_gutter

    gutters = [(270.0, 90.0, 220.0)]  # referencial retificado
    assert _crosses_text_column_gutter(BBox(40, 100, 500, 200), gutters, 0.08)
    assert _crosses_text_column_gutter(BBox(40, 100, 500, 200), gutters) == _crosses_text_column_gutter(
        BBox(40, 100, 500, 200), gutters, 0.0)


# --- T3-P28: duas leituras do mesmo valor em chamadas diferentes ---------------

def _cell_token(text, x0, confidence, line_id, x1=336.19):
    from structured_pdf_text.document import OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox

    return OcrToken(text, BBox(x0, 387.07, x1, 397.70), confidence, "pt-BR", SourceKind.OCR_PAGE,
                    layout_line_id=line_id)


def test_conflicting_rereads_of_one_cell_keep_the_most_confident_reading():
    """V8 p4/p58: página leu "96,40" (0.632) e a releitura da célula "98,40" (0.925)."""
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    page = BBox(0, 0, 595, 842)
    tokens = [
        _cell_token("€", 300.0, 0.9, "pagina:1:6:1:1", x1=308.0),
        _cell_token("96,40", 310.45, 0.632, "pagina:1:6:1:1"),
        _cell_token("98,40", 310.07, 0.925, "celula:1:1:1:1"),
    ]

    (line,) = reconstruct_ocr_lines(tokens, 0, page)

    assert line.text == "€ 98,40"


def test_rereads_are_only_resolved_across_calls_and_for_near_identical_boxes():
    from dataclasses import replace

    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.reconstruct import _drop_conflicting_rereads

    a = _cell_token("96,40", 310.45, 0.632, "pagina:1:6:1:1")
    b = _cell_token("98,40", 310.07, 0.925, "celula:1:1:1:1")
    boxes = lambda tokens: {id(t): t.bbox for t in tokens}  # noqa: E731

    same_call = [a, replace(b, layout_line_id="pagina:1:6:1:2")]
    assert _drop_conflicting_rereads(same_call, boxes(same_call)) == same_call
    partial = [a, replace(b, bbox=BBox(330.0, 387.07, 360.0, 397.70))]
    assert _drop_conflicting_rereads(partial, boxes(partial)) == partial
    other_engine = [replace(a, layout_line_id=None), replace(b, layout_line_id=None)]
    assert _drop_conflicting_rereads(other_engine, boxes(other_engine)) == other_engine
    assert _drop_conflicting_rereads([a, b], boxes([a, b])) == [b]
