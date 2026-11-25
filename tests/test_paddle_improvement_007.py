"""Regressões da run Paddle 007 (corpus/PaddleOCRImprovement.md, §15 e §16).

007-P0-01: numa página reta com imagens coladas tortas (V9 p23, colunas raster
           a 4°), cada linha inclinada vira uma caixa alinhada aos eixos com o
           dobro da altura do texto. As caixas de linhas vizinhas se sobrepõem e
           a reconstrução junta pedaços de linhas diferentes
           ("A divergênciaprevistos,"). O Paddle descartava o quadrilátero do
           detector (``rec_polys``); a reconstrução do núcleo já agrupa linhas
           inclinadas pelo quadrilátero (``_dominant_slope``), como faz com o
           EasyOCR.
007-P1-01: caderno com espiral (V11 p62): cada anel vira uma observação "C"
           isolada, da altura de duas linhas, ao lado do fim das linhas. A
           reconstrução une o "C" a uma linha, que passa a atravessar a margem,
           e linhas inteiras saem no fim da página. A política de glifos
           isolados do Paddle recusa colunas de glifos iguais e altos fora da
           extensão horizontal do texto (marcas de encadernação).
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.paddle import _tokens_from_result
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.pipeline.policies import IsolatedGlyphAdmissionPolicy
from structured_pdf_text.ocr.pipeline.text_columns import LEGACY_TEXT_COLUMN_POLICY, TextColumnPolicy
from structured_pdf_text.text.reading_order import _order_prose_lines
from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
from structured_pdf_text.ocr.reconstruct import _dominant_slope, reconstruct_ocr_lines

PAGE = BBox(0.0, 0.0, 595.0, 842.0)


class _Image:
    size = (1190, 1684)


def _quad(x0, y0, width, height, slope):
    """Quadrilátero do PP-OCR (canto superior esquerdo, sentido horário)."""
    rise = slope * width
    return [[x0, y0], [x0 + width, y0 + rise], [x0 + width, y0 + rise + height], [x0, y0 + height]]


def _bounds(quad):
    xs, ys = [point[0] for point in quad], [point[1] for point in quad]
    return [min(xs), min(ys), max(xs), max(ys)]


# V9 p23: duas colunas de uma imagem colada a 4° (inclinação 0,07), linhas a
# 19 px, texto de 17 px; a caixa de cada linha tem ≈ 36 px.
COLUMNS = {
    130: ["Foram comparados os prazos", "previstos, as notas de recebimento e",
          "os saldos informados. A divergência", "encontrada exige justificativa antes"],
    460: ["Marina Duarte destacou a necessidade", "de conferir as unidades de medida, o",
          "número do empenho e a ordem", "cronológica dos lançamentos. A"],
}


def _raw(slope=0.07):
    texts, quads = [], []
    for x0, lines in COLUMNS.items():
        for row, text in enumerate(lines):
            texts.append(text)
            quads.append(_quad(x0, 500 + 19 * row + (x0 - 130) * slope, 265, 17, slope))
    return [{"rec_texts": texts, "rec_scores": [0.97] * len(texts),
             "rec_boxes": [_bounds(quad) for quad in quads], "rec_polys": quads}]


def test_paddle_tokens_carry_the_detector_quadrilateral():
    tokens = _tokens_from_result(_raw(), 0, _Image(), PAGE)

    assert all(token.polygon is not None and len(token.polygon) == 4 for token in tokens)
    for token in tokens:
        # O quadrilátero fica dentro da própria caixa (a autorização exige).
        assert all(token.bbox.x0 <= p.x <= token.bbox.x1 and token.bbox.y0 <= p.y <= token.bbox.y1
                   for p in token.polygon)
    assert _dominant_slope(tokens) == pytest.approx(0.07, abs=1e-6)


def test_slanted_lines_are_not_spliced_with_their_neighbours():
    tokens = _tokens_from_result(_raw(), 0, _Image(), PAGE)

    # Cada coluna, como a separa a TextColumnPolicy: a mesma linha visual.
    left = [token for token in tokens if token.bbox.x0 < 200]
    assert [line.text for line in reconstruct_ocr_lines(left, 0, PAGE)] == COLUMNS[130]
    # Na página inteira, as linhas das duas colunas seguem a mesma inclinação.
    rows = [line.text for line in reconstruct_ocr_lines(tokens, 0, PAGE)]
    assert rows == [f"{a} {b}" for a, b in zip(COLUMNS[130], COLUMNS[460])]
    # O defeito: sem o quadrilátero, a caixa alta junta pedaços de linhas vizinhas.
    flat = [replace(token, polygon=None) for token in left]
    assert [line.text for line in reconstruct_ocr_lines(flat, 0, PAGE)] != COLUMNS[130]


def test_level_lines_keep_their_grouping():
    tokens = _tokens_from_result(_raw(slope=0.0), 0, _Image(), PAGE)
    assert _dominant_slope(tokens) == 0.0
    flat = [replace(token, polygon=None) for token in tokens]
    assert ([line.text for line in reconstruct_ocr_lines(tokens, 0, PAGE)]
            == [line.text for line in reconstruct_ocr_lines(flat, 0, PAGE)])


@pytest.mark.parametrize("change", ["no_polys", "turned_page", "short_list", "bad_quad", "turned_document"])
def test_quadrilateral_is_dropped_outside_the_plain_frame(change):
    raw = _raw()
    kwargs = {}
    if change == "no_polys":
        del raw[0]["rec_polys"]
    elif change == "turned_page":
        kwargs["page_rotation"] = 90
    elif change == "short_list":
        raw[0]["rec_polys"] = raw[0]["rec_polys"][:-1]
    elif change == "bad_quad":
        raw[0]["rec_polys"] = [quad[:3] for quad in raw[0]["rec_polys"]]
    elif change == "turned_document":
        np = pytest.importorskip("numpy")
        raw[0]["doc_preprocessor_res"] = {"angle": 180, "output_img": np.zeros((1684, 1190, 3), dtype=np.uint8)}

    tokens = _tokens_from_result(raw, 0, _Image(), PAGE, **kwargs)

    assert tokens and all(token.polygon is None for token in tokens)
    # A caixa não muda em nenhum caso.
    plain = _tokens_from_result(_raw(), 0, _Image(), PAGE, **kwargs)
    assert [token.bbox for token in tokens] == [token.bbox for token in plain] or change in ("turned_document",)


# V9 p81: página de uma coluna a ≈ 1° depois do deskew; as caixas de linhas
# vizinhas se sobrepõem e os inícios das linhas oscilam meio ponto.
PROSE = ["As informações financeiras foram conciliadas com os comprovantes de pagamento. Recomenda-se",
         "manter a documentação disponível até o encerramento da análise. Em atendimento ao despacho",
         "anterior, a unidade apresentou registros complementares que permitiram confirmar os responsáveis e",
         "os valores declarados. A Gerência de Contratos consolidou os resultados relativos à contratação de",
         "suporte tecnológico, após conferência das evidências apresentadas pelas equipes da região Sudeste."]


def _tilted_page():
    quads = [_quad(165 - row, 486 + 22.5 * row, 840 - 20 * (row % 2), 20, 0.018) for row in range(len(PROSE))]
    raw = [{"rec_texts": PROSE, "rec_scores": [0.97] * len(PROSE),
            "rec_boxes": [_bounds(quad) for quad in quads], "rec_polys": quads}]
    tokens = _tokens_from_result(raw, 0, _Image(), PAGE)
    return reconstruct_ocr_lines(tokens, 0, PAGE), tokens


def test_tilted_single_column_is_read_in_its_rectified_frame():
    lines, tokens = _tilted_page()
    assert [line.text for line in lines] == PROSE
    # O defeito: depois do título, a ordenação de prosa do núcleo lê as caixas
    # sobrepostas como pares de um formulário e troca linhas vizinhas.
    assert [line.text for line in _order_prose_lines(lines[1:], 430)[0]] != PROSE[1:]

    flows = PaddlePipeline.text_column_policy.arrange(lines, tokens, 0, PAGE)

    assert flows is not None and len(flows) == 1
    assert [line.text for line in flows[0]] == PROSE
    assert LEGACY_TEXT_COLUMN_POLICY.arrange(lines, tokens, 0, PAGE) is None
    assert TextColumnPolicy("test-columns", enabled=True).arrange(lines, tokens, 0, PAGE) is None


def test_level_single_column_keeps_the_callers_order():
    lines, tokens = _tilted_page()
    level = [replace(token, polygon=None) for token in tokens]
    assert PaddlePipeline.text_column_policy.arrange(reconstruct_ocr_lines(level, 0, PAGE), level, 0, PAGE) is None


# --- 007-P1-01 -----------------------------------------------------------------

ADMISSION = PaddlePipeline.admission_policy


def _evidence(token_id, text, x0, y0, x1, y1, confidence=0.98):
    return SimpleNamespace(token_id=token_id, text=text, bbox=BBox(x0, y0, x1, y1), confidence_normalized=confidence)


def _rejected(tokens, policy=ADMISSION, goal=WorkGoal.PAGE):
    return policy.attempt_rejections(tokens, SimpleNamespace(goal=goal))


# V11 p62 (pontos): linhas de 71 a 522, corpo de 7–9 pt; anéis a 532–552, 19 pt.
BODY = [_evidence(f"line{i}", "No expediente V11-P062R201, a cadeia de custódia", 71, 145 + 9.5 * i, 519 + (i % 3), 153 + 9.5 * i)
        for i in range(48)]


def _rings(text="C", count=6, x0=532.0, height=19.0):
    return [_evidence(f"ring{i}", text, x0 + (i % 2), 164 + 64 * i, x0 + 19.5, 164 + 64 * i + height) for i in range(count)]


def test_spiral_rings_beside_the_text_are_rejected():
    rings = _rings()
    reasons = _rejected(BODY + rings)
    assert set(reasons) == {ring.token_id for ring in rings}
    assert set(reasons.values()) == {"paddle_binding_mark"}
    # Furos de um lado ou de outro do texto.
    holes = [_evidence(f"hole{i}", "O", 20, 200 + 150 * i, 40, 220 + 150 * i) for i in range(4)]
    assert set(_rejected(BODY + holes)) == {hole.token_id for hole in holes}


@pytest.mark.parametrize("case", ["three", "short", "inside", "different", "bullets", "legacy", "figure"])
def test_text_columns_and_marks_that_are_not_bindings_are_kept(case):
    policy, goal, extra = ADMISSION, WorkGoal.PAGE, _rings()
    if case == "three":
        extra = _rings(count=3)
    elif case == "short":       # "X" de uma coluna de marcação: altura do texto
        extra = _rings(text="X", height=8.0)
    elif case == "inside":      # coluna de glifos dentro da extensão do texto
        extra = _rings(x0=300.0)
    elif case == "different":   # numeração de linhas: glifos diferentes
        extra = [replace_text(ring, str(i)) for i, ring in enumerate(_rings())]
    elif case == "bullets":     # marcadores não são alfanuméricos
        extra = _rings(text="•")
    elif case == "legacy":
        policy = IsolatedGlyphAdmissionPolicy("test-admission", "test")
    elif case == "figure":
        goal = WorkGoal.FIGURE
    reasons = _rejected(BODY + extra, policy=policy, goal=goal)
    assert not any(reason.endswith("binding_mark") for reason in reasons.values())


def replace_text(evidence, text):
    return SimpleNamespace(**{**vars(evidence), "text": text})
