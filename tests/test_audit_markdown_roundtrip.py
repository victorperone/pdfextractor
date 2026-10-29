"""VQ-29 — IR → Markdown round-trip validation.

Verifies that the Markdown renderer does not silently lose or mangle content
that is correctly present in the IR (StructuredDocument / StructuredTable /
PageContentBlock). Tests use only in-process objects — no OCR or PDF needed.

Coverage:
- GFM pipe table: column count, row count, cell text preserved
- HTML spanned table: rowspan/colspan attributes, cell text
- Empty-cell preservation (not filled with neighbour content)
- Heading levels H1–H4 emitted with correct # prefix
- List items rendered with marker and correct nesting level
- Link URI emitted as [text](uri) when block.link_uri is set
- Unicode NFC, accents, Brazilian numeric separators survive escaping
- Pipe literal inside cell escaped without breaking column count
- Round-trip: parse rendered GFM table back and compare cell multisets
"""
from __future__ import annotations

import re

import pytest

from structured_pdf_text.document import (
    ContentKind,
    PageContentBlock,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.renderers.markdown import _render_table, _render_content_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _cell(row: int, col: int, text: str, rowspan: int = 1, colspan: int = 1) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=rowspan, colspan=colspan,
        bbox=_bbox(x0=col * 50, y0=row * 20, x1=(col + 1) * 50, y1=(row + 1) * 20),
        text=text, tokens=[], confidence=1.0,
    )


def _table(
    cells: list[TableCell],
    row_count: int,
    col_count: int,
    header_rows: tuple[int, ...] = (0,),
) -> StructuredTable:
    frag = TableFragment(
        page_index=0,
        bbox=_bbox(x1=col_count * 50, y1=row_count * 20),
        row_start=0,
        row_end=row_count,
    )
    return StructuredTable(
        table_id="t0",
        page_fragments=[frag],
        cells=cells,
        column_count=col_count,
        row_count=row_count,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
        header_rows=header_rows,
    )


def _block(
    kind: ContentKind,
    text: str,
    *,
    heading_level: int | None = None,
    list_items=None,
    table_id: str | None = None,
    link_uri: str | None = None,
) -> PageContentBlock:
    return PageContentBlock(
        block_id="b0",
        page_index=0,
        kind=kind,
        bbox=_bbox(),
        order_index=0,
        text=text,
        heading_level=heading_level,
        list_items=list_items or [],
        table_id=table_id,
        source_region_ids=[],
        link_uri=link_uri,
    )


def _parse_gfm_table(md: str) -> list[list[str]]:
    """Parse a GFM pipe table into a list of rows (each a list of cell texts).

    Handles escaped pipes (\\|) as literal characters, not column separators.
    """
    rows = []
    for line in md.splitlines():
        line = line.strip()
        if not line or set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        if re.fullmatch(r"[\|\s\-:]+", line):
            continue
        if line.startswith("|"):
            # Replace escaped pipes with a placeholder before splitting
            placeholder = "\x00"
            processed = line.replace(r"\|", placeholder)
            cells = [c.strip().replace(placeholder, "|") for c in processed.strip("|").split("|")]
            rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# GFM pipe table round-trip
# ---------------------------------------------------------------------------

class TestGFMTableRoundtrip:
    """VQ-29: GFM pipe table output must round-trip through parsing."""

    def test_2x3_table_column_count_preserved(self):
        cells = [
            _cell(0, 0, "Nome"), _cell(0, 1, "Valor"), _cell(0, 2, "Data"),
            _cell(1, 0, "Item A"), _cell(1, 1, "R$ 1.234,00"), _cell(1, 2, "01/10/2026"),
        ]
        md = _render_table(_table(cells, 2, 3))
        rows = _parse_gfm_table(md)
        assert len(rows) >= 2
        assert all(len(r) == 3 for r in rows), f"Not all rows have 3 cols: {rows}"

    def test_cell_texts_preserved_in_multiset(self):
        cells = [
            _cell(0, 0, "Código"), _cell(0, 1, "Descrição"),
            _cell(1, 0, "001"), _cell(1, 1, "Produto A"),
            _cell(2, 0, "002"), _cell(2, 1, "Produto B"),
        ]
        md = _render_table(_table(cells, 3, 2))
        for expected in ("Código", "Descrição", "001", "Produto A", "002", "Produto B"):
            assert expected in md, f"Missing: {expected!r}"

    def test_empty_cell_not_filled_by_neighbour(self):
        """A legitimately empty cell must appear as empty in output."""
        cells = [
            _cell(0, 0, "A"), _cell(0, 1, "B"),
            _cell(1, 0, ""), _cell(1, 1, "valor"),
        ]
        md = _render_table(_table(cells, 2, 2))
        rows = _parse_gfm_table(md)
        data_rows = [r for r in rows if "A" not in r and "B" not in r]
        assert data_rows, "No data row found"
        assert data_rows[0][0].strip() == "", f"Empty cell was filled: {data_rows[0][0]!r}"

    def test_pipe_literal_escaped_in_cell(self):
        """A literal | inside cell text must be escaped so column count stays correct."""
        cells = [
            _cell(0, 0, "Condição"), _cell(0, 1, "Fórmula"),
            _cell(1, 0, "a"), _cell(1, 1, "x | y"),
        ]
        md = _render_table(_table(cells, 2, 2))
        rows = _parse_gfm_table(md)
        assert all(len(r) == 2 for r in rows), f"Pipe broke column count: {rows}"

    def test_unicode_accents_not_mangled(self):
        cells = [
            _cell(0, 0, "Índice"), _cell(0, 1, "Ação"),
            _cell(1, 0, "1"), _cell(1, 1, "Aprovação"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert "Índice" in md
        assert "Ação" in md
        assert "Aprovação" in md

    def test_brazilian_currency_separator_preserved(self):
        cells = [
            _cell(0, 0, "Valor"),
            _cell(1, 0, "R$ 1.234,56"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert "R$ 1.234,56" in md or "R\\$ 1.234,56" in md

    def test_negative_value_preserved(self):
        cells = [
            _cell(0, 0, "Resultado"),
            _cell(1, 0, "-0,00"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert "-0,00" in md


# ---------------------------------------------------------------------------
# HTML spanned table
# ---------------------------------------------------------------------------

class TestHTMLSpannedTable:
    """HTML rendering path for tables with merged cells."""

    def test_rowspan_attribute_emitted(self):
        cells = [
            _cell(0, 0, "Header A"), _cell(0, 1, "Header B"),
            _cell(1, 0, "Merged", rowspan=2), _cell(1, 1, "R1"),
            _cell(2, 1, "R2"),
        ]
        md = _render_table(_table(cells, 3, 2))
        assert 'rowspan="2"' in md
        assert "Merged" in md

    def test_colspan_attribute_emitted(self):
        cells = [
            _cell(0, 0, "Título Principal", colspan=2),
            _cell(1, 0, "Col A"), _cell(1, 1, "Col B"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert 'colspan="2"' in md
        assert "Título Principal" in md

    def test_html_special_chars_escaped(self):
        cells = [
            _cell(0, 0, "Cond"),
            _cell(1, 0, "a < b & c > d", rowspan=2),
            _cell(2, 0, "x"),
        ]
        md = _render_table(_table(cells, 3, 1))
        assert "<table>" in md
        assert "&lt;" in md
        assert "&amp;" in md
        assert "&gt;" in md


# ---------------------------------------------------------------------------
# Content block round-trip
# ---------------------------------------------------------------------------

class TestContentBlockRoundtrip:
    """VQ-29: PageContentBlock rendering must preserve structure."""

    def test_heading_h1_prefix(self):
        b = _block(ContentKind.TITLE, "Título Principal", heading_level=1)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("# Título Principal")

    def test_heading_h2_prefix(self):
        b = _block(ContentKind.TITLE, "Seção 1", heading_level=2)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("## Seção 1")

    def test_heading_h3_prefix(self):
        b = _block(ContentKind.TITLE, "Subseção 1.1", heading_level=3)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("### Subseção 1.1")

    def test_heading_h4_prefix(self):
        b = _block(ContentKind.TITLE, "Item 1.1.1", heading_level=4)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered.startswith("#### Item 1.1.1")

    def test_heading_with_wrapped_line_is_joined(self):
        """Fix-F21 (VQ-30): heading text that wraps in the PDF must be joined
        into a single line. Embedded newlines produce a heading fragment and a
        separate paragraph, breaking evaluator alignment."""
        text = "Demonstrativo com cabeçalhos agrupados | Bacia\nNorte"
        b = _block(ContentKind.TITLE, text, heading_level=1)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "\n" not in rendered.split("\n")[0], (
            "Heading must not contain embedded newline after Fix-F21"
        )
        assert "Bacia Norte" in rendered, (
            "Wrapped heading words must be joined with a space"
        )
        assert rendered.startswith("# Demonstrativo"), (
            "H1 prefix must still be present"
        )

    def test_paragraph_soft_wrap_lines_joined(self):
        """Fix-F24: scan-line breaks (\n) within a TEXT block must be joined with
        a space so that raster-OCR column overflow does not produce raw newlines
        in the rendered paragraph (which inflate cer_markdown_raw)."""
        text = "Referência de\nacompanhamento IRINF-2026-0315; atualização de 21/10/2026."
        b = _block(ContentKind.TEXT, text)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "\n" not in rendered, "Soft-wrap scan lines must be joined"
        assert "Referência de acompanhamento" in rendered

    def test_paragraph_double_newline_preserved(self):
        """Fix-F24: intentional paragraph breaks (\n\n) must not be joined."""
        text = "Parágrafo um.\n\nParágrafo dois."
        b = _block(ContentKind.TEXT, text)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "\n\n" in rendered, "Paragraph break (\\n\\n) must be preserved"

    def test_paragraph_text_preserved(self):
        b = _block(ContentKind.TEXT, "O saldo é de R$ 1.234,56.")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "R$ 1.234,56" in rendered

    def test_paragraph_special_chars_not_over_escaped(self):
        """Fix-F25: !, *, _, [, ], <, > in paragraph text must NOT be escaped
        with backslashes, matching the reference documents which never escape
        these characters — over-escaping inflates cer_markdown_raw."""
        text = "Pontuação literal: ! * [ ] < > _ identif_campo x < 3"
        b = _block(ContentKind.TEXT, text)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "\\!" not in rendered, "! must not be escaped in paragraph"
        assert "\\*" not in rendered, "* must not be escaped in paragraph"
        assert "\\[" not in rendered, "[ must not be escaped in paragraph"
        assert "\\]" not in rendered, "] must not be escaped in paragraph"
        assert "\\<" not in rendered, "< must not be escaped in paragraph"
        assert "\\>" not in rendered, "> must not be escaped in paragraph"
        assert "\\_" not in rendered, "_ must not be escaped in paragraph"

    def test_code_block_language_detection(self):
        """Fix-F26: preformatted code blocks must use specific language hints
        when content is detectably JSON/SQL/Python/CSV/YAML, matching reference
        documents that specify language for cer_markdown_raw accuracy."""
        def _make_pre(text: str) -> str:
            b = _block(ContentKind.TEXT, text)
            b.preformatted = True
            return _render_content_block(b, {}, preserve_hf=True)

        assert _make_pre('{"id":"007","ok":true}').startswith("```json")
        assert _make_pre("SELECT id FROM t WHERE x=1;").startswith("```sql")
        assert _make_pre("def f(x: int) -> int:\n    return x").startswith("```python")
        assert _make_pre("a;b;c\n1;2;3\n4;5;6").startswith("```csv")
        assert _make_pre("key: value\nother: 2").startswith("```yaml")
        # Non-specific content must stay as text (no false positives)
        assert _make_pre("PROTOCOLO=X\nVALIDACAO=OK").startswith("```text")
        assert _make_pre("n = 0.013\nDH = 1.25  # m").startswith("```text")

    def test_link_uri_emitted(self):
        b = _block(ContentKind.TEXT, "Clique aqui", link_uri="https://example.com")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "[Clique aqui](https://example.com)" in rendered

    def test_link_uri_with_paren_escaped(self):
        b = _block(ContentKind.TEXT, "Link", link_uri="https://example.com/path(test)")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        # Closing paren inside URI must be escaped
        assert "\\)" in rendered

    def test_link_uri_redundant_suppressed(self):
        # When display text IS the URL, [url](url) is redundant — output plain URL
        url = "https://example.com/verificar/V5-9A7C-2026"
        b = _block(ContentKind.TEXT, url, link_uri=url)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert f"[{url}]({url})" not in rendered
        assert url in rendered

    def test_link_uri_non_redundant_preserved(self):
        # When display text differs from URL, keep [text](url) syntax
        b = _block(ContentKind.TEXT, "Portal de verificação", link_uri="https://example.com/v")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "[Portal de verificação](https://example.com/v)" in rendered

    def test_suppressed_block_returns_empty(self):
        from dataclasses import replace
        b = _block(ContentKind.TEXT, "hidden text")
        b = replace(b, suppressed=True)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""

    def test_figure_block_without_text_returns_empty(self):
        b = _block(ContentKind.FIGURE, "")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""

    def test_table_block_without_matching_table_returns_empty(self):
        b = _block(ContentKind.TABLE, "", table_id="missing-table")
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert rendered == ""

    def test_bold_subtitle_after_h1_suppressed(self):
        # VQ-21: bold TEXT in h1 section (prev_was_h1=True) → rendered as plain
        b = _block(ContentKind.TEXT, "Estruturas nativas com recuo.")
        b.inline_style = "bold"
        rendered = _render_content_block(b, {}, preserve_hf=True, prev_was_h1=True)
        assert "**" not in rendered
        assert "Estruturas nativas com recuo." in rendered

    def test_bold_in_h1_section_suppressed(self):
        # VQ-21 ext: bold TEXT anywhere in h1 section → rendered as plain
        b = _block(ContentKind.TEXT, "Observação: texto.")
        b.inline_style = "italic"
        rendered = _render_content_block(b, {}, preserve_hf=True, in_h1_section=True)
        assert "*" not in rendered
        assert "Observação: texto." in rendered

    def test_bold_non_subtitle_preserved(self):
        # VQ-21: bold TEXT outside h1 section → kept as bold (normal bold text)
        b = _block(ContentKind.TEXT, "Texto importante.")
        b.inline_style = "bold"
        rendered = _render_content_block(b, {}, preserve_hf=True, prev_was_h1=False)
        assert rendered == "**Texto importante.**"

    def test_numbered_list_marker_not_escaped(self):
        """Fix-F39: numbered list markers must NOT be escaped in paragraph context.

        The corpus reference renders '3. Publicar...' as a plain numbered list item
        without backslash escaping. Over-escaping inflates cer_markdown_raw.
        """
        from structured_pdf_text.renderers.markdown import _escape_inline_text
        text = "3. Publicar o resultado."
        result = _escape_inline_text(text, context="paragraph")
        assert result == "3. Publicar o resultado.", f"Unexpected escaping: {result!r}"

    def test_numbered_list_periods_inside_not_escaped(self):
        """Fix-F39: periods inside lines are not escaped (decimal, end-of-sentence)."""
        from structured_pdf_text.renderers.markdown import _escape_inline_text
        text = "1. Preparar o CNPJ 12.345.678/0001-90 e protocolo V5-003."
        result = _escape_inline_text(text, context="paragraph")
        assert "12\\.345" not in result, f"Decimal period wrongly escaped: {result!r}"
        assert "0001-90\\." not in result, f"End period wrongly escaped: {result!r}"
        assert "\\" not in result, f"No backslash expected: {result!r}"


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

class TestStructuralInvariants:
    """Structural properties that must hold for all rendered output."""

    def test_gfm_table_has_separator_row(self):
        """Every GFM pipe table must contain a separator row (--- pattern)."""
        cells = [_cell(0, 0, "H"), _cell(1, 0, "V")]
        md = _render_table(_table(cells, 2, 1))
        if "<table>" not in md:
            assert "---" in md, "GFM table missing separator row"

    def test_html_table_well_formed(self):
        """HTML tables must have balanced <table> and </table> tags."""
        cells = [
            _cell(0, 0, "H1", colspan=2),
            _cell(1, 0, "A"), _cell(1, 1, "B"),
        ]
        md = _render_table(_table(cells, 2, 2))
        assert md.count("<table>") == md.count("</table>")
        assert md.count("<tr>") == md.count("</tr>")

    def test_no_duplicate_content_from_cell_conflict(self):
        """Two cells claiming the same position must not double the first value."""
        cells = [
            _cell(0, 0, "Col"),
            _cell(1, 0, "X"),
            _cell(1, 0, "X"),
        ]
        md = _render_table(_table(cells, 2, 1))
        assert md.count("X / X") == 0
        assert "X" in md

    def test_non_ascii_in_heading_not_lost(self):
        """Heading with accented chars must survive rendering."""
        b = _block(ContentKind.TITLE, "Índice Geral — Seção 2º", heading_level=2)
        rendered = _render_content_block(b, {}, preserve_hf=True)
        assert "Índice" in rendered
        assert "Seção" in rendered

    def test_header_only_table_renders_as_pipe_not_html(self):
        """Fix-F15: single-row table with header_rows=() must use pipe format.

        infer_header_rows() returns () for tables without body rows (header_only_table).
        The renderer must not treat this as 'unusual headers' and fall back to HTML.
        V6 pages 014/049/070 condition: adjacent_tables+header_only_table+narrow_ocr_table.
        """
        cells = [_cell(0, 0, "Campo"), _cell(0, 1, "Status")]
        md = _render_table(_table(cells, 1, 2, header_rows=()))
        assert "<table>" not in md, "header_only table (header_rows=()) must not use HTML"
        assert "| Campo | Status |" in md
        assert "| --- | --- |" in md

    def test_multi_header_rows_still_uses_html(self):
        """Tables with header_rows=(0,1) must still use HTML to preserve multi-header."""
        cells = [
            _cell(0, 0, "A", colspan=2), _cell(1, 0, "B"), _cell(1, 1, "C"),
        ]
        md = _render_table(_table(cells, 2, 2, header_rows=(0, 1)))
        assert "<table>" in md, "multi-header table must use HTML"

    def test_multi_row_data_table_stays_html(self):
        """Fix-F15-AJUSTAR: multi-row table with header_rows=() must stay HTML.

        V3 pages 046/067: data-only tables where no header was detected.
        Pipe format would falsely designate the first data row as a header.
        """
        cells = [
            _cell(0, 0, "PED-0001"), _cell(0, 1, "R$ 1,20"),
            _cell(1, 0, "PED-0002"), _cell(1, 1, "R$ 0,00"),
        ]
        md = _render_table(_table(cells, 2, 2, header_rows=()))
        assert "<table>" in md, "multi-row data table (header_rows=()) must use HTML, not pipe"

    def test_single_cell_table_stays_html(self):
        """Fix-F15-AJUSTAR: 1×1 table with header_rows=() must stay HTML.

        V3 page 049: single data cell. Pipe format looks identical but
        the V3 reference expects HTML for this case.
        """
        cells = [_cell(0, 0, "CÉLULA-ÚNICA-042")]
        md = _render_table(_table(cells, 1, 1, header_rows=()))
        assert "<table>" in md, "1×1 data table (header_rows=()) must use HTML"



# ---------------------------------------------------------------------------
# Visual header row inference (Fix-F16)
# ---------------------------------------------------------------------------

class TestInferVisualHeaderRowsFallback:
    """Fix-F16: content-based fallback when no background shading is present."""

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("numpy") is None,
        reason="numpy required",
    )
    def test_label_first_row_returns_header_zero(self):
        """Unshaded table with all-text first row must return (0,)."""
        import numpy as np
        from structured_pdf_text.tables.headers import infer_visual_header_rows
        from structured_pdf_text.geometry import BBox

        # Uniform gray image — no shading difference between rows
        image = np.full((200, 400, 3), 220, dtype=np.uint8)
        raster_bbox = BBox(x0=0.0, y0=0.0, x1=400.0, y1=200.0)
        cells = [
            _cell(0, 0, "Protocolo"), _cell(0, 1, "Data"),
            _cell(0, 2, "Responsável"), _cell(0, 3, "Situação"),
            _cell(1, 0, "PRO-001"), _cell(1, 1, "01/10/2026"),
            _cell(1, 2, "João Silva"), _cell(1, 3, "Ativo"),
        ]
        result = infer_visual_header_rows(cells, image, raster_bbox)
        assert result == (0,), f"Label first row must detect header, got {result!r}"

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("numpy") is None,
        reason="numpy required",
    )
    def test_numeric_first_row_stays_headerless(self):
        """Unshaded table with numeric first row must NOT trigger fallback."""
        import numpy as np
        from structured_pdf_text.tables.headers import infer_visual_header_rows
        from structured_pdf_text.geometry import BBox

        image = np.full((200, 400, 3), 220, dtype=np.uint8)
        raster_bbox = BBox(x0=0.0, y0=0.0, x1=400.0, y1=200.0)
        cells = [
            _cell(0, 0, "001"), _cell(0, 1, "002"), _cell(0, 2, "003"),
            _cell(1, 0, "100"), _cell(1, 1, "200"), _cell(1, 2, "300"),
        ]
        result = infer_visual_header_rows(cells, image, raster_bbox)
        assert result == (), f"Numeric first row must remain headerless, got {result!r}"


class TestInferHeaderRowsMultiRowSpan:
    """VT-20: infer_header_rows must include row 1 when row 0 has rowspan=2 (F19 fix)."""

    def _bold_cell(self, row, col, text, rowspan=1, colspan=1):
        from types import SimpleNamespace
        tok = SimpleNamespace(font_weight=700, font_size=12.0)
        return TableCell(
            row=row, col=col, rowspan=rowspan, colspan=colspan,
            bbox=BBox(x0=col*50.0, y0=row*20.0, x1=(col+1)*50.0, y1=(row+1)*20.0),
            text=text, tokens=[tok], confidence=1.0,
        )

    def test_rowspan2_in_row0_extends_header_to_row1(self):
        """When row 0 has a cell with rowspan=2, header_rows must be (0, 1)."""
        from structured_pdf_text.tables.headers import infer_header_rows
        cells = [
            self._bold_cell(0, 0, "Região", rowspan=2),
            self._bold_cell(0, 1, "Receita", colspan=2),
            self._bold_cell(0, 2, "Qualidade", colspan=2),
            _cell(1, 1, "1º semestre"),
            _cell(1, 2, "2º semestre"),
            _cell(1, 3, "NPS"),
            _cell(1, 4, "SLA"),
            _cell(2, 0, "Sul"), _cell(2, 1, "R$ 1,20 mi"),
            _cell(2, 2, "R$ 1,35 mi"), _cell(2, 3, "72"), _cell(2, 4, "98,2%"),
        ]
        result = infer_header_rows(cells)
        assert result == (0, 1), f"Expected (0, 1) for rowspan-2 header, got {result!r}"

    def test_rowspan1_header_stays_row0_only(self):
        """Standard header without rowspan must still return (0,)."""
        from structured_pdf_text.tables.headers import infer_header_rows
        from types import SimpleNamespace
        def bold(row, col, text):
            tok = SimpleNamespace(font_weight=700, font_size=12.0)
            return TableCell(
                row=row, col=col, rowspan=1, colspan=1,
                bbox=BBox(x0=col*50.0, y0=row*20.0, x1=(col+1)*50.0, y1=(row+1)*20.0),
                text=text, tokens=[tok], confidence=1.0,
            )
        cells = [
            bold(0, 0, "Categoria"), bold(0, 1, "Produto"), bold(0, 2, "Valor"),
            _cell(1, 0, "Equipamentos", rowspan=2),
            _cell(1, 1, "Monitor"), _cell(1, 2, "R$ 1.200,00"),
            _cell(2, 1, "Teclado"), _cell(2, 2, "R$ 250,00"),
        ]
        result = infer_header_rows(cells)
        assert result == (0,), f"Standard header must return (0,), got {result!r}"
