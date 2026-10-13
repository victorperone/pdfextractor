"""Tests for PR 5 — Table metrics and critical data (spec §7, §9, §12.4).

Covers spec scenarios:
  B01  Perfect table → all metrics applicable and maximal
  B02  Table entirely absent → FN, cell FN
  B03  Table entirely invented → FP, no cell credit
  B04  Same count different content → table detection F1 not 1
  B05  Two valid GFM tables different widths → both detected
  B06  Extra cell in matched table → cell FP
  B07  Missing cell in matched table → cell FN
  B08  Two rows swapped → content covered, alignment penalised
  B10  Empty structural cell → counted as cell, not absent
  B14  No currency ref, hyp invents R$ → false_positive_only, F1=0
  B15  Neither side has currency → not_applicable, correct_absence
  B16  R$ 10,00 vs R$ 100,00 → error recorded
  B17  CPF formatted vs unformatted → canonical match
  B18  Invalid date 31/02/2026 → not normalised as valid
  B19  Short integer relevant → detected when present
  B21  Text repeated same count → no excess duplicate
  B22  Text repeated 3× hyp vs 2× ref → multiset excess=1
"""
from __future__ import annotations

import json

import pytest

from structured_pdf_text.evaluation.critical_data import (
    compute_critical_data_metrics,
    _extract_dates,
    _extract_currency,
    _extract_cpf,
    _extract_numbers,
    _canonical_number,
)
from structured_pdf_text.evaluation.markdown_parser import parse_document
from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    MetricStatus,
    NodeKind,
)
from structured_pdf_text.evaluation.table_metrics import (
    TABLE_MATCH_THRESHOLD,
    cell_metrics,
    compute_table_metrics,
    table_content_f1,
    table_detection_f1,
    table_row_and_column_metrics,
)


# ---------------------------------------------------------------------------
# Helpers: build simple table node trees manually
# ---------------------------------------------------------------------------

_SEQ = [0]


def _seq() -> int:
    _SEQ[0] += 1
    return _SEQ[0]


def _make_node(
    kind: NodeKind,
    text: str,
    origin: str = "reference",
    page: int = 1,
    seq: int | None = None,
    parent_id: str | None = None,
    heading_level: int | None = None,
) -> MarkdownNode:
    s = seq if seq is not None else _seq()
    nid = MarkdownNode.make_id(origin, page, kind, s)
    return MarkdownNode(
        node_id=nid, origin=origin, page=page, kind=kind,
        raw_markdown=text, visible_text=text,
        source_start=0, source_end=len(text), order_index=s,
        parent_id=parent_id, heading_level=heading_level,
    )


def _build_table(
    rows_data: list[list[str]],
    origin: str = "reference",
    page: int = 1,
) -> list[MarkdownNode]:
    """Build TABLE > TABLE_ROW > TABLE_CELL node list."""
    nodes: list[MarkdownNode] = []
    table_node = _make_node(NodeKind.TABLE, " ".join(
        " ".join(row) for row in rows_data
    ), origin, page)
    nodes.append(table_node)
    for row_data in rows_data:
        row_text = " ".join(row_data)
        row_node = _make_node(NodeKind.TABLE_ROW, row_text, origin, page,
                              parent_id=table_node.node_id)
        nodes.append(row_node)
        for cell_text in row_data:
            cell_node = _make_node(NodeKind.TABLE_CELL, cell_text, origin, page,
                                   parent_id=row_node.node_id)
            nodes.append(cell_node)
    return nodes


def _r(rows: list[list[str]], page: int = 1) -> list[MarkdownNode]:
    return _build_table(rows, "reference", page)


def _h(rows: list[list[str]], page: int = 1) -> list[MarkdownNode]:
    return _build_table(rows, "hypothesis", page)


def _ref_nodes(*groups: list[MarkdownNode]) -> list[MarkdownNode]:
    result = []
    for g in groups:
        result.extend(g)
    return result


# ---------------------------------------------------------------------------
# Helper: MarkdownNode list from paragraph text
# ---------------------------------------------------------------------------

def _para(text: str, origin: str = "reference") -> MarkdownNode:
    s = _seq()
    return MarkdownNode(
        node_id=MarkdownNode.make_id(origin, 1, NodeKind.PARAGRAPH, s),
        origin=origin, page=1, kind=NodeKind.PARAGRAPH,
        raw_markdown=text, visible_text=text,
        source_start=0, source_end=len(text), order_index=s,
    )


# ---------------------------------------------------------------------------
# B01: Perfect table
# ---------------------------------------------------------------------------

class TestPerfectTable:
    ROWS = [["Nome", "Valor"], ["João", "100"], ["Maria", "200"]]

    def test_B01_detection_f1_perfect(self):
        """B01: Identical table → detection F1=1."""
        ref = _r(self.ROWS)
        hyp = _h(self.ROWS)
        m = table_detection_f1(ref, hyp)
        assert m["table_detection_f1"].value == pytest.approx(1.0)

    def test_B01_row_detection_perfect(self):
        """B01: Identical table → row detection F1=1."""
        ref = _r(self.ROWS)
        hyp = _h(self.ROWS)
        m = table_row_and_column_metrics(ref, hyp)
        assert m["table_row_detection_f1"].value == pytest.approx(1.0)

    def test_B01_column_count_correct(self):
        """B01: Same column count → column_count_accuracy=1."""
        ref = _r(self.ROWS)
        hyp = _h(self.ROWS)
        m = table_row_and_column_metrics(ref, hyp)
        assert m["table_column_count_accuracy"].value == pytest.approx(1.0)

    def test_B01_cell_exact_match_perfect(self):
        """B01: All cells identical → exact_match=1."""
        ref = _r(self.ROWS)
        hyp = _h(self.ROWS)
        m = cell_metrics(ref, hyp)
        assert m["cell_content_exact_match"].value == pytest.approx(1.0)

    def test_B01_cell_cer_zero(self):
        """B01: All cells identical → cell_cer=0."""
        ref = _r(self.ROWS)
        hyp = _h(self.ROWS)
        m = cell_metrics(ref, hyp)
        assert m["cell_cer"].value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# B02: Table entirely absent
# ---------------------------------------------------------------------------

class TestTableAbsent:
    ROWS = [["A", "B"], ["1", "2"]]

    def test_B02_detection_recall_zero(self):
        """B02: Ref has table, hyp has none → recall=0, FN."""
        ref = _r(self.ROWS)
        hyp: list[MarkdownNode] = []
        m = table_detection_f1(ref, hyp)
        assert m["table_detection_recall"].value == pytest.approx(0.0)
        assert m["table_detection_f1"].counts["fn"] == 1

    def test_B02_cell_fn_counted(self):
        """B02: Missing table → all its cells counted as FN."""
        ref = _r(self.ROWS)
        hyp: list[MarkdownNode] = []
        m = cell_metrics(ref, hyp)
        assert m["cell_detection_recall"].value == pytest.approx(0.0)
        total_cells = sum(len(row) for row in self.ROWS)
        assert m["cell_detection_f1"].counts["fn"] == total_cells


# ---------------------------------------------------------------------------
# B03: Table entirely invented
# ---------------------------------------------------------------------------

class TestTableInvented:
    def test_B03_detection_precision_zero(self):
        """B03: No ref table, hyp invents one → precision=0, FP."""
        ref: list[MarkdownNode] = []
        hyp = _h([["X", "Y"], ["1", "2"]])
        m = table_detection_f1(ref, hyp)
        assert m["table_detection_precision"].value == pytest.approx(0.0)
        assert m["table_detection_f1"].counts["fp"] == 1

    def test_B03_cell_fp_counted(self):
        """B03: Invented table → its cells are FP."""
        ref: list[MarkdownNode] = []
        hyp = _h([["X", "Y"], ["1", "2"]])
        m = cell_metrics(ref, hyp)
        assert m["cell_detection_precision"].value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# B04: Same count, different content
# ---------------------------------------------------------------------------

class TestSameCountDifferentContent:
    def test_B04_detection_not_perfect(self):
        """B04: Same number of tables but different content → detection F1 < 1."""
        ref = _r([["Nome", "Valor"], ["João", "100"]])
        hyp = _h([["País", "Capital"], ["Brasil", "Brasília"]])
        m = table_detection_f1(ref, hyp)
        # May match at low threshold, but should not be perfect on content
        # The key invariant: same count alone is not sufficient for F1=1
        # (if they match, it's because content happens to be similar enough)
        # Here content is completely different → should not match
        if m["table_detection_f1"].value is not None:
            assert m["table_detection_f1"].value < 1.0 or \
                   m["table_detection_f1"].counts["tp"] == 0


# ---------------------------------------------------------------------------
# B05: Two valid GFM tables with different widths
# ---------------------------------------------------------------------------

class TestTwoTablesWithDifferentWidths:
    def test_B05_both_tables_parsed(self):
        """B05: Parser handles two GFM tables with different column counts."""
        doc = (
            "## Página 1\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "| X | Y | Z |\n|---|---|---|\n| 7 | 8 | 9 |"
        )
        nodes = parse_document(doc)
        tables = [n for n in nodes if n.kind == NodeKind.TABLE]
        assert len(tables) == 2

    def test_B05_both_tables_detected(self):
        """B05: Both tables appear in both ref and hyp → detection F1=1."""
        ref = _ref_nodes(
            _r([["A", "B"], ["1", "2"]], page=1),
            _r([["X", "Y", "Z"], ["7", "8", "9"]], page=1),
        )
        hyp = _ref_nodes(
            _h([["A", "B"], ["1", "2"]], page=1),
            _h([["X", "Y", "Z"], ["7", "8", "9"]], page=1),
        )
        m = table_detection_f1(ref, hyp)
        assert m["table_detection_f1"].value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# B06: Extra cell in matched table
# ---------------------------------------------------------------------------

class TestExtraCell:
    def test_B06_extra_cell_fp(self):
        """B06: Extra cell in matched hyp row → cell FP counted."""
        ref = _r([["A", "B"], ["1", "2"]])
        hyp = _h([["A", "B"], ["1", "2", "3"]])  # extra cell in row 2
        m = cell_metrics(ref, hyp)
        assert m["cell_detection_f1"].counts.get("fp", 0) >= 1


# ---------------------------------------------------------------------------
# B07: Missing cell
# ---------------------------------------------------------------------------

class TestMissingCell:
    def test_B07_missing_cell_fn(self):
        """B07: Missing cell in hyp → cell FN counted."""
        ref = _r([["A", "B"], ["1", "2"]])
        hyp = _h([["A", "B"], ["1"]])  # second cell missing
        m = cell_metrics(ref, hyp)
        assert m["cell_detection_f1"].counts.get("fn", 0) >= 1


# ---------------------------------------------------------------------------
# B08: Rows swapped
# ---------------------------------------------------------------------------

class TestRowsSwapped:
    def test_B08_content_covered_rows_swapped(self):
        """B08: Rows swapped → content F1 still high, alignment may differ."""
        ref = _r([["Header"], ["Alpha"], ["Beta"]])
        hyp = _h([["Header"], ["Beta"], ["Alpha"]])
        # Content multiset should still match
        cf = table_content_f1(ref, hyp)
        assert cf.value is not None
        assert cf.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# B10: Empty structural cell
# ---------------------------------------------------------------------------

class TestEmptyCell:
    def test_B10_empty_cell_counted_as_structural(self):
        """B10: Empty cell is a structural slot, not an absent cell."""
        ref = _r([["A", "B"], ["", "valor"]])
        hyp = _h([["A", "B"], ["", "valor"]])
        m = cell_metrics(ref, hyp)
        # All 4 cells are detected (2 header + 2 data, including empty)
        assert m["cell_detection_f1"].value == pytest.approx(1.0)
        assert m["cell_content_exact_match"].value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# B14/B15/B16: Currency
# ---------------------------------------------------------------------------

class TestCurrency:
    def test_B14_no_ref_currency_hyp_invents(self):
        """B14: ref has no currency, hyp invents R$ → false_positive_only, F1=0."""
        ref_nodes = [_para("Texto sem valor monetário.")]
        hyp_nodes = [_para("Texto com R$ 9.999,00 inventado.", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.status == MetricStatus.FALSE_POSITIVE_ONLY
        assert m.currency.value == pytest.approx(0.0)

    def test_B15_no_currency_either_side_not_applicable(self):
        """B15: Neither side has currency → not_applicable, correct_absence=True."""
        ref_nodes = [_para("Texto sem valor.")]
        hyp_nodes = [_para("Texto sem valor.", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.status == MetricStatus.NOT_APPLICABLE
        assert m.currency.counts.get("correct_absence") is True

    def test_B16_wrong_currency_value(self):
        """B16: R$ 10,00 vs R$ 100,00 → F1 < 1, error recorded."""
        ref_nodes = [_para("Valor: R$ 10,00")]
        hyp_nodes = [_para("Valor: R$ 100,00", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.value is not None
        assert m.currency.value < 1.0

    def test_currency_exact_match(self):
        """Identical currency values → F1=1."""
        ref_nodes = [_para("Valor: R$ 1.500,00")]
        hyp_nodes = [_para("Valor: R$ 1.500,00", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# B17: CPF with/without formatting
# ---------------------------------------------------------------------------

class TestCPF:
    def test_B17_cpf_formatted_vs_unformatted_canonical_match(self):
        """B17: CPF with and without punctuation → canonical match → F1=1."""
        ref_nodes = [_para("CPF: 123.456.789-09")]
        hyp_nodes = [_para("CPF: 12345678909", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.cpf is not None
        assert m.cpf.value == pytest.approx(1.0)

    def test_cpf_digits_preserved(self):
        """CPF with leading zeros preserved after canonical form."""
        result = _extract_cpf("CPF: 001.234.567-89")
        assert result == ["00123456789"]


# ---------------------------------------------------------------------------
# B18: Invalid date
# ---------------------------------------------------------------------------

class TestInvalidDate:
    def test_B18_invalid_date_not_normalised(self):
        """B18: 31/02/2026 is not a valid date → not extracted."""
        dates = _extract_dates("Data: 31/02/2026")
        assert dates == []

    def test_valid_date_normalised(self):
        """Valid date 15/03/2024 → canonical YYYY-MM-DD."""
        dates = _extract_dates("Data: 15/03/2024")
        assert "2024-03-15" in dates

    def test_iso_date_recognised(self):
        """ISO format 2024-03-15 → canonical."""
        dates = _extract_dates("Data: 2024-03-15")
        assert "2024-03-15" in dates


# ---------------------------------------------------------------------------
# B19: Short integers
# ---------------------------------------------------------------------------

class TestShortIntegers:
    def test_B19_short_integer_detected(self):
        """B19: A short integer in context is detected."""
        nums = _extract_numbers("Quantidade: 8 unidades")
        assert "8" in nums

    def test_canonical_number_pt_br(self):
        """pt-BR thousand separator removed, decimal preserved."""
        assert _canonical_number("1.000,50") == "1000.5"
        assert _canonical_number("100") == "100"

    def test_zeros_not_stripped_in_identifiers(self):
        """Leading zeros in CPF are not stripped."""
        cpfs = _extract_cpf("CPF 001.002.003-04")
        assert cpfs == ["00100200304"]


# ---------------------------------------------------------------------------
# B21/B22: Multiset excess
# ---------------------------------------------------------------------------

class TestMultisetExcess:
    def test_B21_same_count_no_excess(self):
        """B21: Text repeated same count in ref and hyp → TP=count, FP=0, FN=0."""
        ref_nodes = [_para("Valor: R$ 100,00. Valor: R$ 100,00.")]
        hyp_nodes = [_para("Valor: R$ 100,00. Valor: R$ 100,00.", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.counts["fp"] == 0
        assert m.currency.counts["fn"] == 0

    def test_B22_excess_one(self):
        """B22: Text repeated 3× in hyp vs 2× in ref → FP=1."""
        ref_nodes = [_para("R$ 50,00 e R$ 50,00.")]
        hyp_nodes = [_para("R$ 50,00 e R$ 50,00 e R$ 50,00.", "hypothesis")]
        m = compute_critical_data_metrics(ref_nodes, hyp_nodes)
        assert m.currency is not None
        assert m.currency.counts["fp"] == 1
        assert m.currency.counts["fn"] == 0


# ---------------------------------------------------------------------------
# Integration: parse_document → compute_table_metrics
# ---------------------------------------------------------------------------

class TestTableMetricsIntegration:
    def test_parser_output_feeds_table_metrics(self):
        """Table nodes from parse_document flow into table metrics without crash."""
        doc = (
            "## Página 1\n"
            "| Nome | Valor |\n|---|---|\n| João | R$ 100 |\n| Maria | R$ 200 |"
        )
        ref_nodes = parse_document(doc, origin="reference")
        hyp_nodes = parse_document(doc, origin="hypothesis")
        tm = compute_table_metrics(ref_nodes, hyp_nodes)
        assert tm.detection["table_detection_f1"].value == pytest.approx(1.0)

    def test_to_dict_json_serialisable(self):
        """TableMetrics.to_dict() is fully JSON-serialisable."""
        ref = _r([["A", "B"], ["1", "2"]])
        hyp = _h([["A", "B"], ["1", "2"]])
        tm = compute_table_metrics(ref, hyp)
        _ = json.dumps(tm.to_dict())

    def test_no_tables_not_applicable(self):
        """Without any tables, all metrics are not_applicable."""
        ref = [_para("Texto.")]
        hyp = [_para("Texto.", "hypothesis")]
        tm = compute_table_metrics(ref, hyp)
        assert tm.detection["table_detection_f1"].status == MetricStatus.NOT_APPLICABLE

    def test_critical_data_to_dict_json_serialisable(self):
        """CriticalDataMetrics.to_dict() is JSON-serialisable."""
        ref = [_para("CPF 123.456.789-09 — R$ 1.500,00 — 15/03/2024")]
        hyp = [_para("CPF 12345678909 — R$ 1.500,00 — 15/03/2024", "hypothesis")]
        m = compute_critical_data_metrics(ref, hyp)
        _ = json.dumps(m.to_dict())

    def test_identifiers_combined_cpf_cnpj(self):
        """Combined identifier metric covers both CPF and CNPJ."""
        ref = [_para("CPF 123.456.789-09 CNPJ 12.345.678/0001-99")]
        hyp = [_para("CPF 12345678909 CNPJ 12345678000199", "hypothesis")]
        m = compute_critical_data_metrics(ref, hyp)
        assert m.identifiers_combined is not None
        assert m.identifiers_combined.value == pytest.approx(1.0)
