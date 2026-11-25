"""T3-P44: seleção entre a leitura direta e a releitura endireitada (`_deskew_page`).

A releitura rejeitada pelas médias de confiança é mantida quando, por evidência
comparável (palavras comuns na mesma posição), é mais completa e não é pior.
Os casos reproduzem a forma da V11 p71 (leitura direta sem os blocos de baixo,
média menor na releitura e confiança maior nas palavras comuns) e os riscos:
ruído, duplicação, números divergentes, leitura original correta.
"""
from __future__ import annotations

import math

from PIL import Image

from structured_pdf_text.ocr.backends.tesseract import (
    TesseractBackend,
    _compare_reads,
    _deskewed_read_is_more_complete,
    _parse_tsv,
)

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
PAGE = (1600, 2200)
SLOPE = 0.03  # ≈ 1,7°: dentro da faixa do endireitamento


def _words(lines, start_line=0, confidence=92.0, text="palavra", per_line=8, slope=SLOPE):
    """Palavras (centro x, centro y, conf, texto, linha) em linhas inclinadas da página."""
    found = []
    for line in range(start_line, start_line + lines):
        for word in range(per_line):
            x = 150.0 + word * 160.0
            y = 200.0 + line * 70.0 + slope * x
            value = text(line, word) if callable(text) else text
            conf = confidence(line, word) if callable(confidence) else confidence
            found.append((x, y, conf, value, line + 1))
    return found


def _tsv(words, width=120.0, height=30.0) -> str:
    rows = [HEADER]
    for index, (x, y, conf, text, line) in enumerate(words):
        rows.append(f"5\t1\t1\t1\t{line}\t{index + 1}\t{x - width / 2}\t{y - height / 2}\t{width}\t{height}\t{conf}\t{text}")
    return "\n".join(rows)


def _deskewed_tsv(words, angle: float, rotated_size) -> str:
    """As mesmas palavras lidas na imagem girada (inverso de `_unrotate_tsv`), já niveladas."""
    theta = math.radians(angle)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    rcx, rcy = rotated_size[0] / 2.0, rotated_size[1] / 2.0
    ocx, ocy = PAGE[0] / 2.0, PAGE[1] / 2.0
    moved = []
    for x, y, conf, text, line in words:
        dx, dy = x - ocx, y - ocy
        moved.append((rcx + dx * cos_t + dy * sin_t, rcy - dx * sin_t + dy * cos_t, conf, text, line))
    return _tsv(moved)


def _decide(upright_words, deskewed_words):
    backend = TesseractBackend.__new__(TesseractBackend)
    angle = math.degrees(math.atan(SLOPE))

    def read(pil, dpi):
        return _deskewed_tsv(deskewed_words, angle, pil.size)

    backend._read_tsv = read
    upright = _tsv(upright_words)
    result = backend._deskew_page(Image.new("L", PAGE, 255), 300, upright)
    diagnostics = backend.consume_page_diagnostics()
    return result, upright, diagnostics


def _texts(tsv: str) -> list[str]:
    return [row["text"] for row in _parse_tsv(tsv) if row.get("level") == "5"]


# --- casos positivos ---------------------------------------------------------

def test_more_complete_deskewed_read_is_kept_despite_a_lower_mean_confidence():
    """V11 p71: a leitura direta para no 2º bloco; a releitura acha o resto."""
    top = _words(4, confidence=91.0)
    upright = top
    deskewed = _words(4, confidence=92.5) + _words(5, start_line=6, confidence=80.0, text="conteudo")
    result, original, diagnostics = _decide(upright, deskewed)

    assert result != original
    assert _texts(result).count("conteudo") == 40
    (decision,) = diagnostics["tesseract_deskew_decisions"]
    assert decision["decision"] == "deskewed_more_complete"
    assert decision["deskewed_confidence"] < decision["upright_confidence"]  # a média sozinha rejeitaria
    assert decision["shared_deskewed_confidence"] > decision["shared_upright_confidence"]
    assert diagnostics["tesseract_more_complete_read_discarded"] is False


def test_useful_additional_content_with_lower_overall_confidence_is_kept():
    upright = _words(4, confidence=96.0)
    deskewed = _words(4, confidence=95.5) + _words(3, start_line=5, confidence=75.0, text="anexo")
    result, original, diagnostics = _decide(upright, deskewed)
    assert result != original
    assert diagnostics["tesseract_deskew_decisions"][0]["decision"] == "deskewed_more_complete"


def test_confident_deskewed_read_is_still_kept_by_the_existing_rule():
    """Comportamento de T3-P18 preservado: releitura melhor pelas médias."""
    result, original, diagnostics = _decide(_words(4, confidence=88.0), _words(4, confidence=95.0))
    assert result != original
    assert diagnostics["tesseract_deskew_decisions"][0]["decision"] == "deskewed"


# --- casos negativos ---------------------------------------------------------

def test_incomplete_deskewed_read_with_high_confidence_is_discarded():
    upright = _words(6, confidence=90.0)
    deskewed = _words(3, confidence=97.0)
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    assert diagnostics["tesseract_deskew_decisions"][0]["decision"] == "upright"


def test_additional_noise_recognized_as_text_is_discarded_and_flagged():
    """V11 p67/p70, V4 p180: o excedente da releitura é ruído de baixa confiança."""
    upright = _words(4, confidence=92.0)
    deskewed = _words(4, confidence=92.0) + _words(4, start_line=6, confidence=40.0, text="~;.,")
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    assert diagnostics["tesseract_deskew_decisions"][0]["decision"] == "upright"
    # Ruído a mais não é conteúdo perdido: sem sinal (V9 p58, falso alarme na 1ª forma).
    assert diagnostics["tesseract_more_complete_read_discarded"] is False


def test_duplicated_words_do_not_count_as_additional_text():
    upright = _words(4, confidence=93.0)
    duplicates = [(x + 4.0, y + 3.0, 60.0, text, line) for x, y, _, text, line in _words(4)]
    result, original, diagnostics = _decide(upright, _words(4, confidence=93.0) + duplicates)
    assert result == original
    decision = diagnostics["tesseract_deskew_decisions"][0]
    assert decision["decision"] == "upright" and decision["additional_chars"] == 0


def test_more_complete_read_disagreeing_on_most_shared_numbers_is_discarded():
    def numbers(value):
        return lambda line, word: f"{value}{line}{word}"

    upright = _words(4, confidence=92.0, text=numbers("10"))
    deskewed = _words(4, confidence=92.5, text=numbers("70")) + _words(4, start_line=6, confidence=85.0, text="texto")
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    decision = diagnostics["tesseract_deskew_decisions"][0]
    assert decision["shared_numbers_differing"] == decision["shared_numbers"] == 32
    # Texto a mais, lido com confiança, ficou fora da página: o descarte é sinalizado.
    assert diagnostics["tesseract_more_complete_read_discarded"] is True


def test_correct_upright_read_is_not_replaced_by_an_equally_complete_one():
    """Mesmo conteúdo e confiança menor nas palavras comuns: fica a leitura original."""
    result, original, diagnostics = _decide(_words(5, confidence=95.0), _words(5, confidence=92.0))
    assert result == original
    assert diagnostics["tesseract_deskew_decisions"][0]["decision"] == "upright"


def test_more_complete_read_worse_on_the_shared_words_is_discarded():
    """V9 p58: excedente com ganho, mas a releitura lê pior o que as duas têm em comum."""
    upright = _words(4, confidence=90.5)
    deskewed = _words(4, confidence=72.0) + _words(3, start_line=6, confidence=85.0, text="texto")
    result, original, _ = _decide(upright, deskewed)
    assert result == original


def test_more_complete_read_that_loses_upright_content_is_discarded():
    upright = _words(4, confidence=90.0) + _words(2, start_line=12, confidence=90.0, text="rodape")
    deskewed = _words(4, confidence=91.0) + _words(5, start_line=5, confidence=85.0, text="texto")
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    assert diagnostics["tesseract_deskew_decisions"][0]["upright_kept_ratio"] < 0.9


def test_too_few_shared_words_leave_the_decision_to_the_means():
    upright = _words(3, confidence=95.0, per_line=6)  # 18 palavras comuns (< 20)
    deskewed = _words(3, confidence=95.5, per_line=6) + _words(3, start_line=5, confidence=80.0, per_line=6)
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    decision = diagnostics["tesseract_deskew_decisions"][0]
    assert decision["decision"] == "upright" and decision["shared_words"] == 18


def test_small_additional_text_does_not_override_the_means():
    """V9 p58, V4 p172: ganho de 17–22%, abaixo do limiar de leitura substancialmente mais completa."""
    upright = _words(5, confidence=95.0)
    deskewed = _words(5, confidence=95.5) + _words(1, start_line=7, confidence=75.0, text="texto")
    result, original, diagnostics = _decide(upright, deskewed)
    assert result == original
    decision = diagnostics["tesseract_deskew_decisions"][0]
    assert 0 < decision["additional_chars"] < 0.25 * decision["upright_chars"]


# --- evidência e diagnóstico ---------------------------------------------------

def test_comparison_pairs_words_by_position_not_by_text():
    upright = _tsv(_words(3, confidence=80.0, slope=0.0))
    deskewed = _tsv(_words(3, confidence=90.0, text="outra", slope=0.0))
    evidence = _compare_reads(upright, deskewed)
    assert evidence["shared_words"] == 24
    assert evidence["additional_chars"] == 0
    assert not _deskewed_read_is_more_complete(evidence)


def test_page_diagnostics_are_consumed_once_and_empty_without_a_reread():
    _, _, diagnostics = _decide(_words(4, confidence=88.0), _words(4, confidence=95.0))
    assert diagnostics["tesseract_deskew_decisions"]
    backend = TesseractBackend.__new__(TesseractBackend)
    backend._read_tsv = lambda pil, dpi: ""
    level = _tsv(_words(4, slope=0.0))
    assert backend._deskew_page(Image.new("L", PAGE, 255), 300, level) == level  # sem inclinação: sem releitura
    assert backend.consume_page_diagnostics() == {}
