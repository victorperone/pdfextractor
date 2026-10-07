"""Regression tests for refinement conservation gates — Etapa 1.

Covers R67 (weak-region refinement conservation) and the shared
_refinement_conserves_content helper.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.api import _refinement_conserves_content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tok(text: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.90) -> OcrToken:
    return OcrToken(text, BBox(x0, y0, x1, y1), conf, "pt", SourceKind.OCR_PAGE)


# ---------------------------------------------------------------------------
# _refinement_conserves_content helper
# ---------------------------------------------------------------------------

class TestRefinementConservesContent:
    """The conservation helper must reject refinements that discard the bulk
    of prior content, even when the new quality score is higher."""

    def test_accepts_when_new_has_similar_content(self):
        old = [_tok("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", 0, 0, 400, 15)]
        new = [_tok("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", 0, 0, 400, 15, conf=0.95)]

        assert _refinement_conserves_content(old, new)

    def test_rejects_when_new_truncates_most_content(self):
        """New must be rejected when it loses more than 40% of old non-whitespace chars."""
        old = [_tok("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", 0, 0, 400, 15, conf=0.70)]
        new = [_tok("Valor", 0, 0, 50, 15, conf=0.95)]  # <<60% of old chars

        assert not _refinement_conserves_content(old, new)

    def test_rejects_borderline_short_result(self):
        """Result that is exactly at 59% of old chars must be rejected."""
        old_text = "A" * 100
        new_text = "A" * 59  # 59% of 100
        old = [_tok(old_text, 0, 0, 400, 15)]
        new = [_tok(new_text, 0, 0, 400, 15, conf=0.98)]

        assert not _refinement_conserves_content(old, new)

    def test_accepts_borderline_result_at_sixty_percent(self):
        """Result at exactly 60% of old chars must be accepted."""
        old_text = "A" * 100
        new_text = "A" * 60  # exactly 60%
        old = [_tok(old_text, 0, 0, 400, 15)]
        new = [_tok(new_text, 0, 0, 400, 15, conf=0.98)]

        assert _refinement_conserves_content(old, new)

    def test_old_very_short_always_accepted(self):
        """When old content has <= 4 non-whitespace chars, any new result is accepted."""
        old = [_tok("AB", 0, 0, 20, 15)]  # 2 non-ws chars
        new = [_tok("A", 0, 0, 10, 15, conf=0.98)]

        assert _refinement_conserves_content(old, new)

    def test_empty_old_always_accepted(self):
        """Empty old list has no content to conserve."""
        new = [_tok("qualquer texto", 0, 0, 100, 15)]
        assert _refinement_conserves_content([], new)

    def test_empty_new_rejected_when_old_has_content(self):
        """Replacing content with nothing must be rejected."""
        old = [_tok("conteúdo importante", 0, 0, 150, 15)]
        assert not _refinement_conserves_content(old, [])

    def test_multi_token_old_vs_single_short_new(self):
        """Losing most of a multi-token sequence must be rejected."""
        old = [
            _tok("CPF: 123.456.789-00", 0, 0, 150, 15),
            _tok("Data: 01/01/2024", 0, 20, 120, 35),
            _tok("Valor: R$ 1.234,56", 0, 40, 140, 55),
        ]
        new = [_tok("CPF", 0, 0, 30, 15, conf=0.99)]

        assert not _refinement_conserves_content(old, new)

    def test_custom_min_coverage_parameter(self):
        """The min_coverage parameter controls the threshold."""
        old = [_tok("A" * 100, 0, 0, 400, 15)]
        new_75 = [_tok("A" * 75, 0, 0, 300, 15)]  # 75% — above default 0.60

        # With default (0.60): accept
        assert _refinement_conserves_content(old, new_75)
        # With stricter threshold (0.80): reject
        assert not _refinement_conserves_content(old, new_75, min_coverage=0.80)


# ---------------------------------------------------------------------------
# R67 — weak-region refinement conservation (integration-style unit tests)
# ---------------------------------------------------------------------------

class TestR67WeakRegionConservation:
    """_refinement_conserves_content integration with _recover_weak_ocr_regions.

    These tests exercise the conservation logic directly since we cannot run
    actual OCR in the WSL test environment.
    """

    def test_conservation_rejects_single_word_replacing_full_sentence(self):
        """The reported R67 case: 'Valor' (high conf) must not replace a full sentence."""
        old = [_tok("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", 0, 0, 400, 15, conf=0.65)]
        new = [_tok("Valor", 0, 0, 50, 15, conf=0.98)]

        assert not _refinement_conserves_content(old, new), (
            "R67: short high-confidence reread must not replace long original content"
        )

    def test_conservation_accepts_corrected_sentence_with_same_length(self):
        """A refinement that fixes errors without truncating must be accepted."""
        old = [_tok("Valôr: R$ 9.87G,54; desc0nto: -2,75%", 0, 0, 350, 15, conf=0.60)]
        new = [_tok("Valor: R$ 9.876,54; desconto: -2,75%", 0, 0, 350, 15, conf=0.90)]

        assert _refinement_conserves_content(old, new), (
            "R67: corrected version with same length must be accepted"
        )

    def test_conservation_accepts_extension_of_content(self):
        """A refinement that adds MORE content must always be accepted."""
        old = [_tok("NOTA 1: ver artigo", 0, 0, 150, 15, conf=0.70)]
        new = [_tok("NOTA 1: ver artigo 5.º parágrafo único", 0, 0, 300, 15, conf=0.88)]

        assert _refinement_conserves_content(old, new), (
            "R67: extension of content must always be accepted"
        )

    def test_conservation_rejects_duplicate_of_single_word(self):
        """If new returns just a duplicate word, it must be rejected on char count."""
        old = [_tok("NOTA ABC importante que deve ser preservada", 0, 0, 300, 15)]
        new = [_tok("NOTA NOTA", 0, 0, 70, 15, conf=0.95)]  # < 60% of old chars

        assert not _refinement_conserves_content(old, new)

    def test_conservation_accepts_when_new_is_slightly_shorter_but_within_threshold(self):
        """A refinement that trims noise words slightly (>60% preserved) must be accepted."""
        old = [_tok("Valor R $ 9.876,54 desconto -2,75 quantidade 00042", 0, 0, 400, 15, conf=0.60)]
        new = [_tok("Valor R$ 9.876,54 desconto -2,75% quantidade 00042", 0, 0, 400, 15, conf=0.90)]

        assert _refinement_conserves_content(old, new), (
            "Similar-length correction within 60% threshold must be accepted"
        )

    def test_conservation_uses_non_whitespace_chars_not_total_length(self):
        """Whitespace differences must not affect the conservation decision."""
        old = [_tok("A B C D E F G H I J", 0, 0, 200, 15)]  # 10 non-ws chars
        new = [_tok("ABCDEF", 0, 0, 100, 15)]  # 6 non-ws chars = 60% → accepted

        assert _refinement_conserves_content(old, new)

    def test_conservation_with_currency_and_identifiers_preserved(self):
        """Critical data (CPF, R$, dates) must trigger rejection if lost."""
        old = [
            _tok("CPF: 123.456.789-00", 0, 0, 150, 15, conf=0.65),
            _tok("Data: 01/01/2024", 0, 20, 120, 35, conf=0.65),
        ]
        new = [_tok("CPF", 0, 0, 30, 15, conf=0.98)]  # far less content

        assert not _refinement_conserves_content(old, new)
