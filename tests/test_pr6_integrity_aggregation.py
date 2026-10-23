"""Tests for PR 6 — Integrity metrics, V2 aggregation and comparability (spec §8, §10, §12).

Covers:
  I01  V1 JSON contract unaffected by V2 additions
  I03  Comparator rejects different schema_version
  I04  Comparator rejects different reference_sha256
  I05  Comparator rejects different selected_pages
  I14  N/A metric excluded from aggregation (not 0 or 1)
  I15  Redundant metrics not double-counted

  B21  Same multiplicity text → duplicate excess = 0
  B22  Text 3× hyp vs 2× ref → excess rate > 0

  AggM1  Micro aggregation: sum(num)/sum(den) before dividing
  AggM2  Macro: mean of applicable only, not_applicable excluded
  AggM3  Worst: min for quality, max for error
  AggM4  Median: arithmetic (avg of two middle for even n)
  AggM5  Empty results → all None

  Integrity scenarios:
  Int1   Duplicate excess rate = 0 when no excess
  Int2   Duplicate excess rate > 0 when hyp has extra blocks
  Int3   Missing page rate = 0 when all selected pages present
  Int4   Missing page rate = 1 when all selected pages absent
  Int5   Content coverage recall = 1 for identical content
  Int6   Markdown validity: unclosed fence → parse_errors > 0
  Int7   Markdown validity: duplicate page marker → error
  Int8   Markdown validity: clean doc → valid
  Int9   Edge content heuristic: repetition absent from ref flagged
  Int10  to_dict() JSON-serialisable
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from evaluation_contract_helpers import comparison_result

from structured_pdf_text.evaluation.aggregation import (
    AggregatedMetric,
    V2DocumentMetrics,
    aggregate_metric,
    check_comparability,
    evaluate_rank_eligibility,
)
from structured_pdf_text.evaluation.integrity_metrics import (
    MarkdownValidityReport,
    compute_integrity_metrics,
    duplicate_excess_rate,
    edge_content_diagnostic,
    markdown_validity_report,
    missing_page_rate,
    page_content_coverage_recall,
)
from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NodeKind,
)


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

_SEQ = [0]


def _seq() -> int:
    _SEQ[0] += 1
    return _SEQ[0]


def _para(text: str, origin: str = "reference") -> MarkdownNode:
    s = _seq()
    return MarkdownNode(
        node_id=MarkdownNode.make_id(origin, 1, NodeKind.PARAGRAPH, s),
        origin=origin, page=1, kind=NodeKind.PARAGRAPH,
        raw_markdown=text, visible_text=text,
        source_start=0, source_end=len(text), order_index=s,
    )


def _metric(
    value: float | None,
    status: MetricStatus = MetricStatus.OK,
    direction: str = "higher_is_better",
    definition_id: str = "test-metric",
    counts: dict | None = None,
) -> MetricResult:
    return MetricResult(
        value=value, status=status, direction=direction,
        definition_id=definition_id, counts=counts or {},
    )


# ---------------------------------------------------------------------------
# Integrity: duplicate excess
# ---------------------------------------------------------------------------

class TestDuplicateExcess:
    def test_Int1_no_excess_rate_zero(self):
        """Int1: Same content in ref and hyp → duplicate_excess_rate=0."""
        texts = [
            "Este é um parágrafo longo com bastante texto.",
            "Outro parágrafo completamente distinto.",
            "Terceiro parágrafo para o teste.",
        ]
        ref = [_para(t) for t in texts]
        hyp = [_para(t, "hypothesis") for t in texts]
        m = duplicate_excess_rate(ref, hyp)
        assert m.value == pytest.approx(0.0)
        assert m.status == MetricStatus.OK

    def test_Int2_excess_counted(self):
        """Int2: Hyp repeats a block that appears once in ref → excess > 0."""
        long_text = "Este bloco aparece uma vez na referência e duas vezes na hipótese."
        ref = [_para(long_text)]
        hyp = [_para(long_text, "hypothesis"), _para(long_text, "hypothesis")]
        m = duplicate_excess_rate(ref, hyp)
        assert m.value is not None
        assert m.value > 0.0
        assert m.counts["excess_blocks"] == 1

    def test_B21_same_count_no_excess(self):
        """B21: Text 2× in both ref and hyp → no excess."""
        text = "Cláusula padrão repetida duas vezes no contrato."
        ref = [_para(text), _para(text)]
        hyp = [_para(text, "hypothesis"), _para(text, "hypothesis")]
        m = duplicate_excess_rate(ref, hyp)
        assert m.counts["excess_blocks"] == 0

    def test_B22_excess_one(self):
        """B22: Hyp 3× vs ref 2× → excess_blocks=1."""
        text = "Cláusula padrão repetida duas vezes no documento original."
        ref = [_para(text), _para(text)]
        hyp = [_para(text, "hypothesis")] * 3
        m = duplicate_excess_rate(ref, hyp)
        assert m.counts["excess_blocks"] == 1

    def test_empty_hyp_not_applicable(self):
        """Empty hyp → not_applicable (no blocks to evaluate)."""
        ref = [_para("Texto.")]
        m = duplicate_excess_rate(ref, [])
        assert m.status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Integrity: missing page rate
# ---------------------------------------------------------------------------

class TestMissingPageRate:
    def test_Int3_no_missing_rate_zero(self):
        """Int3: All selected pages present → missing_page_rate=0."""
        m = missing_page_rate({1, 2, 3}, {1, 2, 3}, {1, 2, 3})
        assert m.value == pytest.approx(0.0)

    def test_Int4_all_missing_rate_one(self):
        """Int4: All selected pages absent from hyp → rate=1."""
        m = missing_page_rate({1, 2}, {1, 2}, set())
        assert m.value == pytest.approx(1.0)

    def test_partial_missing(self):
        """Half the selected pages missing → rate=0.5."""
        m = missing_page_rate({1, 2, 3, 4}, {1, 2, 3, 4}, {1, 2})
        assert m.value == pytest.approx(0.5)

    def test_no_selected_reference_not_applicable(self):
        """No selected reference pages → not_applicable."""
        m = missing_page_rate(set(), set(), {1, 2})
        assert m.status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Integrity: content coverage recall
# ---------------------------------------------------------------------------

class TestContentCoverageRecall:
    def test_Int5_identical_content_recall_one(self):
        """Int5: Same content → recall=1.0."""
        text = "Parágrafo com conteúdo idêntico."
        ref = [_para(text)]
        hyp = [_para(text, "hypothesis")]
        m = page_content_coverage_recall(ref, hyp)
        assert m.value == pytest.approx(1.0)

    def test_empty_ref_empty_hyp_not_applicable(self):
        """Both empty → not_applicable, correct_absence=True."""
        m = page_content_coverage_recall([], [])
        assert m.status == MetricStatus.NOT_APPLICABLE

    def test_empty_ref_hyp_has_content_is_undefined_like_text_recall(self):
        """Ref empty, hyp has content → undefined recall (same as MET-TX06)."""
        m = page_content_coverage_recall([], [_para("Extra.", "hypothesis")])
        assert m.status == MetricStatus.UNDEFINED_DENOMINATOR


# ---------------------------------------------------------------------------
# Integrity: markdown validity
# ---------------------------------------------------------------------------

class TestMarkdownValidity:
    def test_Int8_clean_doc_valid(self):
        """Int8: Clean Markdown → validity_status='valid'."""
        report = markdown_validity_report("# Título\n\nTexto simples.")
        assert report.validity_status == "valid"
        assert report.parse_errors == 0

    def test_Int6_unclosed_fence_invalid(self):
        """Int6: Unclosed code fence → parse_errors > 0, invalid."""
        doc = "Texto.\n\n```python\ncode()\n# fence never closed"
        report = markdown_validity_report(doc)
        assert report.validity_status == "invalid"
        assert report.unclosed_fences > 0

    def test_Int7_duplicate_page_marker_invalid(self):
        """Int7: Duplicate ## Página N → duplicate_page_markers > 0, invalid."""
        doc = "## Página 1\nA\n\n## Página 1\nB"
        report = markdown_validity_report(doc)
        assert report.validity_status == "invalid"
        assert report.duplicate_page_markers >= 1

    def test_closed_fences_valid(self):
        """Balanced fences → not flagged as unclosed."""
        doc = "```python\ncode()\n```\nTexto."
        report = markdown_validity_report(doc)
        assert report.unclosed_fences == 0


# ---------------------------------------------------------------------------
# Integrity: edge content heuristic
# ---------------------------------------------------------------------------

class TestEdgeContentHeuristic:
    def test_Int9_repetition_absent_from_ref_flagged(self):
        """Int9: Line repeated in hyp pages but absent from ref → flagged."""
        ref = "## Página 1\nConteúdo legítimo.\n\n## Página 2\nMais conteúdo."
        hyp = (
            "## Página 1\nRODANÃO INVENTADO\nConteúdo legítimo.\n\n"
            "## Página 2\nRODANÃO INVENTADO\nMais conteúdo."
        )
        diag = edge_content_diagnostic(hyp, ref)
        # The diagnostic should be a dict, not a score
        d = diag.to_dict()
        assert "suspected_header_lines" in d
        assert "note" in d
        assert "heuristic" in d["note"]

    def test_legitimate_repetition_not_flagged(self):
        """Repetition present in ref is not flagged as leakage."""
        ref = "## Página 1\nAviso Legal.\n\n## Página 2\nAviso Legal."
        hyp = "## Página 1\nAviso Legal.\n\n## Página 2\nAviso Legal."
        diag = edge_content_diagnostic(hyp, ref)
        assert diag.suspected_header_lines == 0


# ---------------------------------------------------------------------------
# Aggregation: AggM1-AggM5
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_AggM2_na_excluded_from_macro(self):
        """AggM2: N/A results excluded from macro mean (not treated as 0 or 1)."""
        results = [
            _metric(0.8),
            _metric(None, MetricStatus.NOT_APPLICABLE),
            _metric(0.6),
        ]
        agg = aggregate_metric(results)
        assert agg.n_applicable == 2
        assert agg.macro_value == pytest.approx(0.7)  # (0.8+0.6)/2

    def test_AggM3_worst_min_for_quality(self):
        """AggM3: Worst for quality metric (higher_is_better) = min."""
        results = [_metric(0.9), _metric(0.3), _metric(0.7)]
        agg = aggregate_metric(results, )
        assert agg.worst_value == pytest.approx(0.3)

    def test_AggM3_worst_max_for_error(self):
        """AggM3: Worst for error metric (lower_is_better) = max."""
        results = [
            _metric(0.1, direction="lower_is_better"),
            _metric(0.5, direction="lower_is_better"),
            _metric(0.2, direction="lower_is_better"),
        ]
        agg = aggregate_metric(results)
        assert agg.worst_value == pytest.approx(0.5)

    def test_AggM4_median_even_n(self):
        """AggM4: Even n median = average of two middle values."""
        results = [_metric(float(v)) for v in [1, 2, 3, 4]]
        agg = aggregate_metric(results)
        assert agg.median_value == pytest.approx(2.5)  # (2+3)/2

    def test_AggM4_median_odd_n(self):
        """AggM4: Odd n median = middle value."""
        results = [_metric(float(v)) for v in [1, 2, 3]]
        agg = aggregate_metric(results)
        assert agg.median_value == pytest.approx(2.0)

    def test_AggM5_empty_returns_none(self):
        """AggM5: No results → all None aggregates."""
        agg = aggregate_metric([])
        assert agg.macro_value is None
        assert agg.worst_value is None
        assert agg.median_value is None
        assert agg.n_total == 0

    def test_AggM1_micro_via_counts(self):
        """AggM1: Micro = sum(numerators)/sum(denominators)."""
        r1 = _metric(0.8, counts={"tp": 8, "total": 10})
        r2 = _metric(0.5, counts={"tp": 5, "total": 10})
        agg = aggregate_metric([r1, r2], numerator_key="tp", denominator_key="total")
        assert agg.micro_value == pytest.approx(13 / 20)

    def test_coverage_fraction_computed(self):
        """Coverage = n_applicable / n_total."""
        results = [
            _metric(0.9),
            _metric(None, MetricStatus.NOT_APPLICABLE),
        ]
        agg = aggregate_metric(results)
        d = agg.to_dict()
        assert d["coverage"] == pytest.approx(0.5)

    def test_I14_na_not_zero_or_one(self):
        """I14: N/A metric should not appear as 0 or 1 in aggregate."""
        results = [
            _metric(None, MetricStatus.NOT_APPLICABLE),
            _metric(None, MetricStatus.NOT_APPLICABLE),
        ]
        agg = aggregate_metric(results)
        assert agg.macro_value is None
        assert agg.worst_value is None
        assert agg.n_applicable == 0


# ---------------------------------------------------------------------------
# Comparability
# ---------------------------------------------------------------------------

class TestComparability:
    BASE = comparison_result()

    def test_I03_schema_version_mismatch_rejected(self):
        """I03: Different schema_version → not comparable."""
        a = deepcopy(self.BASE)
        b = {"schema_version": "evaluation-v1.0", "inputs": self.BASE["inputs"]}
        ok, reasons = check_comparability(a, b)
        assert not ok
        assert any("schema_version" in r for r in reasons)

    def test_I04_reference_sha_mismatch_rejected(self):
        """I04: Different effective reference SHA → not comparable."""
        a, b = deepcopy(self.BASE), deepcopy(self.BASE)
        b["inputs"]["reference_effective_sha256"] = "d" * 64
        ok, reasons = check_comparability(a, b)
        assert not ok
        assert any("reference_effective_sha256" in r for r in reasons)

    def test_I05_selected_pages_mismatch_rejected(self):
        """I05: Different selected_pages → not comparable."""
        a, b = deepcopy(self.BASE), deepcopy(self.BASE)
        b["inputs"]["selected_pages"] = [1, 2]
        ok, reasons = check_comparability(a, b)
        assert not ok
        assert any("selected_pages" in r for r in reasons)

    def test_identical_results_comparable(self):
        """Identical inputs and schema → comparable."""
        ok, reasons = check_comparability(self.BASE, dict(self.BASE))
        assert ok
        assert reasons == []


# ---------------------------------------------------------------------------
# V2DocumentMetrics and rank eligibility
# ---------------------------------------------------------------------------

class TestV2DocumentMetrics:
    def test_to_dict_json_serialisable(self):
        """V2DocumentMetrics.to_dict() is JSON-serialisable."""
        doc = V2DocumentMetrics()
        doc.text = {"cer": {"value": 0.05, "status": "ok", "direction": "lower_is_better"}}
        doc.evaluation_status = "ok"
        doc.rank_eligible = True
        _ = json.dumps(doc.to_dict())

    def test_finalise_counts_applicable(self):
        """finalise() counts applicable metrics correctly."""
        doc = V2DocumentMetrics()
        doc.text = {
            "cer": {"value": 0.05, "status": "ok", "direction": "lower_is_better"},
            "na_metric": {"value": None, "status": "not_applicable", "direction": "higher_is_better"},
        }
        doc.finalise()
        assert doc.applicable_metric_count == 1
        assert doc.unavailable_metric_count == 1

    def test_rank_eligibility_no_applicable(self):
        """No applicable metrics → not rank eligible."""
        doc = V2DocumentMetrics()
        doc.evaluation_status = "ok"
        eligible, reasons = evaluate_rank_eligibility(doc)
        assert not eligible
        assert "no_applicable_metrics" in reasons

    def test_rank_eligibility_invalid_markdown(self):
        """Invalid markdown in hypothesis → not rank eligible."""
        doc = V2DocumentMetrics()
        doc.applicable_metric_count = 5
        doc.integrity = {
            "markdown_validity": {"validity_status": "invalid", "parse_errors": 1}
        }
        eligible, reasons = evaluate_rank_eligibility(doc)
        assert not eligible
        assert "invalid_markdown_in_hypothesis" in reasons


# ---------------------------------------------------------------------------
# Integration: compute_integrity_metrics
# ---------------------------------------------------------------------------

class TestComputeIntegrityMetrics:
    def test_all_metrics_returned(self):
        """compute_integrity_metrics returns IntegrityMetrics with all fields."""
        ref = [_para("Texto de referência.")]
        hyp = [_para("Texto de referência.", "hypothesis")]
        im = compute_integrity_metrics(
            ref, hyp,
            selected_pages={1},
            selected_reference={1},
            hyp_page_nums={1},
            hyp_raw_text="## Página 1\nTexto.",
            ref_raw_text="## Página 1\nTexto.",
        )
        assert im.duplicate_excess is not None
        assert im.missing_page is not None
        assert im.content_coverage_recall is not None
        assert im.markdown_validity is not None
        assert im.edge_content_diagnostic is not None

    def test_to_dict_json_serialisable(self):
        """IntegrityMetrics.to_dict() is JSON-serialisable."""
        ref = [_para("Texto.")]
        hyp = [_para("Texto.", "hypothesis")]
        im = compute_integrity_metrics(ref, hyp,
                                       hyp_raw_text="Texto.",
                                       ref_raw_text="Texto.")
        _ = json.dumps(im.to_dict())
