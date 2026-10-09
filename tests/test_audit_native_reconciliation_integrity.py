"""VQ-01 — Native reconciliation must not alter critical Brazilian punctuation.

Tests that _reconcile_with_textpage and _compact preserve decimal separators,
currency symbols, signs, date delimiters, and identifier separators even when
the PDFium text-page line suggests a different punctuation form.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.text.line_detector import (
    _compact,
    _punctuation_skeleton,
    _replacement_changes_critical_punctuation,
    reconstruct_native_lines,
)
from structured_pdf_text.document import NativeCharacter, BBox, Point
from structured_pdf_text.geometry import BBox as GB


# ---------------------------------------------------------------------------
# _compact helper
# ---------------------------------------------------------------------------

class TestCompact:
    def test_strips_non_alnum(self):
        assert _compact("R$ 1.234,00") == "r123400"

    def test_casefolded(self):
        assert _compact("ABC") == "abc"

    def test_empty(self):
        assert _compact("") == ""

    def test_only_punctuation(self):
        assert _compact("!@#$%") == ""


# ---------------------------------------------------------------------------
# _punctuation_skeleton helper
# ---------------------------------------------------------------------------

class TestPunctuationSkeleton:
    def test_extracts_punctuation(self):
        assert _punctuation_skeleton("R$ 1.234,00") == "$.,"

    def test_ignores_alphanum(self):
        assert _punctuation_skeleton("abc123") == ""

    def test_ignores_spaces(self):
        assert _punctuation_skeleton("a b c") == ""


# ---------------------------------------------------------------------------
# _replacement_changes_critical_punctuation — VQ-01 core guard
# ---------------------------------------------------------------------------

class TestReplacementChangesCriticalPunctuation:
    """Cases where replacement MUST be blocked (returns True)."""

    def test_comma_to_period_in_currency(self):
        # R$ 1.234,00 → R$ 1.234.00 — comma is the decimal separator
        assert _replacement_changes_critical_punctuation("R$ 1.234,00", "R$ 1.234.00")

    def test_negative_sign_removed(self):
        assert _replacement_changes_critical_punctuation("-2,75%", "2,75%")

    def test_percentage_separator_changed(self):
        assert _replacement_changes_critical_punctuation("12,5%", "12.5%")

    def test_date_delimiter_changed(self):
        assert _replacement_changes_critical_punctuation("01/10/2026", "01.10.2026")

    def test_comparison_operator_changed(self):
        assert _replacement_changes_critical_punctuation("saldo >= 0", "saldo > 0")

    """Cases where replacement is SAFE (returns False)."""

    def test_same_text_safe(self):
        assert not _replacement_changes_critical_punctuation("R$ 1.234,00", "R$ 1.234,00")

    def test_spacing_fix_safe(self):
        assert not _replacement_changes_critical_punctuation("hello world", "hello  world")

    def test_no_critical_pattern_safe(self):
        assert not _replacement_changes_critical_punctuation("Contrato de locação", "Contrato de Locação")

    def test_ligature_fix_safe(self):
        # No critical pattern — punctuation skeleton identical
        assert not _replacement_changes_critical_punctuation("ofício", "oficio")


# ---------------------------------------------------------------------------
# _reconcile_with_textpage integration — via reconstruct_native_lines
# ---------------------------------------------------------------------------

def _make_char(page_index: int, char_index: int, text: str, x0: float, x1: float) -> NativeCharacter:
    return NativeCharacter(
        page_index=page_index,
        char_index=char_index,
        text=text,
        unicode_codepoint=ord(text) if len(text) == 1 else None,
        bbox=GB(x0=x0, y0=0.0, x1=x1, y1=10.0),
    )


def _make_line_chars(page_index: int, text: str, start_x: float = 0.0) -> tuple[NativeCharacter, ...]:
    chars = []
    x = start_x
    for i, ch in enumerate(text):
        chars.append(_make_char(page_index, i, ch, x, x + 8.0))
        x += 8.0
    return tuple(chars)


class TestReconcileIntegration:
    def test_currency_reconciliation_preserves_comma(self):
        """R$ 1.234,00 must not become R$ 1.234.00 via text-page reconciliation."""
        chars = _make_line_chars(0, "R$ 1.234,00")
        # Provide text-page text that changes the decimal separator.
        lines = reconstruct_native_lines(chars, extracted_text="R$ 1.234.00")
        assembled = " ".join(l.text for l in lines if l.text.strip())
        assert "," in assembled, f"Decimal comma must be preserved, got: {assembled!r}"
        assert assembled == "R$ 1.234,00"

    def test_spacing_reconciliation_allowed(self):
        """Fixing spacing (no critical punctuation) is still allowed."""
        chars = _make_line_chars(0, "helloworld")
        lines = reconstruct_native_lines(chars, extracted_text="hello world")
        assembled = " ".join(l.text for l in lines if l.text.strip())
        # spacing fix is allowed but we check content preserved
        assert "hello" in assembled

    def test_negative_sign_not_dropped(self):
        """Leading minus must survive reconciliation."""
        chars = _make_line_chars(0, "-2,75%")
        lines = reconstruct_native_lines(chars, extracted_text="2,75%")
        assembled = " ".join(l.text for l in lines if l.text.strip())
        assert "-" in assembled, f"Negative sign must be preserved, got: {assembled!r}"

    def test_cnpj_separator_preserved(self):
        chars = _make_line_chars(0, "12.345.678/0001-90")
        lines = reconstruct_native_lines(chars, extracted_text="12345678000190")
        assembled = " ".join(l.text for l in lines if l.text.strip())
        # The original CNPJ punctuation must survive
        assert "." in assembled or "/" in assembled or "-" in assembled

    def test_ptbr_decimal_tracked_characters(self):
        """Tracked/spaced glyph rendering should fix spacing without altering separators."""
        chars = _make_line_chars(0, "1 , 2 3 4 , 0 0")
        lines = reconstruct_native_lines(chars, extracted_text="1,234,00")
        assembled = " ".join(l.text for l in lines if l.text.strip())
        # should not have introduced a different separator
        assert "." not in assembled or "," in assembled
