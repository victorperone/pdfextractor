"""Structure, list, and reading-order metrics based on element identity (spec §5).

All metrics consume the AlignedPair list produced by block_alignment.align_blocks()
and the original MarkdownNode lists.  They never re-parse text — only identity
and alignment information is used.

Metrics defined here
--------------------
MET-S01  heading_detection_f1         P/R/F1 for headings present in output
MET-S02  heading_level_match_rate     fraction of matched headings with correct level
MET-S03  heading_text_cer             char-level error rate on matched heading pairs
                                       (uses visible_text; mismatched level does NOT block CER —
                                       T24: '# Título correto' vs '# Titulo correto' → CER > 0)
MET-S04  paragraph_detection_f1      P/R/F1 for paragraph blocks
MET-S05  list_group_detection_f1     P/R/F1 for list container blocks
MET-S06  list_item_detection_f1      P/R/F1 for list item blocks
MET-S07  list_hierarchy_accuracy     fraction of matched items with correct depth
MET-S08  pairwise_reading_order_acc  fraction of ref ordered pairs preserved in hyp
                                      (universe: all textual units)
MET-S09  block_type_confusion        dict mapping (expected, observed) mismatches
MET-S10  block_order_pairwise_acc    pairwise order accuracy for structural blocks only
                                      (universe: heading/paragraph/table/list/code —
                                       distinct from MET-S08 per spec §8.1)
MET-SL01 list_item_text_cer_on_matched  CER on matched item text pairs
MET-SL02 list_kind_accuracy          ordered vs unordered agreement on matched groups
MET-SL03 list_item_order_accuracy    pairwise order of items within each parent list
MET-SL04 ordered_list_ordinal_accuracy  ordinal index accuracy for numbered lists
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NodeKind,
)

# Regex to extract ordinal index from ordered-list item visible_text.
# Matches leading "1." / "1)" / "a." patterns.
_ORDINAL_RE = re.compile(r"^\s*(\d+)[.)]\s")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lev_distance(a: str, b: str) -> int:
    """O(min(m,n)) space Levenshtein distance."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ch_a in a:
        curr = [prev[0] + 1]
        for j, ch_b in enumerate(b):
            curr.append(min(prev[j] + (0 if ch_a == ch_b else 1),
                            curr[-1] + 1, prev[j + 1] + 1))
        prev = curr
    return prev[-1]


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None, MetricStatus]:
    """Compute precision / recall / F1 with spec §9.2 empty-ref handling.

    Returns (precision, recall, f1, status).
    """
    if tp == 0 and fp == 0 and fn == 0:
        return None, None, None, MetricStatus.NOT_APPLICABLE
    if fn > 0 and fp == 0 and tp == 0:
        return 0.0, 0.0, 0.0, MetricStatus.FALSE_NEGATIVE_ONLY
    if fp > 0 and fn == 0 and tp == 0:
        return 0.0, 0.0, 0.0, MetricStatus.FALSE_POSITIVE_ONLY

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall    = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision and recall:
        f1 = 2 * precision * recall / (precision + recall)
    elif precision == 0.0 or recall == 0.0:
        f1 = 0.0
    else:
        f1 = None
    return precision, recall, f1, MetricStatus.OK


def _make_result(
    value: float | None,
    status: MetricStatus,
    definition_id: str,
    counts: dict,
    direction: str = "higher_is_better",
) -> MetricResult:
    return MetricResult(
        value          = round(value, 4) if value is not None else None,
        status         = status,
        direction      = direction,
        unit           = "ratio",
        definition_id  = definition_id,
        counts         = counts,
        scope          = "document",
        normalization_profile = None,
    )


# ---------------------------------------------------------------------------
# MET-S01/S02: Heading detection and level accuracy
# ---------------------------------------------------------------------------

def heading_detection_f1(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-S01: Heading detection P/R/F1.  MET-S02: level match rate."""
    ref_headings = {n.node_id for n in ref_nodes if n.kind == NodeKind.HEADING}
    hyp_headings = {n.node_id for n in hyp_nodes if n.kind == NodeKind.HEADING}

    # Build lookup maps for matched pairs
    ref_to_hyp: dict[str, str] = {}
    for p in pairs:
        if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            if p.reference_id and p.hypothesis_id:
                ref_to_hyp[p.reference_id] = p.hypothesis_id

    hyp_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in hyp_nodes}
    ref_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in ref_nodes}

    # TP: ref heading matched to a hyp heading
    # FP: hyp heading with no matched ref heading
    # FN: ref heading with no match or matched to non-heading hyp
    tp = sum(
        1 for rid in ref_headings
        if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_headings
    )
    fn = len(ref_headings) - tp
    # FP: hyp headings not reached by any matched ref heading
    matched_hyp_headings = {ref_to_hyp[rid] for rid in ref_headings if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_headings}
    fp = len(hyp_headings - matched_hyp_headings)

    precision, recall, f1, status = _prf(tp, fp, fn)

    det_result = _make_result(
        f1, status, "MET-S01",
        {"tp": tp, "fp": fp, "fn": fn,
         "ref_headings": len(ref_headings), "hyp_headings": len(hyp_headings)},
    )

    # MET-S02: heading level match rate
    level_matched = 0
    level_total   = 0
    for rid in ref_headings:
        if rid not in ref_to_hyp:
            continue
        hid = ref_to_hyp[rid]
        if hid not in hyp_headings:
            continue
        level_total += 1
        rn = ref_id_to_node.get(rid)
        hn = hyp_id_to_node.get(hid)
        if rn and hn and rn.heading_level == hn.heading_level:
            level_matched += 1

    if level_total == 0:
        level_result = _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-S02",
                                    {"matched_pairs": 0})
    else:
        level_result = _make_result(
            level_matched / level_total, MetricStatus.OK, "MET-S02",
            {"level_matched": level_matched, "level_total": level_total},
        )

    return {
        "heading_detection_f1":     det_result,
        "heading_precision":        _make_result(precision, status, "MET-S01.precision", {}),
        "heading_recall":           _make_result(recall,    status, "MET-S01.recall",    {}),
        "heading_level_match_rate": level_result,
    }


# ---------------------------------------------------------------------------
# MET-S03: Heading text CER (on matched heading pairs)
# ---------------------------------------------------------------------------

def heading_text_cer(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-S03: CER on matched heading pairs, using visible_text.

    A heading match does NOT require identical text — the alignment matches
    headings that are in the same structural position.  CER then measures how
    far the text deviates.  An accent difference (e.g. 'Título' vs 'Titulo')
    will produce CER > 0 (strict profile: accents preserved).

    - Only pairs where both ref and hyp are HEADING nodes are included.
    - Missing ref headings (FN) contribute D-chars to the numerator and their
      length to the denominator, ensuring they are not silently ignored.
    - Unmatched hyp headings (FP) contribute I-chars tracked separately but
      not added to the denominator (undefined_denominator for those).
    """
    ref_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in ref_nodes}
    hyp_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in hyp_nodes}

    ref_headings = {n.node_id for n in ref_nodes if n.kind == NodeKind.HEADING}
    hyp_headings = {n.node_id for n in hyp_nodes if n.kind == NodeKind.HEADING}

    # Build ref→hyp match for heading pairs only
    ref_to_hyp_heading: dict[str, str] = {}
    matched_hyp_heading_ids: set[str] = set()
    for p in pairs:
        if p.status not in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            continue
        if p.reference_id in ref_headings and p.hypothesis_id in hyp_headings:
            ref_to_hyp_heading[p.reference_id] = p.hypothesis_id
            matched_hyp_heading_ids.add(p.hypothesis_id)

    cer_num = 0
    cer_den = 0
    insertion_only_chars = 0

    # Matched pairs: compute edit distance
    for rid, hid in ref_to_hyp_heading.items():
        rn = ref_id_to_node.get(rid)
        hn = hyp_id_to_node.get(hid)
        if rn is None or hn is None:
            continue
        ref_text = rn.visible_text
        hyp_text = hn.visible_text
        dist = _lev_distance(ref_text, hyp_text)
        if len(ref_text) > 0:
            cer_num += dist
            cer_den += len(ref_text)
        elif len(hyp_text) > 0:
            insertion_only_chars += len(hyp_text)

    # Unmatched ref headings → all chars are deletions
    for rid in ref_headings:
        if rid not in ref_to_hyp_heading:
            rn = ref_id_to_node.get(rid)
            if rn and len(rn.visible_text) > 0:
                cer_num += len(rn.visible_text)
                cer_den += len(rn.visible_text)

    if cer_den == 0 and insertion_only_chars == 0 and not ref_headings:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-S03",
                            {"matched_pairs": 0, "ref_headings": 0})

    if cer_den == 0:
        # Only FP (unmatched hyp headings) or ref headings all had empty text
        return _make_result(None, MetricStatus.UNDEFINED_DENOMINATOR, "MET-S03",
                            {"edit_distance": cer_num, "ref_chars": 0,
                             "insertion_only_chars": insertion_only_chars})

    return _make_result(
        cer_num / cer_den,
        MetricStatus.OK,
        "MET-S03",
        {"edit_distance": cer_num, "ref_chars": cer_den,
         "matched_pairs": len(ref_to_hyp_heading),
         "insertion_only_chars": insertion_only_chars},
        direction="lower_is_better",
    )


# ---------------------------------------------------------------------------
# MET-S04: Paragraph detection
# ---------------------------------------------------------------------------

def paragraph_detection_f1(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-S03: Paragraph block detection P/R/F1."""
    ref_paras = {n.node_id for n in ref_nodes if n.kind == NodeKind.PARAGRAPH}
    hyp_paras = {n.node_id for n in hyp_nodes if n.kind == NodeKind.PARAGRAPH}

    ref_to_hyp: dict[str, str] = {}
    for p in pairs:
        if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            if p.reference_id and p.hypothesis_id:
                ref_to_hyp[p.reference_id] = p.hypothesis_id

    tp = sum(1 for rid in ref_paras if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_paras)
    fn = len(ref_paras) - tp
    matched_hyp_paras = {ref_to_hyp[rid] for rid in ref_paras if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_paras}
    fp = len(hyp_paras - matched_hyp_paras)

    precision, recall, f1, status = _prf(tp, fp, fn)

    return {
        "paragraph_detection_f1":       _make_result(f1, status, "MET-S03", {"tp": tp, "fp": fp, "fn": fn}),
        "paragraph_detection_precision": _make_result(precision, status, "MET-S03.precision", {}),
        "paragraph_detection_recall":    _make_result(recall,    status, "MET-S03.recall",    {}),
    }


# ---------------------------------------------------------------------------
# MET-S04/S05/S06: List metrics
# ---------------------------------------------------------------------------

def list_metrics(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-S04 through MET-S06 plus additional list metrics (spec §6.4)."""
    ref_lists = {n.node_id for n in ref_nodes if n.kind == NodeKind.LIST}
    ref_items = {n.node_id for n in ref_nodes if n.kind == NodeKind.LIST_ITEM}
    hyp_lists = {n.node_id for n in hyp_nodes if n.kind == NodeKind.LIST}
    hyp_items = {n.node_id for n in hyp_nodes if n.kind == NodeKind.LIST_ITEM}

    ref_to_hyp: dict[str, str] = {}
    for p in pairs:
        if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            if p.reference_id and p.hypothesis_id:
                ref_to_hyp[p.reference_id] = p.hypothesis_id

    hyp_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in hyp_nodes}
    ref_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in ref_nodes}

    # MET-S04: list group (LIST container) detection
    tp_l = sum(1 for rid in ref_lists if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_lists)
    fn_l = len(ref_lists) - tp_l
    matched_hyp_lists = {ref_to_hyp[rid] for rid in ref_lists if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_lists}
    fp_l = len(hyp_lists - matched_hyp_lists)
    p_l, r_l, f1_l, st_l = _prf(tp_l, fp_l, fn_l)

    # MET-S05: list item detection
    tp_i = sum(1 for rid in ref_items if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_items)
    fn_i = len(ref_items) - tp_i
    matched_hyp_items = {ref_to_hyp[rid] for rid in ref_items if rid in ref_to_hyp and ref_to_hyp[rid] in hyp_items}
    fp_i = len(hyp_items - matched_hyp_items)
    p_i, r_i, f1_i, st_i = _prf(tp_i, fp_i, fn_i)

    # MET-S06: list hierarchy accuracy — fraction of matched items with correct depth
    depth_matched = 0
    depth_total   = 0

    # Shared loop for S06 + new P1 metrics on matched item pairs
    cer_num_items = 0
    cer_den_items = 0
    kind_matched  = 0   # ordered/unordered kind agreement
    kind_total    = 0
    ordinal_correct = 0
    ordinal_total   = 0

    # For item order accuracy: group matched items by their ref parent list
    # ref_parent → list of (ref_order_index, hyp_order_index) pairs
    parent_order: dict[str | None, list[tuple[int, int]]] = defaultdict(list)

    for rid in ref_items:
        if rid not in ref_to_hyp:
            continue
        hid = ref_to_hyp[rid]
        if hid not in hyp_items:
            continue

        rn = ref_id_to_node.get(rid)
        hn = hyp_id_to_node.get(hid)
        if not rn or not hn:
            continue

        # MET-S06: depth
        depth_total += 1
        if rn.list_depth == hn.list_depth:
            depth_matched += 1

        # MET-SL01: CER on matched item text
        ref_text = rn.visible_text
        hyp_text = hn.visible_text
        ref_len  = len(ref_text)
        if ref_len > 0:
            cer_num_items += _lev_distance(ref_text, hyp_text)
            cer_den_items += ref_len

        # MET-SL02: list kind accuracy (ordered vs unordered)
        if rn.ordered_list is not None and hn.ordered_list is not None:
            kind_total += 1
            if rn.ordered_list == hn.ordered_list:
                kind_matched += 1

        # MET-SL04: ordered list ordinal accuracy (regex on visible_text)
        if rn.ordered_list:
            m_ref = _ORDINAL_RE.match(ref_text)
            m_hyp = _ORDINAL_RE.match(hyp_text)
            if m_ref:
                ordinal_total += 1
                if m_hyp and m_ref.group(1) == m_hyp.group(1):
                    ordinal_correct += 1

        # MET-SL03: item order accuracy — collect (ref_order, hyp_order) per parent list
        parent_order[rn.parent_id].append((rn.order_index, hn.order_index))

    # MET-S06 result
    if depth_total == 0:
        hier_result = _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-S06", {"matched_pairs": 0})
    else:
        hier_result = _make_result(
            depth_matched / depth_total, MetricStatus.OK, "MET-S06",
            {"depth_matched": depth_matched, "depth_total": depth_total},
        )

    # MET-SL01: list_item_text_cer_on_matched
    if cer_den_items > 0:
        item_cer_result = _make_result(
            cer_num_items / cer_den_items, MetricStatus.OK, "MET-SL01",
            {"edit_distance": cer_num_items, "ref_chars": cer_den_items},
            direction="lower_is_better",
        )
    else:
        item_cer_result = _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-SL01", {})

    # MET-SL02: list_kind_accuracy
    if kind_total > 0:
        kind_result = _make_result(
            kind_matched / kind_total, MetricStatus.OK, "MET-SL02",
            {"kind_matched": kind_matched, "kind_total": kind_total},
        )
    else:
        kind_result = _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-SL02", {})

    # MET-SL03: list_item_order_accuracy — pairwise concordant pairs across all lists
    total_pairs = 0
    concordant  = 0
    for orders in parent_order.values():
        n = len(orders)
        if n < 2:
            continue
        orders_sorted = sorted(orders, key=lambda x: x[0])  # sort by ref order
        hyp_seq = [o[1] for o in orders_sorted]
        for i in range(n):
            for j in range(i + 1, n):
                total_pairs += 1
                if hyp_seq[i] < hyp_seq[j]:
                    concordant += 1

    if total_pairs == 0:
        order_result = _make_result(
            None,
            MetricStatus.NOT_APPLICABLE if depth_total < 2 else MetricStatus.INSUFFICIENT_EVIDENCE,
            "MET-SL03", {"matched_items": depth_total},
        )
    else:
        order_result = _make_result(
            concordant / total_pairs, MetricStatus.OK, "MET-SL03",
            {"concordant_pairs": concordant, "total_pairs": total_pairs},
        )

    # MET-SL04: ordered_list_ordinal_accuracy
    if ordinal_total > 0:
        ordinal_result = _make_result(
            ordinal_correct / ordinal_total, MetricStatus.OK, "MET-SL04",
            {"ordinal_correct": ordinal_correct, "ordinal_total": ordinal_total},
        )
    else:
        ordinal_result = _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-SL04", {})

    return {
        "list_group_detection_f1":        _make_result(f1_l, st_l, "MET-S04", {"tp": tp_l, "fp": fp_l, "fn": fn_l}),
        "list_group_detection_precision":  _make_result(p_l,  st_l, "MET-S04.precision", {}),
        "list_group_detection_recall":     _make_result(r_l,  st_l, "MET-S04.recall",    {}),
        "list_item_detection_f1":          _make_result(f1_i, st_i, "MET-S05", {"tp": tp_i, "fp": fp_i, "fn": fn_i}),
        "list_item_detection_precision":   _make_result(p_i,  st_i, "MET-S05.precision", {}),
        "list_item_detection_recall":      _make_result(r_i,  st_i, "MET-S05.recall",    {}),
        "list_hierarchy_accuracy":         hier_result,
        "list_item_text_cer_on_matched":   item_cer_result,
        "list_kind_accuracy":              kind_result,
        "list_item_order_accuracy":        order_result,
        "ordered_list_ordinal_accuracy":   ordinal_result,
    }


# ---------------------------------------------------------------------------
# MET-S07: Pairwise reading order accuracy
# ---------------------------------------------------------------------------

def pairwise_reading_order_accuracy(
    pairs: Sequence[AlignedPair],
) -> MetricResult:
    """MET-S07: Fraction of ref ordered pairs whose relative order is preserved in hyp.

    For all matched pairs (ri, rj) where ri appears before rj in the reference,
    count how many have hyp_order(ri) < hyp_order(rj).

    This is equivalent to Kendall's τ normalised to [0, 1].
    """
    # Collect matched pairs with both orders defined
    matched = [
        p for p in pairs
        if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS)
        and p.reference_order is not None
        and p.hypothesis_order is not None
    ]

    n = len(matched)
    if n < 2:
        status = MetricStatus.NOT_APPLICABLE if n == 0 else MetricStatus.INSUFFICIENT_EVIDENCE
        return _make_result(None, status, "MET-S07", {"matched_elements": n})

    # Sort by reference order
    matched_sorted = sorted(matched, key=lambda p: p.reference_order)  # type: ignore[arg-type]
    hyp_orders = [p.hypothesis_order for p in matched_sorted]

    # Count concordant pairs: i < j in ref → hyp_order[i] < hyp_order[j]
    total_pairs = 0
    concordant  = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if hyp_orders[i] < hyp_orders[j]:  # type: ignore[operator]
                concordant += 1

    accuracy = concordant / total_pairs if total_pairs > 0 else None
    return _make_result(
        accuracy, MetricStatus.OK, "MET-S07",
        {"matched_elements": n, "concordant_pairs": concordant,
         "total_pairs": total_pairs},
    )


# ---------------------------------------------------------------------------
# MET-S08: Block type confusion matrix
# ---------------------------------------------------------------------------

def block_type_confusion(
    pairs: Sequence[AlignedPair],
) -> dict[str, int]:
    """MET-S08: Count (expected_kind, observed_kind) mismatches for matched pairs.

    Returns a dict with keys ``"{expected}→{observed}"`` and int counts.
    Only pairs where expected != observed are included.
    """
    confusion: dict[str, int] = defaultdict(int)
    for p in pairs:
        if p.status not in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            continue
        if p.type_expected is None or p.type_observed is None:
            continue
        if p.type_expected != p.type_observed:
            key = f"{p.type_expected.value}→{p.type_observed.value}"
            confusion[key] += 1
    return dict(confusion)


# ---------------------------------------------------------------------------
# MET-S09: Block order pairwise accuracy (structural blocks only)
# ---------------------------------------------------------------------------

# Universe of top-level structural block kinds for block order.
# Excludes internal list items and table rows/cells — those have their own
# order metrics. Tables and list containers are treated as atomic blocks.
_BLOCK_ORDER_KINDS = frozenset({
    NodeKind.HEADING,
    NodeKind.PARAGRAPH,
    NodeKind.TABLE,
    NodeKind.LIST,
    NodeKind.CODE,
    NodeKind.BLOCKQUOTE,
    NodeKind.THEMATIC_BREAK,
})


def block_order_pairwise_accuracy(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-S09: Pairwise order accuracy for structural blocks (spec §8.1).

    Universe: matched pairs where the ref element is a top-level structural
    block (heading, paragraph, table, list, code, blockquote, thematic_break).
    List items and table internals are excluded — they have separate order metrics.

    This gives a distinct measurement from reading_order_pairwise_accuracy,
    which spans all textual units including list items.
    """
    ref_id_to_node: dict[str, MarkdownNode] = {n.node_id: n for n in ref_nodes}

    matched = [
        p for p in pairs
        if p.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS)
        and p.reference_id is not None
        and p.reference_order is not None
        and p.hypothesis_order is not None
        and ref_id_to_node.get(p.reference_id, None) is not None
        and ref_id_to_node[p.reference_id].kind in _BLOCK_ORDER_KINDS
    ]

    n = len(matched)
    if n < 2:
        status = MetricStatus.NOT_APPLICABLE if n == 0 else MetricStatus.INSUFFICIENT_EVIDENCE
        return _make_result(None, status, "MET-S09",
                            {"matched_blocks": n, "universe": "structural_blocks"})

    matched_sorted = sorted(matched, key=lambda p: p.reference_order)  # type: ignore[arg-type]
    hyp_orders = [p.hypothesis_order for p in matched_sorted]

    total_pairs = 0
    concordant  = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if hyp_orders[i] < hyp_orders[j]:  # type: ignore[operator]
                concordant += 1

    accuracy = concordant / total_pairs if total_pairs > 0 else None
    return _make_result(
        accuracy, MetricStatus.OK, "MET-S09",
        {"matched_blocks": n, "concordant_pairs": concordant,
         "total_pairs": total_pairs, "universe": "structural_blocks"},
    )


# ---------------------------------------------------------------------------
# Convenience: compute all structure metrics at once
# ---------------------------------------------------------------------------

@dataclass
class StructureMetrics:
    """All structure/list/order metrics for one evaluation run."""
    heading:         dict[str, MetricResult] = field(default_factory=dict)
    heading_cer:     MetricResult | None = None
    paragraph:       dict[str, MetricResult] = field(default_factory=dict)
    lists:           dict[str, MetricResult] = field(default_factory=dict)
    reading_order:   MetricResult | None = None
    block_order:     MetricResult | None = None
    type_confusion:  dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result: dict = {}
        for k, v in self.heading.items():
            result[k] = v.to_dict()
        if self.heading_cer:
            result["heading_text_cer"] = self.heading_cer.to_dict()
        for k, v in self.paragraph.items():
            result[k] = v.to_dict()
        for k, v in self.lists.items():
            result[k] = v.to_dict()
        if self.reading_order:
            result["pairwise_reading_order_accuracy"] = self.reading_order.to_dict()
        if self.block_order:
            result["block_order_pairwise_accuracy"] = self.block_order.to_dict()
        result["block_type_confusion"] = self.type_confusion
        return result


def compute_structure_metrics(
    pairs: Sequence[AlignedPair],
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> StructureMetrics:
    """Compute all structure metrics (MET-S01 to MET-S10, MET-SL01 to MET-SL04).

    Parameters
    ----------
    pairs     : Output of align_blocks(ref_nodes, hyp_nodes).
    ref_nodes : Reference MarkdownNode list (full document).
    hyp_nodes : Hypothesis MarkdownNode list (full document).

    Returns
    -------
    StructureMetrics dataclass with all metric results.
    """
    return StructureMetrics(
        heading        = heading_detection_f1(pairs, ref_nodes, hyp_nodes),
        heading_cer    = heading_text_cer(pairs, ref_nodes, hyp_nodes),
        paragraph      = paragraph_detection_f1(pairs, ref_nodes, hyp_nodes),
        lists          = list_metrics(pairs, ref_nodes, hyp_nodes),
        reading_order  = pairwise_reading_order_accuracy(pairs),
        block_order    = block_order_pairwise_accuracy(pairs, ref_nodes, hyp_nodes),
        type_confusion = block_type_confusion(pairs),
    )
