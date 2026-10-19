"""Text-quality metrics for the V2 evaluation pipeline (spec §4, MET-TX*).

Metrics
-------
MET-TX01  cer_sequential_raw         CER on raw Markdown text (ref→hyp direction)
MET-TX02  cer_sequential_normalized  CER after unicode_layout normalisation
MET-TX03  cer_sequential_visible     CER on parser-extracted visible text
MET-TX04  wer_sequential             WER on normalised word tokens
MET-TX05  aligned_block_text_cer     CER per paired block (independent of position)
MET-TX06  text_coverage_recall       token-level multiset recall (no order required)
MET-TX07  text_coverage_precision    token-level multiset precision

Design notes
------------
- Direction is always ref → hyp:  D = omissions, I = extra insertions.
- S + D + I == edit_distance (invariant tested in test suite).
- No max(N, 1) shims.  ref=0 + hyp>0 → undefined_denominator; both
  empty → not_applicable.
- `aligned_block_text_cer` is computed only on MATCHED/AMBIGUOUS pairs;
  unmatched ref nodes contribute FN chars, unmatched hyp nodes contribute FP
  chars via a synthetic empty-ref / empty-hyp pair (spec §4.3).
- `text_coverage_*` use Counter intersection for multiset TP, so repeated
  words are not double-counted (spec §4.3).
- Normalisation profile is recorded in each MetricResult.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize


# ---------------------------------------------------------------------------
# Levenshtein helpers (self-contained, no external dependency)
# ---------------------------------------------------------------------------

def _lev_distance(a: list, b: list) -> int:
    """O(min(m,n)) space Levenshtein distance between two token sequences."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


def _lev_ops(ref: list, hyp: list) -> tuple[int, int, int]:
    """Return (S, D, I) via DP traceback.

    Direction: ref → hyp.
      D = tokens in ref not reproduced (omissions).
      I = tokens in hyp not in ref (insertions).
    Tie-breaking: prefer S over D+I; prefer D before I.
    Invariant: S + D + I == _lev_distance(ref, hyp).
    """
    la, lb = len(ref), len(hyp)
    if la == 0:
        return 0, 0, lb
    if lb == 0:
        return 0, la, 0

    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        dp[i][0] = i
    for j in range(lb + 1):
        dp[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j - 1] + cost,
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
            )

    S = D = I = 0
    i, j = la, lb
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            S += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            D += 1; i -= 1
        else:
            I += 1; j -= 1
    return S, D, I


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str, profile: NormProfile) -> str:
    return normalize(text, profile)


def _collect_visible_text(nodes: Sequence[MarkdownNode]) -> str:
    """Concatenate visible_text of all nodes, space-separated."""
    return " ".join(n.visible_text for n in nodes if n.visible_text.strip())


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


def _cer_result(
    ref_chars: list[str],
    hyp_chars: list[str],
    definition_id: str,
    profile: NormProfile,
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

    S, D, I = _lev_ops(ref_chars, hyp_chars)
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

    S, D, I = _lev_ops(ref_words, hyp_words)
    dist = S + D + I
    value = dist / n_ref

    return _make_result(value, MetricStatus.OK, definition_id, "lower_is_better",
                        {"S": S, "D": D, "I": I,
                         "ref_words": n_ref, "hyp_words": n_hyp,
                         "edit_distance": dist},
                        profile)


# ---------------------------------------------------------------------------
# MET-TX01 / TX02 / TX03: CER sequential
# ---------------------------------------------------------------------------

def cer_sequential(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    profile: NormProfile,
    definition_id: str,
) -> MetricResult:
    """CER on the full concatenated text under the given normalisation profile."""
    ref_text = _collect_visible_text(ref_nodes)
    hyp_text = _collect_visible_text(hyp_nodes)

    ref_n = _norm(ref_text, profile)
    hyp_n = _norm(hyp_text, profile)

    return _cer_result(list(ref_n), list(hyp_n), definition_id, profile)


# ---------------------------------------------------------------------------
# MET-TX04: WER sequential
# ---------------------------------------------------------------------------

def wer_sequential(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """WER on normalised word tokens (unicode_layout profile)."""
    profile = NormProfile.UNICODE_LAYOUT
    ref_text = _collect_visible_text(ref_nodes)
    hyp_text = _collect_visible_text(hyp_nodes)
    ref_words = _norm(ref_text, profile).split()
    hyp_words = _norm(hyp_text, profile).split()
    return _wer_result(ref_words, hyp_words, "MET-TX04", profile)


# ---------------------------------------------------------------------------
# MET-TX05: aligned block text CER
# ---------------------------------------------------------------------------

def aligned_block_text_cer(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    pairs: Sequence[AlignedPair],
) -> MetricResult:
    """CER computed per paired block, then aggregated (spec §4.3).

    - MATCHED/AMBIGUOUS pairs: CER between ref and hyp visible_text.
    - UNMATCHED ref nodes (no hyp): all ref chars counted as deletions.
    - EXTRA hyp nodes (no ref): all hyp chars counted as insertions.
    - Denominator = total reference characters across all blocks.
    - The profile is unicode_layout; page markers already excluded from nodes.
    """
    profile = NormProfile.UNICODE_LAYOUT

    # Build lookup tables
    ref_by_id = {n.node_id: n for n in ref_nodes}
    hyp_by_id = {n.node_id: n for n in hyp_nodes}

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
            S, D, I = _lev_ops(list(ref_t), list(hyp_t))
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

def text_coverage_recall(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-TX06: token-level multiset recall, order-independent (spec §4.3).

    recall = Σ min(ref_count, hyp_count) / Σ ref_count
    """
    profile = NormProfile.UNICODE_LAYOUT
    ref_text = _collect_visible_text(ref_nodes)
    hyp_text = _collect_visible_text(hyp_nodes)
    ref_tokens = Counter(_norm(ref_text, profile).split())
    hyp_tokens = Counter(_norm(hyp_text, profile).split())

    n_ref = sum(ref_tokens.values())
    n_hyp = sum(hyp_tokens.values())

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-TX06",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": 0, "hyp_tokens": 0,
                             "correct_absence": True},
                            profile)

    if n_ref == 0:
        return _make_result(None, MetricStatus.FALSE_POSITIVE_ONLY, "MET-TX06",
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
) -> MetricResult:
    """MET-TX07: token-level multiset precision, order-independent (spec §4.3).

    precision = Σ min(ref_count, hyp_count) / Σ hyp_count
    """
    profile = NormProfile.UNICODE_LAYOUT
    ref_text = _collect_visible_text(ref_nodes)
    hyp_text = _collect_visible_text(hyp_nodes)
    ref_tokens = Counter(_norm(ref_text, profile).split())
    hyp_tokens = Counter(_norm(hyp_text, profile).split())

    n_ref = sum(ref_tokens.values())
    n_hyp = sum(hyp_tokens.values())

    if n_ref == 0 and n_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-TX07",
                            "higher_is_better",
                            {"tp_tokens": 0, "ref_tokens": 0, "hyp_tokens": 0,
                             "correct_absence": True},
                            profile)

    if n_hyp == 0:
        return _make_result(None, MetricStatus.FALSE_NEGATIVE_ONLY, "MET-TX07",
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
    cer_raw:                MetricResult | None = None
    cer_normalized:         MetricResult | None = None
    cer_visible:            MetricResult | None = None
    wer_sequential:         MetricResult | None = None
    aligned_block_text_cer: MetricResult | None = None
    coverage_recall:        MetricResult | None = None
    coverage_precision:     MetricResult | None = None

    def to_dict(self) -> dict:
        result: dict = {}
        _MAP = {
            "cer_sequential_raw":        self.cer_raw,
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
    pairs: Sequence[AlignedPair],
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
    return TextMetrics(
        cer_raw=cer_sequential(
            ref_nodes, hyp_nodes,
            NormProfile.RAW, "MET-TX01",
        ),
        cer_normalized=cer_sequential(
            ref_nodes, hyp_nodes,
            NormProfile.UNICODE_LAYOUT, "MET-TX02",
        ),
        cer_visible=cer_sequential(
            ref_nodes, hyp_nodes,
            NormProfile.VISIBLE_TEXT, "MET-TX03",
        ),
        wer_sequential=wer_sequential(ref_nodes, hyp_nodes),
        aligned_block_text_cer=aligned_block_text_cer(
            ref_nodes, hyp_nodes, pairs,
        ),
        coverage_recall=text_coverage_recall(ref_nodes, hyp_nodes),
        coverage_precision=text_coverage_precision(ref_nodes, hyp_nodes),
    )
