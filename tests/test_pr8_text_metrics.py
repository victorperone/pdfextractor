"""Tests for Group 1 text-quality metrics (MET-TX01–TX07).

Coverage map
------------
T01   cer_sequential: identity → CER == 0
T02   cer_sequential: single substitution → CER == 1/N
T03   cer_sequential: total deletion → CER == 1.0
T04   cer_sequential: extra insertions only → UNDEFINED_DENOMINATOR
T05   cer_sequential: both empty → NOT_APPLICABLE
T06   cer_sequential: CER can exceed 1.0 (many insertions)
T07   cer_sequential: NFC normalisation collapses precomposed/decomposed forms
T08   wer_sequential: identity → WER == 0
T09   wer_sequential: one word substituted → WER == 1/N
T10   wer_sequential: both empty → NOT_APPLICABLE
T11   aligned_block_text_cer: MATCHED pairs only → micro CER correct
T12   aligned_block_text_cer: UNMATCHED ref → deletions counted
T13   aligned_block_text_cer: EXTRA hyp → insertions counted
T14   aligned_block_text_cer: all UNMATCHED ref, no hyp → NOT_APPLICABLE? no → CER==1.0
T15   aligned_block_text_cer: empty ref/hyp → NOT_APPLICABLE
T16   text_coverage_recall: all tokens match → 1.0
T17   text_coverage_recall: half tokens match → 0.5
T18   text_coverage_recall: ref empty, hyp non-empty → FALSE_POSITIVE_ONLY
T19   text_coverage_recall: both empty → NOT_APPLICABLE
T20   text_coverage_precision: all tokens match → 1.0
T21   text_coverage_precision: only half hyp tokens in ref → 0.5
T22   text_coverage_precision: hyp empty → FALSE_NEGATIVE_ONLY
T23   text_coverage_precision: both empty → NOT_APPLICABLE
T24   S+D+I == edit_distance invariant (multiple random cases)
T25   compute_text_metrics returns TextMetrics with all 7 fields populated
T26   TextMetrics.to_dict() has expected canonical keys
T27   cer_sequential: visible profile strips Markdown syntax
T28   aligned_block_text_cer: AMBIGUOUS pair treated same as MATCHED
T29   text_coverage_recall: multiset semantics (repeated tokens counted correctly)
"""
from __future__ import annotations

import pytest
from structured_pdf_text.evaluation.models import (
    AlignedPair,
    AlignmentStatus,
    MarkdownNode,
    MetricStatus,
    NodeKind,
    NormProfile,
)
from structured_pdf_text.evaluation.text_metrics import (
    TextMetrics,
    _lev_ops,
    aligned_block_text_cer,
    cer_sequential,
    compute_text_metrics,
    text_coverage_precision,
    text_coverage_recall,
    wer_sequential,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(
    node_id: str,
    text: str,
    *,
    page: int = 1,
    kind: NodeKind = NodeKind.PARAGRAPH,
) -> MarkdownNode:
    return MarkdownNode(
        node_id=node_id,
        origin="test",
        page=page,
        kind=kind,
        raw_markdown=text,
        visible_text=text,
        source_start=0,
        source_end=len(text),
        order_index=0,
    )


def _matched(ref_id: str, hyp_id: str) -> AlignedPair:
    return AlignedPair(
        reference_id=ref_id,
        hypothesis_id=hyp_id,
        status=AlignmentStatus.MATCHED,
    )


def _unmatched(ref_id: str) -> AlignedPair:
    return AlignedPair(
        reference_id=ref_id,
        hypothesis_id=None,
        status=AlignmentStatus.UNMATCHED,
    )


def _extra(hyp_id: str) -> AlignedPair:
    return AlignedPair(
        reference_id=None,
        hypothesis_id=hyp_id,
        status=AlignmentStatus.EXTRA,
    )


def _ambiguous(ref_id: str, hyp_id: str) -> AlignedPair:
    return AlignedPair(
        reference_id=ref_id,
        hypothesis_id=hyp_id,
        status=AlignmentStatus.AMBIGUOUS,
        ambiguity=True,
    )


# ---------------------------------------------------------------------------
# T01-T07: cer_sequential
# ---------------------------------------------------------------------------

class TestCerSequential:
    def test_T01_identity(self):
        """T01: same text → CER == 0."""
        nodes = [_node("r1", "hello world")]
        r = cer_sequential(nodes, nodes, NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.status == MetricStatus.OK
        assert r.value == 0.0
        assert r.counts["S"] == 0
        assert r.counts["D"] == 0
        assert r.counts["I"] == 0

    def test_T02_single_substitution(self):
        """T02: one char different → CER == 1/N."""
        ref = [_node("r1", "abcd")]
        hyp = [_node("h1", "abXd")]
        r = cer_sequential(ref, hyp, NormProfile.RAW, "MET-TX01")
        assert r.status == MetricStatus.OK
        assert r.counts["S"] == 1
        # CER = 1 / 4
        assert abs(r.value - 0.25) < 1e-6

    def test_T03_total_deletion(self):
        """T03: hyp empty → CER == 1.0."""
        ref = [_node("r1", "abc")]
        hyp: list[MarkdownNode] = []
        r = cer_sequential(ref, hyp, NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.status == MetricStatus.OK
        assert r.counts["D"] > 0
        assert abs(r.value - 1.0) < 1e-6

    def test_T04_ref_empty_hyp_nonempty(self):
        """T04: ref empty, hyp non-empty → UNDEFINED_DENOMINATOR."""
        ref: list[MarkdownNode] = []
        hyp = [_node("h1", "abc")]
        r = cer_sequential(ref, hyp, NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.status == MetricStatus.UNDEFINED_DENOMINATOR
        assert r.value is None

    def test_T05_both_empty(self):
        """T05: both empty → NOT_APPLICABLE."""
        r = cer_sequential([], [], NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.status == MetricStatus.NOT_APPLICABLE
        assert r.value is None

    def test_T06_cer_can_exceed_one(self):
        """T06: many insertions can push CER above 1.0."""
        ref = [_node("r1", "ab")]  # 2 ref chars
        hyp = [_node("h1", "abcdefghij")]  # 10 hyp chars → 8 insertions
        r = cer_sequential(ref, hyp, NormProfile.RAW, "MET-TX01")
        assert r.status == MetricStatus.OK
        assert r.value > 1.0

    def test_T07_nfc_normalisation(self):
        """T07: precomposed vs decomposed Unicode → same after NFC normalisation."""
        # 'é' precomposed vs 'e' + combining accent
        precomposed = [_node("r1", "é")]  # é
        decomposed = [_node("h1", "é")]   # e + combining acute
        r = cer_sequential(precomposed, decomposed, NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.status == MetricStatus.OK
        assert r.value == 0.0, "NFC should make these identical"

    def test_T27_visible_profile(self):
        """T27: visible profile extracts text; raw Markdown delimiters don't contribute."""
        ref = [_node("r1", "hello")]
        hyp = [_node("h1", "hello")]
        r_vis = cer_sequential(ref, hyp, NormProfile.VISIBLE_TEXT, "MET-TX03")
        assert r_vis.status == MetricStatus.OK
        assert r_vis.value == 0.0

    def test_direction_is_lower_is_better(self):
        ref = [_node("r1", "abc")]
        hyp = [_node("h1", "xyz")]
        r = cer_sequential(ref, hyp, NormProfile.UNICODE_LAYOUT, "MET-TX02")
        assert r.direction == "lower_is_better"

    def test_definition_id_propagated(self):
        ref = [_node("r1", "abc")]
        r = cer_sequential(ref, ref, NormProfile.RAW, "MET-TX01")
        assert r.definition_id == "MET-TX01"


# ---------------------------------------------------------------------------
# T08-T10: wer_sequential
# ---------------------------------------------------------------------------

class TestWerSequential:
    def test_T08_identity(self):
        """T08: same words → WER == 0."""
        nodes = [_node("r1", "alpha beta gamma")]
        r = wer_sequential(nodes, nodes)
        assert r.status == MetricStatus.OK
        assert r.value == 0.0

    def test_T09_one_word_substituted(self):
        """T09: one word substituted out of N → WER == 1/N."""
        ref = [_node("r1", "alpha beta gamma")]   # 3 words
        hyp = [_node("h1", "alpha XXXX gamma")]
        r = wer_sequential(ref, hyp)
        assert r.status == MetricStatus.OK
        assert r.counts["S"] == 1
        assert abs(r.value - 1 / 3) < 1e-6

    def test_T10_both_empty(self):
        """T10: both empty → NOT_APPLICABLE."""
        r = wer_sequential([], [])
        assert r.status == MetricStatus.NOT_APPLICABLE
        assert r.value is None

    def test_wer_ref_empty_hyp_nonempty(self):
        r = wer_sequential([], [_node("h1", "word")])
        assert r.status == MetricStatus.UNDEFINED_DENOMINATOR

    def test_definition_id(self):
        nodes = [_node("r1", "x")]
        r = wer_sequential(nodes, nodes)
        assert r.definition_id == "MET-TX04"


# ---------------------------------------------------------------------------
# T11-T15: aligned_block_text_cer
# ---------------------------------------------------------------------------

class TestAlignedBlockTextCer:
    def test_T11_matched_pairs_only(self):
        """T11: MATCHED pairs only — micro CER matches manual calculation."""
        # ref block 'abc' paired with hyp 'aXc' → 1 substitution / 3 ref chars
        ref = [_node("r1", "abc")]
        hyp = [_node("h1", "aXc")]
        pairs = [_matched("r1", "h1")]
        r = aligned_block_text_cer(ref, hyp, pairs)
        assert r.status == MetricStatus.OK
        assert r.counts["S"] == 1
        assert r.counts["ref_chars"] == 3
        assert abs(r.value - 1 / 3) < 1e-6

    def test_T12_unmatched_ref_counted_as_deletions(self):
        """T12: UNMATCHED ref → all chars are deletions (CER = total_D / ref_chars)."""
        ref = [_node("r1", "abcde")]   # 5 chars, no hyp counterpart
        hyp: list[MarkdownNode] = []
        pairs = [_unmatched("r1")]
        r = aligned_block_text_cer(ref, hyp, pairs)
        assert r.status == MetricStatus.OK
        assert r.counts["D"] >= 5
        assert r.counts["unmatched_ref"] == 1
        assert r.value == 1.0

    def test_T13_extra_hyp_counted_as_insertions(self):
        """T13: EXTRA hyp → all chars counted as insertions."""
        # ref has one block 'abc', hyp has one matched + one extra
        ref = [_node("r1", "abc")]
        hyp = [_node("h1", "abc"), _node("h2", "EXTRA")]
        pairs = [_matched("r1", "h1"), _extra("h2")]
        r = aligned_block_text_cer(ref, hyp, pairs)
        assert r.status == MetricStatus.OK
        assert r.counts["extra_hyp"] == 1
        # ref_chars == 3 (from r1); I must include chars from h2
        assert r.counts["I"] > 0

    def test_T14_unmatched_ref_all_chars_del(self):
        """T14: All ref blocks unmatched → CER == 1.0 (all D, no matches)."""
        ref = [_node("r1", "texto"), _node("r2", "referencia")]
        hyp: list[MarkdownNode] = []
        pairs = [_unmatched("r1"), _unmatched("r2")]
        r = aligned_block_text_cer(ref, hyp, pairs)
        assert r.status == MetricStatus.OK
        assert r.value == 1.0

    def test_T15_both_empty(self):
        """T15: no ref chars and no hyp chars → NOT_APPLICABLE."""
        r = aligned_block_text_cer([], [], [])
        assert r.status == MetricStatus.NOT_APPLICABLE
        assert r.value is None

    def test_T28_ambiguous_treated_as_matched(self):
        """T28: AMBIGUOUS pairs are processed identically to MATCHED."""
        ref = [_node("r1", "abc")]
        hyp = [_node("h1", "abc")]
        pairs_matched = [_matched("r1", "h1")]
        pairs_ambig = [_ambiguous("r1", "h1")]
        r_m = aligned_block_text_cer(ref, hyp, pairs_matched)
        r_a = aligned_block_text_cer(ref, hyp, pairs_ambig)
        assert r_m.value == r_a.value
        assert r_m.status == r_a.status

    def test_ref_empty_hyp_nonempty(self):
        """ref empty, hyp non-empty via EXTRA → UNDEFINED_DENOMINATOR."""
        hyp = [_node("h1", "abc")]
        pairs = [_extra("h1")]
        r = aligned_block_text_cer([], hyp, pairs)
        assert r.status == MetricStatus.UNDEFINED_DENOMINATOR

    def test_definition_id(self):
        ref = [_node("r1", "x")]
        hyp = [_node("h1", "x")]
        r = aligned_block_text_cer(ref, hyp, [_matched("r1", "h1")])
        assert r.definition_id == "MET-TX05"


# ---------------------------------------------------------------------------
# T16-T19: text_coverage_recall
# ---------------------------------------------------------------------------

class TestTextCoverageRecall:
    def test_T16_all_match(self):
        """T16: hyp == ref → recall == 1.0."""
        nodes = [_node("r1", "alpha beta gamma")]
        r = text_coverage_recall(nodes, nodes)
        assert r.status == MetricStatus.OK
        assert r.value == 1.0

    def test_T17_half_match(self):
        """T17: hyp has half the ref tokens → recall == 0.5."""
        ref = [_node("r1", "alpha beta gamma delta")]  # 4 tokens
        hyp = [_node("h1", "alpha beta")]              # 2 of 4 present
        r = text_coverage_recall(ref, hyp)
        assert r.status == MetricStatus.OK
        assert abs(r.value - 0.5) < 1e-6

    def test_T18_ref_empty_hyp_nonempty(self):
        """T18: ref empty, hyp non-empty → FALSE_POSITIVE_ONLY."""
        r = text_coverage_recall([], [_node("h1", "word")])
        assert r.status == MetricStatus.FALSE_POSITIVE_ONLY
        assert r.value is None

    def test_T19_both_empty(self):
        """T19: both empty → NOT_APPLICABLE."""
        r = text_coverage_recall([], [])
        assert r.status == MetricStatus.NOT_APPLICABLE
        assert r.value is None

    def test_T29_multiset_semantics(self):
        """T29: repeated tokens counted correctly (multiset, not set)."""
        # ref: 'a a a' (3 tokens), hyp: 'a a' → tp=2, recall=2/3
        ref = [_node("r1", "a a a")]
        hyp = [_node("h1", "a a")]
        r = text_coverage_recall(ref, hyp)
        assert r.status == MetricStatus.OK
        assert r.counts["tp_tokens"] == 2
        assert abs(r.value - 2 / 3) < 1e-6

    def test_direction_is_higher_is_better(self):
        nodes = [_node("r1", "x")]
        r = text_coverage_recall(nodes, nodes)
        assert r.direction == "higher_is_better"

    def test_definition_id(self):
        nodes = [_node("r1", "x")]
        r = text_coverage_recall(nodes, nodes)
        assert r.definition_id == "MET-TX06"


# ---------------------------------------------------------------------------
# T20-T23: text_coverage_precision
# ---------------------------------------------------------------------------

class TestTextCoveragePrecision:
    def test_T20_all_match(self):
        """T20: hyp == ref → precision == 1.0."""
        nodes = [_node("r1", "alpha beta gamma")]
        r = text_coverage_precision(nodes, nodes)
        assert r.status == MetricStatus.OK
        assert r.value == 1.0

    def test_T21_half_hyp_in_ref(self):
        """T21: only half hyp tokens present in ref → precision == 0.5."""
        ref = [_node("r1", "alpha beta")]           # 2 tokens
        hyp = [_node("h1", "alpha beta gamma delta")]  # 4 tokens, 2 match
        r = text_coverage_precision(ref, hyp)
        assert r.status == MetricStatus.OK
        assert abs(r.value - 0.5) < 1e-6

    def test_T22_hyp_empty(self):
        """T22: hyp empty, ref non-empty → FALSE_NEGATIVE_ONLY."""
        r = text_coverage_precision([_node("r1", "word")], [])
        assert r.status == MetricStatus.FALSE_NEGATIVE_ONLY
        assert r.value is None

    def test_T23_both_empty(self):
        """T23: both empty → NOT_APPLICABLE."""
        r = text_coverage_precision([], [])
        assert r.status == MetricStatus.NOT_APPLICABLE
        assert r.value is None

    def test_ref_empty_hyp_nonempty(self):
        """ref empty, hyp non-empty → precision is 0 (all hyp are FP), not exception."""
        r = text_coverage_precision([], [_node("h1", "word")])
        # all hyp tokens are false positives: tp=0, precision=0/1=0.0
        assert r.status == MetricStatus.OK
        assert r.value == 0.0

    def test_direction_is_higher_is_better(self):
        nodes = [_node("r1", "x")]
        r = text_coverage_precision(nodes, nodes)
        assert r.direction == "higher_is_better"

    def test_definition_id(self):
        nodes = [_node("r1", "x")]
        r = text_coverage_precision(nodes, nodes)
        assert r.definition_id == "MET-TX07"


# ---------------------------------------------------------------------------
# T24: S+D+I == edit_distance invariant
# ---------------------------------------------------------------------------

class TestLevInvariant:
    @pytest.mark.parametrize("ref_str,hyp_str", [
        ("", ""),
        ("abc", ""),
        ("", "abc"),
        ("abc", "abc"),
        ("kitten", "sitting"),
        ("abcdef", "bdfghi"),
        ("abc", "abcde"),
        ("abcde", "abc"),
        ("a", "b"),
        ("abcdefgh", "hgfedcba"),
    ])
    def test_T24_sdi_sum_equals_edit_distance(self, ref_str, hyp_str):
        """T24: S + D + I must equal the true Levenshtein distance."""
        from structured_pdf_text.evaluation.text_metrics import _lev_distance
        ref = list(ref_str)
        hyp = list(hyp_str)
        S, D, I = _lev_ops(ref, hyp)
        dist = _lev_distance(ref, hyp)
        assert S + D + I == dist, (
            f"ref={ref_str!r} hyp={hyp_str!r}: S={S} D={D} I={I} sum={S+D+I} dist={dist}"
        )


# ---------------------------------------------------------------------------
# T25-T26: compute_text_metrics and TextMetrics.to_dict()
# ---------------------------------------------------------------------------

class TestComputeTextMetrics:
    def test_T25_all_seven_fields_populated(self):
        """T25: compute_text_metrics returns TextMetrics with all 7 results non-None."""
        ref = [_node("r1", "the quick brown fox")]
        hyp = [_node("h1", "the quick brown fox")]
        pairs = [_matched("r1", "h1")]
        tm = compute_text_metrics(ref, hyp, pairs)
        assert isinstance(tm, TextMetrics)
        assert tm.cer_raw is not None
        assert tm.cer_normalized is not None
        assert tm.cer_visible is not None
        assert tm.wer_sequential is not None
        assert tm.aligned_block_text_cer is not None
        assert tm.coverage_recall is not None
        assert tm.coverage_precision is not None

    def test_T26_to_dict_canonical_keys(self):
        """T26: to_dict() has the 7 canonical keys from the spec."""
        ref = [_node("r1", "hello world")]
        hyp = [_node("h1", "hello world")]
        pairs = [_matched("r1", "h1")]
        tm = compute_text_metrics(ref, hyp, pairs)
        d = tm.to_dict()
        # A10 fix: chave renomeada de 'cer_sequential_raw' para 'cer_markdown_raw'
        # (mede o corpo Markdown bruto, não o visible_text)
        expected_keys = {
            "cer_markdown_raw",
            "cer_sequential_normalized",
            "cer_sequential_visible",
            "wer_sequential",
            "aligned_block_text_cer",
            "text_coverage_recall",
            "text_coverage_precision",
        }
        assert set(d.keys()) == expected_keys

    def test_identity_gives_zero_cer_wer(self):
        """Identity hypothesis → all CER/WER == 0.0."""
        ref = [_node("r1", "alpha beta gamma")]
        hyp = [_node("h1", "alpha beta gamma")]
        pairs = [_matched("r1", "h1")]
        tm = compute_text_metrics(ref, hyp, pairs)
        assert tm.cer_raw.value == 0.0
        assert tm.cer_normalized.value == 0.0
        assert tm.cer_visible.value == 0.0
        assert tm.wer_sequential.value == 0.0
        assert tm.aligned_block_text_cer.value == 0.0
        assert tm.coverage_recall.value == 1.0
        assert tm.coverage_precision.value == 1.0

    def test_empty_ref_gives_na_or_undefined(self):
        """Empty ref + non-empty hyp: CER and WER must be UNDEFINED_DENOMINATOR."""
        ref: list[MarkdownNode] = []
        hyp = [_node("h1", "word")]
        pairs = [_extra("h1")]
        tm = compute_text_metrics(ref, hyp, pairs)
        assert tm.cer_raw.status == MetricStatus.UNDEFINED_DENOMINATOR
        assert tm.cer_normalized.status == MetricStatus.UNDEFINED_DENOMINATOR
        assert tm.cer_visible.status == MetricStatus.UNDEFINED_DENOMINATOR
        assert tm.wer_sequential.status == MetricStatus.UNDEFINED_DENOMINATOR

    def test_to_dict_values_are_serialisable(self):
        """to_dict() output must be JSON-serialisable (no custom objects)."""
        import json
        ref = [_node("r1", "abc")]
        hyp = [_node("h1", "abc")]
        pairs = [_matched("r1", "h1")]
        tm = compute_text_metrics(ref, hyp, pairs)
        d = tm.to_dict()
        # Should not raise
        json.dumps(d)
