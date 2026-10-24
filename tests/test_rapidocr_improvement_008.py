"""Run 008 corrections from corpus/RapidOCRImprovement.md (section 13).

Each test pins one cause found in the run 007 review: upside-down page frame,
orientation choice, checkbox options inside a row, grid artefacts, skewed OCR
rows, prose gutters, raised marks in cells, vertical text, one-word frames,
list nesting, one-line code, mixed emphasis, region order and the versioned
supplemental audit.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from structured_pdf_text.assemble.content import _native_text_units
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    EvidenceRef,
    LayoutRegion,
    NativeCharacter,
    NativeObjectEvidence,
    NativePageEvidence,
    OcrToken,
    PathEvidence,
    PageContentBlock,
    Point,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredListItem,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.evaluation.supplemental import critical_entities
from structured_pdf_text.geometry import BBox
from structured_pdf_text.native.reading_frame import to_upright_frame, upside_down
from structured_pdf_text.ocr.pipeline.authorization import union_contains
from structured_pdf_text.ocr.pipelines.rapidocr.policy import (
    ADMISSION_POLICY,
    ORIENTATION_POLICY,
    SELECTION_MARK_POLICY,
)
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
from structured_pdf_text.renderers.markdown import _render_content_block
from structured_pdf_text.tables.detector import _cell_text_preserving_vertical_lines, _scalar_identifier_region
from structured_pdf_text.tables.text_tracks import _merge_same_baseline_lines
from structured_pdf_text.tables.visual import VisualGrid, _without_sliver_slots
from structured_pdf_text.text.reading_order import _region_pair_score


def _token(text, box, *, font="DejaVuSans", weight=400, size=10.0):
    return TextToken(text=text, bbox=box, sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"t:{text}")],
                     confidence=1.0, normalized_text=text, font_name=font, font_size=size, font_weight=weight)


def _line(tokens, *, direction=WritingDirection.LEFT_TO_RIGHT, line_id=None):
    box = BBox.union_all([token.bbox for token in tokens])
    return TextLine(tokens=list(tokens), bbox=box, baseline=Baseline(y=box.y1), direction=direction,
                    native_order_min=None, native_order_max=None, line_id=line_id)


def _region(region_id, box, lines=()):
    return LayoutRegion(region_id=region_id, kind=RegionKind.TEXT, bbox=box, layout_confidence=1.0,
                        native_lines=list(lines), ocr_tokens=[], quality=RegionQuality(RegionDecision.KEEP_NATIVE))


def _ocr(text, x0, y0, x1, y1, slope=0.0):
    polygon = (Point(x0, y0), Point(x1, y0 + slope * (x1 - x0)), Point(x1, y1 + slope * (x1 - x0)), Point(x0, y1))
    return OcrToken(text=text, bbox=BBox(x0, min(y0, y0 + slope * (x1 - x0)), x1, max(y1, y1 + slope * (x1 - x0))),
                    confidence=0.99, language="pt", source=SourceKind.OCR_PAGE, polygon=polygon)


# -- P1-01/P1-02: upside-down frame and orientation decision -----------------

def test_upside_down_text_is_analysed_in_its_upright_frame():
    chars = tuple(NativeCharacter(page_index=0, char_index=i, text="a", unicode_codepoint=97,
                                  bbox=BBox(500 - 6 * i, 700, 505 - 6 * i, 710), angle=math.pi) for i in range(30))
    frame = BBox(0, 0, 600, 800)
    objects = NativeObjectEvidence(images=(), paths=(PathEvidence(0, 0, BBox(100, 50, 500, 52)),), annotations=(),
                                   structure_tree=None, page_bbox=frame, crop_bbox=frame, rotation=180)
    page = NativePageEvidence(page_index=0, bbox=frame, characters=chars, objects=objects, extracted_text="a" * 30)
    assert upside_down(chars)
    turned, visible = to_upright_frame(page, chars)
    assert turned.objects.paths[0].bbox == BBox(100, 748, 500, 750)
    assert turned.objects.rotation == 0
    assert visible[0].bbox == BBox(95, 90, 100, 100) and visible[0].angle == 0.0
    assert visible[0].bbox.x0 < visible[1].bbox.x0  # left to right in the upright frame


def test_upright_glyphs_keep_the_canonical_frame():
    chars = tuple(NativeCharacter(page_index=0, char_index=i, text="a", unicode_codepoint=97,
                                  bbox=BBox(10 + 6 * i, 10, 15 + 6 * i, 20), angle=0.0) for i in range(30))
    assert not upside_down(chars)


def test_orientation_accepts_a_confident_and_complete_turn_only():
    policy = ORIENTATION_POLICY
    assert policy.acceptance(0.834, 85.25, 0.9996, 101.97) == "confident_and_complete"
    assert policy.acceptance(0.834, 85.25, 0.9996, 40.0) is None          # little text
    assert policy.acceptance(0.95, 85.25, 0.99, 101.97) is None           # no confidence gap
    assert policy.acceptance(0.60, 40.0, 0.92, 90.0) == "more_text"
    assert policy.acceptance(0.50, 0.4, 0.9988, 1.0) is None              # one shape read as "L"


# -- P1-10 / checkbox negatives ------------------------------------------------

def _canvas(width=900, height=120):
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _row(x0, y0, x1, y1, text):
    return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, 0.99)


def _ink_word(image, x0, x1, y0=40, y1=70):
    for x in range(x0, x1, 7):
        image[y0:y1, x:x + 3] = 0


def test_empty_option_inside_a_row_splits_the_options():
    image = _canvas()
    _ink_word(image, 100, 300)                  # "DOCUMENTO CONFERIDO"
    image[40:62, 340] = image[40:62, 361] = 0   # empty box
    image[40, 340:362] = image[61, 340:362] = 0
    _ink_word(image, 400, 560)                  # "COMPLEMENTO"
    rows = SELECTION_MARK_POLICY.apply(image, [_row(100, 35, 560, 75, "DOCUMENTO CONFERIDO COMPLEMENTO")])
    assert [row[1] for row in rows] == ["DOCUMENTO CONFERIDO", "COMPLEMENTO", "☐"]


def test_grid_cells_and_bold_glyphs_are_not_check_boxes():
    image = _canvas()
    _ink_word(image, 100, 300)
    image[20:100, 340] = image[20:100, 361] = 0  # rules continuing past the "box"
    image[40, 300:420] = image[61, 300:420] = 0
    _ink_word(image, 400, 560)
    rows = [_row(100, 35, 560, 75, "Número como texto")]
    assert SELECTION_MARK_POLICY.apply(image, rows) == rows
    blob = _canvas()
    _ink_word(blob, 100, 300)
    blob[40:62, 340:362] = 0                     # solid glyph blob
    _ink_word(blob, 400, 560)
    assert SELECTION_MARK_POLICY.apply(blob, rows) == rows


def test_single_letter_table_label_is_not_a_shape_glyph():
    from ocr_pipeline_helpers import make_work
    from test_ocr_pipeline_contracts import evidence
    from dataclasses import replace
    from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
    _, request, auth = make_work(goal=WorkGoal.FIGURE)
    token = replace(evidence(auth, confidence_normalized=0.97, normalization="domain_0..1"),
                    text="C", bbox=BBox(0, 0, 13, 15.3))
    assert ADMISSION_POLICY.rejection_reason(token, request) is None
    giant = replace(token, bbox=BBox(0, 0, 44.5, 50.2), confidence_normalized=0.993)
    assert ADMISSION_POLICY.rejection_reason(giant, request) == "rapidocr_oversized_lone_figure_glyph"


# -- Tables ------------------------------------------------------------------------

def test_sliver_slots_between_double_rules_are_collapsed():
    grid = VisualGrid(x_edges=(10, 100, 200, 300), y_edges=(74, 75, 77, 87, 101, 114, 127, 141), confidence=1.0,
                      horizontal_lines=(), vertical_lines=())
    assert len(_without_sliver_slots(grid).y_edges) == 6
    normal = VisualGrid(x_edges=(10, 100, 200), y_edges=(10, 24, 38, 52), confidence=1.0,
                        horizontal_lines=(), vertical_lines=())
    assert _without_sliver_slots(normal) is normal


def test_skewed_rows_are_not_merged_into_one_track_row():
    rows = [_line([_token(text, BBox(30 + 120 * i, 100 + 3 * i + 12 * r, 90 + 120 * i, 110 + 3 * i + 12 * r))
                   for i, text in enumerate(("Tempo", "8,15", "31,31", "s"))]) for r in range(2)]
    assert len(_merge_same_baseline_lines(rows)) == 2
    cells = [_line([_token(text, BBox(30 + 120 * i, 100, 90 + 120 * i, 110))]) for i, text in enumerate(("A", "B"))]
    assert len(_merge_same_baseline_lines(cells)) == 1


def test_skewed_scan_groups_ocr_rows_along_the_tilt():
    slope = 0.05
    tokens = [_ocr(f"Linha longa {n} da página inclinada", 30, 30 + 14 * n, 330, 40 + 14 * n, slope) for n in range(3)]
    for row, y in enumerate((100, 118, 136)):
        for column, x in enumerate((30, 150, 270, 390)):
            yy = y + slope * x
            tokens.append(_ocr(f"r{row}c{column}", x, yy, x + 60, yy + 10, slope))
    lines = reconstruct_ocr_lines(tokens, 0, BBox(0, 0, 595, 842), dehyphenate=False)
    rows = [[token.text for token in line.tokens if token.text.strip()] for line in lines]
    assert ["r0c0", "r0c1", "r0c2", "r0c3"] in rows and ["r1c0", "r1c1", "r1c2", "r1c3"] in rows


def test_prose_columns_are_split_at_the_gutter_but_table_rows_are_not():
    prose = []
    for row in range(4):
        y = 100 + 14 * row
        prose += [_ocr("palavras da coluna esquerda seguem aqui", 30, y, 270, y + 10),
                   _ocr("palavras da coluna direita seguem aqui", 310, y, 560, y + 10)]
    lines = reconstruct_ocr_lines(prose, 0, BBox(0, 0, 595, 842), dehyphenate=False)
    assert len(lines) == 8
    table = [_ocr(f"T10B-{row}-{column}", 30 + 45 * column, 100 + 14 * row, 70 + 45 * column, 110 + 14 * row)
             for row in range(4) for column in range(12)]
    assert len(reconstruct_ocr_lines(table, 0, BBox(0, 0, 595, 842), dehyphenate=False)) == 4


def test_raised_marks_stay_in_place_inside_a_cell():
    word = [_token(c, BBox(142 + 4.5 * i, 183.6, 146 + 4.5 * i, 188.3)) for i, c in enumerate("code")]
    marks = [_token("`", BBox(138.2, 181.7, 140.1, 183.2)), _token("`", BBox(161.8, 181.7, 163.7, 183.2))]
    lines = [_line(marks), _line(word)]
    assert _cell_text_preserving_vertical_lines(lines, [*marks, *word]) == "`code`"


def test_rotated_cell_text_follows_its_line_order():
    letters = "CONTROLE"
    tokens = [_token(c, BBox(68, 411 - 6 * i, 74, 416 - 6 * i)) for i, c in enumerate(letters)]
    line = _line(tokens, direction=WritingDirection.TOP_TO_BOTTOM)
    line.text_override = letters
    assert _cell_text_preserving_vertical_lines([line], tokens) == "CONTROLE"


def test_one_word_status_frame_is_a_scalar_value():
    word = _region("r", BBox(40, 40, 140, 60), [_line([_token("Autorizado", BBox(45, 45, 100, 55))])])
    sentence = _region("s", BBox(40, 40, 400, 60), [_line([_token("Nota explicativa sobre o quadro.", BBox(45, 45, 300, 55))])])
    assert _scalar_identifier_region(word) and not _scalar_identifier_region(sentence)


def test_union_containment_forgives_float_rounding_only():
    authorized = (BBox(32.163545837060596, 75.3790194364675, 562.6120764037129, 140.98154114204462),)
    assert union_contains(authorized, BBox(32.33, 75.66, 562.612076403713, 140.98154114204462))
    assert not union_contains(authorized, BBox(32.33, 75.66, 562.62, 140.9))


# -- Order, lists, code and style -------------------------------------------------

def test_block_above_and_left_precedes_block_below_right():
    title = _region("t", BBox(43, 169, 137, 182))
    table = _region("x", BBox(318, 221, 536, 296))
    assert _region_pair_score(title, table, None) > 0
    upper_right = _region("b1", BBox(310, 100, 540, 200))
    lower_left = _region("a2", BBox(40, 400, 280, 500))
    assert _region_pair_score(upper_right, lower_left, None) == 0


def _block(**kwargs):
    defaults = dict(block_id="b", page_index=0, kind=ContentKind.TEXT, bbox=BBox(0, 0, 1, 1), order_index=0)
    return PageContentBlock(**{**defaults, **kwargs})


def test_children_of_ordered_items_nest_at_the_content_column():
    item = lambda marker, text, level: StructuredListItem(marker, text, level, BBox(0, 0, 1, 1), 0)
    block = _block(kind=ContentKind.LIST, list_items=[item("1.", "Pai", 0), item("•", "Filho", 1), item("10.", "Outro", 0)])
    assert _render_content_block(block, {}, preserve_hf=False) == "1. Pai\n   - Filho\n10. Outro"


def test_single_monospaced_line_with_code_syntax_is_verbatim():
    code = _line([_token('pedido_id = "HY-30001"', BBox(60, 100, 200, 110), font="DejaVuSansMono")])
    word = _line([_token("HASH-0001", BBox(60, 130, 120, 140), font="DejaVuSansMono")])
    assert _native_text_units([code]) == [([code], True)]
    assert _native_text_units([word]) == [([word], False)]


def test_mixed_emphasis_runs_render_around_words():
    block = _block(text="Negrito, itálico e tachado.", styled_runs=[
        ("Negrito", "b"), (", ", ""), ("itálico", "i"), (" e ", ""), ("tachado", "s"), (".", "")])
    assert _render_content_block(block, {}, preserve_hf=False) == "**Negrito**, *itálico* e ~~tachado~~."


# -- P3-02/P3-03: supplemental audit 1.1.0 -----------------------------------------

def test_supplemental_money_reads_sentence_commas_and_usd_locale():
    assert critical_entities("Valor R$ 48.930,75, conforme medição.")[0][("currency", "BRL:48930.75")] == 1
    assert critical_entities("US$ 1,234.50 pago")[0][("currency", "USD:1234.5")] == 1
    assert critical_entities("R$ +5.720.40")[1] == ["R$ +5.720.40"]


def test_supplemental_checkbox_ignores_literal_brackets_outside_task_lists():
    from structured_pdf_text.evaluation.supplemental import _representation
    nodes = [SimpleNamespace(metadata={"page_markdown": "| Campo |\n| --- |\n| [x] {y} *z* |\n\n- [ ] tarefa\n- ☑ feito"},
                             kind=None, parent_id=None)]
    _, marks, _ = _representation(nodes)
    assert marks == {("checkbox", "unchecked"): 1, ("checkbox", "checked"): 1}
