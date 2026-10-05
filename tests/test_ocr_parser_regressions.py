"""Regression coverage for malformed OCR rows and confidence values."""
from __future__ import annotations

import math

import pytest

from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends.easyocr import (
    _result_to_ocr_tokens as easy_canonical,
    _result_to_pipeline_tokens as easy_pipeline,
)
from structured_pdf_text.ocr.backends.rapidocr import (
    _result_to_ocr_tokens as rapid_canonical,
    _result_to_pipeline_tokens as rapid_pipeline,
)
from structured_pdf_text.ocr.backends.tesseract import (
    _tsv_to_ocr_tokens,
    _tsv_to_pipeline_tokens,
)
from structured_pdf_text.ocr.paddle import _tokens_from_result


QUAD = [[1, 2], [11, 2], [11, 12], [1, 12]]


@pytest.mark.parametrize("parser", [easy_canonical, rapid_canonical])
def test_quadrilateral_parsers_skip_bad_rows_and_nonfinite_geometry(parser):
    rows = [
        [QUAD, "good", 0.8],
        [[['bad']], "malformed", 0.9],
        [[[math.nan, 0], [2, 0], [2, 2], [0, 2]], "nonfinite", 0.9],
    ]
    parsed = parser(rows, "test")
    assert [token.text for token in parsed] == ["good"]
    assert parsed[0].bbox_px == (1.0, 2.0, 11.0, 12.0)


@pytest.mark.parametrize("parser", [easy_pipeline, rapid_pipeline])
def test_pipeline_quadrilateral_parsers_clamp_scores_and_keep_good_rows(parser):
    rows = [[QUAD, "high", 1.4], [QUAD, "low", -0.2], [[['bad']], "bad", 0.5]]
    parsed = parser(rows, 0, "pt", offset_x=20)
    assert [(token.text, token.confidence) for token in parsed] == [
        ("high", 1.0), ("low", 0.0)
    ]
    assert parsed[0].bbox.x0 == 21.0


def _tsv_row(text="ok", conf="0", left="1", top="2", width="3", height="4"):
    return {
        "level": "5", "text": text, "conf": conf, "left": left,
        "top": top, "width": width, "height": height,
    }


def test_tesseract_parser_accepts_zero_confidence_and_skips_bad_rows():
    rows = [
        _tsv_row(),
        _tsv_row(text="bad confidence", conf="nan"),
        _tsv_row(text="bad box", left="x"),
        _tsv_row(text="zero area", width="0"),
    ]
    canonical = _tsv_to_ocr_tokens(rows, "tesseract")
    pipeline = _tsv_to_pipeline_tokens(rows, 0, "pt")
    assert [token.text for token in canonical] == ["ok"]
    assert canonical[0].confidence_native == 0.0
    assert [token.text for token in pipeline] == ["ok"]
    assert pipeline[0].confidence == 0.0


def test_paddle_parser_preserves_good_row_after_malformed_geometry():
    class Image:
        size = (100, 100)

    rows = [
        ([['bad']], ("broken", 0.8)),
        ([[1, 2], [11, 2], [11, 12], [1, 12]], ("good", 0.9)),
    ]
    parsed = _tokens_from_result(rows, 0, Image(), None)
    assert [token.text for token in parsed] == ["good"]
    assert parsed[0].bbox == BBox(1, 2, 11, 12)


# ---------------------------------------------------------------------------
# §31 — pt-BR punctuation spacing in reconstruct_ocr_lines
# ---------------------------------------------------------------------------

def _ocr_token(text: str, x0: float, x1: float, y0: float = 0.0, y1: float = 10.0) -> "Any":
    """Create an OcrToken with a simple axis-aligned bbox."""
    from structured_pdf_text.document import OcrToken, SourceKind
    return OcrToken(text, BBox(x0, y0, x1, y1), 0.9, "pt", SourceKind.OCR_PAGE)


def _reconstruct_text(tokens: list) -> str:
    """Run reconstruct_ocr_lines and return joined text."""
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
    lines = reconstruct_ocr_lines(tokens, page_index=0)
    return " ".join(line.text for line in lines)


class TestPtBrSpacingRules:
    """§31: OCR reconstruction must suppress spaces before/after pt-BR punctuation."""

    def test_no_space_before_comma(self):
        tokens = [_ocr_token("palavra", 0, 50), _ocr_token(",", 52, 56)]
        text = _reconstruct_text(tokens)
        assert "palavra," in text, f"Expected 'palavra,' but got: {text!r}"
        assert "palavra ," not in text

    def test_no_space_before_period(self):
        tokens = [_ocr_token("fim", 0, 30), _ocr_token(".", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "fim." in text
        assert "fim ." not in text

    def test_no_space_before_percent(self):
        tokens = [_ocr_token("12,5", 0, 30), _ocr_token("%", 31, 35)]
        text = _reconstruct_text(tokens)
        assert "12,5%" in text
        assert "12,5 %" not in text

    def test_no_space_before_closing_paren(self):
        tokens = [_ocr_token("(texto", 0, 40), _ocr_token(")", 41, 44)]
        text = _reconstruct_text(tokens)
        assert "texto)" in text
        assert "texto )" not in text

    def test_no_space_before_colon(self):
        tokens = [_ocr_token("Total", 0, 30), _ocr_token(":", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "Total:" in text
        assert "Total :" not in text

    def test_no_space_before_semicolon(self):
        tokens = [_ocr_token("item", 0, 30), _ocr_token(";", 31, 34)]
        text = _reconstruct_text(tokens)
        assert "item;" in text
        assert "item ;" not in text

    def test_currency_prefix_attached_to_digits(self):
        tokens = [_ocr_token("R$", 0, 15), _ocr_token("1.234,56", 16, 60)]
        text = _reconstruct_text(tokens)
        assert "R$1.234,56" in text
        assert "R$ 1.234" not in text

    def test_normal_word_gap_still_inserts_space(self):
        tokens = [_ocr_token("uma", 0, 30), _ocr_token("palavra", 32, 80)]
        text = _reconstruct_text(tokens)
        assert "uma palavra" in text
