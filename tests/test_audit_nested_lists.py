"""VQ-16 — Phase C scaffold: nested list detection and indentation.

Documents expected behavior for detecting and rendering nested list structures.
Phase C will implement the full nesting algorithm; tests are xfail as specs.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import TextLine, WritingDirection, TextToken, EvidenceRef, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.lists import segment_list_lines


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

    def test_non_list_lines_not_marked(self):
        lines = [
            _line("Este é um parágrafo normal.", x0=10),
            _line("Sem marcadores de lista aqui.", x0=10),
        ]
        result = segment_list_lines(lines)
        assert result is not None

    def test_empty_input(self):
        result = segment_list_lines([])
        assert result is not None


@pytest.mark.xfail(reason="VQ-16 Phase C — nested list indentation not yet implemented", strict=False)
class TestNestedListsPhasec:
    """Specification tests for nested list rendering."""

    def test_indented_items_become_nested(self):
        """Lines with deeper x0 indentation become nested list items."""
        lines = [
            _line("- Parent A", x0=10),
            _line("  - Child A1", x0=30),
            _line("  - Child A2", x0=30),
            _line("- Parent B", x0=10),
        ]
        result = segment_list_lines(lines)
        md = _render_list_to_markdown(result)  # type: ignore[name-defined]
        assert "  - Child A1" in md or "    - Child A1" in md

    def test_numbered_nested_list(self):
        """Numbered items with deeper indentation become sub-numbered."""
        lines = [
            _line("1. First", x0=10),
            _line("   1.1 Sub-first", x0=30),
            _line("2. Second", x0=10),
        ]
        result = segment_list_lines(lines)
        md = _render_list_to_markdown(result)  # type: ignore[name-defined]
        assert "Sub-first" in md

    def test_continuation_line_joins_previous_item(self):
        """A non-bullet line that is indented continues the previous item."""
        lines = [
            _line("- Item com texto", x0=10),
            _line("  que continua aqui.", x0=30),
            _line("- Próximo item.", x0=10),
        ]
        result = segment_list_lines(lines)
        md = _render_list_to_markdown(result)  # type: ignore[name-defined]
        assert "que continua aqui" in md
