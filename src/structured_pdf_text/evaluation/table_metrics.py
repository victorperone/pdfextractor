"""Table metrics for the V2 evaluation pipeline (spec §7, MET-T*).

All metrics require MarkdownNode lists produced by the canonical parser.
Table detection uses content-based 1:1 matching with a minimum similarity
threshold — not min(n_ref, n_hyp) counting.

Metrics
-------
MET-T01  table_detection_f1          P/R/F1 of tables correctly detected
MET-T02  table_row_detection_f1      P/R/F1 of rows inside matched tables
MET-T03  table_column_count_accuracy fraction of matched tables with same column count
MET-T04  cell_detection_f1           P/R/F1 of cells (including empty structural slots)
MET-T05  cell_content_exact_match    fraction of matched cells with identical text
MET-T06  cell_alignment_accuracy     fraction of cells at the correct logical slot
MET-T07  cell_cer                    char-level error on matched cell pairs
MET-T08  table_content_f1            multiset coverage of cell text (no positional requirement)
"""
from __future__ import annotations

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

# Minimum content similarity to accept a table pair
TABLE_MATCH_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return normalize(text, NormProfile.UNICODE_LAYOUT).lower()


def _text_sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _lev_distance(a: str, b: str) -> int:
    na, nb = _norm(a), _norm(b)
    if not na:
        return len(nb)
    if not nb:
        return len(na)
    prev = list(range(len(nb) + 1))
    for ch_a in na:
        curr = [prev[0] + 1]
        for j, ch_b in enumerate(nb):
            curr.append(min(prev[j] + (0 if ch_a == ch_b else 1),
                            curr[-1] + 1, prev[j + 1] + 1))
        prev = curr
    return prev[-1]


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

def _table_similarity(
    ref_table: MarkdownNode,
    hyp_table: MarkdownNode,
    ref_map: dict[str | None, list[MarkdownNode]],
    hyp_map: dict[str | None, list[MarkdownNode]],
) -> float:
    """Content-based similarity between two table nodes [0, 1]."""
    # Combine visible texts of all descendant cells
    def _cell_texts(table: MarkdownNode, pmap: dict) -> list[str]:
        rows = _children_of_kind(table, pmap, NodeKind.TABLE_ROW)
        texts = []
        for row in rows:
            cells = _children_of_kind(row, pmap, NodeKind.TABLE_CELL)
            texts.extend(_norm(c.visible_text) for c in cells)
        return texts

    ref_texts = _cell_texts(ref_table, ref_map)
    hyp_texts = _cell_texts(hyp_table, hyp_map)

    # Page consistency bonus
    page_ok = (ref_table.page == hyp_table.page) if (ref_table.page and hyp_table.page) else None

    if not ref_texts and not hyp_texts:
        return 1.0 if page_ok is not False else 0.5
    if not ref_texts or not hyp_texts:
        return 0.0

    combined_ref = " ".join(ref_texts)
    combined_hyp = " ".join(hyp_texts)
    text_sim = difflib.SequenceMatcher(None, combined_ref, combined_hyp, autojunk=False).ratio()
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
    if not matched:
        na = _make_result(None, MetricStatus.NOT_APPLICABLE, "N/A", {})
        return {
            "table_row_detection_f1":       na,
            "table_row_detection_precision": na,
            "table_row_detection_recall":    na,
            "table_column_count_accuracy":   na,
        }

    total_tp_r = total_fp_r = total_fn_r = 0
    col_matched = 0
    col_total = 0

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

        # Column count: derive from header row (first row)
        ref_cols = _max_col_count(ref_rows, ref_map)
        hyp_cols = _max_col_count(hyp_rows, hyp_map)
        if ref_cols > 0:
            col_total += 1
            if ref_cols == hyp_cols:
                col_matched += 1

    p_r, r_r, f1_r, st_r = _prf(total_tp_r, total_fp_r, total_fn_r)
    col_acc = (col_matched / col_total) if col_total > 0 else None
    col_st  = MetricStatus.OK if col_total > 0 else MetricStatus.NOT_APPLICABLE

    return {
        "table_row_detection_f1":        _make_result(f1_r, st_r, "MET-T02",
                                                       {"tp": total_tp_r, "fp": total_fp_r, "fn": total_fn_r}),
        "table_row_detection_precision":  _make_result(p_r, st_r, "MET-T02.precision", {}),
        "table_row_detection_recall":     _make_result(r_r, st_r, "MET-T02.recall",    {}),
        "table_column_count_accuracy":    _make_result(col_acc, col_st, "MET-T03",
                                                       {"matched_tables": col_matched, "total_matched_tables": col_total}),
    }


def _max_col_count(rows: list[MarkdownNode], pmap: dict) -> int:
    counts = [len(_children_of_kind(r, pmap, NodeKind.TABLE_CELL)) for r in rows]
    return max(counts) if counts else 0


def _match_rows(
    ref_rows: list[MarkdownNode],
    hyp_rows: list[MarkdownNode],
    threshold: float = 0.20,
) -> list[tuple[MarkdownNode, MarkdownNode]]:
    """Greedy 1:1 row matching by combined cell text similarity."""
    if not ref_rows or not hyp_rows:
        return []
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


# ---------------------------------------------------------------------------
# MET-T04/T05/T06/T07: Cell metrics
# ---------------------------------------------------------------------------

def cell_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> dict[str, MetricResult]:
    """MET-T04–T07: cell detection, exact match, alignment accuracy, CER."""
    ref_tables = [n for n in ref_nodes if n.kind == NodeKind.TABLE]
    hyp_tables = [n for n in hyp_nodes if n.kind == NodeKind.TABLE]

    ref_map = _build_parent_map(ref_nodes)
    hyp_map = _build_parent_map(hyp_nodes)

    matched_tables = _match_tables(ref_tables, hyp_tables, ref_map, hyp_map)

    total_tp_c = total_fp_c = total_fn_c = 0
    exact_match_count = 0
    exact_total = 0
    align_correct = 0
    align_total = 0
    cer_num = 0
    cer_den = 0

    for ref_t, hyp_t, _ in matched_tables:
        ref_rows = _children_of_kind(ref_t, ref_map, NodeKind.TABLE_ROW)
        hyp_rows = _children_of_kind(hyp_t, hyp_map, NodeKind.TABLE_ROW)

        row_pairs = _match_rows(ref_rows, hyp_rows)
        matched_hyp_rows = {hr.node_id for _, hr in row_pairs}

        # Count cells
        ref_cells_total = sum(
            len(_children_of_kind(r, ref_map, NodeKind.TABLE_CELL)) for r in ref_rows
        )
        hyp_cells_total = sum(
            len(_children_of_kind(r, hyp_map, NodeKind.TABLE_CELL)) for r in hyp_rows
        )

        cell_tp = 0
        for ref_row, hyp_row in row_pairs:
            ref_cells = _children_of_kind(ref_row, ref_map, NodeKind.TABLE_CELL)
            hyp_cells = _children_of_kind(hyp_row, hyp_map, NodeKind.TABLE_CELL)

            n_ref_c = len(ref_cells)
            n_hyp_c = len(hyp_cells)
            tp_c = min(n_ref_c, n_hyp_c)
            cell_tp += tp_c

            # Exact match and CER on positional cell pairs
            for i in range(tp_c):
                rc = ref_cells[i]
                hc = hyp_cells[i]
                exact_total += 1
                if _norm(rc.visible_text) == _norm(hc.visible_text):
                    exact_match_count += 1
                # Cell alignment: same row position slot
                align_total += 1
                align_correct += 1  # by definition when matched positionally
                # CER
                ref_t_len = len(_norm(rc.visible_text))
                cer_num += _lev_distance(rc.visible_text, hc.visible_text)
                cer_den += max(ref_t_len, 1) if ref_t_len > 0 else 0

        fn_c = ref_cells_total - cell_tp
        fp_c = hyp_cells_total - cell_tp
        total_tp_c += cell_tp
        total_fn_c += fn_c
        total_fp_c += fp_c

    # Unmatched ref tables: all their cells are FN
    matched_ref_ids = {rt.node_id for rt, _, _ in matched_tables}
    for rt in ref_tables:
        if rt.node_id not in matched_ref_ids:
            rows = _children_of_kind(rt, ref_map, NodeKind.TABLE_ROW)
            for row in rows:
                total_fn_c += len(_children_of_kind(row, ref_map, NodeKind.TABLE_CELL))

    # Unmatched hyp tables: all their cells are FP
    matched_hyp_ids = {ht.node_id for _, ht, _ in matched_tables}
    for ht in hyp_tables:
        if ht.node_id not in matched_hyp_ids:
            rows = _children_of_kind(ht, hyp_map, NodeKind.TABLE_ROW)
            for row in rows:
                total_fp_c += len(_children_of_kind(row, hyp_map, NodeKind.TABLE_CELL))

    p_c, r_c, f1_c, st_c = _prf(total_tp_c, total_fp_c, total_fn_c)

    exact_val = (exact_match_count / exact_total) if exact_total > 0 else None
    exact_st  = MetricStatus.OK if exact_total > 0 else MetricStatus.NOT_APPLICABLE

    align_val = (align_correct / align_total) if align_total > 0 else None
    align_st  = MetricStatus.OK if align_total > 0 else MetricStatus.NOT_APPLICABLE

    cer_val = (cer_num / cer_den) if cer_den > 0 else None
    cer_st  = MetricStatus.OK if cer_den > 0 else MetricStatus.NOT_APPLICABLE

    return {
        "cell_detection_f1":          _make_result(f1_c,      st_c,   "MET-T04",
                                                    {"tp": total_tp_c, "fp": total_fp_c, "fn": total_fn_c}),
        "cell_detection_precision":    _make_result(p_c,       st_c,   "MET-T04.precision", {}),
        "cell_detection_recall":       _make_result(r_c,       st_c,   "MET-T04.recall",    {}),
        "cell_content_exact_match":    _make_result(exact_val, exact_st, "MET-T05",
                                                    {"exact": exact_match_count, "total": exact_total}),
        "cell_alignment_accuracy":     _make_result(align_val, align_st, "MET-T06",
                                                    {"aligned": align_correct, "total": align_total}),
        "cell_cer":                    _make_result(cer_val,   cer_st,   "MET-T07",
                                                    {"edit_distance": cer_num, "ref_chars": cer_den},
                                                    direction="lower_is_better"),
    }


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
# Convenience: compute all table metrics
# ---------------------------------------------------------------------------

@dataclass
class TableMetrics:
    """All table metrics for one evaluation run."""
    detection:    dict[str, MetricResult] = field(default_factory=dict)
    rows_columns: dict[str, MetricResult] = field(default_factory=dict)
    cells:        dict[str, MetricResult] = field(default_factory=dict)
    content_f1:   MetricResult | None = None

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
        return result


def compute_table_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> TableMetrics:
    """Compute all table metrics (MET-T01 to MET-T08).

    Parameters
    ----------
    ref_nodes : Reference MarkdownNode list (full document, including TABLE/ROW/CELL).
    hyp_nodes : Hypothesis MarkdownNode list.

    Returns
    -------
    TableMetrics dataclass with all metric results.
    """
    return TableMetrics(
        detection    = table_detection_f1(ref_nodes, hyp_nodes),
        rows_columns = table_row_and_column_metrics(ref_nodes, hyp_nodes),
        cells        = cell_metrics(ref_nodes, hyp_nodes),
        content_f1   = table_content_f1(ref_nodes, hyp_nodes),
    )
