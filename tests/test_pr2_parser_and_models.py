"""Tests for PR 2 — Markdown parser, models and page scope (spec §12.3).

Covers:
  P05  Duplicate page marker → ValueError
  P06  Title containing 'Página 2' is kept as semantic heading
  P09  No page markers → no crash; scope handled
  S18  Single-item list detected
  S19  Correct nested list hierarchy
  S24  Multi-line list item interpreted as single item
  S25  '#' inside code fence is NOT a heading
  B05  Two GFM tables with different widths → both valid (no crash)
  B10  Empty cell is a structural cell, not an absent cell
  B11  Escaped pipe \| in cell is not split into two cells
  B12  '|' in code block does NOT create a false table
"""
from __future__ import annotations

import pytest

from structured_pdf_text.evaluation.markdown_parser import parse_document, parse_page
from structured_pdf_text.evaluation.models import NodeKind
from structured_pdf_text.evaluation.normalization import normalize, NormProfile
from structured_pdf_text.evaluation.page_scope import build_page_scope


# ---------------------------------------------------------------------------
# Parser: headings
# ---------------------------------------------------------------------------

class TestHeadingParsing:
    def test_heading_level_extracted(self):
        nodes = parse_document("# H1\n\n## H2\n\n### H3")
        headings = [n for n in nodes if n.kind == NodeKind.HEADING]
        assert len(headings) == 3
        assert headings[0].heading_level == 1
        assert headings[1].heading_level == 2
        assert headings[2].heading_level == 3

    def test_heading_visible_text(self):
        nodes = parse_document("# Título **importante**\n")
        h = [n for n in nodes if n.kind == NodeKind.HEADING]
        assert h[0].visible_text == "Título importante"

    def test_P06_page_word_in_heading_not_stripped(self):
        """P06: A semantic heading containing 'Página 2' is kept as heading."""
        doc = "## Página 1\nConteúdo.\n\n# Relatório da Página 2\n"
        nodes = parse_document(doc)
        headings = [n for n in nodes if n.kind == NodeKind.HEADING]
        assert any("Página 2" in n.visible_text or "Relatório" in n.visible_text
                   for n in headings), f"heading not found: {[n.visible_text for n in headings]}"

    def test_S25_hash_inside_fence_not_heading(self):
        """S25: '#' inside a code fence must not produce a heading node."""
        doc = "Texto.\n\n```python\n# this is a comment\ncode()\n```\n"
        nodes = parse_document(doc)
        headings = [n for n in nodes if n.kind == NodeKind.HEADING]
        assert headings == [], f"false heading detected: {[n.visible_text for n in headings]}"
        code_nodes = [n for n in nodes if n.kind == NodeKind.CODE]
        assert len(code_nodes) == 1


# ---------------------------------------------------------------------------
# Parser: page markers
# ---------------------------------------------------------------------------

class TestPageMarkers:
    def test_page_numbers_assigned(self):
        doc = "## Página 1\nTexto um.\n\n## Página 2\n# Título\n\nTexto dois."
        nodes = parse_document(doc)
        p1 = [n for n in nodes if n.page == 1]
        p2 = [n for n in nodes if n.page == 2]
        assert p1
        assert p2
        assert any(n.kind == NodeKind.HEADING for n in p2)

    def test_P05_duplicate_page_marker_raises(self):
        """P05: duplicate '## Página N' → ValueError with clear message."""
        with pytest.raises(ValueError, match="Duplicate page marker"):
            parse_document("## Página 1\nA\n\n## Página 1\nB")

    def test_P09_no_page_markers_no_crash(self):
        """P09: document without page markers → nodes with page=None."""
        nodes = parse_document("# Título\n\nTexto.")
        assert all(n.page is None for n in nodes)
        assert len([n for n in nodes if n.kind == NodeKind.HEADING]) == 1

    def test_empty_page_produces_no_nodes(self):
        doc = "## Página 1\n\n## Página 2\nTexto."
        nodes = parse_document(doc)
        assert all(n.page == 2 for n in nodes)


# ---------------------------------------------------------------------------
# Parser: lists
# ---------------------------------------------------------------------------

class TestListParsing:
    def test_S18_single_item_list(self):
        """S18: A single-item list is still detected as a list."""
        nodes = parse_document("- único item")
        list_nodes = [n for n in nodes if n.kind == NodeKind.LIST]
        assert len(list_nodes) == 1
        items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
        assert len(items) == 1

    def test_S19_nested_list_hierarchy(self):
        """S19: Nested list → correct depth levels."""
        doc = "- A\n  - A1\n  - A2\n- B"
        nodes = parse_document(doc)
        items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
        depths = [n.list_depth for n in items]
        assert 1 in depths  # top-level items
        assert 2 in depths  # nested items

    def test_S24_multiline_item_single_item(self):
        """S24: A list item spanning multiple lines is one item."""
        doc = "- primeira linha\n  continuação da mesma"
        nodes = parse_document(doc)
        items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
        assert len(items) == 1

    def test_ordered_list_detected(self):
        doc = "1. primeiro\n2. segundo\n3. terceiro"
        nodes = parse_document(doc)
        lists = [n for n in nodes if n.kind == NodeKind.LIST]
        assert lists[0].ordered_list is True
        items = [n for n in nodes if n.kind == NodeKind.LIST_ITEM]
        assert all(n.ordered_list is True for n in items)

    def test_unordered_list_detected(self):
        doc = "- alpha\n- beta\n+ gamma"
        nodes = parse_document(doc)
        lists = [n for n in nodes if n.kind == NodeKind.LIST]
        assert any(n.ordered_list is False for n in lists)


# ---------------------------------------------------------------------------
# Parser: tables
# ---------------------------------------------------------------------------

class TestTableParsing:
    def test_table_cells_extracted(self):
        doc = "| A | B |\n|---|---|\n| 1 | 2 |"
        nodes = parse_document(doc)
        cells = [n for n in nodes if n.kind == NodeKind.TABLE_CELL]
        texts = [n.visible_text for n in cells]
        assert "A" in texts
        assert "B" in texts
        assert "1" in texts

    def test_B05_two_tables_different_widths(self):
        """B05: Two GFM tables with different column counts → both parsed, no crash."""
        doc = (
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "| X | Y | Z |\n|---|---|---|\n| 7 | 8 | 9 |"
        )
        nodes = parse_document(doc)
        tables = [n for n in nodes if n.kind == NodeKind.TABLE]
        assert len(tables) == 2

    def test_B10_empty_cell_is_structural(self):
        """B10: An empty cell is a structural slot, not an absent cell."""
        doc = "| A | B |\n|---|---|\n|  | valor |"
        nodes = parse_document(doc)
        cells = [n for n in nodes if n.kind == NodeKind.TABLE_CELL]
        # Should have 4 cells (header + data row × 2 columns)
        assert len(cells) == 4

    def test_B11_escaped_pipe_not_split(self):
        """B11: '\\|' inside a cell should not split the cell."""
        doc = "| Col A | Col B |\n|---|---|\n| valor \\| extra | ok |"
        nodes = parse_document(doc)
        cells = [n for n in nodes if n.kind == NodeKind.TABLE_CELL]
        # Should have 4 cells, not 5 (no false split on escaped pipe)
        assert len(cells) == 4

    def test_B12_pipe_in_code_not_table(self):
        """B12: '|' inside a code block must not produce a table node."""
        doc = "Texto.\n\n```\nA | B\n```\n"
        nodes = parse_document(doc)
        tables = [n for n in nodes if n.kind == NodeKind.TABLE]
        assert tables == [], f"false table detected: {tables}"


# ---------------------------------------------------------------------------
# Parser: node IDs and determinism
# ---------------------------------------------------------------------------

class TestNodeIds:
    def test_ids_are_deterministic(self):
        doc = "# Título\n\nParágrafo."
        nodes1 = parse_document(doc, origin="reference")
        nodes2 = parse_document(doc, origin="reference")
        ids1 = [n.node_id for n in nodes1]
        ids2 = [n.node_id for n in nodes2]
        assert ids1 == ids2

    def test_ids_differ_by_origin(self):
        doc = "# Título\n\nParágrafo."
        ref_nodes = parse_document(doc, origin="reference")
        hyp_nodes = parse_document(doc, origin="hypothesis")
        ref_ids = {n.node_id for n in ref_nodes}
        hyp_ids = {n.node_id for n in hyp_nodes}
        assert ref_ids.isdisjoint(hyp_ids)

    def test_source_hash_computed(self):
        nodes = parse_document("# Título\n\nTexto.")
        for n in nodes:
            assert n.source_hash
            assert len(n.source_hash) == 16  # 16 hex chars

    def test_order_index_monotonic(self):
        doc = "# A\n\nB.\n\n# C\n\nD."
        nodes = parse_document(doc)
        indices = [n.order_index for n in nodes]
        assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# Normalisation profiles
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_raw_preserves_markdown(self):
        text = "# Heading\n\n**bold**"
        result = normalize(text, NormProfile.RAW)
        assert "#" in result
        assert "**" in result

    def test_unicode_layout_collapses_spaces(self):
        text = "texto  com    espaços"
        result = normalize(text, NormProfile.UNICODE_LAYOUT)
        assert "  " not in result

    def test_visible_text_strips_markdown(self):
        text = "# **Título** em negrito"
        result = normalize(text, NormProfile.VISIBLE_TEXT)
        assert "#" not in result
        assert "**" not in result
        assert "Título" in result

    def test_nfc_normalisation(self):
        import unicodedata
        # 'é' as NFD (e + combining accent)
        nfd = unicodedata.normalize("NFD", "café")
        nfc = unicodedata.normalize("NFC", "café")
        result = normalize(nfd, NormProfile.UNICODE_LAYOUT)
        assert result == nfc or result == normalize(nfc, NormProfile.UNICODE_LAYOUT)

    def test_visible_text_table_cells_extracted(self):
        text = "| Nome | Valor |\n|---|---|\n| João | R$ 10 |"
        result = normalize(text, NormProfile.VISIBLE_TEXT)
        assert "João" in result
        assert "R$ 10" in result


# ---------------------------------------------------------------------------
# Page scope
# ---------------------------------------------------------------------------

class TestPageScope:
    def test_P01_selected_restricts_evaluation(self):
        """P01: ref pages 1-5, selected 2 and 4, hyp has 1,2,4 → only 2,4 scored."""
        scope = build_page_scope(
            ref_page_nums={1, 2, 3, 4, 5},
            hyp_page_nums={1, 2, 4},
            run_manifest_pages={2, 4},
        )
        assert scope.selected_present == {2, 4}
        assert scope.selected_extra == {1}      # hyp has 1 but it's not selected
        assert scope.selected_missing == set()

    def test_P02_missing_page_penalised(self):
        """P02: selected page 4 absent from hyp → in selected_missing."""
        scope = build_page_scope(
            ref_page_nums={1, 2, 4},
            hyp_page_nums={1, 2},
            run_manifest_pages={1, 2, 4},
        )
        assert 4 in scope.selected_missing

    def test_P04_selected_without_ref_is_config_error(self):
        """P04: selected page 6 not in reference → selected_without_ref."""
        scope = build_page_scope(
            ref_page_nums={1, 2},
            hyp_page_nums={1, 2},
            run_manifest_pages={1, 2, 6},
        )
        assert 6 in scope.selected_without_ref

    def test_no_run_manifest_uses_ref_pages(self):
        """Without a run manifest, all ref pages are selected."""
        scope = build_page_scope(
            ref_page_nums={1, 2, 3},
            hyp_page_nums={1, 2},
            run_manifest_pages=None,
        )
        assert scope.selected == {1, 2, 3}
        assert scope.selected_missing == {3}

    def test_scope_to_dict(self):
        scope = build_page_scope({1, 2}, {1}, {1, 2})
        d = scope.to_dict()
        assert "selected" in d
        assert "selected_missing" in d
        assert d["selected_missing"] == [2]


# ---------------------------------------------------------------------------
# parse_page helper
# ---------------------------------------------------------------------------

class TestParsePage:
    def test_parse_page_assigns_page_number(self):
        nodes = parse_page("# Título\n\nTexto.", page=5, origin="reference")
        assert all(n.page == 5 for n in nodes)

    def test_parse_page_empty_returns_empty(self):
        assert parse_page("", page=1) == []
        assert parse_page("   \n  ", page=1) == []

    def test_start_seq_offsets_order_index(self):
        nodes = parse_page("# A\n\nB.", page=1, start_seq=10)
        assert nodes[0].order_index == 10
        assert nodes[1].order_index == 11
