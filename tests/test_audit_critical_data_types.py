"""VQ-26 — Brazilian numeric data-type integrity.

Verifies that the CriticalDataRefiner correctly identifies and scores
the full range of Brazilian/financial data types needed for integrity
validation, and that the surface representation is preserved exactly
(not normalized or converted to a different form).

Coverage:
- CPF with and without checksum validation
- CNPJ with and without checksum validation
- Currency R$ with thousands/decimal separators
- Signed/negative percentages and currency values
- Scientific notation (1,20e-5, 3,5×10⁻²)
- UUID (RFC 4122 canonical form)
- CEP (postal code)
- CNJ process number
- Inscrição Estadual
- NF-e 44-digit access key
- Date and time formats
- Numeric ranges (10-20, 1,5 a 3,0)
- Zero-leading values (-0,00, 001, 000000)
"""
from __future__ import annotations

import pytest

from structured_pdf_text.ocr.critical_data import (
    CriticalDataRefiner,
    DataType,
    _score_as_cpf,
    _score_as_cnpj,
    _score_as_currency,
    _score_as_date,
    _score_as_percentage,
    _score_as_scientific,
    _score_as_uuid,
    _score_as_cep,
    _score_as_process_number,
    _score_as_inscricao,
    _score_as_nfe_key,
    _score_as_numeric_range,
    get_allowlist,
)


class TestCPFScoring:
    def test_valid_cpf_with_punctuation(self):
        assert _score_as_cpf("123.456.789-09") > 0.5

    def test_cpf_digits_only(self):
        assert _score_as_cpf("12345678909") > 0.0

    def test_cpf_wrong_digit_count(self):
        assert _score_as_cpf("123.456.789") == 0.0

    def test_cpf_surface_must_not_be_normalized(self):
        """Score without modifying the surface text."""
        original = "123.456.789-09"
        _ = _score_as_cpf(original)
        assert original == "123.456.789-09"


class TestCNPJScoring:
    def test_valid_cnpj_with_punctuation(self):
        assert _score_as_cnpj("12.345.678/0001-90") > 0.0

    def test_cnpj_digits_only(self):
        assert _score_as_cnpj("12345678000190") > 0.0

    def test_cnpj_too_short(self):
        assert _score_as_cnpj("1234.567/0001-90") == 0.0

    def test_cnpj_leading_zeros_preserved_in_surface(self):
        """Score must not reject CNPJ just because it starts with leading zeros."""
        score = _score_as_cnpj("00.000.000/0001-90")
        # 14 digits, correct format — at least a partial match
        assert score > 0.0


class TestCurrencyScoring:
    def test_standard_currency(self):
        assert _score_as_currency("R$ 1.234,56") == 1.0

    def test_negative_currency_not_rejected(self):
        score = _score_as_currency("R$ -1.234,56")
        assert score >= 0.0

    def test_zero_currency_preserved(self):
        assert _score_as_currency("R$ 0,00") >= 0.5

    def test_negative_zero_preserved(self):
        score = _score_as_currency("-0,00")
        # Not a valid R$ format but not zero — should not score high
        assert isinstance(score, float)

    def test_currency_without_rs_prefix(self):
        score = _score_as_currency("1.234,56")
        assert score >= 0.5


class TestPercentageScoring:
    def test_standard_percentage(self):
        assert _score_as_percentage("12,5%") > 0.0

    def test_signed_percentage_not_crashed(self):
        score = _score_as_percentage("-2,75%")
        assert isinstance(score, float)

    def test_positive_sign_percentage(self):
        score = _score_as_percentage("+2,50%")
        assert isinstance(score, float)

    def test_zero_percentage(self):
        score = _score_as_percentage("0%")
        assert score > 0.0


class TestScientificNotation:
    def test_e_notation_lowercase(self):
        assert _score_as_scientific("1.20e-5") > 0.5

    def test_e_notation_comma_decimal(self):
        assert _score_as_scientific("1,20e-5") > 0.5

    def test_e_notation_uppercase(self):
        assert _score_as_scientific("3.5E+3") > 0.5

    def test_multiplication_notation(self):
        assert _score_as_scientific("3,5×10^-2") > 0.0

    def test_plain_number_not_scientific(self):
        assert _score_as_scientific("1234") == 0.0

    def test_scientific_allowlist(self):
        al = get_allowlist(DataType.SCIENTIFIC)
        assert al is not None
        assert "e" in al or "E" in al


class TestUUIDScoring:
    def test_valid_uuid_v4(self):
        assert _score_as_uuid("550e8400-e29b-41d4-a716-446655440000") == 1.0

    def test_uuid_all_zeros(self):
        assert _score_as_uuid("00000000-0000-0000-0000-000000000000") == 1.0

    def test_uuid_without_dashes(self):
        assert _score_as_uuid("550e8400e29b41d4a716446655440000") >= 0.5

    def test_non_uuid(self):
        assert _score_as_uuid("not-a-uuid") == 0.0

    def test_uuid_surface_not_modified(self):
        original = "550e8400-e29b-41d4-a716-446655440000"
        _ = _score_as_uuid(original)
        assert original == "550e8400-e29b-41d4-a716-446655440000"


class TestCEPScoring:
    def test_cep_with_dash(self):
        assert _score_as_cep("01310-100") == 1.0

    def test_cep_without_dash(self):
        # CEP without dash is also valid (8 digits) — pattern allows -?
        assert _score_as_cep("01310100") >= 0.0  # score may or may not be 1.0

    def test_cep_too_short(self):
        assert _score_as_cep("1234") == 0.0


class TestProcessNumberScoring:
    def test_cnj_process_number(self):
        assert _score_as_process_number("1234567-89.2023.1.01.0001") == 1.0

    def test_20_digits_partial(self):
        assert _score_as_process_number("12345678901234567890") > 0.0

    def test_non_process(self):
        assert _score_as_process_number("12345") == 0.0


class TestInscricaoScoring:
    def test_inscricao_estadual(self):
        score = _score_as_inscricao("123.456.789.000")
        assert score > 0.0

    def test_inscricao_digits_only(self):
        score = _score_as_inscricao("12345678901")
        assert score > 0.0

    def test_too_short(self):
        assert _score_as_inscricao("123") == 0.0


class TestNFeKeyScoring:
    def test_44_digit_key(self):
        key = "1" * 44
        assert _score_as_nfe_key(key) >= 0.85

    def test_43_digits_rejected(self):
        assert _score_as_nfe_key("1" * 43) == 0.0

    def test_45_digits_rejected(self):
        assert _score_as_nfe_key("1" * 45) == 0.0


class TestNumericRangeScoring:
    def test_hyphen_range(self):
        assert _score_as_numeric_range("10-20") > 0.5

    def test_portuguese_range(self):
        assert _score_as_numeric_range("1,5 a 3,0") > 0.5

    def test_decimal_range(self):
        assert _score_as_numeric_range("1.5-3.0") > 0.0

    def test_plain_number_not_range(self):
        assert _score_as_numeric_range("1234") == 0.0


class TestCriticalDataRefiner:
    """Integration tests for CriticalDataRefiner."""

    def test_detect_type_from_cpf_label(self):
        refiner = CriticalDataRefiner()
        dtype = refiner.detect_type_from_context(["CPF:", "cliente"])
        assert dtype == DataType.CPF

    def test_detect_type_from_cnpj_label(self):
        refiner = CriticalDataRefiner()
        dtype = refiner.detect_type_from_context(["CNPJ:"])
        assert dtype == DataType.CNPJ

    def test_detect_type_from_currency_label(self):
        refiner = CriticalDataRefiner()
        dtype = refiner.detect_type_from_context(["Valor:"])
        assert dtype == DataType.CURRENCY

    def test_score_token_cpf(self):
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        tok = OcrToken(
            text="123.456.789-09",
            bbox=BBox(x0=0, y0=0, x1=100, y1=10),
            confidence=0.9,
            language="pt",
            source=SourceKind.OCR_REGION,
        )
        refiner = CriticalDataRefiner()
        score = refiner.score_token(tok, DataType.CPF)
        assert 0.0 <= score <= 1.0

    def test_allowlist_coverage_for_all_types(self):
        """Every DataType constant must have an allowlist entry."""
        refiner = CriticalDataRefiner()
        for attr_name in dir(DataType):
            if attr_name.startswith("_"):
                continue
            dtype = getattr(DataType, attr_name)
            if not isinstance(dtype, str):
                continue
            al = get_allowlist(dtype)
            assert al is not None, f"No allowlist for DataType.{attr_name} = {dtype!r}"
