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



# --- T3-P32, tentativa 3: texto da leitura vertical, orientação da horizontal ---
#
# Upright crop 300 × 200. A word read horizontally on the crop turned by 90°
# at (left 20, top y, 60 × 14) lies on the crop turned by 180° at
# (left 300 - y - 14, top 20, 14 × 60) — computed by hand from the 90°/180°
# quarter-turn formulas, so the fixture does not reuse the code under test.

def _word_rows(words):
    rows = [HEADER]
    for line, (left, top, width, height, confidence, text) in enumerate(words, start=1):
        rows.append(f"5\t1\t1\t1\t{line}\t1\t{left}\t{top}\t{width}\t{height}\t{confidence}\t{text}")
    return "\n".join(rows)


_TOPS = (40, 70, 100, 130)


def _horizontal_at_90(confidence, texts):
    return _word_rows([(20, top, 60, 14, confidence, text) for top, text in zip(_TOPS, texts)])


def _vertical_at_180(confidence, texts, shift=0):
    return _word_rows([(300 - top - 14 + shift, 20, 14, 60, confidence, text) for top, text in zip(_TOPS, texts)])


def _texts(tsv):
    from structured_pdf_text.ocr.backends.tesseract import _parse_tsv
    return [row["text"] for row in _parse_tsv(tsv) if row.get("level") == "5"]


def test_clearly_better_vertical_reading_supplies_the_text_in_the_chosen_frame():
    from structured_pdf_text.ocr.backends.tesseract import _parse_tsv, _vertical_reading_in_frame

    chosen = _horizontal_at_90(84.0, ["Ação", "oi1", "=pm", "Prazo"])
    vertical = _vertical_at_180(92.0, ["Ação", "0101", "0202", "Prazo"])

    result = _vertical_reading_in_frame({0: _cells_tsv(53.0, vertical=True), 90: chosen, 180: vertical}, 90, chosen, (300, 200))

    assert _texts(result) == ["Ação", "0101", "0202", "Prazo"]
    boxes = [(float(r["left"]), float(r["top"]), float(r["width"]), float(r["height"]))
             for r in _parse_tsv(result) if r.get("level") == "5"]
    assert boxes == [(20.0, top, 60.0, 14.0) for top in _TOPS]


def test_vertical_reading_needs_a_clear_confidence_gain():
    from structured_pdf_text.ocr.backends.tesseract import _vertical_reading_in_frame

    chosen = _horizontal_at_90(90.0, ["Ação", "oi1", "=pm", "Prazo"])
    vertical = _vertical_at_180(93.0, ["Ação", "0101", "0202", "Prazo"])

    assert _vertical_reading_in_frame({90: chosen, 180: vertical}, 90, chosen, (300, 200)) is chosen


def test_vertical_reading_of_other_positions_is_not_used():
    from structured_pdf_text.ocr.backends.tesseract import _vertical_reading_in_frame

    chosen = _horizontal_at_90(84.0, ["Ação", "oi1", "=pm", "Prazo"])
    elsewhere = _vertical_at_180(95.0, ["Ação", "0101", "0202", "Prazo"], shift=-150)

    assert _vertical_reading_in_frame({90: chosen, 180: elsewhere}, 90, chosen, (300, 200)) is chosen


def test_retry_keeps_the_horizontal_orientation_with_the_better_vertical_text():
    upright = _cells_tsv(53.0, vertical=True)
    readings = {
        90: _horizontal_at_90(84.0, ["Ação", "oi1", "=pm", "Prazo"]),
        180: _vertical_at_180(92.0, ["Ação", "0101", "0202", "Prazo"]),
        270: _cells_tsv(40.0, vertical=False),
    }
    backend, _ = _backend(readings)

    tsv, rotation = backend._retry_quarter_turns(Image.new("RGB", (300, 200)), 300, upright)

    assert rotation == 90
    assert _texts(tsv) == ["Ação", "0101", "0202", "Prazo"]


def test_better_vertical_texts_keep_the_segmentation_of_the_horizontal_read():
    """A vertical read groups a turned table by rows; only its texts are taken."""
    from structured_pdf_text.ocr.backends.tesseract import _parse_tsv, _vertical_reading_in_frame

    chosen = _horizontal_at_90(84.0, ["Ação", "oi1", "=pm", "Prazo"])
    rows = [HEADER]
    for top, text in zip(_TOPS, ["Ação", "0101", "0202", "Prazo"]):
        # every vertical word on one Tesseract line
        rows.append(f"5\t1\t1\t1\t1\t1\t{300 - top - 14}\t20\t14\t60\t92.0\t{text}")
    vertical = "\n".join(rows)

    result = _vertical_reading_in_frame({90: chosen, 180: vertical}, 90, chosen, (300, 200))
    words = [row for row in _parse_tsv(result) if row.get("level") == "5"]

    assert [row["text"] for row in words] == ["Ação", "0101", "0202", "Prazo"]
    assert [row["line_num"] for row in words] == ["1", "2", "3", "4"]


def test_unpaired_words_are_kept_and_no_word_is_added():
    from structured_pdf_text.ocr.backends.tesseract import _vertical_reading_in_frame

    chosen = _word_rows([(20, top, 60, 14, 84.0, text) for top, text in zip(_TOPS, ["Ação", "oi1", "=pm", "Prazo"])]
                        + [(150, 160, 40, 14, 70.0, "Situação")])
    vertical = _vertical_at_180(92.0, ["Ação", "0101", "0202", "Prazo"]) + "\n" + \
        "5\t1\t1\t1\t9\t1\t5\t150\t14\t40\t99.0\textra"

    result = _vertical_reading_in_frame({90: chosen, 180: vertical}, 90, chosen, (300, 200))

    assert _texts(result) == ["Ação", "0101", "0202", "Prazo", "Situação"]
