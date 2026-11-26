"""Regressões encontradas na auditoria da RUN 009 (output/tesseract/009).

T3-P36: a regra de "tabela girada ilegível" do pós-processamento Tesseract
        suprimia a tabela inteira por uma única palavra com Q sem U; a tabela
        multilíngue da V4 p167/p168 ("Grego | aByAQnA") sumia e a página
        continuava valid/clean, porque o pós-processamento roda depois do
        registro de conservação.
T3-P39: duas leituras do mesmo ponto vindas de chamadas diferentes do
        Tesseract, com uma caixa dentro da outra (IoU < 0.6), eram unidas na
        célula: "R$ 2.333,52333,92", "R$ER$ 845,26" (V9 p58).
"""
from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.document import ContentKind, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


def _block(text, kind=ContentKind.TEXT, suppressed=False, reason=None):
    return SimpleNamespace(order_index=0, text=text, kind=kind, suppressed=suppressed,
                           suppression_reason=reason, inline_style=None, styled_runs=[],
                           heading_level=None, list_items=[])


def _document(blocks, preserve_headers_footers=False):
    page = SimpleNamespace(page_index=166, content_blocks=blocks,
                           diagnostics=SimpleNamespace(warnings=[], facts={}))
    facts = {"preserve_headers_footers": preserve_headers_footers}
    return SimpleNamespace(pages=[page], diagnostics=SimpleNamespace(warnings=[], facts=facts))


_P167_TABLE = (
    "Idioma\tTexto\tControle\n"
    "| Português\tação, órgão, coração\tPT-TIZA\n"
    "English\tevidence and reading order\tEN-TIZA\n"
    "Grego\taByAQnA\tGR-TIZA\n"
    "Cirílico\tMockBa, naHHbie, aHanv3\tRU-TIZA"
)


def test_legible_table_with_one_impossible_word_is_kept_and_reported():
    """V4 p167: a tabela inteira sumia por "aByAQnA" (leitura latina do grego)."""
    from structured_pdf_text.ocr.tesseract.postprocess import apply_tesseract_postprocessors

    table = _block(_P167_TABLE, kind=ContentKind.TABLE)
    document = _document([_block("Controle de página: V4-P167"), table])

    apply_tesseract_postprocessors(document)

    assert table.suppressed is False
    assert table.text == _P167_TABLE
    page = document.pages[0]
    assert any(w.startswith("suspected_ocr_garble") for w in page.diagnostics.warnings)
    assert any(w.startswith("page 167: suspected_ocr_garble") for w in document.diagnostics.warnings)


def test_page_furniture_removed_by_postprocessing_is_counted_and_text_is_kept():
    """Supressões depois do registro de conservação ficam registradas no diagnóstico.

    Contrato anterior: o "$Y $H $U" era suprimido como lixo de OCR (Fix-P) e virava
    aviso. A regra foi removida (T3-P27): "$" seguido de maiúscula também é `$PATH`
    e `$A$1`. Só a mobília da página sai, contada nos fatos e sem aviso.
    """
    from structured_pdf_text.ocr.tesseract.postprocess import apply_tesseract_postprocessors

    separator = _block("Página 167 | OCR de tabela", kind=ContentKind.TITLE)
    garbled = _block("$Y $H $U", kind=ContentKind.TEXT)
    document = _document([separator, garbled])

    apply_tesseract_postprocessors(document)

    assert separator.suppressed and separator.suppression_reason == "tesseract_pagina_separator"
    assert not garbled.suppressed and garbled.text == "$Y $H $U"
    facts = document.pages[0].diagnostics.facts["tesseract_postprocess_suppressed_chars"]
    assert facts == {"tesseract_pagina_separator": len("Página 167 | OCR de tabela")}
    assert document.pages[0].diagnostics.warnings == []


def test_page_furniture_is_kept_when_headers_and_footers_are_preserved():
    """T3-P54: com preserve_headers_footers=True nada é suprimido nem contado."""
    from structured_pdf_text.ocr.tesseract.postprocess import apply_tesseract_postprocessors

    separator = _block("Página 167 | OCR de tabela", kind=ContentKind.TITLE)
    document = _document([separator], preserve_headers_footers=True)

    apply_tesseract_postprocessors(document)

    assert not separator.suppressed
    assert "tesseract_postprocess_suppressed_chars" not in document.pages[0].diagnostics.facts


def test_any_other_suppression_after_postprocessing_becomes_a_warning():
    """T3-P36: uma supressão que não é mobília vira aviso da página e do documento."""
    from structured_pdf_text.ocr.tesseract.postprocess import _report_postprocess_suppressions

    block = _block("conteúdo")
    document = _document([block])
    visible_before = {id(block)}
    block.suppressed, block.suppression_reason = True, "regra_futura"

    _report_postprocess_suppressions(document, visible_before)

    assert document.pages[0].diagnostics.warnings == ["content_suppressed_by_postprocess: regra_futura (8 chars)"]
    assert document.diagnostics.warnings == ["page 167: content_suppressed_by_postprocess: regra_futura (8 chars)"]


def _token(text, box, confidence, call):
    return OcrToken(text, BBox(*box), confidence, "pt-BR", SourceKind.OCR_PAGE,
                    layout_line_id=f"{call}:1:6:1:1")


def _boxes(tokens):
    return {id(token): token.bbox for token in tokens}


def test_partial_reread_inside_a_confident_reading_is_dropped():
    """V9 p58: "333,92" (0.672, outro recorte) dentro de "2.333,52" (0.966); IoU 0.57."""
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    tokens = [
        _token("R$", (378.1, 366.0, 387.0, 372.8), 0.961, "pagina"),
        _token("2.333,52", (390.5, 366.6, 423.3, 374.3), 0.966, "pagina"),
        _token("333,92", (397.8, 368.5, 423.3, 374.5), 0.672, "releitura"),
    ]

    (line,) = reconstruct_ocr_lines(tokens, 57, BBox(0, 0, 595, 842))

    assert [token.text for token in line.tokens if token.text.strip()] == ["R$", "2.333,52"]


def test_noisy_reading_around_a_confident_word_is_dropped():
    """V9 p58: "R$E" (0.315) envolve "R$" (0.959) de outra chamada."""
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    tokens = [
        _token("R$E", (292.5, 386.4, 311.0, 398.9), 0.315, "ruido"),
        _token("R$", (295.8, 391.9, 305.0, 399.1), 0.959, "pagina"),
        _token("845,26", (308.1, 392.4, 334.1, 399.9), 0.959, "pagina"),
    ]

    (line,) = reconstruct_ocr_lines(tokens, 57, BBox(0, 0, 595, 842))

    assert [token.text for token in line.tokens if token.text.strip()] == ["R$", "845,26"]


def test_contained_readings_are_kept_when_both_are_confident_or_from_one_call():
    """Proteções: duas leituras confiantes ficam; leituras da mesma chamada não são tocadas."""
    from dataclasses import replace

    from structured_pdf_text.ocr.reconstruct import _drop_conflicting_rereads

    outer = _token("1.045,56", (392.6, 336.0, 425.3, 343.6), 0.80, "pagina")
    inner = _token("045,56", (408.0, 336.5, 425.0, 343.4), 0.95, "releitura")  # IoU 0.47: fora de T3-P28
    both = [outer, inner]
    assert _drop_conflicting_rereads(both, _boxes(both)) == both

    noisy_outer = replace(outer, confidence=0.30)
    same_call = [noisy_outer, replace(inner, layout_line_id="pagina:1:6:1:2")]
    assert _drop_conflicting_rereads(same_call, _boxes(same_call)) == same_call

    side_by_side = [_token("R$", (378.1, 366.0, 387.0, 372.8), 0.30, "a"),
                    _token("2.333,52", (386.0, 366.6, 423.3, 374.3), 0.97, "b")]
    assert _drop_conflicting_rereads(side_by_side, _boxes(side_by_side)) == side_by_side
