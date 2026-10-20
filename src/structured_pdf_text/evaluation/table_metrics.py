"""Table metrics for the V2 evaluation pipeline (spec §7, MET-T*).

All metrics require MarkdownNode lists produced by the canonical parser.
Table detection uses content-based 1:1 matching with a minimum similarity
threshold — not min(n_ref, n_hyp) counting.

Metrics
-------
MET-T01  table_detection_f1          P/R/F1 of tables correctly detected
MET-T02  table_row_detection_f1      P/R/F1 of rows, including unmatched tables
MET-T03  table_column_count_accuracy same column count over paired/missing/extra tables
MET-T04  cell_detection_f1           P/R/F1 of cells (including empty structural slots)
MET-T05  cell_content_exact_match    exact pairs over paired/missing/extra cells
MET-T06  cell_alignment_accuracy     correctly placed content matches over all cells
MET-T07  cell_cer                    edits over all reference characters, including losses/insertions
MET-T12  cell_span_accuracy          rowspan/colspan agreement over all cells
MET-T08  table_content_f1            multiset coverage of cell text (no positional requirement)
MET-T09  table_row_order_accuracy    pairwise row order accuracy within matched tables
MET-T10  table_column_order_accuracy pairwise column order accuracy within matched tables
MET-T11  table_page_location_accuracy fraction of matched tables where hyp page == ref page;
                                       separate from detection F1 — a table may be detected but
                                       placed on the wrong page (spec §7.4 / T20)
"""
from __future__ import annotations

from .order_counts import concordance_counts

import difflib
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NodeKind,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize
from structured_pdf_text.evaluation.edit_distance import (
    DEFAULT_MAX_EDIT_CELLS, EditDistanceBudget, EditDistanceResourceLimit,
    levenshtein_ops,
)

# Minimum content similarity to accept a table pair
TABLE_MATCH_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return normalize(text, NormProfile.UNICODE_LAYOUT).lower()


def _text_sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _make_result(
    value: float | None,
    status: MetricStatus,
    definition_id: str,
    counts: dict,
    direction: str = "higher_is_better",
) -> MetricResult:
    return MetricResult(
        value         = round(value, 4) if value is not None else None,
        status        = status,
        direction     = direction,
        unit          = "ratio",
        definition_id = definition_id,
        counts        = counts,
        scope         = "document",
    )


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None, MetricStatus]:
    if tp == 0 and fp == 0 and fn == 0:
        return None, None, None, MetricStatus.NOT_APPLICABLE
    if tp == 0 and fp == 0:
        return 0.0, 0.0, 0.0, MetricStatus.FALSE_NEGATIVE_ONLY
    if tp == 0 and fn == 0:
        return 0.0, 0.0, 0.0, MetricStatus.FALSE_POSITIVE_ONLY
    p = tp / (tp + fp) if tp + fp > 0 else None
    r = tp / (tp + fn) if tp + fn > 0 else None
    if p and r:
        f1 = 2 * p * r / (p + r)
    elif p == 0.0 or r == 0.0:
        f1 = 0.0
    else:
        f1 = None
    return p, r, f1, MetricStatus.OK


# ---------------------------------------------------------------------------
# Tree helpers — extract children of a given kind from a parent node's subtree
# ---------------------------------------------------------------------------

def _children_of_kind(
    parent: MarkdownNode,
    nodes_by_parent: dict[str | None, list[MarkdownNode]],
    kind: NodeKind,
) -> list[MarkdownNode]:
    """Return immediate children of *parent* that have the requested kind."""
    return [n for n in nodes_by_parent.get(parent.node_id, []) if n.kind == kind]


def _build_parent_map(nodes: Sequence[MarkdownNode]) -> dict[str | None, list[MarkdownNode]]:
    result: dict[str | None, list[MarkdownNode]] = {}
    for n in nodes:
        result.setdefault(n.parent_id, []).append(n)
    return result


# ---------------------------------------------------------------------------
# Table similarity score used for 1:1 matching
# ---------------------------------------------------------------------------

def _table_cell_texts(table: MarkdownNode, parent_map: dict) -> tuple[str, ...]:
    return tuple(_norm(cell.visible_text)
                 for row in _children_of_kind(table, parent_map, NodeKind.TABLE_ROW)
                 for cell in _children_of_kind(row, parent_map, NodeKind.TABLE_CELL))


def _table_similarity(
    ref_table: MarkdownNode,
    hyp_table: MarkdownNode,
    ref_map: dict[str | None, list[MarkdownNode]],
    hyp_map: dict[str | None, list[MarkdownNode]],
) -> float:
    """Content-based similarity between two table nodes [0, 1]."""
    ref_texts = _table_cell_texts(ref_table, ref_map)
    hyp_texts = _table_cell_texts(hyp_table, hyp_map)

    # Page consistency bonus
    page_ok = (ref_table.page == hyp_table.page) if (ref_table.page and hyp_table.page) else None

    if not ref_texts and not hyp_texts:
        return 1.0 if page_ok is not False else 0.5
    if not ref_texts or not hyp_texts:
        return 0.0

    combined_ref = " ".join(ref_texts)
    combined_hyp = " ".join(hyp_texts)
    text_sim = (1.0 if combined_ref == combined_hyp else
                difflib.SequenceMatcher(None, combined_ref, combined_hyp, autojunk=False).ratio())
    page_bonus = 0.1 if page_ok else 0.0
    return min(1.0, text_sim + page_bonus)


# ---------------------------------------------------------------------------
# Table 1:1 matching
# ---------------------------------------------------------------------------

def _match_tables(
    ref_tables: list[MarkdownNode],
    hyp_tables: list[MarkdownNode],
    ref_map: dict[str | None, list[MarkdownNode]],
    hyp_map: dict[str | None, list[MarkdownNode]],
    threshold: float = TABLE_MATCH_THRESHOLD,
) -> list[tuple[MarkdownNode, MarkdownNode, float]]:
    """Greedy 1:1 matching; returns list of (ref, hyp, score)."""
    if not ref_tables or not hyp_tables:
        return []
    if (threshold <= 1 and len(ref_tables) == len(hyp_tables)
            and all(rt.page == ht.page and _table_cell_texts(rt, ref_map) == _table_cell_texts(ht, hyp_map)
                    for rt, ht in zip(ref_tables, hyp_tables))):
        return [(rt, ht, 1.0) for rt, ht in zip(ref_tables, hyp_tables)]

    # Build score matrix
    scores: list[tuple[float, int, int]] = []
    for ri, rt in enumerate(ref_tables):
        for hi, ht in enumerate(hyp_tables):
            s = _table_similarity(rt, ht, ref_map, hyp_map)
            if s >= threshold:
                scores.append((s, ri, hi))
    scores.sort(key=lambda x: x[0], reverse=True)

    matched: list[tuple[MarkdownNode, MarkdownNode, float]] = []
    used_ref: set[int] = set()
    used_hyp: set[int] = set()
    for s, ri, hi in scores:
        if ri in used_ref or hi in used_hyp:
            continue
        matched.append((ref_tables[ri], hyp_tables[hi], s))
        used_ref.add(ri)
        used_hyp.add(hi)
    return matched


# ---------------------------------------------------------------------------
# MET-T01: Table detection
# ---------------------------------------------------------------------------

def table_detection_f1(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-T01: Table detection P/R/F1 with content-based 1:1 matching."""
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]

    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    tp = len(matched)
    fn = len(ref_tables) - tp
    fp = len(hyp_tables) - tp

    p, r, f1, status = _prf(tp, fp, fn)
    return {
        "table_detection_f1":       _make_result(f1, status, "MET-T01",
                                                  {"tp": tp, "fp": fp, "fn": fn,
                                                   "ref_tables": len(ref_tables),
                                                   "hyp_tables": len(hyp_tables)}),
        "table_detection_precision": _make_result(p, status, "MET-T01.precision", {}),
        "table_detection_recall":    _make_result(r, status, "MET-T01.recall",    {}),
    }


# ---------------------------------------------------------------------------
# MET-T02/T03: Row detection and column count accuracy
# ---------------------------------------------------------------------------

def table_row_and_column_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-T02: row detection F1; MET-T03: column count accuracy."""
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]

    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    total_tp_r = total_fp_r = total_fn_r = 0
    col_matched = 0
    # A missing/extra table is a failed dimension comparison, not N/A.
    col_total = len(ref_tables) + len(hyp_tables) - len(matched)

    for ref_t, hyp_t, _ in matched:
        ref_rows = _children_of_kind(ref_t, ref_map, NodeKind.TABLE_ROW)
        hyp_rows = _children_of_kind(hyp_t, hyp_map, NodeKind.TABLE_ROW)

        # Row matching by content similarity within the pair
        row_pairs = _match_rows(ref_rows, hyp_rows)
        tp_r = len(row_pairs)
        fn_r = len(ref_rows) - tp_r
        fp_r = len(hyp_rows) - tp_r
        total_tp_r += tp_r
        total_fn_r += fn_r
        total_fp_r += fp_r

        # Maximum occupied width, including HTML column spans.
        ref_cols = _max_col_count(ref_rows, ref_map)
        hyp_cols = _max_col_count(hyp_rows, hyp_map)
        if ref_cols == hyp_cols:
            col_matched += 1

    matched_ref = {rt.node_id for rt, _, _ in matched}
    matched_hyp = {ht.node_id for _, ht, _ in matched}
    total_fn_r += sum(len(_children_of_kind(t, ref_map, NodeKind.TABLE_ROW))
                      for t in ref_tables if t.node_id not in matched_ref)
    total_fp_r += sum(len(_children_of_kind(t, hyp_map, NodeKind.TABLE_ROW))
                      for t in hyp_tables if t.node_id not in matched_hyp)

    p_r, r_r, f1_r, st_r = _prf(total_tp_r, total_fp_r, total_fn_r)
    col_acc = (col_matched / col_total) if col_total > 0 else None
    col_st  = MetricStatus.OK if col_total > 0 else MetricStatus.NOT_APPLICABLE

    return {
        "table_row_detection_f1":        _make_result(f1_r, st_r, "MET-T02",
                                                       {"tp": total_tp_r, "fp": total_fp_r, "fn": total_fn_r}),
        "table_row_detection_precision":  _make_result(p_r, st_r, "MET-T02.precision", {}),
        "table_row_detection_recall":     _make_result(r_r, st_r, "MET-T02.recall",    {}),
        "table_column_count_accuracy":    _make_result(col_acc, col_st, "MET-T03",
                                                       {"matched_tables": col_matched,
                                                        "total_matched_tables": len(matched),
                                                        "total_tables": col_total}),
    }


def _max_col_count(rows: list[MarkdownNode], pmap: dict) -> int:
    counts = [max((c.metadata.get("column_index", i) + c.metadata.get("colspan", 1)
                   for i, c in enumerate(_children_of_kind(r, pmap, NodeKind.TABLE_CELL))),
                  default=0) for r in rows]
    return max(counts) if counts else 0


def _match_rows(
    ref_rows: list[MarkdownNode],
    hyp_rows: list[MarkdownNode],
    threshold: float = 0.20,
) -> list[tuple[MarkdownNode, MarkdownNode]]:
    """Greedy 1:1 row matching by combined cell text similarity."""
    if not ref_rows or not hyp_rows:
        return []
    if (threshold <= 1 and len(ref_rows) == len(hyp_rows)
            and all(_norm(r.visible_text) == _norm(h.visible_text) for r, h in zip(ref_rows, hyp_rows))):
        return list(zip(ref_rows, hyp_rows))
    scores: list[tuple[float, int, int]] = []
    for ri, rr in enumerate(ref_rows):
        for hi, hr in enumerate(hyp_rows):
            s = _text_sim(rr.visible_text, hr.visible_text)
            if s >= threshold:
                scores.append((s, ri, hi))
    scores.sort(key=lambda x: x[0], reverse=True)
    matched: list[tuple[MarkdownNode, MarkdownNode]] = []
    used_r: set[int] = set()
    used_h: set[int] = set()
    for s, ri, hi in scores:
        if ri in used_r or hi in used_h:
            continue
        matched.append((ref_rows[ri], hyp_rows[hi]))
        used_r.add(ri)
        used_h.add(hi)
    return matched


def _match_cells_by_content(
    ref_cells: list[MarkdownNode],
    hyp_cells: list[MarkdownNode],
    threshold: float = 0.10,
) -> list[tuple[int, int]]:
    """Greedy 1:1 cell matching by text similarity; returns (ref_col_idx, hyp_col_idx) pairs."""
    if not ref_cells or not hyp_cells:
        return []
    scores: list[tuple[float, int, int]] = []
    for ri, rc in enumerate(ref_cells):
        for hi, hc in enumerate(hyp_cells):
            s = _text_sim(rc.visible_text, hc.visible_text)
            scores.append((s, ri, hi))
    scores.sort(key=lambda x: x[0], reverse=True)
    pairs: list[tuple[int, int]] = []
    used_r: set[int] = set()
    used_h: set[int] = set()
    for s, ri, hi in scores:
        if ri in used_r or hi in used_h:
            continue
        if s >= threshold:
            pairs.append((ri, hi))
            used_r.add(ri)
            used_h.add(hi)
    return pairs


# ---------------------------------------------------------------------------
# MET-T04/T05/T06/T07: Cell metrics
# ---------------------------------------------------------------------------

def cell_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> dict[str, MetricResult]:
    """MET-T04–T07/T12 over every cell, including unmatched rows/tables.

    Exact match uses the union of positional cell pairs and unmatched cells.
    CER sums edits and all reference characters; insertions contribute to the
    numerator even when their individual reference cell is empty. A globally
    empty reference remains undefined, never an invented zero denominator.
    Merged cells carry text once per anchor.
    """
    budget = EditDistanceBudget(max_edit_cells)
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]
    ref_map, hyp_map = _build_parent_map(ref_nodes), _build_parent_map(hyp_nodes)
    tables = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    cell_pairs: list[tuple[MarkdownNode, MarkdownNode]] = []
    align_correct = 0
    for rt, ht, _ in tables:
        rows = _match_rows(_children_of_kind(rt, ref_map, NodeKind.TABLE_ROW),
                           _children_of_kind(ht, hyp_map, NodeKind.TABLE_ROW))
        for rr, hr in rows:
            rc = _children_of_kind(rr, ref_map, NodeKind.TABLE_CELL)
            hc = _children_of_kind(hr, hyp_map, NodeKind.TABLE_CELL)
            # Anchor coordinates matter when spans reserve columns in a row.
            hyp_columns = {c.metadata.get("column_index", i): c for i, c in enumerate(hc)}
            cell_pairs.extend((c, hyp_columns[c.metadata.get("column_index", i)])
                              for i, c in enumerate(rc)
                              if c.metadata.get("column_index", i) in hyp_columns)
            for ri, hi in _match_cells_by_content(rc, hc):
                if rc[ri].metadata.get("column_index", ri) == hc[hi].metadata.get("column_index", hi):
                    align_correct += 1

    ref_cells = [n for n in ref_nodes if n.kind == NodeKind.TABLE_CELL]
    hyp_cells = [n for n in hyp_nodes if n.kind == NodeKind.TABLE_CELL]
    paired_ref = {r.node_id for r, _ in cell_pairs}
    paired_hyp = {h.node_id for _, h in cell_pairs}
    missing = [c for c in ref_cells if c.node_id not in paired_ref]
    extra = [c for c in hyp_cells if c.node_id not in paired_hyp]
    tp, fn, fp = len(cell_pairs), len(missing), len(extra)
    total = tp + fn + fp
    exact = sum(_norm(r.visible_text) == _norm(h.visible_text) for r, h in cell_pairs)
    span_correct = sum((r.metadata.get("rowspan", 1), r.metadata.get("colspan", 1)) ==
                       (h.metadata.get("rowspan", 1), h.metadata.get("colspan", 1))
                       for r, h in cell_pairs)
    p, recall, f1, status = _prf(tp, fp, fn)
    global_status = MetricStatus.OK if total else MetricStatus.NOT_APPLICABLE
    results = {
        "cell_detection_f1": _make_result(f1, status, "MET-T04", {"tp": tp, "fp": fp, "fn": fn}),
        "cell_detection_precision": _make_result(p, status, "MET-T04.precision", {}),
        "cell_detection_recall": _make_result(recall, status, "MET-T04.recall", {}),
        "cell_content_exact_match": _make_result(exact / total if total else None,
            global_status, "MET-T05", {"exact": exact, "total": total,
            "matched": tp, "missing": fn, "extra": fp}),
        "cell_content_exact_match_matched": _make_result(exact / tp if tp else None,
            MetricStatus.OK if tp else MetricStatus.NOT_APPLICABLE,
            "MET-T05.matched", {"exact": exact, "total": tp}),
        "cell_alignment_accuracy": _make_result(align_correct / total if total else None,
            global_status, "MET-T06", {"aligned": align_correct, "total": total}),
        "cell_span_accuracy": _make_result(span_correct / total if total else None,
            global_status, "MET-T12", {"correct_spans": span_correct, "total": total}),
    }
    coverage = tp / len(ref_cells) if ref_cells else None
    for metric in results.values():
        metric.coverage = round(coverage, 4) if coverage is not None else None

    ref_chars = sum(len(_norm(c.visible_text)) for c in ref_cells)
    hyp_chars = sum(len(_norm(c.visible_text)) for c in hyp_cells)
    deleted = sum(len(_norm(c.visible_text)) for c in missing)
    inserted = sum(len(_norm(c.visible_text)) for c in extra)
    insertion_only = inserted + sum(len(_norm(h.visible_text)) for r, h in cell_pairs
                                    if not _norm(r.visible_text))
    counts = {"ref_chars": ref_chars, "hyp_chars": hyp_chars,
              "missing_chars": deleted, "extra_chars": inserted,
              "insertion_only_chars": insertion_only,
              "matched_cells": tp, "missing_cells": fn, "extra_cells": fp}
    try:
        distance = deleted + inserted + sum(
            sum(levenshtein_ops(_norm(r.visible_text), _norm(h.visible_text), budget=budget))
            for r, h in cell_pairs)
    except EditDistanceResourceLimit as exc:
        cer = _make_result(None, MetricStatus.RESOURCE_LIMIT, "MET-T07",
                           {**counts, "max_cells": exc.max_cells,
                            "evaluated_cells": exc.evaluated_cells}, "lower_is_better")
        cer.warnings = [str(exc)]
    else:
        cer_status = (MetricStatus.OK if ref_chars else
                      MetricStatus.UNDEFINED_DENOMINATOR if hyp_chars else MetricStatus.NOT_APPLICABLE)
        cer = _make_result(distance / ref_chars if ref_chars else None, cer_status,
                           "MET-T07", {**counts, "edit_distance": distance}, "lower_is_better")
    cer.coverage = round(coverage, 4) if coverage is not None else None
    results["cell_cer"] = cer
    return results


# ---------------------------------------------------------------------------
# MET-T09/T10: Row and column order accuracy
# ---------------------------------------------------------------------------

def _pairwise_order_accuracy(indices: list[tuple[int, int]]) -> tuple[int, int]:
    """Count concordant and total pairs from (ref_idx, hyp_idx) pairs.

    Sorts by ref_idx, then checks if hyp_idx is monotonically increasing.
    Returns (concordant, total_pairs).
    """
    if len(indices) < 2:
        return 0, 0
    ordered = sorted(indices, key=lambda x: x[0])
    hyp_seq = [o[1] for o in ordered]
    return concordance_counts(hyp_seq)


def table_row_order_accuracy(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-T09: Pairwise row order accuracy within matched tables.

    For each matched table pair, align rows by content, then measure the
    fraction of row pairs where ref order == hyp order.
    """
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]
    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    if not matched:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T09", {})

    total_concordant = total_pairs = 0
    for ref_t, hyp_t, _ in matched:
        ref_rows = _children_of_kind(ref_t, ref_map, NodeKind.TABLE_ROW)
        hyp_rows = _children_of_kind(hyp_t, hyp_map, NodeKind.TABLE_ROW)
        row_pairs = _match_rows(ref_rows, hyp_rows)
        if len(row_pairs) < 2:
            continue
        # Map matched row pairs to (ref_position, hyp_position) index pairs
        ref_idx_map  = {r.node_id: i for i, r in enumerate(ref_rows)}
        hyp_idx_map  = {h.node_id: i for i, h in enumerate(hyp_rows)}
        index_pairs  = [(ref_idx_map[rr.node_id], hyp_idx_map[hr.node_id])
                        for rr, hr in row_pairs]
        c, t = _pairwise_order_accuracy(index_pairs)
        total_concordant += c
        total_pairs      += t

    if total_pairs == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T09",
                            {"matched_table_pairs": len(matched)})
    return _make_result(
        total_concordant / total_pairs, MetricStatus.OK, "MET-T09",
        {"concordant_pairs": total_concordant, "total_pairs": total_pairs},
    )


def table_column_order_accuracy(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-T10: Pairwise column order accuracy within matched tables.

    For each matched table pair, align columns by content profile (using
    the first matched row as a proxy), then measure concordant column pairs.
    """
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]
    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    if not matched:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T10", {})

    total_concordant = total_pairs = 0
    for ref_t, hyp_t, _ in matched:
        ref_rows = _children_of_kind(ref_t, ref_map, NodeKind.TABLE_ROW)
        hyp_rows = _children_of_kind(hyp_t, hyp_map, NodeKind.TABLE_ROW)

        # Use header row (index 0) for column alignment when available
        row_pairs = _match_rows(ref_rows, hyp_rows)
        if not row_pairs:
            continue
        ref_hdr, hyp_hdr = row_pairs[0]

        ref_cells = _children_of_kind(ref_hdr, ref_map, NodeKind.TABLE_CELL)
        hyp_cells = _children_of_kind(hyp_hdr, hyp_map, NodeKind.TABLE_CELL)
        col_pairs = _match_cells_by_content(ref_cells, hyp_cells)
        if len(col_pairs) < 2:
            continue

        c, t = _pairwise_order_accuracy(col_pairs)
        total_concordant += c
        total_pairs      += t

    if total_pairs == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T10",
                            {"matched_table_pairs": len(matched)})
    return _make_result(
        total_concordant / total_pairs, MetricStatus.OK, "MET-T10",
        {"concordant_pairs": total_concordant, "total_pairs": total_pairs},
    )


# ---------------------------------------------------------------------------
# MET-T08: Table content F1 (multiset, no position)
# ---------------------------------------------------------------------------

def table_content_f1(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-T08: Multiset coverage of cell text ignoring positional requirement."""
    ref_cells = [n for n in ref_nodes if n.kind == NodeKind.TABLE_CELL]
    hyp_cells = [n for n in hyp_nodes if n.kind == NodeKind.TABLE_CELL]

    ref_counter = Counter(_norm(c.visible_text) for c in ref_cells)
    hyp_counter = Counter(_norm(c.visible_text) for c in hyp_cells)

    tp = sum((ref_counter & hyp_counter).values())
    fp = sum(hyp_counter.values()) - tp
    fn = sum(ref_counter.values()) - tp

    _, _, f1, status = _prf(tp, fp, fn)
    return _make_result(f1, status, "MET-T08",
                        {"tp": tp, "fp": fp, "fn": fn,
                         "ref_cell_count": len(ref_cells),
                         "hyp_cell_count": len(hyp_cells)})


# ---------------------------------------------------------------------------
# MET-T11: Table page location accuracy
# ---------------------------------------------------------------------------

def table_page_location_accuracy(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-T11: Fraction of matched tables where hyp page equals ref page.

    A table may be content-matched (MET-T01 TP) but still appear on the wrong
    page in the hypothesis output.  This metric is distinct from detection F1
    and measures placement fidelity specifically.

    - Only matched table pairs with page information on BOTH sides are counted.
    - Pairs where either side has page=None are excluded (not penalised).
    - If no matched pairs have page information, status=not_applicable.
    """
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]
    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)
    if not matched:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T11",
                            {"matched_table_pairs": 0})

    total = 0
    correct = 0
    for ref_t, hyp_t, _ in matched:
        if ref_t.page is None or hyp_t.page is None:
            continue
        total += 1
        if ref_t.page == hyp_t.page:
            correct += 1

    if total == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-T11",
                            {"matched_table_pairs": len(matched),
                             "pairs_with_page_info": 0})
    return _make_result(
        correct / total, MetricStatus.OK, "MET-T11",
        {"correct_page": correct, "total_with_page_info": total,
         "matched_table_pairs": len(matched)},
    )


# ---------------------------------------------------------------------------
# Convenience: compute all table metrics
# ---------------------------------------------------------------------------

@dataclass
class TableMetrics:
    """All table metrics for one evaluation run."""
    detection:      dict[str, MetricResult] = field(default_factory=dict)
    rows_columns:   dict[str, MetricResult] = field(default_factory=dict)
    cells:          dict[str, MetricResult] = field(default_factory=dict)
    content_f1:     MetricResult | None = None
    row_order:      MetricResult | None = None
    col_order:      MetricResult | None = None
    page_location:  MetricResult | None = None

    def to_dict(self) -> dict:
        result: dict = {}
        for k, v in self.detection.items():
            result[k] = v.to_dict()
        for k, v in self.rows_columns.items():
            result[k] = v.to_dict()
        for k, v in self.cells.items():
            result[k] = v.to_dict()
        if self.content_f1:
            result["table_content_f1"] = self.content_f1.to_dict()
        if self.row_order:
            result["table_row_order_accuracy"] = self.row_order.to_dict()
        if self.col_order:
            result["table_column_order_accuracy"] = self.col_order.to_dict()
        if self.page_location:
            result["table_page_location_accuracy"] = self.page_location.to_dict()
        return result


def compute_table_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> TableMetrics:
    """Compute all table metrics (MET-T01 to MET-T12).

    Parameters
    ----------
    ref_nodes : Reference MarkdownNode list (full document, including TABLE/ROW/CELL).
    hyp_nodes : Hypothesis MarkdownNode list.

    Returns
    -------
    TableMetrics dataclass with all metric results.
    """
    return TableMetrics(
        detection     = table_detection_f1(ref_nodes, hyp_nodes),
        rows_columns  = table_row_and_column_metrics(ref_nodes, hyp_nodes),
        cells         = cell_metrics(ref_nodes, hyp_nodes, max_edit_cells=max_edit_cells),
        content_f1    = table_content_f1(ref_nodes, hyp_nodes),
        row_order     = table_row_order_accuracy(ref_nodes, hyp_nodes),
        col_order     = table_column_order_accuracy(ref_nodes, hyp_nodes),
        page_location = table_page_location_accuracy(ref_nodes, hyp_nodes),
    )
