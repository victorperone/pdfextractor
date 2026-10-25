"""Runs 011/012: 008 regressions and OCR table repairs.

OCR variants of a running footer are confirmed together, so it is suppressed
on every page; a skewed wide title does not inflate the line tolerance nor
join the label below it; a regional table reread does not replace a cell
reading without evidence that it is better. OCR tables recover lost row and
column dividers from token geometry, and misread decimal dots or currency
marks follow the convention of the rest of the table.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers
from structured_pdf_text.document import (
    Baseline, EvidenceRef, SourceKind, StructuredTable, TableCell, TableFragment, TableMethod, TextLine, TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipelines.rapidocr.policy import TOKEN_REFINEMENT_POLICY
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines, segment_ocr_paragraphs
from structured_pdf_text.tables.grid_repair import repair_missing_dividers
from structured_pdf_text.tables.text_join import join_table_tokens
from structured_pdf_text.tables.value_surfaces import harmonize_value_surfaces
from test_rapidocr_improvement_008 import _ocr

PAGE = BBox(0, 0, 595, 842)


def _texts(lines):
    return ["".join(token.text for token in line.tokens).strip() for line in lines]


def _edge_page(index, lines, *, line_prefix="ocr"):
    from test_repeated_suppression import _line, _page
    built = [_line(text, line_id=f"{line_prefix}:{index}:{n}", y=y) for n, (text, y) in enumerate(lines)]
    return replace(_page(built, edge_role="bottom_candidate" if lines[0][1] > 100 else "top_candidate"), page_index=index)


def test_ocr_variants_of_a_running_footer_are_confirmed_together():
    pages = [
        _edge_page(0, [("RODAPÉ DECORATIVO · OMITIR NO GABARITO Página 1 de 4", 186)]),
        _edge_page(1, [("RODAPÉ DECORATIVO · OMITIR NO GABARITO Página 2 de 4", 186)]),
        _edge_page(2, [("RODAPE DECORATIVO + OMITIR NO CABAR O Pagina 3 ce 4", 186)]),
        # V4 p112: the counter read apart from its label.
        _edge_page(3, [("RODAPÉ DECORATIVO · OMITIR NO GABARITO", 186), ("Página 4 de 4", 186)]),
    ]
    confirmed = detect_repeated_headers_footers(pages)
    assert {page for pages_ in confirmed.values() for page in pages_} == {0, 1, 2, 3}
    assert confirmed["footer:<page-number>"] == [3]


def test_similar_page_titles_are_not_ocr_furniture_variants():
    titles = ["horizontal_streaks", "vertical_streaks", "diagonal_streaks"]
    pages = [_edge_page(n, [(f"Página {n + 1} | OCR de texto: {title} - repetição 1", 5)]) for n, title in enumerate(titles)]
    assert detect_repeated_headers_footers(pages) == {}


def test_native_furniture_keeps_exact_signatures():
    pages = [_edge_page(0, [("RODAPÉ DECORATIVO · OMITIR NO GABARITO", 186)], line_prefix="native"),
             _edge_page(1, [("RODAPE DECORATIVO + OMITIR NO CABAR O", 186)], line_prefix="native")]
    assert detect_repeated_headers_footers(pages) == {}


def test_wrapped_line_of_a_skewed_title_stays_apart_from_the_next_line():
    slope = -0.031
    tokens = [
        _ocr("Página 172 | OCR de tabela: Tabela com unidades e medições - skew -", 28.7, 65, 495.9, 83.2, slope),
        _ocr("repetição 2", 28.3, 79.5, 110.3, 97.2, slope),
        _ocr("Controle de página: V4-P172", 29.6, 93.4, 128.9, 105.7, slope),
    ]
    for row, y in enumerate((150, 175, 200)):
        for x0 in (40, 260):
            yy = y + slope * x0
            tokens.append(_ocr(f"célula larga {row}-{x0}", x0, yy, x0 + 200, yy + 13, slope))
    texts = _texts(reconstruct_ocr_lines(tokens, 0, PAGE, dehyphenate=False))
    assert "repetição 2" in texts
    assert "Controle de página: V4-P172" in texts


# -- table reread: a different crop alone is no evidence ---------------------

def _cell(text, confidences, *, boxes=None, col=0):
    boxes = boxes or [BBox(10 + 20 * i, 10, 28 + 20 * i, 20) for i in range(len(confidences))]
    words = text.split() or [""]
    tokens = [SimpleNamespace(text=word, confidence=value, bbox=box) for word, value, box in zip(words, confidences, boxes)]
    return SimpleNamespace(text=text, tokens=tokens, bbox=BBox(0 + 100 * col, 0, 100 + 100 * col, 40),
                           row=0, col=col, rowspan=1, colspan=1)


def _keeps(old, new):
    return not TOKEN_REFINEMENT_POLICY.accept_visual_table(SimpleNamespace(cells=[old]), SimpleNamespace(cells=[new]))


@pytest.mark.parametrize("old,old_conf,new,new_conf", [
    ("Jul", .8156, "Jui", .8191),     # V4 p184
    ("C", .6898, "c", .609),          # V3 p94
    ("s", .8996, "S", .668),          # V4 p148
    ("81,25", .886, "B1,25", .8526),  # V4 p178
])
def test_reread_without_a_confidence_gain_keeps_the_cell(old, old_conf, new, new_conf):
    assert _keeps(_cell(old, [old_conf]), _cell(new, [new_conf]))


def test_reread_with_a_clear_confidence_gain_replaces_the_cell():
    assert not _keeps(_cell("πI", [.71]), _cell("TI", [.9788]))  # V3 p99
    assert not _keeps(_cell("", []), _cell("39,25", [.5]))           # recovery


@pytest.mark.parametrize("old,old_conf,new,new_conf,kept", [
    ("78.25", .9653, "78,25", .9483, False),  # V4 p178: the comma reading wins
    ("55,25", .962, "55.25", .9167, True),
    ("-0.01%", .9245, "-0,01%", .9962, False),
])
def test_separator_only_reread_prefers_the_decimal_comma(old, old_conf, new, new_conf, kept):
    assert _keeps(_cell(old, [old_conf]), _cell(new, [new_conf])) is kept


def test_reread_that_adds_content_replaces_the_cell():
    # V4 p148: keeping "Métrica" would drop the page control observed above it.
    assert not _keeps(_cell("Métrica", [.9694]), _cell("Controle de página: V4-P148 Métrica", [.997] * 5))
    assert not _keeps(_cell("Serviço de", [.99, .99]), _cell("Serviço de suporte", [.90, .90, .90]))


def test_kept_grid_reread_is_projected_before_the_policy_decides(monkeypatch):
    from dataclasses import replace
    from PIL import Image
    from structured_pdf_text import api, SecurityLimits
    from structured_pdf_text.document import OcrToken, SourceKind, StructuredTable, TableCell, TableFragment, TableMethod
    from structured_pdf_text.ocr.pipeline.adapters import LegacyInjectedPipeline
    from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
    from test_ocr_pipeline_services import SpySource

    box = BBox(20, 20, 120, 60)
    month = OcrToken("Jul", BBox(30, 30, 60, 40), .8156, "pt-BR", SourceKind.OCR_REGION)
    value = OcrToken("55.25", BBox(80, 30, 110, 40), .99, "pt-BR", SourceKind.OCR_REGION)
    table = StructuredTable("kept-grid", [TableFragment(0, box, 0, 1)], [
        TableCell(0, 0, 1, 1, BBox(20, 20, 70, 60), month.text, [month], .8156),
        TableCell(0, 1, 1, 1, BBox(70, 20, 120, 60), value.text, [value], .99),
    ], 2, 1, .99, TableMethod.VISUAL_MODEL)

    class Engine:
        def recognize_page(self, *args, **kwargs):
            return [replace(month, text="Jui", confidence=.8191), replace(value, text="55,25")]

    # The reread grid loses a column, so the original grid is kept (V4 p184).
    monkeypatch.setattr(api, "detect_visual_table_in_region", lambda **kwargs: None)
    pipeline = LegacyInjectedPipeline(Engine())
    pipeline.token_refinement_policy = TOKEN_REFINEMENT_POLICY
    coordinator = PageCoordinator(pipeline, SpySource(), extraction_id="kept", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    try:
        result = api._refine_visual_table_ocr(OCRPageFacade(coordinator), Image.new("RGB", (200, 100)),
                                             SimpleNamespace(bbox=coordinator.page_bbox), 0, table, [], "adaptive")
        assert result
        assert [cell.text for cell in table.cells] == ["Jul", "55,25"]
    finally:
        coordinator.close()


def _plain_line(text, x0, y0, x1, y1):
    token = TextToken(text=text, bbox=BBox(x0, y0, x1, y1), sources=[EvidenceRef(SourceKind.OCR_PAGE, 0, text)],
                      confidence=1.0, normalized_text=text)
    return TextLine(tokens=[token], bbox=token.bbox, baseline=Baseline(y=y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None)


def test_wrapped_title_line_and_smaller_label_are_separate_paragraphs():
    # V4 p172 boxes: the narrower title line is taller than the wider label.
    title, label = _plain_line("repetição 2", 28.3, 76.0, 110.3, 97.2), _plain_line("Controle de página: V4-P172", 29.6, 90.4, 128.9, 105.7)
    assert segment_ocr_paragraphs([title, label]) == [[title], [label]]
    # Same type on a skewed scan: the wider line is the taller one.
    wide, last = _plain_line("linha larga de prosa inclinada", 30, 100, 500, 127), _plain_line("fim.", 30, 115, 80, 129)
    assert segment_ocr_paragraphs([wide, last]) == [[wide, last]]


# -- OCR table dividers and value surfaces ----------------------------

def _ocr_cell(row, col, box, *words, rowspan=1, colspan=1):
    tokens = [TextToken(text=text, bbox=BBox(*b), sources=[EvidenceRef(SourceKind.OCR_REGION, 0, text)],
                        confidence=.99, normalized_text=text) for text, b in words]
    return TableCell(row, col, rowspan, colspan, BBox(*box), join_table_tokens(tokens), tokens, .99)


def _ocr_table(cells, rows, cols):
    box = BBox.union_all([cell.bbox for cell in cells])
    return StructuredTable("t", [TableFragment(0, box, 0, rows)], cells, cols, rows, .99, TableMethod.VISUAL_MODEL)


def _grid(table):
    return {(c.row, c.col, c.rowspan, c.colspan): c.text for c in table.cells}


def test_merged_rows_with_aligned_values_are_split():
    table = _ocr_table([  # V4 p160: the divider between two item rows is lost.
        _ocr_cell(0, 0, (31, 88, 119, 105), ("Categoria", (33, 90, 76, 101))),
        _ocr_cell(0, 1, (119, 88, 296, 105), ("Item", (210, 90, 232, 100))),
        _ocr_cell(0, 2, (296, 88, 418, 105), ("Valor", (387, 90, 412, 100))),
        _ocr_cell(1, 0, (31, 105, 119, 129), ("Serviços", (33, 110, 67, 121))),
        _ocr_cell(1, 1, (119, 105, 296, 129), ("T08B-S1", (210, 104, 243, 114)), ("T08B-S2", (210, 117, 244, 127))),
        _ocr_cell(1, 2, (296, 105, 418, 129), ("100,00", (387, 103, 416, 114)), ("200,00", (387, 117, 416, 128))),
    ], 2, 3)
    assert _grid(repair_missing_dividers(table)) == {
        (0, 0, 1, 1): "Categoria", (0, 1, 1, 1): "Item", (0, 2, 1, 1): "Valor",
        (1, 0, 2, 1): "Serviços", (1, 1, 1, 1): "T08B-S1", (1, 2, 1, 1): "100,00",
        (2, 1, 1, 1): "T08B-S2", (2, 2, 1, 1): "200,00"}


def test_wrapped_descriptions_do_not_split_a_row():
    table = _ocr_table([
        _ocr_cell(0, 0, (0, 0, 100, 30), ("Descrição longa", (2, 2, 90, 12)), ("continua aqui", (2, 15, 80, 25))),
        _ocr_cell(0, 1, (100, 0, 200, 30), ("Outra coluna", (102, 2, 190, 12)), ("também quebra", (102, 15, 180, 25))),
    ], 1, 2)
    assert repair_missing_dividers(table) is table


def test_spanned_cell_with_one_value_per_column_is_split():
    table = _ocr_table([  # V4 p164
        _ocr_cell(0, 0, (0, 0, 40, 12), ("T10B-01-01", (1, 1, 35, 11))),
        _ocr_cell(0, 1, (40, 0, 80, 12), ("T10B-01-02", (41, 1, 75, 11))),
        _ocr_cell(1, 0, (0, 12, 80, 24), ("T10B-02-01", (1, 13, 35, 23)), ("T10B-02-02", (42, 13, 76, 23)), colspan=2),
    ], 2, 2)
    assert _grid(repair_missing_dividers(table))[(1, 1, 1, 1)] == "T10B-02-02"


def test_column_with_two_values_split_by_one_band_is_split():
    table = _ocr_table([  # V4 p157
        _ocr_cell(0, 0, (32, 90, 296, 103), ("Grupo A", (32, 89, 70, 102))),
        _ocr_cell(1, 0, (32, 103, 296, 115), ("Código", (32, 102, 68, 116)), ("Valor", (164, 102, 194, 116))),
        _ocr_cell(2, 0, (32, 115, 296, 129), ("T07A-A1", (32, 116, 68, 129)), ("10,25", (165, 116, 195, 129))),
        _ocr_cell(3, 0, (32, 129, 296, 143), ("T07A-A2", (33, 130, 69, 142)), ("30,75", (164, 129, 195, 143))),
    ], 4, 1)
    assert _grid(repair_missing_dividers(table)) == {
        (0, 0, 1, 2): "Grupo A", (1, 0, 1, 1): "Código", (1, 1, 1, 1): "Valor",
        (2, 0, 1, 1): "T07A-A1", (2, 1, 1, 1): "10,25", (3, 0, 1, 1): "T07A-A2", (3, 1, 1, 1): "30,75"}


def test_misread_decimal_dots_follow_the_table_convention():
    texts = ["1,25 mm", "0.0025 g", "7.3 kg", "1.200 µg", "29.09.2026", "VERSÃO 4.0", "R$ +5.720.40", "R$-350,00", "RS-0.01", "R$ 10,00"]
    table = _ocr_table([_ocr_cell(0, n, (20 * n, 0, 20 * n + 20, 10), (text, (20 * n, 0, 20 * n + 20, 10)))
                        for n, text in enumerate(texts)], 1, len(texts))
    assert harmonize_value_surfaces(table)
    assert [c.text for c in table.cells] == [
        "1,25 mm", "0,0025 g", "7,3 kg", "1.200 µg", "29.09.2026", "VERSÃO 4.0", "R$ +5.720,40", "R$ -350,00", "R$ -0,01", "R$ 10,00"]


def test_dot_decimal_tables_and_native_text_are_not_harmonized():
    us = _ocr_table([_ocr_cell(0, 0, (0, 0, 20, 10), ("US$ 1,234.50", (0, 0, 20, 10))),
                     _ocr_cell(0, 1, (20, 0, 40, 10), ("0.25", (20, 0, 40, 10)))], 1, 2)
    assert not harmonize_value_surfaces(us)
    native = _ocr_table([_ocr_cell(0, 0, (0, 0, 20, 10), ("1,25", (0, 0, 20, 10))),
                         _ocr_cell(0, 1, (20, 0, 40, 10), ("0.25", (20, 0, 40, 10)))], 1, 2)
    native.cells[1].tokens[0] = replace(native.cells[1].tokens[0], sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "n")])
    assert not harmonize_value_surfaces(native)
    assert [c.text for c in native.cells] == ["1,25", "0.25"]
