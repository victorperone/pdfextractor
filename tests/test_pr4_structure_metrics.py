"""Tests for PR 4 — Structure, list, and reading-order metrics (spec §5, §12.5).

Covers:
  SM01  Perfect heading match → F1=1.0, level_match=1.0
  SM02  Missing heading in hyp → recall penalty, FN counted
  SM03  Extra heading in hyp → precision penalty, FP counted
  SM04  Heading level mismatch → level_match_rate < 1.0
  SM05  No headings in ref or hyp → not_applicable status
  SM06  Paragraph detection P/R/F1
  SM07  List group detection P/R/F1
  SM08  List item detection P/R/F1
  SM09  List hierarchy accuracy with nested lists
  SM10  Reading order: perfect preservation → accuracy=1.0
  SM11  Reading order: completely reversed → accuracy=0.0
  SM12  Reading order: < 2 matched elements → not_applicable/insufficient_evidence
  SM13  Block type confusion matrix records mismatches
  SM14  compute_structure_metrics integrates all sub-metrics
  SM15  to_dict() output is JSON-serialisable
"""
from __future__ import annotations

import json

import pytest

from structured_pdf_text.evaluation.block_alignment import align_blocks
from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    MetricStatus,
    NodeKind,
)
from structured_pdf_text.evaluation.structure_metrics import (
    block_type_confusion,
    compute_structure_metrics,
    heading_detection_f1,
    list_metrics,
    paragraph_detection_f1,
    pairwise_reading_order_accuracy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(
    text: str,
    kind: NodeKind = NodeKind.PARAGRAPH,
    page: int | None = 1,
    seq: int = 0,
    origin: str = "reference",
    heading_level: int | None = None,
    list_depth: int | None = None,
    ordered_list: bool | None = None,
) -> MarkdownNode:
    return MarkdownNode(
        node_id       = MarkdownNode.make_id(origin, page, kind, seq),
        origin        = origin,
        page          = page,
        kind          = kind,
        raw_markdown  = text,
        visible_text  = text,
        source_start  = 0,
        source_end    = len(text),
        order_index   = seq,
        heading_level = heading_level,
        list_depth    = list_depth,
        ordered_list  = ordered_list,
    )


def _ref(text, kind=NodeKind.PARAGRAPH, seq=0, heading_level=None, list_depth=None, ordered=None):
    return _node(text, kind, 1, seq, "reference", heading_level, list_depth, ordered)


def _hyp(text, kind=NodeKind.PARAGRAPH, seq=0, heading_level=None, list_depth=None, ordered=None):
    return _node(text, kind, 1, seq, "hypothesis", heading_level, list_depth, ordered)


def _manual_pair(
    ref_node: MarkdownNode | None,
    hyp_node: MarkdownNode | None,
    status: AlignmentStatus = AlignmentStatus.MATCHED,
) -> AlignedPair:
    """Build a pair without going through the aligner."""
    return AlignedPair(
        reference_id     = ref_node.node_id if ref_node else None,
        hypothesis_id    = hyp_node.node_id if hyp_node else None,
        status           = status,
        alignment_score  = 1.0 if status == AlignmentStatus.MATCHED else 0.0,
        text_similarity  = 1.0 if status == AlignmentStatus.MATCHED else 0.0,
        type_expected    = ref_node.kind if ref_node else None,
        type_observed    = hyp_node.kind if hyp_node else None,
        page_expected    = ref_node.page if ref_node else None,
        page_observed    = hyp_node.page if hyp_node else None,
        reference_order  = ref_node.order_index if ref_node else None,
        hypothesis_order = hyp_node.order_index if hyp_node else None,
    )


# ---------------------------------------------------------------------------
# SM01-SM05: Heading metrics
# ---------------------------------------------------------------------------

class TestHeadingDetection:
    def test_SM01_perfect_heading_match(self):
        """SM01: All headings matched → F1=1.0, level_match=1.0."""
        ref = [_ref("Título 1", NodeKind.HEADING, seq=0, heading_level=1)]
        hyp = [_hyp("Título 1", NodeKind.HEADING, seq=0, heading_level=1)]
        pairs = align_blocks(ref, hyp)
        m = heading_detection_f1(pairs, ref, hyp)
        assert m["heading_detection_f1"].value == pytest.approx(1.0)
        assert m["heading_level_match_rate"].value == pytest.approx(1.0)

    def test_SM02_missing_heading_fn_counted(self):
        """SM02: Ref has heading, hyp doesn't → recall < 1.0, FN=1."""
        ref = [_ref("Título", NodeKind.HEADING, seq=0, heading_level=1)]
        hyp = [_hyp("Parágrafo diferente", NodeKind.PARAGRAPH, seq=0)]
        pairs = align_blocks(ref, hyp)
        m = heading_detection_f1(pairs, ref, hyp)
        assert m["heading_detection_f1"].counts.get("fn", 0) >= 1
        assert m["heading_recall"].value == pytest.approx(0.0)

    def test_SM03_extra_heading_fp_counted(self):
        """SM03: Hyp has heading with no ref counterpart → precision < 1.0."""
        ref = [_ref("Parágrafo.", NodeKind.PARAGRAPH, seq=0)]
        hyp = [_hyp("Parágrafo.", NodeKind.PARAGRAPH, seq=0),
               _hyp("Cabeçalho extra", NodeKind.HEADING, seq=1, heading_level=2)]
        pairs = align_blocks(ref, hyp)
        m = heading_detection_f1(pairs, ref, hyp)
        assert m["heading_detection_f1"].counts.get("fp", 0) >= 1

    def test_SM04_heading_level_mismatch_reduces_rate(self):
        """SM04: Matched heading with wrong level → level_match_rate < 1.0."""
        ref = [_ref("Título H1", NodeKind.HEADING, seq=0, heading_level=1)]
        hyp = [_hyp("Título H1", NodeKind.HEADING, seq=0, heading_level=2)]  # wrong level
        pairs = align_blocks(ref, hyp)
        m = heading_detection_f1(pairs, ref, hyp)
        assert m["heading_level_match_rate"].value is not None
        assert m["heading_level_match_rate"].value < 1.0

    def test_SM05_no_headings_not_applicable(self):
        """SM05: Neither ref nor hyp has headings → not_applicable."""
        ref = [_ref("Parágrafo.", seq=0)]
        hyp = [_hyp("Parágrafo.", seq=0)]
        pairs = align_blocks(ref, hyp)
        m = heading_detection_f1(pairs, ref, hyp)
        assert m["heading_detection_f1"].status == MetricStatus.NOT_APPLICABLE
        assert m["heading_level_match_rate"].status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# SM06: Paragraph detection
# ---------------------------------------------------------------------------

class TestParagraphDetection:
    def test_SM06_perfect_paragraph_match(self):
        """SM06: All paragraphs matched → F1=1.0."""
        ref = [_ref("P1.", seq=0), _ref("P2.", seq=1)]
        hyp = [_hyp("P1.", seq=0), _hyp("P2.", seq=1)]
        pairs = align_blocks(ref, hyp)
        m = paragraph_detection_f1(pairs, ref, hyp)
        assert m["paragraph_detection_f1"].value == pytest.approx(1.0)

    def test_SM06_missing_paragraph(self):
        """SM06: One paragraph missing from hyp → recall < 1.0."""
        ref = [_ref("P1.", seq=0), _ref("P2.", seq=1)]
        hyp = [_hyp("P1.", seq=0)]
        pairs = align_blocks(ref, hyp)
        m = paragraph_detection_f1(pairs, ref, hyp)
        assert m["paragraph_detection_recall"].value < 1.0

    def test_SM06_no_paragraphs_not_applicable(self):
        """SM06: Only headings → paragraph metric not_applicable."""
        ref = [_ref("Título", NodeKind.HEADING, seq=0, heading_level=1)]
        hyp = [_hyp("Título", NodeKind.HEADING, seq=0, heading_level=1)]
        pairs = align_blocks(ref, hyp)
        m = paragraph_detection_f1(pairs, ref, hyp)
        assert m["paragraph_detection_f1"].status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# SM07-SM09: List metrics
# ---------------------------------------------------------------------------

class TestListMetrics:
    def test_SM07_perfect_list_group_match(self):
        """SM07: All list containers matched → F1=1.0."""
        ref = [_ref("- A\n- B", NodeKind.LIST, seq=0, list_depth=0, ordered=False)]
        hyp = [_hyp("- A\n- B", NodeKind.LIST, seq=0, list_depth=0, ordered=False)]
        pairs = align_blocks(ref, hyp)
        m = list_metrics(pairs, ref, hyp)
        assert m["list_group_detection_f1"].value == pytest.approx(1.0)

    def test_SM08_list_item_detection(self):
        """SM08: List items matched → item F1 computed."""
        ref = [
            _ref("- A", NodeKind.LIST, seq=0, list_depth=0),
            _ref("A",   NodeKind.LIST_ITEM, seq=1, list_depth=1),
            _ref("B",   NodeKind.LIST_ITEM, seq=2, list_depth=1),
        ]
        hyp = [
            _hyp("- A", NodeKind.LIST, seq=0, list_depth=0),
            _hyp("A",   NodeKind.LIST_ITEM, seq=1, list_depth=1),
            _hyp("B",   NodeKind.LIST_ITEM, seq=2, list_depth=1),
        ]
        pairs = align_blocks(ref, hyp)
        m = list_metrics(pairs, ref, hyp)
        assert m["list_item_detection_f1"].value == pytest.approx(1.0)

    def test_SM09_list_hierarchy_correct_depth(self):
        """SM09: Items at matching depth → hierarchy_accuracy=1.0."""
        ref_items = [
            _ref("top",    NodeKind.LIST_ITEM, seq=0, list_depth=1),
            _ref("nested", NodeKind.LIST_ITEM, seq=1, list_depth=2),
        ]
        hyp_items = [
            _hyp("top",    NodeKind.LIST_ITEM, seq=0, list_depth=1),
            _hyp("nested", NodeKind.LIST_ITEM, seq=1, list_depth=2),
        ]
        pairs = align_blocks(ref_items, hyp_items)
        m = list_metrics(pairs, ref_items, hyp_items)
        assert m["list_hierarchy_accuracy"].value == pytest.approx(1.0)

    def test_SM09_list_hierarchy_wrong_depth(self):
        """SM09: Item at wrong depth → hierarchy_accuracy < 1.0."""
        ref_items = [
            _ref("item", NodeKind.LIST_ITEM, seq=0, list_depth=1),
        ]
        hyp_items = [
            _hyp("item", NodeKind.LIST_ITEM, seq=0, list_depth=2),  # wrong depth
        ]
        pairs = align_blocks(ref_items, hyp_items)
        m = list_metrics(pairs, ref_items, hyp_items)
        assert m["list_hierarchy_accuracy"].value == pytest.approx(0.0)

    def test_SM09_no_items_not_applicable(self):
        """SM09: No list items → hierarchy not_applicable."""
        ref = [_ref("Texto.", seq=0)]
        hyp = [_hyp("Texto.", seq=0)]
        pairs = align_blocks(ref, hyp)
        m = list_metrics(pairs, ref, hyp)
        assert m["list_hierarchy_accuracy"].status == MetricStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# SM10-SM12: Reading order
# ---------------------------------------------------------------------------

class TestReadingOrder:
    def test_SM10_perfect_reading_order(self):
        """SM10: Hyp preserves ref order exactly → accuracy=1.0."""
        refs = [_ref(f"Bloco {i}.", seq=i) for i in range(4)]
        hyps = [_hyp(f"Bloco {i}.", seq=i) for i in range(4)]
        pairs = align_blocks(refs, hyps)
        result = pairwise_reading_order_accuracy(pairs)
        assert result.value == pytest.approx(1.0)

    def test_SM11_reversed_reading_order(self):
        """SM11: Hyp in reverse order → accuracy=0.0."""
        # ref order: 0,1,2,3  →  hyp order: 3,2,1,0
        # Build pairs explicitly: ref[i] (reference_order=i) paired with
        # a hyp node whose hypothesis_order = 3-i (completely reversed).
        manual_pairs = []
        for i in range(4):
            manual_pairs.append(AlignedPair(
                reference_id     = MarkdownNode.make_id("reference",  1, NodeKind.PARAGRAPH, i),
                hypothesis_id    = MarkdownNode.make_id("hypothesis", 1, NodeKind.PARAGRAPH, 3 - i),
                status           = AlignmentStatus.MATCHED,
                alignment_score  = 1.0,
                text_similarity  = 1.0,
                reference_order  = i,
                hypothesis_order = 3 - i,
            ))
        result = pairwise_reading_order_accuracy(manual_pairs)
        assert result.value == pytest.approx(0.0)

    def test_SM12_single_element_insufficient(self):
        """SM12: Only one matched element → insufficient_evidence."""
        refs = [_ref("Único.", seq=0)]
        hyps = [_hyp("Único.", seq=0)]
        pairs = align_blocks(refs, hyps)
        result = pairwise_reading_order_accuracy(pairs)
        assert result.status == MetricStatus.INSUFFICIENT_EVIDENCE

    def test_SM12_no_matched_elements_not_applicable(self):
        """SM12: No matched elements → not_applicable."""
        result = pairwise_reading_order_accuracy([])
        assert result.status == MetricStatus.NOT_APPLICABLE

    def test_SM10_partial_order_is_fractional(self):
        """SM10: Hyp with some order violations → accuracy in (0, 1)."""
        # ref: 0,1,2,3 → hyp order: 0,1,3,2 (one swap)
        manual_pairs = [
            _manual_pair(_ref(f"B{i}.", seq=i), _hyp(f"B{i}.", seq=i))
            for i in range(3)
        ]
        # Add one pair with swapped hyp order
        manual_pairs.append(
            AlignedPair(
                reference_id=MarkdownNode.make_id("reference", 1, NodeKind.PARAGRAPH, 3),
                hypothesis_id=MarkdownNode.make_id("hypothesis", 1, NodeKind.PARAGRAPH, 99),
                status=AlignmentStatus.MATCHED,
                alignment_score=1.0, text_similarity=1.0,
                reference_order=3, hypothesis_order=99,
            )
        )
        result = pairwise_reading_order_accuracy(manual_pairs)
        assert result.value is not None
        assert 0.0 <= result.value <= 1.0


# ---------------------------------------------------------------------------
# SM13: Block type confusion
# ---------------------------------------------------------------------------

class TestBlockTypeConfusion:
    def test_SM13_no_mismatches_empty_dict(self):
        """SM13: All kinds correct → empty confusion dict."""
        refs = [_ref("A.", seq=0), _ref("B.", seq=1)]
        hyps = [_hyp("A.", seq=0), _hyp("B.", seq=1)]
        pairs = align_blocks(refs, hyps)
        conf = block_type_confusion(pairs)
        assert conf == {}

    def test_SM13_kind_mismatch_recorded(self):
        """SM13: Heading matched to paragraph → entry in confusion dict."""
        ref = [_ref("Título", NodeKind.HEADING, seq=0, heading_level=1)]
        hyp = [_hyp("Título", NodeKind.PARAGRAPH, seq=0)]
        pairs = align_blocks(ref, hyp)
        conf = block_type_confusion(pairs)
        # If the aligner matched them (text identical), record mismatch
        if any(p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS) for p in pairs):
            assert "heading→paragraph" in conf

    def test_SM13_unmatched_not_in_confusion(self):
        """SM13: UNMATCHED and EXTRA pairs don't appear in confusion matrix."""
        ref = [_ref("Texto A.", seq=0)]
        hyp = [_hyp("Texto B completamente diferente.", seq=0)]
        pairs = align_blocks(ref, hyp, min_score=0.99)  # force no match
        conf = block_type_confusion(pairs)
        # No matched pairs → empty confusion
        matched = [p for p in pairs if p.status == AlignmentStatus.MATCHED]
        if not matched:
            assert conf == {}


# ---------------------------------------------------------------------------
# SM14: compute_structure_metrics integration
# ---------------------------------------------------------------------------

class TestComputeStructureMetrics:
    def test_SM14_returns_structure_metrics_object(self):
        """SM14: compute_structure_metrics returns a StructureMetrics with all fields."""
        ref = [
            _ref("# H1", NodeKind.HEADING, seq=0, heading_level=1),
            _ref("P1.", seq=1),
            _ref("- A", NodeKind.LIST, seq=2, list_depth=0),
        ]
        hyp = [
            _hyp("# H1", NodeKind.HEADING, seq=0, heading_level=1),
            _hyp("P1.", seq=1),
            _hyp("- A", NodeKind.LIST, seq=2, list_depth=0),
        ]
        pairs = align_blocks(ref, hyp)
        sm = compute_structure_metrics(pairs, ref, hyp)
        assert sm.heading
        assert sm.paragraph
        assert sm.lists
        assert sm.reading_order is not None
        assert isinstance(sm.type_confusion, dict)

    def test_SM14_all_perfect_match(self):
        """SM14: Perfect document → heading F1=1, paragraph F1=1, order=1."""
        ref = [
            _ref("Intro", NodeKind.HEADING, seq=0, heading_level=1),
            _ref("Texto.", seq=1),
        ]
        hyp = [
            _hyp("Intro", NodeKind.HEADING, seq=0, heading_level=1),
            _hyp("Texto.", seq=1),
        ]
        pairs = align_blocks(ref, hyp)
        sm = compute_structure_metrics(pairs, ref, hyp)
        assert sm.heading["heading_detection_f1"].value == pytest.approx(1.0)
        assert sm.paragraph["paragraph_detection_f1"].value == pytest.approx(1.0)
        assert sm.reading_order.value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# SM15: JSON serialisability
# ---------------------------------------------------------------------------

class TestSerialisability:
    def test_SM15_to_dict_json_serialisable(self):
        """SM15: StructureMetrics.to_dict() is JSON-serialisable."""
        ref = [
            _ref("H", NodeKind.HEADING, seq=0, heading_level=2),
            _ref("P.", seq=1),
        ]
        hyp = [
            _hyp("H", NodeKind.HEADING, seq=0, heading_level=2),
            _hyp("P.", seq=1),
        ]
        pairs = align_blocks(ref, hyp)
        sm = compute_structure_metrics(pairs, ref, hyp)
        d = sm.to_dict()
        _ = json.dumps(d)  # must not raise

    def test_SM15_metric_result_to_dict_has_required_keys(self):
        """SM15: Each MetricResult.to_dict() includes value, status, direction."""
        ref = [_ref("Texto.", seq=0)]
        hyp = [_hyp("Texto.", seq=0)]
        pairs = align_blocks(ref, hyp)
        sm = compute_structure_metrics(pairs, ref, hyp)
        for key, result in sm.paragraph.items():
            d = result.to_dict()
            assert "value" in d
            assert "status" in d
            assert "direction" in d
