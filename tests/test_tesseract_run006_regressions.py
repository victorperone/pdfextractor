"""Regressões da RUN 006 (output/tesseract/006).

T3-P32: tabela raster girada 90°/270° lida pelo Tesseract como palavras
        verticais com confiança alta. As células têm uma ou duas palavras por
        linha, então o teste por linhas não dispara o retry; os tokens ficavam
        sem orientação e a grade saía transposta (V9 p20/p71).
"""
from __future__ import annotations

from PIL import Image

from structured_pdf_text.ocr.backends.tesseract import (
    TesseractBackend,
    _tsv_reads_vertically,
    _tsv_vertical_word_ratio,
)

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _cells_tsv(confidence: float, *, vertical: bool, texts=("Rubrica", "Qtde.", "Total", "Situação", "001", "R$")) -> str:
    """One word per line, as Tesseract returns the cells of a table."""
    rows = [HEADER]
    for line, text in enumerate(texts):
        width, height = (14, 60) if vertical else (60, 14)
        left, top = (20 + line * 40, 30) if vertical else (30, 20 + line * 40)
        rows.append(f"5\t1\t{line + 1}\t1\t1\t1\t{left}\t{top}\t{width}\t{height}\t{confidence}\t{text}")
    return "\n".join(rows)


def test_vertical_words_are_measured_on_words_with_enough_characters():
    shaped, ratio = _tsv_vertical_word_ratio(_cells_tsv(95.0, vertical=True))
    # "R$" has two alphanumerics and does not count.
    assert (shaped, ratio) == (5, 1.0)
    assert _tsv_vertical_word_ratio(_cells_tsv(95.0, vertical=False)) == (5, 0.0)


def test_confident_table_read_as_vertical_words_counts_as_vertical():
    assert _tsv_reads_vertically(_cells_tsv(95.0, vertical=True))
    assert not _tsv_reads_vertically(_cells_tsv(95.0, vertical=False))


def test_short_numbers_and_symbols_alone_never_trigger_the_vertical_test():
    narrow = _cells_tsv(95.0, vertical=True, texts=("01", "R$", "|", "02"))
    assert _tsv_vertical_word_ratio(narrow) == (0, 0.0)
    assert not _tsv_reads_vertically(narrow)


def _backend(readings: dict[int, str]):
    backend = TesseractBackend.__new__(TesseractBackend)
    calls = []

    def read(pil, dpi):
        rotation = (90, 180, 270)[len(calls)]
        calls.append(rotation)
        return readings[rotation]

    backend._read_tsv = read
    return backend, calls


def test_confident_vertical_table_crop_is_reread_and_the_upright_reading_kept():
    readings = {
        90: _cells_tsv(40.0, vertical=False),
        180: _cells_tsv(85.0, vertical=True),
        270: _cells_tsv(94.0, vertical=False),
    }
    backend, calls = _backend(readings)

    tsv, rotation = backend._retry_quarter_turns(Image.new("RGB", (200, 300)), 300, _cells_tsv(95.0, vertical=True))

    assert (tsv, rotation) == (readings[270], 270)
    assert calls == [90, 180, 270]


def test_confident_horizontal_table_crop_is_not_reread():
    backend, calls = _backend({})
    upright = _cells_tsv(95.0, vertical=False)

    assert backend._retry_quarter_turns(Image.new("RGB", (300, 200)), 300, upright) == (upright, 0)
    assert calls == []


def test_vertical_crop_keeps_the_upright_reading_when_no_rotation_is_confident():
    readings = {rotation: _cells_tsv(50.0, vertical=False) for rotation in (90, 180, 270)}
    backend, _ = _backend(readings)
    upright = _cells_tsv(95.0, vertical=True)

    assert backend._retry_quarter_turns(Image.new("RGB", (200, 300)), 300, upright) == (upright, 0)


def test_low_confidence_read_still_accepts_a_confident_vertical_word_reading():
    """Previous T3-P02 behaviour (V8 p4: '-01' at 56 -> 'L-01' at 82 read as vertical words)."""
    readings = {
        90: _cells_tsv(82.0, vertical=True),
        180: _cells_tsv(22.0, vertical=False),
        270: _cells_tsv(13.0, vertical=True),
    }
    backend, _ = _backend(readings)

    upright = _cells_tsv(56.0, vertical=False)
    assert backend._retry_quarter_turns(Image.new("RGB", (300, 200)), 300, upright) == (readings[90], 90)


def test_horizontal_rotated_reading_is_preferred_over_a_more_confident_vertical_one():
    readings = {
        90: _cells_tsv(97.0, vertical=True),
        180: _cells_tsv(30.0, vertical=False),
        270: _cells_tsv(91.0, vertical=False),
    }
    backend, _ = _backend(readings)

    upright = _cells_tsv(95.0, vertical=True)
    assert backend._retry_quarter_turns(Image.new("RGB", (200, 300)), 300, upright) == (readings[270], 270)


def test_word_triggered_retry_never_swaps_one_vertical_reading_for_another():
    readings = {rotation: _cells_tsv(96.0, vertical=True) for rotation in (90, 180, 270)}
    backend, _ = _backend(readings)

    upright = _cells_tsv(95.0, vertical=True)
    assert backend._retry_quarter_turns(Image.new("RGB", (200, 300)), 300, upright) == (upright, 0)

