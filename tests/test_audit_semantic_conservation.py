"""VQ-07 — _refinement_conserves_content must protect negations and operators.

Tests that replacing OCR tokens never silently removes negation words,
comparison operators, or negative signs that change document meaning.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.api import (
    _refinement_conserves_content,
    _extract_protected_semantic_tokens,
)
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


def _tok(text: str, conf: float = 0.9) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(0, 0, 200, 15),
        confidence=conf,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )


class TestProtectedSemanticTokens:
    def test_negation_extracted(self):
        assert "não" in _extract_protected_semantic_tokens("não é válido")

    def test_negation_nao_extracted(self):
        assert "nao" in _extract_protected_semantic_tokens("nao aprovado")

    def test_comparison_operator_extracted(self):
        tokens = _extract_protected_semantic_tokens("saldo >= 0")
        assert ">=" in tokens

    def test_negative_sign_extracted(self):
        tokens = _extract_protected_semantic_tokens("resultado: -2,5%")
        assert "-" in tokens

    def test_positive_sign_not_extracted(self):
        # Positive sign is redundant; removing it does not change meaning
        tokens = _extract_protected_semantic_tokens("R$ +350,00")
        assert "+" not in tokens

    def test_empty_text(self):
        assert _extract_protected_semantic_tokens("") == frozenset()


class TestRefinementConservesContentNegation:
    """VQ-07 reproductions from the audit (R/C evidence)."""

    def test_negation_removal_blocked(self):
        old = [_tok("O paciente não apresenta alteração importante neste exame")]
        new = [_tok("O paciente apresenta alteração importante neste exame")]
        assert _refinement_conserves_content(old, new) is False

    def test_negation_nao_removal_blocked(self):
        old = [_tok("aprovação nao concedida")]
        new = [_tok("aprovação concedida")]
        assert _refinement_conserves_content(old, new) is False

    def test_sem_removal_blocked(self):
        old = [_tok("contrato sem validade")]
        new = [_tok("contrato validade")]
        assert _refinement_conserves_content(old, new) is False

    def test_deferido_indeferido_swap_blocked(self):
        old = [_tok("pedido indeferido")]
        new = [_tok("pedido deferido")]
        assert _refinement_conserves_content(old, new) is False

    def test_negative_sign_removal_blocked(self):
        old = [_tok("-2,75%")]
        new = [_tok("2,75%")]
        assert _refinement_conserves_content(old, new) is False

    def test_comparison_operator_removal_blocked(self):
        old = [_tok("valor >= limite")]
        new = [_tok("valor limite")]
        assert _refinement_conserves_content(old, new) is False

    def test_less_than_operator_removal_blocked(self):
        old = [_tok("a < b")]
        new = [_tok("a b")]
        assert _refinement_conserves_content(old, new) is False


class TestRefinementConservesContentBenign:
    """Changes that should be accepted."""

    def test_spacing_fix_accepted(self):
        old = [_tok("hello world")]
        new = [_tok("hello  world")]
        assert _refinement_conserves_content(old, new) is True

    def test_explicit_positive_sign_removal_accepted(self):
        old = [_tok("Valor: R$ +350,00")]
        new = [_tok("Valor: R$ 350,00", conf=0.99)]
        assert _refinement_conserves_content(old, new) is True

    def test_same_text_accepted(self):
        old = [_tok("texto igual")]
        new = [_tok("texto igual")]
        assert _refinement_conserves_content(old, new) is True

    def test_critical_data_preserved_accepted(self):
        old = [_tok("CPF: 123.456.789-01")]
        new = [_tok("CPF: 123.456.789-01")]
        assert _refinement_conserves_content(old, new) is True

    def test_critical_data_removed_blocked(self):
        old = [_tok("CNPJ: 12.345.678/0001-90")]
        new = [_tok("CNPJ:")]
        assert _refinement_conserves_content(old, new) is False

    def test_short_text_bypass_still_checks_negation(self):
        # Short texts bypass coverage check but NOT negation check
        old = [_tok("não")]
        new = [_tok("sim")]
        assert _refinement_conserves_content(old, new) is False
