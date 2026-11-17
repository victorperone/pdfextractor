"""Regressões da run Paddle 001 (corpus/PaddleOCRImprovement.md).

001-P0-01: OCR de página descartado por uma figura do tamanho da página
           (V10 L06: imagem de corpo inteiro + linha nativa de assinatura).
           Mesma correção do T3-P01 do branch Tesseract.
001-P0-02: réguas de cabeçalho/rodapé estendiam uma grade de células
           retangulares até a página inteira (V10 p20/p27 e outras dez).
"""
from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.api import _append_hybrid_ocr_regions
from structured_pdf_text.document import RegionDecision, RegionKind, RegionQuality
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.detector import _detect_rectangular_grid

PAGE = BBox(0, 0, 595, 842)


def _line(x0, y0, x1, y1):
    box = BBox(x0, y0, x1, y1)
    return SimpleNamespace(bbox=box), SimpleNamespace(bbox=box)


def _figure(bbox, decision=None, ocr_lines=None):
    return SimpleNamespace(
        region_id="page-1:region-1",
        kind=RegionKind.FIGURE,
        bbox=bbox,
        ocr_lines=list(ocr_lines or []),
        ocr_tokens=[],
        quality=RegionQuality(decision=decision) if decision else None,
    )


def _supplement(regions):
    return [r for r in regions if r.region_id.endswith(":ocr-supplement")]


def _text_region(monkeypatch):
    monkeypatch.setattr("structured_pdf_text.api.full_page_text_region",
                        lambda page_index, bbox, lines, complexity: SimpleNamespace(
                            region_id="", kind=RegionKind.TEXT, bbox=bbox, ocr_lines=[], ocr_tokens=[]))


# --- 001-P0-01: figura do tamanho da página -----------------------------------

def test_page_sized_figure_on_raster_primary_page_releases_page_ocr(monkeypatch):
    _text_region(monkeypatch)
    page_figure = _figure(PAGE, RegionDecision.ESCALATE_PAGE_OCR)
    line, token = _line(60, 100, 500, 115)
    regions = [page_figure]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[line], unmatched_tokens=[token],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=True,
    )

    (supplement,) = _supplement(regions)
    assert supplement.ocr_lines == [line]
    assert supplement.ocr_tokens == [token]


def test_page_sized_figure_keeps_ownership_when_page_is_not_raster_primary():
    page_figure = _figure(PAGE, RegionDecision.ESCALATE_PAGE_OCR)
    line, token = _line(60, 100, 500, 115)
    regions = [page_figure]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[line], unmatched_tokens=[token],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=False,
    )

    assert regions == [page_figure]
    assert page_figure.ocr_lines == []


def test_small_figure_on_raster_primary_page_is_not_released():
    logo = _figure(BBox(50, 50, 150, 120))
    inside, inside_tok = _line(60, 60, 140, 70)
    regions = [logo]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside], unmatched_tokens=[inside_tok],
        page_index=0, page_bbox=PAGE, complexity=None, page_raster_primary=True,
    )

    assert regions == [logo]
    assert logo.ocr_lines == []


def test_text_bearing_figure_receives_page_ocr_inside_it(monkeypatch):
    _text_region(monkeypatch)
    stamp = _figure(BBox(120, 180, 310, 250), RegionDecision.MERGE_OCR)
    inside, inside_tok = _line(130, 190, 300, 205)
    outside, outside_tok = _line(60, 400, 500, 415)
    regions = [stamp]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside, outside],
        unmatched_tokens=[inside_tok, outside_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert stamp.ocr_lines == [inside]
    assert stamp.ocr_tokens == [inside_tok]
    (supplement,) = _supplement(regions)
    assert supplement.ocr_lines == [outside]


def test_figure_without_text_decision_does_not_receive_page_ocr():
    logo = _figure(BBox(120, 180, 310, 250), RegionDecision.KEEP_NATIVE)
    inside, inside_tok = _line(130, 190, 300, 205)
    regions = [logo]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[inside], unmatched_tokens=[inside_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert regions == [logo]
    assert logo.ocr_lines == []


def test_figure_with_own_ocr_binding_is_not_overwritten():
    own, _ = _line(130, 190, 300, 205)
    stamp = _figure(BBox(120, 180, 310, 250), RegionDecision.OCR_REGION, ocr_lines=[own])
    page_line, page_tok = _line(131, 191, 299, 204)
    regions = [stamp]

    _append_hybrid_ocr_regions(
        regions=regions, tables=[], unmatched_lines=[page_line], unmatched_tokens=[page_tok],
        page_index=0, page_bbox=PAGE, complexity=None,
    )

    assert stamp.ocr_lines == [own]
    assert regions == [stamp]


# --- 001-P0-02: grade retangular limitada às células -----------------------------

def _cell_grid(x_edges, y_edges):
    return [
        BBox(x_edges[col], y_edges[row], x_edges[col + 1], y_edges[row + 1])
        for row in range(len(y_edges) - 1)
        for col in range(len(x_edges) - 1)
    ]


# Geometria da V10 p20: cinco colunas, seis linhas, réguas de página fora da tabela.
X_EDGES = [54.5, 151.6, 248.2, 344.8, 441.4, 538.3]
Y_EDGES = [342.6, 381.3, 419.5, 457.6, 495.5, 533.7, 572.4]
HEADER_RULE = BBox(52.5, 40.5, 542.8, 41.5)
FOOTER_RULE = BBox(53.5, 796.4, 541.8, 797.4)


def test_page_rules_do_not_stretch_rectangular_grid():
    grid = _detect_rectangular_grid(_cell_grid(X_EDGES, Y_EDGES) + [HEADER_RULE, FOOTER_RULE], PAGE)

    assert grid is not None
    assert grid.bbox == BBox(54.5, 342.6, 538.3, 572.4)
    assert len(grid.x_edges) == 6 and len(grid.y_edges) == 7
    assert len(grid.rectangular_cells) == 30
    assert min(cell[0] for cell in grid.rectangular_cells) == 0
    assert min(cell[1] for cell in grid.rectangular_cells) == 0


def test_header_only_rectangles_are_still_rejected():
    # V10 p64: só o cabeçalho é de retângulos; o corpo usa réguas finas. A grade
    # retangular era rejeitada (ocupação 4/30) e outro detector monta a tabela.
    columns = [55.0, 175.0, 295.0, 415.0, 535.0]
    header = _cell_grid(columns, [395.9, 449.6])
    separators = [BBox(54.7, y, 535.3, y + 0.7) for y in (503.0, 556.8, 610.5)]

    assert _detect_rectangular_grid(header + separators + [HEADER_RULE, FOOTER_RULE], PAGE) is None


def test_contiguous_separator_row_below_cells_is_kept():
    separator = BBox(54.5, 610.0, 538.3, 610.8)
    grid = _detect_rectangular_grid(
        _cell_grid(X_EDGES, Y_EDGES) + [separator, HEADER_RULE, FOOTER_RULE], PAGE)

    assert grid is not None
    assert len(grid.y_edges) == 8
    assert grid.bbox.y1 == 610.8
    assert grid.bbox.y0 == 342.6


def test_rectangular_grid_without_page_rules_is_unchanged():
    grid = _detect_rectangular_grid(_cell_grid(X_EDGES, Y_EDGES), PAGE)

    assert grid is not None
    assert grid.bbox == BBox(54.5, 342.6, 538.3, 572.4)
    assert grid.coherence == 1.0
