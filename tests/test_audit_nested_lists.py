"""VQ-16 — Nested list detection and hierarchical marker parsing.

Tests that parse_list_marker recognises hierarchical markers (1.1., 1.2.1.)
and rejects false positives (dates, version strings). Phase C rendering
tests for indent-based nesting remain xfail until implemented.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import TextLine, WritingDirection, TextToken, EvidenceRef, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.lists import parse_list_marker, segment_list_lines


def _text_token(text: str, x0: float) -> TextToken:
    return TextToken(
        text=text,
        bbox=BBox(x0=x0, y0=0, x1=x0 + len(text) * 6, y1=12),
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "r0")],
        confidence=1.0,
        normalized_text=text,
    )


def _line(text: str, x0: float = 0.0) -> TextLine:
    tok = _text_token(text, x0)
    return TextLine(
        tokens=[tok],
        bbox=BBox(x0=x0, y0=0, x1=x0 + 200, y1=12),
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
        text_override=text,
    )


class TestParseListMarker:
    """VQ-16: parse_list_marker must handle hierarchical markers."""

    def test_simple_bullet(self):
        result = parse_list_marker("• Item simples")
        assert result is not None
        assert result[0] == "•"

    def test_dash_bullet(self):
        result = parse_list_marker("- Item com traço")
        assert result is not None

    def test_simple_numbered(self):
        result = parse_list_marker("1. Primeiro item")
        assert result is not None
        assert result[0] == "1."

    def test_hierarchical_two_levels(self):
        """1.1. Texto deve ser reconhecido como marcador hierárquico."""
        result = parse_list_marker("1.1. Validar o CNPJ")
        assert result is not None, "Hierarchical marker 1.1. must be parsed"
        assert result[0] == "1.1."
        assert "Validar" in result[1]

    def test_hierarchical_three_levels(self):
        """1.2.1. Texto deve ser reconhecido como marcador hierárquico."""
        result = parse_list_marker("1.2.1. Anexar a memória de cálculo")
        assert result is not None, "Hierarchical marker 1.2.1. must be parsed"
        assert result[0] == "1.2.1."

    def test_date_not_a_marker(self):
        """2026.10 é ano.mês — não deve ser marcador de lista."""
        result = parse_list_marker("2026.10 é a versão")
        assert result is None, "Date-like string 2026.10 must not be a list marker"

    def test_version_string_not_a_marker(self):
        """Versão 1.0.0 não deve ser marcador de lista (sem ponto terminal após números)."""
        result = parse_list_marker("1.0.0 Release notes")
        # Sem ponto terminal, não deve ser marcador hierárquico
        assert result is None or result[0] not in {"1.0.0.", "1.0."}

    def test_checkbox_markers(self):
        """Checkboxes devem ser reconhecidos como marcadores."""
        assert parse_list_marker("☑ Item concluído") is not None
        assert parse_list_marker("☐ Item pendente") is not None

    def test_alpha_marker(self):
        """a) Texto deve ser marcador de lista."""
        result = parse_list_marker("a) Alínea A")
        assert result is not None

    def test_roman_numeral_marker(self):
        """iv) Texto deve ser marcador de lista."""
        result = parse_list_marker("iv) Sub-item quatro")
        assert result is not None

    def test_non_list_line_returns_none(self):
        assert parse_list_marker("Este é um parágrafo normal.") is None
        assert parse_list_marker("R$ 1.234,00") is None
        assert parse_list_marker("CNPJ: 12.345.678/0001-90") is None


class TestListDetectionUnit:
    """Tests on the existing list segmentation logic."""

    def test_bullet_lines_detected(self):
        lines = [
            _line("- Item A", x0=10),
            _line("- Item B", x0=10),
            _line("- Item C", x0=10),
        ]
        result = segment_list_lines(lines)
        assert result is not None
        assert result.list_item_count >= 3

    def test_hierarchical_markers_form_list(self):
        """Linhas com marcadores hierárquicos formam uma lista."""
        lines = [
            _line("1. Primeiro", x0=10),
            _line("1.1. Sub-item A", x0=30),
            _line("1.2. Sub-item B", x0=30),
            _line("2. Segundo", x0=10),
        ]
        result = segment_list_lines(lines)
        assert result is not None
        # At minimum, items with recognised markers should be found
        assert result.list_item_count >= 2

    def test_non_list_lines_not_marked(self):
        lines = [
            _line("Este é um parágrafo normal.", x0=10),
            _line("Sem marcadores de lista aqui.", x0=10),
        ]
        result = segment_list_lines(lines)
        assert result is not None
        assert result.list_item_count == 0

    def test_empty_input(self):
        result = segment_list_lines([])
        assert result is not None

    def test_date_lines_not_mistaken_for_list(self):
        """Linhas com datas não devem ser tratadas como itens de lista."""
        lines = [
            _line("2026.10 é a versão atual", x0=10),
            _line("2026.11 está planejada", x0=10),
        ]
        result = segment_list_lines(lines)
        # These should NOT be detected as list items
        assert result.list_item_count == 0


@pytest.mark.xfail(reason="VQ-16 Phase C — indent-based nesting rendering not yet implemented", strict=False)
class TestNestedListsPhasec:
    """Specification tests for nested list rendering based on indentation."""

    def test_indented_items_become_nested(self):
        lines = [
            _line("- Parent A", x0=10),
            _line("  - Child A1", x0=30),
            _line("  - Child A2", x0=30),
            _line("- Parent B", x0=10),
        ]
        result = segment_list_lines(lines)
        md = _render_list_to_markdown(result)  # type: ignore[name-defined]
        assert "  - Child A1" in md or "    - Child A1" in md

    def test_continuation_line_joins_previous_item(self):
        lines = [
            _line("- Item com texto", x0=10),
            _line("  que continua aqui.", x0=30),
            _line("- Próximo item.", x0=10),
        ]
        result = segment_list_lines(lines)
        md = _render_list_to_markdown(result)  # type: ignore[name-defined]
        assert "que continua aqui" in md
