"""Regressões da run Paddle 003 (corpus/PaddleOCRImprovement.md, §7 e §8).

003-P0-01: o deskew do Paddle girava no sentido errado e dobrava a inclinação
           (V4 p107/p108 a −2,5°, tabelas inclinadas p148–p180, V10 L01/L02).
003-P0-02: o classificador de orientação de linha marca como 180° linhas retas
           de digitalizações degradadas; a leitura girada sai vazia ou lixo e a
           linha some (V4 p87, p90, p93, p97, p99, p103, p109, p126, p139...).
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

import structured_pdf_text.ocr.paddle as paddle
from structured_pdf_text.ocr.paddle import (
    PaddleOcrEngine,
    _deskew_image,
    _estimate_skew_deg,
    _prefer_upright_line,
)


def _text_page() -> Image.Image:
    page = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(page)
    for y in range(150, 1450, 40):
        for x in range(120, 1080, 60):
            draw.rectangle([x, y, x + 45, y + 14], fill="black")
    return page


def _skew_of(image) -> float:
    gray = np.asarray(image.convert("L"))
    return _estimate_skew_deg(gray) or 0.0


# --- 003-P0-01 ---------------------------------------------------------------


@pytest.mark.parametrize("degrees", [1.5, -1.5, 2.5, -2.5, 4.5, -4.5])
def test_deskew_removes_the_skew_instead_of_doubling_it(degrees):
    skewed = _text_page().rotate(degrees, fillcolor="white")
    corrected, applied = _deskew_image(skewed)
    assert applied != 0.0
    assert abs(_skew_of(corrected)) < 0.3


def test_deskew_reports_the_historical_sign():
    # PIL turns counter-clockwise: the lines climb to the right (negative skew
    # in image coordinates), the value the engine always reported.
    _, applied = _deskew_image(_text_page().rotate(2.5, fillcolor="white"))
    assert applied == pytest.approx(-2.5, abs=0.1)


def test_deskew_leaves_a_straight_page_untouched():
    page = _text_page()
    corrected, applied = _deskew_image(page)
    assert applied == 0.0
    assert corrected is page


def test_deskew_discards_a_rotation_that_does_not_remove_the_skew(monkeypatch):
    # A wrong estimate (scan noise, a new angle convention) must never make
    # the page more skewed than it was: the turn does not sharpen the line
    # profile and is dropped (V4 p121: speckle read as −0,87°).
    page = _text_page()
    monkeypatch.setattr(paddle, "_estimate_skew_deg", lambda gray: 3.0)
    corrected, applied = _deskew_image(page)
    assert applied == 0.0
    assert corrected is page


# --- 003-P0-02 ---------------------------------------------------------------


def test_upright_reading_wins_when_the_turned_one_has_no_text():
    assert _prefer_upright_line("", 0.99, "Parágrafo 093.01", 0.41)
    assert _prefer_upright_line("·'.", 0.95, "controle", 0.62)


def test_confidence_decides_between_two_textual_readings():
    assert _prefer_upright_line("ojǝʇxǝʇ", 0.31, "texto", 0.97)
    assert not _prefer_upright_line("SELO 2026", 0.97, "9Z0Z OTES", 0.40)


def test_upright_reading_without_text_never_wins():
    assert not _prefer_upright_line("x", 0.10, "", 0.99)
    assert not _prefer_upright_line("x", 0.10, "..", 0.99)


def _result(texts, scores, angles):
    # PaddleOCR 3.x reports the line classifier's class: 1 means turned 180°.
    boxes = [[10, 10 + 30 * i, 500, 30 + 30 * i] for i in range(len(texts))]
    return [{"rec_texts": list(texts), "rec_scores": list(scores),
             "textline_orientation_angles": list(angles), "rec_boxes": boxes}]


def _engine(upright_raw):
    calls = []

    def predict(ocr, image, **options):
        calls.append(options)
        return upright_raw

    engine = SimpleNamespace(_dbg_stage="baseline", _predict_counted=predict,
                             last_textline_flips_seen=0, last_textline_flips_restored=0)
    return engine, calls


def test_minority_of_turned_lines_is_reread_without_the_line_classifier():
    raw = _result(["Linha um", "", "Linha três", "ɐɥuıl"], [0.98, 0.0, 0.97, 0.35], [0, 1, 0, 1])
    upright = _result(["Linha um", "Linha dois", "Linha três", "linha quatro"], [0.98, 0.93, 0.97, 0.90], [0, 0, 0, 0])
    engine, calls = _engine(upright)
    out = PaddleOcrEngine._restore_misturned_lines(engine, None, object(), raw)
    item = out[0]
    assert calls == [{"use_textline_orientation": False}]
    assert item["rec_texts"] == ["Linha um", "Linha dois", "Linha três", "linha quatro"]
    assert item["textline_orientation_angles"] == [0, 0, 0, 0]
    assert (engine.last_textline_flips_seen, engine.last_textline_flips_restored) == (2, 2)


def test_truly_turned_line_keeps_its_reading():
    raw = _result(["Corpo", "CARIMBO 2026", "Corpo"], [0.98, 0.96, 0.98], [0, 1, 0])
    upright = _result(["Corpo", "9Z0Z OBWIɹ∀Ɔ", "Corpo"], [0.98, 0.30, 0.98], [0, 0, 0])
    engine, _ = _engine(upright)
    item = PaddleOcrEngine._restore_misturned_lines(engine, None, object(), raw)[0]
    assert item["rec_texts"][1] == "CARIMBO 2026"
    assert item["textline_orientation_angles"][1] == 1


def test_mostly_turned_call_is_left_to_page_rotation_handling():
    raw = _result(["a", "b", "c"], [0.9, 0.9, 0.9], [1, 1, 0])
    engine, calls = _engine(None)
    assert PaddleOcrEngine._restore_misturned_lines(engine, None, object(), raw) is raw
    assert calls == []


def test_different_detection_boxes_keep_the_original_reading():
    raw = _result(["Linha um", "", "Linha três"], [0.98, 0.0, 0.97], [0, 1, 0])
    upright = _result(["Linha um", "Linha dois", "Linha três"], [0.98, 0.93, 0.97], [0, 0, 0])
    upright[0]["rec_boxes"][1] = [12, 40, 480, 60]
    engine, _ = _engine(upright)
    item = PaddleOcrEngine._restore_misturned_lines(engine, None, object(), raw)[0]
    assert item["rec_texts"][1] == ""
    assert engine.last_textline_flips_restored == 0
