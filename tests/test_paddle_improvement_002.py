"""Regressões da run Paddle 002 (corpus/PaddleOCRImprovement.md).

001-P1-01: colunas de texto corrido lidas linha a linha através das colunas
           em páginas raster (V10: três colunas acima, duas abaixo; inclusive
           digitalizações inclinadas ±4,5°). ``TextColumnPolicy`` do Paddle.
001-P1-02: glifos inventados em páginas sem texto (V10: "O", "Q", "典",
           "新场督局"). ``IsolatedGlyphAdmissionPolicy`` do Paddle.
001-P0-03: classificador de orientação de documento gira 90° uma página
           reta com sombra lateral (V4 p152/p160).
"""
from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.document import (
    LayoutRegion, OcrProvenance, OcrToken, RegionDecision, RegionKind, RegionQuality, SourceKind,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.heading import _can_merge_heading_fragments
from structured_pdf_text.layout.ocr_aware import reconstruct_ocr_layout
from structured_pdf_text.ocr.paddle import _quarter_turn_contradicted
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.pipeline.policies import IsolatedGlyphAdmissionPolicy
from structured_pdf_text.ocr.pipeline.text_columns import (
    LEGACY_TEXT_COLUMN_POLICY, TextColumnPolicy, crosses_gutter,
)
from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
from structured_pdf_text.text.reading_order import order_regions

PAGE = BBox(0, 0, 595, 842)
POLICY = TextColumnPolicy("test-columns", enabled=True)
WORDS = "No procedimento examinado a unidade registrou as ocorrências do período".split()


def _token(text, x0, y0, x1, y1, confidence=0.99):
    return OcrToken(text, BBox(x0, y0, x1, y1), confidence, None, SourceKind.OCR_PAGE,
                    ocr_provenance=OcrProvenance(engine="paddle"))


def _column_lines(column, x0, x1, y0, count, pitch=9.0, tilt=0.0):
    """One line-level observation per column line, as PP-OCR returns them."""
    tokens = []
    for index in range(count):
        y = y0 + index * pitch
        shift = -tilt * y  # the left edge of a skewed column drifts by -tilt per unit of height
        text = f"c{column}l{index} " + " ".join(WORDS[(index + column) % 5:(index + column) % 5 + 5])
        tokens.append(_token(text, x0 + shift, y + tilt * x0, x1 + shift, y + 7 + tilt * x1))
    return tokens


def _three_over_two(tilt=0.0):
    title = [_token("Boletim administrativo sintético", 55, 60, 420, 76)]
    top = (_column_lines(0, 55, 196, 170, 12, tilt=tilt) + _column_lines(1, 224, 365, 170, 12, tilt=tilt)
           + _column_lines(2, 392, 535, 170, 12, tilt=tilt))
    bottom = _column_lines(3, 55, 285, 450, 10, tilt=tilt) + _column_lines(4, 308, 538, 450, 10, tilt=tilt)
    return title + top + bottom


def _flow_texts(flows):
    return [[line.text.split()[0] for line in flow] for flow in flows]


# --- 001-P1-01: colunas de texto corrido ---------------------------------------

def test_rows_crossing_three_then_two_columns_are_read_column_by_column():
    tokens = _three_over_two()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert any("c0l0" in line.text and "c1l0" in line.text for line in lines)  # o defeito

    flows = POLICY.arrange(lines, tokens, 0, PAGE)

    assert flows is not None
    firsts = [flow[0].text.split()[0] for flow in flows]
    assert firsts == ["Boletim", "c0l0", "c1l0", "c2l0", "c3l0", "c4l0"]
    assert _flow_texts(flows)[1] == [f"c0l{index}" for index in range(12)]
    assert _flow_texts(flows)[5] == [f"c4l{index}" for index in range(10)]


def test_skewed_scan_columns_are_read_column_by_column():
    tokens = _three_over_two(tilt=0.08)

    flows = POLICY.arrange(reconstruct_ocr_lines(tokens, 0, PAGE), tokens, 0, PAGE)

    assert flows is not None
    assert _flow_texts(flows)[1] == [f"c0l{index}" for index in range(12)]
    assert _flow_texts(flows)[3] == [f"c2l{index}" for index in range(12)]


def test_disabled_policy_and_single_column_keep_caller_order():
    tokens = _three_over_two()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert LEGACY_TEXT_COLUMN_POLICY.arrange(lines, tokens, 0, PAGE) is None
    single = _column_lines(0, 55, 535, 170, 12)
    assert POLICY.arrange(reconstruct_ocr_lines(single, 0, PAGE), single, 0, PAGE) is None


def test_only_paddle_enables_text_columns():
    assert PaddlePipeline.text_column_policy.enabled
    assert not getattr(AuthorizedBackendPipeline, "text_column_policy", LEGACY_TEXT_COLUMN_POLICY).enabled


def test_layout_keeps_column_flows_in_order_and_separate():
    tokens = _three_over_two()
    region = LayoutRegion("page-1:ocr-primary", RegionKind.TEXT, PAGE, None, [], tokens,
                          RegionQuality(decision=RegionDecision.OCR_REGION),
                          ocr_lines=reconstruct_ocr_lines(tokens, 0, PAGE))

    regions = reconstruct_ocr_layout([region], PAGE, text_columns=POLICY)
    ordered, _ = order_regions(regions)

    starts = [r.ocr_lines[0].text.split()[0] for r in ordered]
    assert starts == ["Boletim", "c0l0", "c1l0", "c2l0", "c3l0", "c4l0"]
    assert all(r.ocr_flow is not None for r in ordered)


def test_titles_of_different_flows_never_merge():
    first = LayoutRegion("a", RegionKind.TITLE, BBox(55, 100, 200, 110), None, [], [],
                         RegionQuality(decision=RegionDecision.OCR_REGION), ocr_flow=("p", 1))
    second = LayoutRegion("b", RegionKind.TITLE, BBox(55, 112, 200, 122), None, [], [],
                          RegionQuality(decision=RegionDecision.OCR_REGION), ocr_flow=("p", 2))
    assert not _can_merge_heading_fragments(first, second)


def test_gutter_between_prose_columns_vetoes_a_borderless_table():
    gutters, slope = POLICY.layout(_three_over_two(), PAGE)
    assert gutters
    assert crosses_gutter(BBox(55, 170, 535, 270), gutters, slope)
    assert not crosses_gutter(BBox(55, 60, 420, 76), gutters, slope)


# --- 001-P1-02: glifos isolados em páginas sem texto ---------------------------

ADMISSION = IsolatedGlyphAdmissionPolicy("test-admission", "test")


def _evidence(token_id, text, box, confidence=0.98):
    return SimpleNamespace(token_id=token_id, text=text, bbox=box, confidence_normalized=confidence)


def _rejected(tokens, goal=WorkGoal.PAGE):
    return set(ADMISSION.attempt_rejections(tokens, SimpleNamespace(goal=goal)))


def test_lone_shapes_on_textless_page_are_rejected():
    # Uma página negativa por caso, como no V10 (p22, p7, p2, p61).
    for token in (
        _evidence("emblem", "O", BBox(32, 133, 132, 217), 0.97),
        _evidence("seal", "新场督局", BBox(400, 59, 517, 161), 0.99),
        _evidence("mark", "_", BBox(328, 74, 336, 78), 0.98),
        _evidence("digit", "1", BBox(295, 494, 302, 504), 0.58),
    ):
        assert _rejected([token]) == {token.token_id}
    # Glifos que só têm um ao outro por vizinho continuam admitidos.
    pair = [_evidence("a", "Q", BBox(100, 100, 140, 140)), _evidence("b", "O", BBox(150, 100, 190, 140))]
    assert _rejected(pair) == set()


def test_text_page_numbers_and_glyphs_beside_text_are_kept():
    body = [_evidence(f"line{i}", "texto corrido da página", BBox(55, 100 + 10 * i, 300, 108 + 10 * i)) for i in range(5)]
    tokens = body + [
        _evidence("page-number", "12", BBox(290, 800, 302, 808), 0.99),
        _evidence("check", "X", BBox(40, 100, 48, 108), 0.62),
    ]
    assert _rejected(tokens) == set()
    assert _rejected([_evidence("emblem", "O", BBox(32, 133, 132, 217))], goal=WorkGoal.FIGURE) == set()
    # V4 p99: uma linha inteira mal lida como "de c" (0,68) não é um glifo isolado.
    assert _rejected([_evidence("line", "de c", BBox(31, 416, 372, 423), 0.68)]) == set()


def test_only_paddle_uses_isolated_glyph_admission():
    assert isinstance(PaddlePipeline.admission_policy, IsolatedGlyphAdmissionPolicy)
    assert not hasattr(AuthorizedBackendPipeline.admission_policy, "attempt_rejections")


# --- 001-P0-03: quarto de volta do classificador de orientação -------------------

def test_quarter_turn_is_contradicted_by_horizontal_boxes():
    wide = [_token("00000042", 100, 100 + 20 * i, 180, 110 + 20 * i) for i in range(5)]
    tall = [_token("00000042", 100 + 20 * i, 100, 110 + 20 * i, 180) for i in range(5)]
    assert _quarter_turn_contradicted(wide)
    assert not _quarter_turn_contradicted(tall)
    assert not _quarter_turn_contradicted(wide[:2])
