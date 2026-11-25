"""Regressões da run Paddle 006 (corpus/PaddleOCRImprovement.md, §13 e §14).

005-P1-02: o Paddle lê a página inclinada endireitada (deskew), mas a grade da
           tabela era procurada na imagem não endireitada: a caixa da tabela
           cortava a 1ª coluna e os valores mudavam de linha (V11 p63 a 8°,
           p72; V4 p156–p180). Com ``PageRasterAlignmentPolicy`` (só Paddle) a
           grade é procurada na imagem girada pelo mesmo ângulo.
006-P0-01: uma linha de prosa unida à fileira de células da tabela ao lado
           (V9 p36, tabela girada 90° ao lado do parágrafo) era reclamada
           inteira pela tabela, e a prosa sumia. ``TextColumnPolicy`` devolve
           as observações fora da caixa da tabela como prosa.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from structured_pdf_text.api import _append_hybrid_ocr_regions, _page_raster_alignment
from structured_pdf_text.document import (
    OcrProvenance, OcrToken, SourceKind, StructuredTable, TableCell, TableFragment, TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.paddle import _deskew_image
from structured_pdf_text.ocr.pipeline.raster_alignment import (
    LEGACY_PAGE_RASTER_ALIGNMENT_POLICY, PageRasterAlignmentPolicy,
)
from structured_pdf_text.ocr.pipeline.text_columns import LEGACY_TEXT_COLUMN_POLICY, TextColumnPolicy
from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

PAGE = BBox(0, 0, 595, 842)
ALIGN = PaddlePipeline.page_raster_alignment_policy


def _engine(diagnostics):
    return SimpleNamespace(last_result=SimpleNamespace(engine_diagnostics=diagnostics))


# --- 005-P1-02 -----------------------------------------------------------------


def test_only_paddle_aligns_the_page_raster():
    assert ALIGN.enabled and ALIGN.engine_name == "paddle"
    assert not LEGACY_PAGE_RASTER_ALIGNMENT_POLICY.enabled
    # Motores sem a política (e o facade de qualquer outro pipeline) ficam no legado.
    assert _page_raster_alignment(SimpleNamespace()) is LEGACY_PAGE_RASTER_ALIGNMENT_POLICY
    image = Image.new("RGB", (40, 30), "white")
    assert LEGACY_PAGE_RASTER_ALIGNMENT_POLICY.align(image, 8.0) is image
    assert LEGACY_PAGE_RASTER_ALIGNMENT_POLICY.page_angle(_engine({"paddle": {"deskew_angle": 8.02}})) == 0.0


@pytest.mark.parametrize("diagnostics, expected", [
    ({"paddle": {"deskew_angle": 8.02}}, 8.02),
    ({"paddle": {"deskew_angle": -3.41}}, -3.41),
    ({"paddle": {"deskew_angle": 0.0}}, 0.0),      # página não endireitada
    ({"paddle": {"deskew_angle": 0.3}}, 0.0),      # abaixo do mínimo do motor
    ({"paddle": {"deskew_angle": 45.0}}, 0.0),     # fora da faixa do motor
    ({"paddle": {"deskew_angle": float("nan")}}, 0.0),
    ({"paddle": {"deskew_angle": True}}, 0.0),
    ({"paddle": {}}, 0.0),
    ({"rapidocr": {"deskew_angle": 8.0}}, 0.0),    # outro motor
    (None, 0.0),
])
def test_page_angle_comes_from_the_last_page_call(diagnostics, expected):
    assert ALIGN.page_angle(_engine(diagnostics)) == expected


def _skewed_page(angle):
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    for row in range(6):
        draw.rectangle((60, 60 + row * 50, 540, 72 + row * 50), fill="black")
    return image.rotate(angle, resample=Image.BICUBIC, fillcolor="white")


def test_aligned_raster_matches_the_page_the_engine_read():
    page = _skewed_page(-6.0)
    straightened, angle = _deskew_image(page)
    assert abs(angle) > 5.0

    aligned = ALIGN.align(page, angle)

    assert isinstance(aligned, Image.Image) and aligned.size == page.size
    # O ângulo reportado é arredondado a 0,01°: diferença sub-pixel.
    difference = np.abs(np.asarray(aligned, dtype=np.int16) - np.asarray(straightened, dtype=np.int16))
    assert difference.mean() < 1.0
    array = ALIGN.align(np.asarray(page), angle)
    assert isinstance(array, np.ndarray) and array.shape == np.asarray(page).shape


# --- 006-P0-01 -----------------------------------------------------------------


def _token(text, x0, y0, x1, y1):
    return OcrToken(text, BBox(x0, y0, x1, y1), 0.98, None, SourceKind.OCR_PAGE,
                    ocr_provenance=OcrProvenance(engine="paddle"))


# V9 p36: parágrafo à esquerda, tabela de 4 colunas à direita; cada linha de
# prosa fica na altura de uma fileira de células.
TABLE_BOX = BBox(300, 230, 520, 330)
PROSE = [_token("A conclusão considera os riscos operacionais da", 63, 246, 250, 257),
         _token("etapa, a integridade das evidências e a", 63, 258, 222, 268),
         _token("pelos responsáveis técnicos. A revisão identificou", 63, 296, 262, 307)]
CELLS = [_token(text, 310 + 55 * column, 247 + 50 * row, 340 + 55 * column, 256 + 50 * row)
         for row, texts in enumerate((("004", "003", "002", "001"), ("10", "08", "01", "06")))
         for column, text in enumerate(texts)]


def _table():
    cells = [TableCell(row, column, 1, 1, BBox(300 + 55 * column, 230 + 50 * row, 355 + 55 * column, 280 + 50 * row),
                       token.text, [token], 0.99)
             for token, (row, column) in zip(CELLS, [(r, c) for r in range(2) for c in range(4)])]
    return StructuredTable("page-1:visual-grid", [TableFragment(0, TABLE_BOX, 0, 2)], cells, 4, 2, 0.9,
                           TableMethod.VISUAL_MODEL)


def _lines():
    tokens = PROSE + CELLS
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert any("conclusão" in line.text and "004" in line.text for line in lines)  # o defeito
    return lines, tokens


# Página raster: a região de corpo é de OCR de página inteira.
COMPLEXITY = SimpleNamespace(full_page_ocr_candidate=True, visual_recovery_needed=False, reasons=[],
                             native_text_score=0.0)


def _body_text(text_columns):
    lines, tokens = _lines()
    regions = []
    _append_hybrid_ocr_regions(regions=regions, tables=[_table()], unmatched_lines=lines, unmatched_tokens=tokens,
                               page_index=0, page_bbox=PAGE, complexity=COMPLEXITY, text_columns=text_columns)
    table = [region for region in regions if region.kind.value == "table"]
    body = " ".join(line.text for region in regions if region.kind.value != "table" for line in region.ocr_lines)
    return body, table


def test_prose_joined_with_a_cell_row_is_returned_to_the_body():
    body, table = _body_text(PaddlePipeline.text_column_policy)

    assert "A conclusão considera os riscos operacionais da" in body
    assert "pelos responsáveis técnicos. A revisão identificou" in body
    assert "etapa, a integridade" in body
    assert not any(cell in body.split() for cell in ("004", "003", "08", "06"))  # as células ficam na tabela
    assert len(table) == 1


def test_other_engines_keep_the_whole_line_with_the_table():
    body, _ = _body_text(LEGACY_TEXT_COLUMN_POLICY)
    assert "A conclusão considera" not in body
    assert "etapa, a integridade" in body


def test_release_keeps_owned_and_rotated_observations():
    policy = TextColumnPolicy("test-columns", enabled=True)
    lines, tokens = _lines()
    line = next(line for line in lines if "conclusão" in line.text)

    def owned(token):
        return token.bbox.x0 >= TABLE_BOX.x0

    released = policy.release_unowned(line, tokens, owned, 0, PAGE)
    assert [item.text for item in released] == ["A conclusão considera os riscos operacionais da"]
    assert LEGACY_TEXT_COLUMN_POLICY.release_unowned(line, tokens, owned, 0, PAGE) == []
    assert policy.release_unowned(line, tokens, lambda token: True, 0, PAGE) == []
    # Observações ausentes da lista: nada é devolvido.
    assert policy.release_unowned(line, CELLS, owned, 0, PAGE) == []
