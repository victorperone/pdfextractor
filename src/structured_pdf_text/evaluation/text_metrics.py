"""Text-quality metrics for the unified evaluation pipeline (spec §4, MET-TX*).

Metrics
-------
MET-TX01  cer_markdown_raw           CER on raw Markdown body (no page markers, no visible extraction)
MET-TX02  cer_sequential_normalized  CER after unicode_layout normalisation of visible text
MET-TX03  cer_sequential_visible     same projection as TX02 (parser text is already visible); copied
MET-TX04  wer_sequential             WER on normalised word tokens (leaf nodes only)
MET-TX05  aligned_block_text_cer     CER per paired block (independent of position)
MET-TX06  text_coverage_recall       token-level multiset recall (no order required)
MET-TX07  text_coverage_precision    token-level multiset precision

Design notes
------------
- Direction is always ref → hyp:  D = omissions, I = extra insertions.
- S + D + I == edit_distance (invariant tested in test suite).
- No max(N, 1) shims.  ref=0 + hyp>0 → undefined_denominator; both
  empty → not_applicable.
- ``cer_markdown_raw`` (MET-TX01) operates on the raw Markdown body strings,
  NOT on visible_text extracted from nodes.  This measures representation
  fidelity including formatting markers.
- ``cer_sequential_visible`` and WER operate on the deduplicated leaf-node
  projection from CanonicalDocument: TABLE, TABLE_ROW, LIST container nodes
  are excluded so their text is not counted multiple times (bug A02 fix).
- `aligned_block_text_cer` uses the aligner's leaf pairs (MATCHED/AMBIGUOUS);
  unmatched ref nodes contribute FN chars, unmatched hyp nodes contribute FP
  chars via a synthetic empty-ref / empty-hyp pair (spec §4.3).
- `text_coverage_*` use Counter intersection for multiset TP, so repeated
  words are not double-counted (spec §4.3).
- Normalisation profile is recorded in each MetricResult.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NodeKind,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize

# Import here to avoid circular imports; canonical_document imports models/normalization only.
from structured_pdf_text.evaluation.canonical_document import CanonicalDocument


# Shared exact implementation; aliases retain the existing low-level API.
from .edit_distance import (
    DEFAULT_MAX_EDIT_CELLS, EditDistanceResourceLimit, EditDistanceBudget,
    levenshtein_distance as _lev_distance, levenshtein_ops as _lev_ops,
)
from .block_alignment import align_blocks, AlignmentResourceLimit


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str, profile: NormProfile) -> str:
    # Already projected text must not undergo a second Markdown extraction.
    effective = NormProfile.UNICODE_LAYOUT if profile == NormProfile.VISIBLE_TEXT else profile
    return normalize(text, effective)


def _collect_visible_text(nodes: Sequence[MarkdownNode]) -> str:
    """Deduplicated visible text: uses CanonicalDocument leaf projection.

    Container nodes (TABLE, TABLE_ROW, LIST) are excluded so their text is
    not counted multiple times alongside their children (bug A02 fix).
    """
    doc = CanonicalDocument.from_nodes(nodes)
    return doc.visible_text


def _node_markdown_repr(node: MarkdownNode) -> str:
    """Return the raw Markdown text for a node.

    When raw_markdown is populated by the parser, use it directly.
    When it is empty (e.g. HEADING tokens whose tok.map is None), reconstruct
    a synthetic Markdown representation that preserves formatting markers so
    that cer_markdown_raw can still distinguish `# H` from `## H`.
    """
    if node.raw_markdown.strip():
        return node.raw_markdown
    # Reconstruct: use kind + heading_level + visible_text
    if node.kind == NodeKind.HEADING:
        level = node.heading_level or 1
        return f"{'#' * level} {node.visible_text}"
    return node.visible_text


def _collect_raw_markdown(nodes: Sequence[MarkdownNode]) -> str:
    """Use retained source bodies; fabricate Markdown only for synthetic nodes."""
    doc = CanonicalDocument.from_nodes(nodes)
    if any("page_markdown" in n.metadata for n in nodes):
        return doc.raw_markdown
    # Compatibility for fabricated nodes that have no source document.
    parts = [_node_markdown_repr(n) for n in doc.leaf_nodes]
    return "\n".join(p for p in parts if p.strip())


def _make_result(
    value: float | None,
    status: MetricStatus,
    definition_id: str,
    direction: str,
    counts: dict,
    profile: NormProfile | None = None,
    coverage: float | None = None,
) -> MetricResult:
    return MetricResult(
        value=round(value, 6) if value is not None else None,
        status=status,
        direction=direction,
        unit="ratio",
        definition_id=definition_id,
        counts=counts,
        scope="document",
        normalization_profile=profile.value if profile else None,
        coverage=coverage,
    )


def _resource_result(definition_id: str, profile: NormProfile, counts: dict, error: Exception) -> MetricResult:
    result = _make_result(None, MetricStatus.RESOURCE_LIMIT, definition_id,
                          "lower_is_better", counts, profile)
    result.warnings = [str(error)]
    return result


def _cer_result(
    ref_chars: Sequence[str],
    hyp_chars: Sequence[str],
    definition_id: str,
    profile: NormProfile,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> MetricResult:
    """Build a CER MetricResult for two character sequences."""
    n_ref = len(ref_chars)
    n_hyp = len(hyp_chars)

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, definition_id,
                            "lower_is_better",
                            {"S": 0, "D": 0, "I": 0, "ref_chars": 0, "hyp_chars": 0},
                            profile)

    if n_ref == 0:
        # pure insertions — denominator undefined
        S, D, I = _lev_ops(ref_chars, hyp_chars)
        return _make_result(None, MetricStatus.UNDEFINED_DENOMINATOR, definition_id,
                            "lower_is_better",
                            {"S": S, "D": D, "I": I, "ref_chars": 0, "hyp_chars": n_hyp},
                            profile)

    try:
        S, D, I = _lev_ops(ref_chars, hyp_chars, max_cells=max_edit_cells)
    except EditDistanceResourceLimit as exc:
        return _resource_result(definition_id, profile,
            {"ref_chars": n_ref, "hyp_chars": n_hyp,
             "max_edit_cells": exc.max_cells, "evaluated_cells": exc.evaluated_cells}, exc)
    dist = S + D + I
    value = dist / n_ref

    return _make_result(value, MetricStatus.OK, definition_id, "lower_is_better",
                        {"S": S, "D": D, "I": I,
                         "ref_chars": n_ref, "hyp_chars": n_hyp,
                         "edit_distance": dist},
                        profile)


def _wer_result(
    ref_words: list[str],
    hyp_words: list[str],
    definition_id: str,
    profile: NormProfile,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> MetricResult:
    """Build a WER MetricResult for two word sequences."""
    n_ref = len(ref_words)
    n_hyp = len(hyp_words)

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, definition_id,
                            "lower_is_better",
                            {"S": 0, "D": 0, "I": 0, "ref_words": 0, "hyp_words": 0},
                            profile)

    if n_ref == 0:
        S, D, I = _lev_ops(ref_words, hyp_words)
        return _make_result(None, MetricStatus.UNDEFINED_DENOMINATOR, definition_id,
                            "lower_is_better",
                            {"S": S, "D": D, "I": I, "ref_words": 0, "hyp_words": n_hyp},
                            profile)

    try:
        S, D, I = _lev_ops(ref_words, hyp_words, max_cells=max_edit_cells)
    except EditDistanceResourceLimit as exc:
        return _resource_result(definition_id, profile,
            {"ref_words": n_ref, "hyp_words": n_hyp,
             "max_edit_cells": exc.max_cells, "evaluated_cells": exc.evaluated_cells}, exc)
    dist = S + D + I
    value = dist / n_ref

    return _make_result(value, MetricStatus.OK, definition_id, "lower_is_better",
                        {"S": S, "D": D, "I": I,
                         "ref_words": n_ref, "hyp_words": n_hyp,
                         "edit_distance": dist},
                        profile)


# ---------------------------------------------------------------------------
# MET-TX01: CER on raw Markdown body (not visible text)
# ---------------------------------------------------------------------------

def cer_markdown_raw(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *, ref_markdown: str | None = None, hyp_markdown: str | None = None,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> MetricResult:
    """MET-TX01: CER on raw Markdown body strings (bug A10 fix).

    Explicit body strings take precedence. Otherwise use source bodies retained
    by the parser, including formatting and blank lines. Fabricated nodes have
    a compatibility fallback. Delimiters must be stripped by the caller.
    NFC + CRLF normalisation applied; no Markdown syntax removal.
    """
    profile = NormProfile.RAW
    ref_text = _collect_raw_markdown(ref_nodes) if ref_markdown is None else ref_markdown
    hyp_text = _collect_raw_markdown(hyp_nodes) if hyp_markdown is None else hyp_markdown
    ref_n = _norm(ref_text, profile)
    hyp_n = _norm(hyp_text, profile)
    return _cer_result(ref_n, hyp_n, "MET-TX01", profile, max_edit_cells)


# ---------------------------------------------------------------------------
# MET-TX02 / TX03: CER sequential on visible text (leaf projection)
# ---------------------------------------------------------------------------

def cer_sequential(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    profile: NormProfile,
    definition_id: str,
    *, max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
    ref_text: str | None = None, hyp_text: str | None = None,
) -> MetricResult:
    """CER on deduplicated visible text under the given normalisation profile.

    Uses CanonicalDocument leaf projection so container nodes (TABLE, LIST)
    do not duplicate their children's text (bug A02 fix). Precomputed
    projections may be supplied by the caller.
    """
    if ref_text is None:
        ref_text = _collect_visible_text(ref_nodes)
    if hyp_text is None:
        hyp_text = _collect_visible_text(hyp_nodes)

    ref_n = _norm(ref_text, profile)
    hyp_n = _norm(hyp_text, profile)

    return _cer_result(ref_n, hyp_n, definition_id, profile, max_edit_cells)


# ---------------------------------------------------------------------------
# MET-TX04: WER sequential
# ---------------------------------------------------------------------------

def wer_sequential(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *, max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
    ref_words: list[str] | None = None, hyp_words: list[str] | None = None,
) -> MetricResult:
    """WER on normalised word tokens (unicode_layout profile)."""
    profile = NormProfile.UNICODE_LAYOUT
    if ref_words is None:
        ref_words = _visible_tokens(ref_nodes)
    if hyp_words is None:
        hyp_words = _visible_tokens(hyp_nodes)
    return _wer_result(ref_words, hyp_words, "MET-TX04", profile, max_edit_cells)


# ---------------------------------------------------------------------------
# MET-TX05: aligned block text CER
# ---------------------------------------------------------------------------

def aligned_block_text_cer(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    pairs: Sequence[AlignedPair] | None,
    *, max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
    max_alignment_pairs: int | None = None,
) -> MetricResult:
    """CER computed per paired block, then aggregated (spec §4.3).

    - MATCHED/AMBIGUOUS pairs: CER between ref and hyp visible_text.
    - UNMATCHED ref nodes (no hyp): all ref chars counted as deletions.
    - EXTRA hyp nodes (no ref): all hyp chars counted as insertions.
    - Denominator = total reference characters across all blocks.
    - The profile is unicode_layout; page markers already excluded from nodes.
    """
    profile = NormProfile.UNICODE_LAYOUT

    ref_leaves = CanonicalDocument.from_nodes(ref_nodes).leaf_nodes
    hyp_leaves = CanonicalDocument.from_nodes(hyp_nodes).leaf_nodes
    if pairs is not None:
        # The layered aligner pairs leaves only with leaves; their pairs cover
        # every terminal text unit once. Container pairs carry no own text.
        leaf_ids = {n.node_id for n in ref_leaves} | {n.node_id for n in hyp_leaves}
        pairs = [p for p in pairs if p.reference_id in leaf_ids or p.hypothesis_id in leaf_ids]
    else:
        # Structural parents must not compete with their descendants for text.
        try:
            kwargs = {} if max_alignment_pairs is None else {"max_candidate_pairs": max_alignment_pairs}
            pairs = align_blocks(ref_leaves, hyp_leaves, **kwargs)
        except AlignmentResourceLimit as exc:
            return _resource_result("MET-TX05", profile,
                                    {"ref_blocks": len(ref_leaves), "hyp_blocks": len(hyp_leaves)}, exc)
    ref_nodes, hyp_nodes = ref_leaves, hyp_leaves

    # Build lookup tables
    ref_by_id = {n.node_id: n for n in ref_nodes}
    hyp_by_id = {n.node_id: n for n in hyp_nodes}

    # The aligner contract covers each endpoint once. Missing or duplicated
    # endpoints are an invalid computation, never a perfect zero-distance run.
    seen_ref: list[str] = []
    seen_hyp: list[str] = []
    valid = len(ref_by_id) == len(ref_nodes) and len(hyp_by_id) == len(hyp_nodes)
    for pair in pairs:
        ref_id, hyp_id = pair.reference_id, pair.hypothesis_id
        if ref_id is not None:
            seen_ref.append(ref_id)
        if hyp_id is not None:
            seen_hyp.append(hyp_id)
        valid &= (
            pair.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS)
            and ref_id is not None and hyp_id is not None
            or pair.status == AlignmentStatus.UNMATCHED and ref_id is not None and hyp_id is None
            or pair.status == AlignmentStatus.EXTRA and ref_id is None and hyp_id is not None
        )
    valid &= (len(seen_ref) == len(set(seen_ref)) and set(seen_ref) == set(ref_by_id)
              and len(seen_hyp) == len(set(seen_hyp)) and set(seen_hyp) == set(hyp_by_id))
    if not valid:
        result = _make_result(None, MetricStatus.INVALID_INPUT, "MET-TX05", "lower_is_better",
                              {"ref_blocks": len(ref_nodes), "hyp_blocks": len(hyp_nodes)}, profile)
        result.warnings = ["Text alignment must cover every terminal node exactly once"]
        return result

    work = EditDistanceBudget(max_edit_cells)

    total_S = total_D = total_I = 0
    total_ref_chars = 0
    matched_blocks = 0
    unmatched_ref = 0
    extra_hyp = 0
    coverage_eligible = 0

    for pair in pairs:
        if pair.status in (AlignmentStatus.MATCHED, AlignmentStatus.AMBIGUOUS):
            ref_node = ref_by_id.get(pair.reference_id or "")
            hyp_node = hyp_by_id.get(pair.hypothesis_id or "")
            if ref_node is None:
                continue
            ref_t = _norm(ref_node.visible_text, profile)
            hyp_t = _norm(hyp_node.visible_text if hyp_node else "", profile)
            try:
                S, D, I = _lev_ops(ref_t, hyp_t, max_cells=max_edit_cells, budget=work)
            except EditDistanceResourceLimit as exc:
                return _resource_result("MET-TX05", profile,
                    {"max_edit_cells": exc.max_cells, "evaluated_cells": exc.evaluated_cells}, exc)
            total_S += S
            total_D += D
            total_I += I
            total_ref_chars += len(ref_t)
            matched_blocks += 1
            coverage_eligible += 1

        elif pair.status == AlignmentStatus.UNMATCHED:
            # ref block with no hyp counterpart: all chars are deletions
            ref_node = ref_by_id.get(pair.reference_id or "")
            if ref_node:
                ref_t = _norm(ref_node.visible_text, profile)
                total_D += len(ref_t)
                total_ref_chars += len(ref_t)
                unmatched_ref += 1

        elif pair.status == AlignmentStatus.EXTRA:
            # hyp block with no ref counterpart: all chars are insertions
            hyp_node = hyp_by_id.get(pair.hypothesis_id or "")
            if hyp_node:
                hyp_t = _norm(hyp_node.visible_text, profile)
                total_I += len(hyp_t)
                extra_hyp += 1

    if total_ref_chars == 0:
        n_hyp_chars = sum(
            len(_norm(n.visible_text, profile)) for n in hyp_nodes
        )
        if n_hyp_chars == 0:
            return _make_result(
                None, MetricStatus.NOT_APPLICABLE, "MET-TX05",
                "lower_is_better",
                {"S": 0, "D": 0, "I": 0, "ref_chars": 0,
                 "matched_blocks": 0, "unmatched_ref": 0, "extra_hyp": 0},
                profile,
            )
        return _make_result(
            None, MetricStatus.UNDEFINED_DENOMINATOR, "MET-TX05",
            "lower_is_better",
            {"S": 0, "D": 0, "I": total_I, "ref_chars": 0,
             "matched_blocks": 0, "unmatched_ref": 0, "extra_hyp": extra_hyp},
            profile,
        )

    dist = total_S + total_D + total_I
    value = dist / total_ref_chars
    total_blocks = matched_blocks + unmatched_ref + extra_hyp
    cov = coverage_eligible / total_blocks if total_blocks > 0 else None

    return _make_result(
        value, MetricStatus.OK, "MET-TX05",
        "lower_is_better",
        {"S": total_S, "D": total_D, "I": total_I,
         "ref_chars": total_ref_chars,
         "edit_distance": dist,
         "matched_blocks": matched_blocks,
         "unmatched_ref": unmatched_ref,
         "extra_hyp": extra_hyp},
        profile,
        coverage=round(cov, 4) if cov is not None else None,
    )


# ---------------------------------------------------------------------------
# MET-TX06 / TX07: text coverage recall and precision
# ---------------------------------------------------------------------------

def _visible_tokens(nodes: Sequence[MarkdownNode]) -> list[str]:
    return _norm(_collect_visible_text(nodes), NormProfile.UNICODE_LAYOUT).split()


def text_coverage_recall(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *, ref_tokens: Counter | None = None, hyp_tokens: Counter | None = None,
) -> MetricResult:
    """MET-TX06: token-level multiset recall, order-independent (spec §4.3).

    recall = Σ min(ref_count, hyp_count) / Σ ref_count
    Uses leaf-only projection to avoid double-counting (bug A02 fix).
    Precomputed token counters may be supplied by the caller.
    """
    profile = NormProfile.UNICODE_LAYOUT
    if ref_tokens is None:
        ref_tokens = Counter(_visible_tokens(ref_nodes))
    if hyp_tokens is None:
        hyp_tokens = Counter(_visible_tokens(hyp_nodes))

    n_ref = sum(ref_tokens.values())
    n_hyp = sum(hyp_tokens.values())

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-TX06",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": 0, "hyp_tokens": 0,
                             "correct_absence": True},
                            profile)

    if n_ref == 0:
        return _make_result(None, MetricStatus.UNDEFINED_DENOMINATOR, "MET-TX06",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": 0, "hyp_tokens": n_hyp},
                            profile)

    tp = sum((ref_tokens & hyp_tokens).values())
    value = tp / n_ref

    return _make_result(value, MetricStatus.OK, "MET-TX06", "higher_is_better",
                        {"tp_tokens": tp, "ref_tokens": n_ref, "hyp_tokens": n_hyp},
                        profile)


def text_coverage_precision(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *, ref_tokens: Counter | None = None, hyp_tokens: Counter | None = None,
) -> MetricResult:
    """MET-TX07: token-level multiset precision, order-independent (spec §4.3).

    precision = Σ min(ref_count, hyp_count) / Σ hyp_count
    """
    profile = NormProfile.UNICODE_LAYOUT
    if ref_tokens is None:
        ref_tokens = Counter(_visible_tokens(ref_nodes))
    if hyp_tokens is None:
        hyp_tokens = Counter(_visible_tokens(hyp_nodes))

    n_ref = sum(ref_tokens.values())
    n_hyp = sum(hyp_tokens.values())

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-TX07",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": 0, "hyp_tokens": 0,
                             "correct_absence": True},
                            profile)

    if n_hyp == 0:
        return _make_result(None, MetricStatus.UNDEFINED_DENOMINATOR, "MET-TX07",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": n_ref, "hyp_tokens": 0},
                            profile)

    tp = sum((ref_tokens & hyp_tokens).values())
    value = tp / n_hyp

    return _make_result(value, MetricStatus.OK, "MET-TX07", "higher_is_better",
                        {"tp_tokens": tp, "ref_tokens": n_ref, "hyp_tokens": n_hyp},
                        profile)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

@dataclass
class TextMetrics:
    """All text-quality metrics for one evaluation run."""
    cer_raw:                MetricResult | None = None   # MET-TX01: raw Markdown body
    cer_normalized:         MetricResult | None = None   # MET-TX02: visible text, unicode_layout
    cer_visible:            MetricResult | None = None   # MET-TX03: visible text, visible_text profile
    wer_sequential:         MetricResult | None = None   # MET-TX04
    aligned_block_text_cer: MetricResult | None = None   # MET-TX05
    coverage_recall:        MetricResult | None = None   # MET-TX06
    coverage_precision:     MetricResult | None = None   # MET-TX07

    def to_dict(self) -> dict:
        result: dict = {}
        _MAP = {
            "cer_markdown_raw":          self.cer_raw,        # A10 fix: was cer_sequential_raw
            "cer_sequential_normalized": self.cer_normalized,
            "cer_sequential_visible":    self.cer_visible,
            "wer_sequential":            self.wer_sequential,
            "aligned_block_text_cer":    self.aligned_block_text_cer,
            "text_coverage_recall":      self.coverage_recall,
            "text_coverage_precision":   self.coverage_precision,
        }
        for key, metric in _MAP.items():
            if metric is not None:
                result[key] = metric.to_dict()
        return result


def compute_text_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    pairs: Sequence[AlignedPair] | None,
    *, ref_markdown: str | None = None, hyp_markdown: str | None = None,
    max_edit_cells: int = DEFAULT_MAX_EDIT_CELLS,
    max_alignment_pairs: int | None = None,
) -> TextMetrics:
    """Compute all Group 1 text-quality metrics (spec §4).

    Parameters
    ----------
    ref_nodes : Reference MarkdownNode list (full document).
    hyp_nodes : Hypothesis MarkdownNode list.
    pairs     : Aligned pairs from block_alignment.align_blocks().

    Returns
    -------
    TextMetrics dataclass with all metric results.
    """
    ref_text, hyp_text = _collect_visible_text(ref_nodes), _collect_visible_text(hyp_nodes)
    ref_words = _norm(ref_text, NormProfile.UNICODE_LAYOUT).split()
    hyp_words = _norm(hyp_text, NormProfile.UNICODE_LAYOUT).split()
    ref_tokens, hyp_tokens = Counter(ref_words), Counter(hyp_words)
    normalized = cer_sequential(
        ref_nodes, hyp_nodes, NormProfile.UNICODE_LAYOUT, "MET-TX02",
        max_edit_cells=max_edit_cells, ref_text=ref_text, hyp_text=hyp_text,
    )
    # Parser-visible text is already projected, so the visible profile reduces
    # to unicode_layout (see _norm): MET-TX03 is the same CER, computed once.
    visible = replace(normalized, definition_id="MET-TX03",
                      normalization_profile=NormProfile.VISIBLE_TEXT.value,
                      counts=dict(normalized.counts), warnings=list(normalized.warnings))
    return TextMetrics(
        cer_raw=cer_markdown_raw(ref_nodes, hyp_nodes,
            ref_markdown=ref_markdown, hyp_markdown=hyp_markdown, max_edit_cells=max_edit_cells),          # MET-TX01: raw body
        cer_normalized=normalized,
        cer_visible=visible,
        wer_sequential=wer_sequential(ref_nodes, hyp_nodes, max_edit_cells=max_edit_cells,
                                      ref_words=ref_words, hyp_words=hyp_words),
        aligned_block_text_cer=aligned_block_text_cer(
            ref_nodes, hyp_nodes, pairs, max_edit_cells=max_edit_cells,
            max_alignment_pairs=max_alignment_pairs,
        ),
        coverage_recall=text_coverage_recall(ref_nodes, hyp_nodes,
                                             ref_tokens=ref_tokens, hyp_tokens=hyp_tokens),
        coverage_precision=text_coverage_precision(ref_nodes, hyp_nodes,
                                                   ref_tokens=ref_tokens, hyp_tokens=hyp_tokens),
    )


# ---------------------------------------------------------------------------
# Page-anchored document view (policy page-anchored-micro-v1)
# ---------------------------------------------------------------------------

_UNAVAILABLE = (MetricStatus.RESOURCE_LIMIT.value, MetricStatus.INVALID_INPUT.value)


def _unavailable_pages(definition_id: str, profile: NormProfile, pages: list[dict]) -> MetricResult | None:
    """A page whose metric could not be computed makes the document unavailable."""
    failed = [page for page in pages if page["status"] in _UNAVAILABLE]
    if not failed:
        return None
    status = (MetricStatus.RESOURCE_LIMIT if any(p["status"] == _UNAVAILABLE[0] for p in failed)
              else MetricStatus.INVALID_INPUT)
    result = _make_result(None, status, definition_id, "lower_is_better",
                          {"unavailable_pages": len(failed)}, profile)
    result.warnings = sorted({warning for page in failed for warning in page["warnings"]})
    return result


def page_anchored_edit_rate(
    pages: Sequence[dict], definition_id: str, profile: NormProfile, unit: str,
) -> MetricResult:
    """Document CER/WER as Σ page edits / Σ page reference length.

    Pages are aligned by page number, so content moved to another page counts
    as a deletion on its page and an insertion on the other. *pages* are the
    serialized page results of the same metric.
    """
    pages = list(pages)
    unavailable = _unavailable_pages(definition_id, profile, pages)
    if unavailable is not None:
        return unavailable
    S, D, I, n_ref, n_hyp = (sum(p["counts"][key] for p in pages)
                             for key in ("S", "D", "I", f"ref_{unit}", f"hyp_{unit}"))
    lengths = {f"ref_{unit}": n_ref, f"hyp_{unit}": n_hyp}
    if n_ref == 0:
        status = MetricStatus.NOT_APPLICABLE if n_hyp == 0 else MetricStatus.UNDEFINED_DENOMINATOR
        return _make_result(None, status, definition_id, "lower_is_better",
                            {"S": S, "D": D, "I": I, **lengths}, profile)
    return _make_result((S + D + I) / n_ref, MetricStatus.OK, definition_id, "lower_is_better",
                        {"S": S, "D": D, "I": I, **lengths, "edit_distance": S + D + I}, profile)


def page_anchored_aligned_cer(pages: Sequence[dict]) -> MetricResult:
    """MET-TX05 over the union of page-local block alignments."""
    profile = NormProfile.UNICODE_LAYOUT
    pages = list(pages)
    unavailable = _unavailable_pages("MET-TX05", profile, pages)
    if unavailable is not None:
        return unavailable
    keys = ("S", "D", "I", "ref_chars", "matched_blocks", "unmatched_ref", "extra_hyp")
    totals = {key: sum(p["counts"][key] for p in pages) for key in keys}
    if totals["ref_chars"] == 0:
        # With no reference text, any hypothesis character is an insertion.
        status = MetricStatus.NOT_APPLICABLE if totals["I"] == 0 else MetricStatus.UNDEFINED_DENOMINATOR
        return _make_result(None, status, "MET-TX05", "lower_is_better", totals, profile)
    distance = totals["S"] + totals["D"] + totals["I"]
    blocks = totals["matched_blocks"] + totals["unmatched_ref"] + totals["extra_hyp"]
    return _make_result(distance / totals["ref_chars"], MetricStatus.OK, "MET-TX05", "lower_is_better",
                        {**totals, "edit_distance": distance}, profile,
                        coverage=round(totals["matched_blocks"] / blocks, 4) if blocks else None)
