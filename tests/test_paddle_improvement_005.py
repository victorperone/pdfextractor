"""Regressões da run Paddle 005 (corpus/PaddleOCRImprovement.md, §11 e §12).

002-P1-02: páginas digitalizadas giradas 90°/180° com duas colunas (V4 p110,
           p112) eram lidas linha a linha através das colunas: a
           ``TextColumnPolicy`` ignorava observações com ``rotation``. Agora
           analisa a página no referencial de leitura.
005-P1-01: a política desistia da página inteira quando alguma linha da região
           trazia uma observação ausente da lista de tokens (células de
           tabela já reclamadas, V11 p96, três colunas). A linha fica solta na
           sua posição, salvo se atravessar uma calha.
"""
from __future__ import annotations

from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.text_columns import LEGACY_TEXT_COLUMN_POLICY, TextColumnPolicy
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

W, H = 595.0, 842.0
PAGE = BBox(0, 0, W, H)
POLICY = TextColumnPolicy("test-columns", enabled=True)
WORDS = "No procedimento examinado a unidade registrou as ocorrências do período".split()


def _upright_columns(width, top, count=12, pitch=9.0, columns=2):
    """``(column, line, upright box)`` of prose columns in a reading frame ``width`` wide."""
    margin, gap = 40.0, 24.0
    column_width = (width - 2 * margin - (columns - 1) * gap) / columns
    boxes = []
    for column in range(columns):
        x0 = margin + column * (column_width + gap)
        for line in range(count):
            y = top + line * pitch
            boxes.append((column, line, BBox(x0, y, x0 + column_width, y + 7)))
    return boxes


def _to_page(box: BBox, rotation: int) -> BBox:
    """Page box whose ``_virtual_bbox`` (upright reading frame) is *box*."""
    if rotation == 180:
        return BBox(W - box.x1, H - box.y1, W - box.x0, H - box.y0)
    if rotation == 90:  # reading frame H wide; its y runs over [H - W, H]
        return BBox(H - box.y1, box.x0, H - box.y0, box.x1)
    raise ValueError(rotation)


def _token(text, box, rotation=0):
    return OcrToken(text, box, 0.98, None, SourceKind.OCR_PAGE, rotation=rotation,
                    ocr_provenance=OcrProvenance(engine="paddle"))


def _rotated_page(rotation, columns=2):
    width, top = (H, H - W + 60.0) if rotation == 90 else (W, 80.0)
    tokens = []
    for column, line, box in _upright_columns(width, top, columns=columns):
        text = f"c{column}l{line} " + " ".join(WORDS[(line + column) % 5:(line + column) % 5 + 5])
        tokens.append(_token(text, _to_page(box, rotation), rotation))
    return tokens


def _flow_texts(flows):
    return [[line.text.split()[0] for line in flow] for flow in flows]


# --- 002-P1-02 -----------------------------------------------------------------


def test_two_columns_read_at_180_degrees_are_read_column_by_column():
    tokens = _rotated_page(180)
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    assert [line.text.split()[0] for line in lines][:2] == ["c0l0", "c1l0"]  # o defeito: linha a linha

    flows = POLICY.arrange(lines, tokens, 0, PAGE)

    assert flows is not None
    assert _flow_texts(flows) == [[f"c0l{index}" for index in range(12)], [f"c1l{index}" for index in range(12)]]
    # As linhas são refeitas no referencial da página, a partir das observações originais.
    page_boxes = {token.bbox for token in tokens}
    assert all(token.bbox in page_boxes for flow in flows for line in flow
               for token in line.tokens if token.text.strip())


def test_two_columns_read_at_90_degrees_are_read_column_by_column():
    tokens = _rotated_page(90)
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    flows = POLICY.arrange(lines, tokens, 0, PAGE)

    assert flows is not None
    assert _flow_texts(flows) == [[f"c0l{index}" for index in range(12)], [f"c1l{index}" for index in range(12)]]


def test_single_column_rotated_page_and_legacy_policy_are_untouched():
    single = _rotated_page(180, columns=1)
    assert POLICY.arrange(reconstruct_ocr_lines(single, 0, PAGE), single, 0, PAGE) is None
    tokens = _rotated_page(180)
    assert LEGACY_TEXT_COLUMN_POLICY.arrange(reconstruct_ocr_lines(tokens, 0, PAGE), tokens, 0, PAGE) is None


def test_mixed_rotations_keep_caller_order():
    tokens = _rotated_page(180)
    tokens[0] = _token(tokens[0].text, tokens[0].bbox, 0)
    assert POLICY.arrange(reconstruct_ocr_lines(tokens, 0, PAGE), tokens, 0, PAGE) is None


# --- 005-P1-01 -----------------------------------------------------------------


def _upright_page_with_table_row():
    tokens = [_token(f"c{column}l{line} " + " ".join(WORDS[line % 5:line % 5 + 5]), box)
              for column, line, box in _upright_columns(W, 80.0, columns=3)]
    # Uma linha de tabela abaixo das colunas, cujas células foram reclamadas
    # pela tabela: estão nas linhas da região, não na lista de tokens.
    cells = [_token("Triagem", BBox(60, 400, 100, 408)), _token("Registro", BBox(130, 400, 175, 408))]
    return tokens, cells


def test_line_with_claimed_table_cells_stays_loose_and_columns_still_read():
    tokens, cells = _upright_page_with_table_row()
    lines = reconstruct_ocr_lines(tokens + cells, 0, PAGE)

    flows = POLICY.arrange(lines, tokens, 0, PAGE)

    assert flows is not None
    texts = _flow_texts(flows)
    assert texts[:3] == [[f"c{column}l{index}" for index in range(12)] for column in range(3)]
    assert texts[3] == ["Triagem"]  # a linha solta, na sua posição (abaixo das colunas)


def test_loose_line_running_across_a_gutter_keeps_caller_order():
    tokens, _ = _upright_page_with_table_row()
    # Uma linha solta à altura das colunas, atravessando as calhas.
    crossing = _token("Responsabilidade pela guarda de todos os volumes", BBox(60, 120, 520, 127))
    lines = reconstruct_ocr_lines(tokens + [crossing], 0, PAGE)

    assert POLICY.arrange(lines, tokens, 0, PAGE) is None
