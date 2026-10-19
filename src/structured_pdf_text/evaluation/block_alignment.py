"""Block aligner: 1:1 matching of reference vs hypothesis MarkdownNode lists.

Algorithm (spec §4.1)
---------------------
1. Build a similarity matrix between all ref and hyp top-level blocks.
2. Run greedy score-descending assignment (Hungarian-lite) to obtain
   a 1:1 bipartite matching.
3. Mark ambiguous pairs where the second-best candidate scored within
   AMBIGUITY_MARGIN of the selected match.
4. Emit AlignedPair for every ref node (MATCHED/AMBIGUOUS/UNMATCHED)
   and every unmatched hyp node (EXTRA).
5. Detect SPLIT and MERGE provenance when one ref maps to many hyp
   or many ref map to one hyp — recorded in pair.reasons.

Similarity score (spec §4.2)
-----------------------------
score = w_text * text_sim + w_kind * kind_sim + w_page * page_sim + w_order * order_sim

  text_sim   : edit-ratio similarity on normalised visible_text
  kind_sim   : 1.0 if kinds match exactly, KIND_PARTIAL_SCORE for
               compatible types (e.g. list vs list_item)
  page_sim   : 1.0 same page, 0.0 different, 0.5 one/both have no page
  order_sim  : 1 − |norm_ref_order − norm_hyp_order| (normalised by max)

The minimum score threshold to accept a match is MIN_SCORE.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    NodeKind,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize

# ---------------------------------------------------------------------------
# Weights and thresholds
# ---------------------------------------------------------------------------

_W_TEXT  = 0.55
_W_KIND  = 0.20
_W_PAGE  = 0.15
_W_ORDER = 0.10

MIN_SCORE       = 0.30   # below this, no match is attempted
AMBIGUITY_MARGIN = 0.05  # second-best within this → pair is ambiguous
DEFAULT_MAX_ALIGNMENT_PAIRS = 250_000
MAX_SIMILARITY_TEXT_PRODUCT = 50_000_000


class AlignmentResourceLimit(RuntimeError):
    """An exact alignment cannot be produced within the declared work policy."""

# NodeKind groups: kinds within the same group get partial credit
_KIND_GROUPS: list[frozenset[NodeKind]] = [
    frozenset({NodeKind.LIST, NodeKind.LIST_ITEM}),
    frozenset({NodeKind.TABLE, NodeKind.TABLE_ROW, NodeKind.TABLE_CELL}),
    frozenset({NodeKind.PARAGRAPH, NodeKind.BLOCKQUOTE}),
]
KIND_PARTIAL_SCORE = 0.4


# ---------------------------------------------------------------------------
# Provenance detection thresholds
# ---------------------------------------------------------------------------

_SPLIT_COVERAGE  = 0.85   # hyp fragments together cover this fraction of ref text → SPLIT
_MERGE_COVERAGE  = 0.85   # ref fragments together cover this fraction of hyp text → MERGE


# ---------------------------------------------------------------------------
# Internal data
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    ref_idx: int
    hyp_idx: int
    score:   float
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def _text_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on NFC-collapsed visible text."""
    a = normalize(a, NormProfile.UNICODE_LAYOUT)
    b = normalize(b, NormProfile.UNICODE_LAYOUT)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) * len(b) > MAX_SIMILARITY_TEXT_PRODUCT:
        raise AlignmentResourceLimit(
            f"Block similarity exceeds text product budget {MAX_SIMILARITY_TEXT_PRODUCT}"
        )
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _kind_similarity(a: NodeKind, b: NodeKind) -> float:
    if a == b:
        return 1.0
    for grp in _KIND_GROUPS:
        if a in grp and b in grp:
            return KIND_PARTIAL_SCORE
    return 0.0


def _page_similarity(a: int | None, b: int | None) -> float:
    if a is None or b is None:
        return 0.5
    return 1.0 if a == b else 0.0


def _order_similarity(
    ri: int, total_ref: int,
    hi: int, total_hyp: int,
) -> float:
    """Normalised position similarity [0, 1]."""
    nr = ri / max(total_ref - 1, 1)
    nh = hi / max(total_hyp - 1, 1)
    return 1.0 - abs(nr - nh)


def compute_pair_score(
    ref: MarkdownNode,
    hyp: MarkdownNode,
    ri: int, total_ref: int,
    hi: int, total_hyp: int,
) -> tuple[float, list[str]]:
    """Return (composite_score, reasons_list)."""
    t = _text_similarity(ref.visible_text, hyp.visible_text)
    k = _kind_similarity(ref.kind, hyp.kind)
    p = _page_similarity(ref.page, hyp.page)
    o = _order_similarity(ri, total_ref, hi, total_hyp)
    score = _W_TEXT * t + _W_KIND * k + _W_PAGE * p + _W_ORDER * o

    reasons: list[str] = []
    if k == 0.0:
        reasons.append(f"kind_mismatch:{ref.kind.value}→{hyp.kind.value}")
    elif k == KIND_PARTIAL_SCORE:
        reasons.append(f"kind_compatible:{ref.kind.value}~{hyp.kind.value}")
    if p == 0.0:
        reasons.append(f"page_mismatch:{ref.page}→{hyp.page}")
    if t < 0.5:
        reasons.append(f"low_text_sim:{t:.2f}")

    return score, reasons


# ---------------------------------------------------------------------------
# Split / merge detection
# ---------------------------------------------------------------------------

def _coverage(long_text: str, short_texts: list[str]) -> float:
    """Fraction of long_text that is covered by the union of short_texts."""
    if not long_text:
        return 1.0
    combined = " ".join(normalize(s, NormProfile.UNICODE_LAYOUT) for s in short_texts)
    long_n = normalize(long_text, NormProfile.UNICODE_LAYOUT)
    if long_n == combined:
        return 1.0
    if len(long_n) * len(combined) > MAX_SIMILARITY_TEXT_PRODUCT:
        raise AlignmentResourceLimit(
            f"Provenance similarity exceeds text product budget {MAX_SIMILARITY_TEXT_PRODUCT}"
        )
    return difflib.SequenceMatcher(None, long_n, combined, autojunk=False).ratio()


def _detect_split(
    ref: MarkdownNode,
    unmatched_hyp: list[MarkdownNode],
    threshold: float = _SPLIT_COVERAGE,
) -> list[str]:
    """Return list of hyp node_ids that together cover ref (SPLIT pattern)."""
    if not unmatched_hyp:
        return []
    candidates = sorted(
        unmatched_hyp,
        key=lambda h: _text_similarity(ref.visible_text, h.visible_text),
        reverse=True,
    )[:8]
    best: list[MarkdownNode] = []
    for c in candidates:
        best.append(c)
        if _coverage(ref.visible_text, [n.visible_text for n in best]) >= threshold:
            return [n.node_id for n in best]
    return []


def _detect_merge(
    hyp: MarkdownNode,
    unmatched_ref: list[MarkdownNode],
    threshold: float = _MERGE_COVERAGE,
) -> list[str]:
    """Return list of ref node_ids that together cover hyp (MERGE pattern)."""
    if not unmatched_ref:
        return []
    candidates = sorted(
        unmatched_ref,
        key=lambda r: _text_similarity(hyp.visible_text, r.visible_text),
        reverse=True,
    )[:8]
    best: list[MarkdownNode] = []
    for c in candidates:
        best.append(c)
        if _coverage(hyp.visible_text, [n.visible_text for n in best]) >= threshold:
            return [n.node_id for n in best]
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def align_blocks(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *,
    min_score: float = MIN_SCORE,
    ambiguity_margin: float = AMBIGUITY_MARGIN,
    detect_provenance: bool = True,
    max_candidate_pairs: int = DEFAULT_MAX_ALIGNMENT_PAIRS,
) -> list[AlignedPair]:
    """Align reference and hypothesis node lists into a list of AlignedPair.

    Parameters
    ----------
    ref_nodes         : Ordered reference block list.
    hyp_nodes         : Ordered hypothesis block list.
    min_score         : Minimum composite score to accept a match.
    ambiguity_margin  : Score gap below which a pair is flagged ambiguous.
    detect_provenance : Whether to run split/merge provenance detection.

    Returns
    -------
    List of AlignedPair covering every ref (MATCHED/AMBIGUOUS/UNMATCHED)
    and every unmatched hyp (EXTRA).  Order: ref elements first (in ref
    order), then unmatched hyp elements (in hyp order).
    """
    R = list(ref_nodes)
    H = list(hyp_nodes)
    nr, nh = len(R), len(H)

    if (isinstance(max_candidate_pairs, bool) or not isinstance(max_candidate_pairs, int)
            or max_candidate_pairs < 1):
        raise ValueError("max_candidate_pairs must be a positive integer")
    if nr * nh > max_candidate_pairs:
        raise AlignmentResourceLimit(
            f"Block alignment requires {nr * nh} pairs; budget is {max_candidate_pairs}"
        )
    if nr == 0 and nh == 0:
        return []

    # ------------------------------------------------------------------ #
    # Step 1/2: score candidates once; no duplicate R × H score matrix.
    # ------------------------------------------------------------------ #
    # Collect all valid candidates above min_score
    candidates: list[_Candidate] = []
    for ri in range(nr):
        for hi in range(nh):
            sc, reasons = compute_pair_score(R[ri], H[hi], ri, nr, hi, nh)
            if sc >= min_score:
                candidates.append(_Candidate(ri, hi, sc, reasons))
    candidates.sort(key=lambda c: c.score, reverse=True)

    matched_ref: dict[int, _Candidate] = {}
    matched_hyp: set[int] = set()
    second_best: dict[int, float] = {}   # ref_idx → best rejected score

    for cand in candidates:
        ri, hi = cand.ref_idx, cand.hyp_idx
        if ri in matched_ref or hi in matched_hyp:
            # Track second-best for ambiguity
            if ri in matched_ref and hi not in matched_hyp:
                pass  # hyp still free but ref taken
            if ri not in matched_ref:
                second_best[ri] = max(second_best.get(ri, 0.0), cand.score)
            continue
        matched_ref[ri] = cand
        matched_hyp.add(hi)

    # ------------------------------------------------------------------ #
    # Step 3: build AlignedPair list
    # ------------------------------------------------------------------ #
    pairs: list[AlignedPair] = []

    unmatched_ref_nodes: list[MarkdownNode] = []
    unmatched_hyp_nodes: list[MarkdownNode] = [H[hi] for hi in range(nh) if hi not in matched_hyp]

    for ri in range(nr):
        ref = R[ri]
        if ri not in matched_ref:
            unmatched_ref_nodes.append(ref)
            pairs.append(AlignedPair(
                reference_id     = ref.node_id,
                hypothesis_id    = None,
                status           = AlignmentStatus.UNMATCHED,
                alignment_score  = 0.0,
                text_similarity  = 0.0,
                type_expected    = ref.kind,
                type_observed    = None,
                page_expected    = ref.page,
                page_observed    = None,
                reference_order  = ref.order_index,
                hypothesis_order = None,
                ambiguity        = False,
                reasons          = ["no_candidate_above_threshold"],
            ))
        else:
            cand = matched_ref[ri]
            hi   = cand.hyp_idx
            hyp  = H[hi]
            text_sim = _text_similarity(ref.visible_text, hyp.visible_text)

            is_ambiguous = (
                second_best.get(ri, 0.0) >= cand.score - ambiguity_margin
                and second_best.get(ri, 0.0) >= min_score
            )
            status = AlignmentStatus.AMBIGUOUS if is_ambiguous else AlignmentStatus.MATCHED

            pairs.append(AlignedPair(
                reference_id     = ref.node_id,
                hypothesis_id    = hyp.node_id,
                status           = status,
                alignment_score  = round(cand.score, 4),
                text_similarity  = round(text_sim, 4),
                type_expected    = ref.kind,
                type_observed    = hyp.kind,
                page_expected    = ref.page,
                page_observed    = hyp.page,
                reference_order  = ref.order_index,
                hypothesis_order = hyp.order_index,
                ambiguity        = is_ambiguous,
                reasons          = cand.reasons,
            ))

    # ------------------------------------------------------------------ #
    # Step 4: provenance (split / merge)
    # ------------------------------------------------------------------ #
    if detect_provenance and (unmatched_ref_nodes or unmatched_hyp_nodes):
        _annotate_provenance(pairs, unmatched_ref_nodes, unmatched_hyp_nodes)

    # ------------------------------------------------------------------ #
    # Step 5: EXTRA pairs for unmatched hyp nodes
    # ------------------------------------------------------------------ #
    for hyp in unmatched_hyp_nodes:
        pairs.append(AlignedPair(
            reference_id     = None,
            hypothesis_id    = hyp.node_id,
            status           = AlignmentStatus.EXTRA,
            alignment_score  = 0.0,
            text_similarity  = 0.0,
            type_expected    = None,
            type_observed    = hyp.kind,
            page_expected    = None,
            page_observed    = hyp.page,
            reference_order  = None,
            hypothesis_order = hyp.order_index,
            ambiguity        = False,
            reasons          = ["no_reference_counterpart"],
        ))

    return pairs


# ---------------------------------------------------------------------------
# Provenance annotation (mutates UNMATCHED/EXTRA pair reasons in-place)
# ---------------------------------------------------------------------------

def _annotate_provenance(
    pairs: list[AlignedPair],
    unmatched_ref: list[MarkdownNode],
    unmatched_hyp: list[MarkdownNode],
) -> None:
    """Detect SPLIT and MERGE patterns and record them in pair.reasons."""
    # Build lookup maps
    ref_by_id: dict[str, MarkdownNode] = {n.node_id: n for n in unmatched_ref}
    hyp_by_id: dict[str, MarkdownNode] = {n.node_id: n for n in unmatched_hyp}

    # Find unmatched-ref pairs (UNMATCHED status), check for SPLIT
    for pair in pairs:
        if pair.status != AlignmentStatus.UNMATCHED:
            continue
        ref_node = ref_by_id.get(pair.reference_id or "")
        if ref_node is None:
            continue
        split_ids = _detect_split(ref_node, unmatched_hyp)
        if split_ids:
            pair.reasons.append(f"split_into:{','.join(split_ids)}")

    # Find unmatched-hyp nodes (EXTRA), check for MERGE
    # We'll annotate directly when building EXTRA pairs — post-process here
    # by finding the EXTRA pairs already in the list
    for pair in pairs:
        if pair.status != AlignmentStatus.EXTRA:
            continue
        hyp_node = hyp_by_id.get(pair.hypothesis_id or "")
        if hyp_node is None:
            continue
        merge_ids = _detect_merge(hyp_node, unmatched_ref)
        if merge_ids:
            pair.reasons.append(f"merge_from:{','.join(merge_ids)}")


# ---------------------------------------------------------------------------
# Convenience summary
# ---------------------------------------------------------------------------

@dataclass
class AlignmentSummary:
    """Aggregate counts from an alignment result."""
    total_ref:        int
    total_hyp:        int
    matched:          int
    ambiguous:        int
    unmatched_ref:    int
    extra_hyp:        int
    split_detected:   int
    merge_detected:   int
    mean_score:       float
    mean_text_sim:    float

    @staticmethod
    def from_pairs(pairs: list[AlignedPair], total_ref: int, total_hyp: int) -> "AlignmentSummary":
        matched   = [p for p in pairs if p.status == AlignmentStatus.MATCHED]
        ambiguous = [p for p in pairs if p.status == AlignmentStatus.AMBIGUOUS]
        unmatched = [p for p in pairs if p.status == AlignmentStatus.UNMATCHED]
        extra     = [p for p in pairs if p.status == AlignmentStatus.EXTRA]

        scored = matched + ambiguous
        mean_score    = sum(p.alignment_score for p in scored) / len(scored) if scored else 0.0
        mean_text_sim = sum(p.text_similarity  for p in scored) / len(scored) if scored else 0.0

        splits = sum(1 for p in unmatched if any("split_into" in r for r in p.reasons))
        merges = sum(1 for p in extra     if any("merge_from" in r  for r in p.reasons))

        return AlignmentSummary(
            total_ref      = total_ref,
            total_hyp      = total_hyp,
            matched        = len(matched),
            ambiguous      = len(ambiguous),
            unmatched_ref  = len(unmatched),
            extra_hyp      = len(extra),
            split_detected = splits,
            merge_detected = merges,
            mean_score     = round(mean_score, 4),
            mean_text_sim  = round(mean_text_sim, 4),
        )

    def to_dict(self) -> dict:
        return {
            "total_ref":      self.total_ref,
            "total_hyp":      self.total_hyp,
            "matched":        self.matched,
            "ambiguous":      self.ambiguous,
            "unmatched_ref":  self.unmatched_ref,
            "extra_hyp":      self.extra_hyp,
            "split_detected": self.split_detected,
            "merge_detected": self.merge_detected,
            "mean_score":     self.mean_score,
            "mean_text_sim":  self.mean_text_sim,
        }
