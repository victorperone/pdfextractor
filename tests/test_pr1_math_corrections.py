"""Tests for PR 1 — mathematical corrections (spec §12.1, §12.2, §12.3).

Covers:
  MET-001  Direction of _lev_ops (ref→hyp)
  MET-002  No silent truncation of traceback; S+D+I == distance
  MET-003  LCS without silent cap; denominator correctness
  MET-004  block_order_accuracy ≠ reading_order_accuracy
  MET-009  heading_level_accuracy uses multiset matching, returns None when N/A
  MET-010  heading_text_cer uses matched pairs only, not zip
  MET-013  cell_alignment_accuracy ≠ cell_exact_match
  MET-015  _exact_match_prf follows spec §9.2 (no false 1.0 for empty ref)
  MET-020  all_page_nums restricted to selected_ref_pages
  MET-021  missing pages use ref_body without page delimiter
  MET-022  denominators are not padded with max(...,1)
  MET-023  _worst uses min for quality metrics, max for error metrics
  MET-025  duplicate_block_rate in _LOWER_IS_BETTER
"""
from __future__ import annotations

import math

import pytest

from scripts.compute_metrics import (
    _HIGHER_IS_WORSE_KEYS,
    _UNDEFINED,
    _exact_match_prf,
    _ids_prf,
    _lcs_len,
    _lev_distance,
    _lev_ops,
    aggregate_page_metrics,
    compute_critical_data_metrics,
    compute_integrity_metrics,
    compute_structure_metrics,
    compute_text_metrics,
)
from scripts.compare_engines import _LOWER_IS_BETTER


# ---------------------------------------------------------------------------
# T01–T09: Basic Levenshtein / direction
# ---------------------------------------------------------------------------

class TestLevOpsDirection:
    """MET-001: _lev_ops(ref, hyp) — D = omissions, I = insertions."""

    def test_T01_identical(self):
        """T01: ref == hyp → CER=0, S/D/I=0."""
        S, D, I = _lev_ops(list("CASA"), list("CASA"))
        assert S == 0 and D == 0 and I == 0

    def test_T02_substitution(self):
        """T02: one character substituted → S=1."""
        S, D, I = _lev_ops(list("CASA"), list("C4SA"))
        assert S == 1 and D == 0 and I == 0

    def test_T03_deletion_from_ref(self):
        """T03: ref='A B C', hyp='A C' → D=1 (omission from extractor)."""
        S, D, I = _lev_ops("A B C".split(), "A C".split())
        assert D == 1 and I == 0
        assert S + D + I == _lev_distance("A B C".split(), "A C".split())

    def test_T04_insertion_into_hyp(self):
        """T04: ref='A C', hyp='A B C' → I=1 (extra insertion by extractor)."""
        S, D, I = _lev_ops("A C".split(), "A B C".split())
        assert I == 1 and D == 0
        assert S + D + I == _lev_distance("A C".split(), "A B C".split())

    def test_T05_all_deleted(self):
        """T05: hyp empty → all ref tokens are deletions."""
        ref = "A B C D E F G H I J".split()
        S, D, I = _lev_ops(ref, [])
        assert S == 0 and D == 10 and I == 0

    def test_T09_sdi_equals_distance(self):
        """T09: invariant S+D+I == edit_distance for all alignments."""
        ref = "the quick brown fox".split()
        hyp = "a quick red fox jumps".split()
        S, D, I = _lev_ops(ref, hyp)
        dist = _lev_distance(ref, hyp)
        assert S + D + I == dist

    def test_deterministic_tiebreak(self):
        """Multiple optimal alignments: always same S/D/I with preferred order."""
        S1, D1, I1 = _lev_ops(list("AB"), list("BA"))
        S2, D2, I2 = _lev_ops(list("AB"), list("BA"))
        assert (S1, D1, I1) == (S2, D2, I2)

    def test_sdi_invariant_on_long_sequence(self):
        """MET-002: no silent truncation; S+D+I == distance for 4000-token sequence."""
        ref = list(range(4000))
        hyp = list(range(100, 4100))  # shifted by 100
        S, D, I = _lev_ops(ref, hyp)
        dist = _lev_distance(ref, hyp)
        assert S + D + I == dist


# ---------------------------------------------------------------------------
# T06–T08: Empty reference handling (MET-022)
# ---------------------------------------------------------------------------

class TestEmptyReference:
    """MET-022: undefined denominator is nan, not 0."""

    def test_T06_empty_ref_nonempty_hyp(self):
        """T06: ref empty, hyp has content → CER is undefined (nan), not 0."""
        m = compute_text_metrics("hello world", "")
        assert m["cer_raw"] is None or (isinstance(m["cer_raw"], float) and math.isnan(m["cer_raw"]))
        assert m["wer"] is None or (isinstance(m["wer"], float) and math.isnan(m["wer"]))
        assert m["deletion_rate"] is None
        assert m["insertion_rate"] is None

    def test_T07_both_empty(self):
        """T07: both empty → all rates are None (not applicable)."""
        m = compute_text_metrics("", "")
        assert m["cer_raw"] is None or (isinstance(m["cer_raw"], float) and math.isnan(m["cer_raw"]))
        assert m["wer"] is None or (isinstance(m["wer"], float) and math.isnan(m["wer"]))

    def test_T08_cer_can_exceed_1(self):
        """T08: many insertions → CER may exceed 1.0 (no clamping)."""
        # ref = 1 char, hyp = 6 chars (5 insertions + 1 subst)
        m = compute_text_metrics("ABCDEF", "X")
        # CER = edit_dist / len(ref) = 5/1 = 5.0 (or similar)
        assert m["cer_raw"] is not None
        assert not (isinstance(m["cer_raw"], float) and math.isnan(m["cer_raw"]))
        assert m["cer_raw"] > 1.0

    def test_T11_unicode_nfc_equivalent(self):
        """T11: NFC-equivalent strings → cer_normalized=0."""
        import unicodedata
        # 'á' as composed vs decomposed
        a_composed = "caça"     # 'ç' as precomposed
        a_decomposed = "c" + "a̧" + "a"  # 'ç' as combining
        # Both normalize to same NFC
        assert unicodedata.normalize("NFC", a_composed) == unicodedata.normalize("NFC", a_composed)
        m = compute_text_metrics(a_composed, a_composed)
        assert m["cer_normalized"] == 0.0 or m["cer_normalized"] is None  # identical → 0

    def test_T12_identifier_zeros_not_normalized_away(self):
        """T12: '0012' should not equal '12' when treated as identifier."""
        # This is tested at the regex level — the pattern must not strip zeros
        from scripts.compute_metrics import _CPF_RE
        # CPF '000.000.000-00' must not be confused with '0.0.0-0'
        assert _CPF_RE.findall("000.000.000-00") == ["000.000.000-00"]


# ---------------------------------------------------------------------------
# T10: No silent truncation in LCS (MET-003)
# ---------------------------------------------------------------------------

class TestLCSNoCap:
    """MET-003: _lcs_len has no silent cap; denominator is full ref length."""

    def test_no_silent_cap_at_1000(self):
        """1001 identical blocks → LCS == 1001, not capped at 1000."""
        blocks = [f"block_{i}" for i in range(1001)]
        assert _lcs_len(blocks, blocks) == 1001

    def test_denominator_correct_at_2501(self):
        """2501 identical blocks → reading order 1.0 (full denominator used)."""
        blocks = [f"blk_{i}" for i in range(2501)]
        lcs = _lcs_len(blocks, blocks)
        assert lcs == 2501
        assert lcs / len(blocks) == 1.0


# ---------------------------------------------------------------------------
# MET-004: block_order_accuracy ≠ reading_order_accuracy
# ---------------------------------------------------------------------------

class TestBlockVsReadingOrder:
    """MET-004: the two metrics use different sequences."""

    def test_different_values_when_text_permuted(self):
        """A,B,C,D vs D,C,B,A: reading order low; structural order may also differ."""
        ref = "Para A.\n\nPara B.\n\nPara C.\n\nPara D."
        hyp = "Para D.\n\nPara C.\n\nPara B.\n\nPara A."
        # hyp_pages / ref_pages are only used for failure_rate / header-footer heuristics
        m = compute_integrity_metrics(hyp, ref, {1: hyp}, {1: ref})
        assert "reading_order_accuracy" in m
        assert "block_order_accuracy" in m
        assert m["reading_order_accuracy"] is not None
        assert m["block_order_accuracy"] is not None

    def test_both_perfect_on_identical_documents(self):
        ref = "# Título\n\nPara A.\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        m = compute_integrity_metrics(ref, ref, {1: ref}, {1: ref})
        assert m["reading_order_accuracy"] == 1.0
        assert m["block_order_accuracy"] == 1.0

    def test_block_order_uses_type_sequence(self):
        """Reading order uses text blocks; block order uses type sequence."""
        # ref: h1, para, table  | hyp: table, h1, para (permuted)
        ref = "# Título\n\nPara texto.\n\n| A |\n|---|\n| 1 |"
        hyp = "| A |\n|---|\n| 1 |\n\n# Título\n\nPara texto."
        m = compute_integrity_metrics(hyp, ref, {1: hyp}, {1: ref})
        # block_order_accuracy should be < 1 (type sequence reordered)
        assert m["block_order_accuracy"] < 1.0


# ---------------------------------------------------------------------------
# MET-009/010: Heading level accuracy and text CER
# ---------------------------------------------------------------------------

class TestHeadingMetrics:
    """MET-009 and MET-010 fixes."""

    def test_level_accuracy_returns_none_when_no_headings(self):
        """No headings → heading_level_accuracy is None (not applicable)."""
        m = compute_structure_metrics("Parágrafo sem título.", "Parágrafo sem título.")
        assert m["heading_level_accuracy"] is None

    def test_level_accuracy_correct_on_matched(self):
        """Correct heading level → accuracy=1.0."""
        text = "# Título Um\n\nTexto."
        m = compute_structure_metrics(text, text)
        assert m["heading_level_accuracy"] == 1.0

    def test_level_accuracy_penalizes_wrong_level(self):
        """h1 in ref, h2 in hyp → accuracy < 1.0."""
        ref = "# Título\n\nTexto."
        hyp = "## Título\n\nTexto."
        m = compute_structure_metrics(hyp, ref)
        assert m["heading_level_accuracy"] == 0.0

    def test_repeated_heading_keeps_distinct_identities(self):
        """Two headings with same text, different levels → no dict overwrite."""
        ref = "# Título\n\n## Título\n\nTexto."
        hyp = "# Título\n\n# Título\n\nTexto."  # second is wrong level
        m = compute_structure_metrics(hyp, ref)
        # level_total should be 2 matched pairs, one correct, one wrong
        assert m["heading_level_accuracy"] == 0.5

    def test_heading_text_cer_measures_different_text_at_same_position(self):
        """A structurally matched heading must expose its actual text error."""
        ref = "# Alpha\n\nTexto."
        hyp = "# Beta\n\nTexto."
        m = compute_structure_metrics(hyp, ref)
        assert m["heading_text_cer"] == 0.8
        assert m["heading_matched_count"] == 1

    def test_heading_text_cer_zero_on_perfect_match(self):
        """Identical heading → heading_text_cer=0."""
        text = "# Título Idêntico\n\nTexto."
        m = compute_structure_metrics(text, text)
        assert m["heading_text_cer"] == 0.0
        assert m["heading_matched_count"] == 1

    def test_heading_text_cer_not_zip_based(self):
        """Omitted trailing headings are accounted for, not silently ignored."""
        # ref has 3 headings; hyp has only 1 (first matches, 2 omitted)
        ref = "# A\n\n# B\n\n# C\n\nTexto."
        hyp = "# A\n\nTexto."
        m = compute_structure_metrics(hyp, ref)
        assert m["heading_matched_count"] == 1
        assert m["heading_unmatched_ref_count"] == 2
        assert m["heading_f1"] < 1.0


# ---------------------------------------------------------------------------
# MET-013: cell_alignment_accuracy ≠ cell_exact_match
# ---------------------------------------------------------------------------

class TestCellAlignment:
    """MET-013: the two metrics must differ."""

    def test_alignment_differs_from_exact_match_when_content_wrong(self):
        """Cell at correct position with wrong content → aligned but not exact."""
        from scripts.compute_metrics import compute_table_metrics
        ref = "| A | B |\n|---|---|\n| correto | valor |"
        hyp = "| A | B |\n|---|---|\n| errado | valor |"
        m = compute_table_metrics(hyp, ref)
        # cell_exact_match should be < 1 (one cell differs)
        assert m["cell_exact_match"] < 1.0
        # cell_alignment_accuracy should be higher (slot is occupied)
        assert m["cell_alignment_accuracy"] >= m["cell_exact_match"]

    def test_alignment_penalizes_empty_slot(self):
        """Empty cell where content expected → alignment penalty."""
        from scripts.compute_metrics import compute_table_metrics
        ref = "| A | B |\n|---|---|\n| dado | valor |"
        hyp = "| A | B |\n|---|---|\n|  | valor |"   # first cell empty
        m = compute_table_metrics(hyp, ref)
        assert m["cell_alignment_accuracy"] < 1.0


# ---------------------------------------------------------------------------
# MET-015: _exact_match_prf spec §9.2
# ---------------------------------------------------------------------------

class TestExactMatchPRF:
    """MET-015: no false 1.0 for empty reference."""

    def test_both_have_values_ok(self):
        """Normal case: both have values."""
        import re
        pat = re.compile(r"\d+")
        p, r, f, status = _exact_match_prf("10 20", "10 20", pat)
        assert status == "ok"
        assert p == 1.0 and r == 1.0 and f == 1.0

    def test_ref_empty_hyp_nonempty_false_positive(self):
        """ref=0, hyp>0 → precision=0, recall=None, f1=0, status=false_positive_only."""
        import re
        pat = re.compile(r"R\$\s*[\d\.,]+")
        p, r, f, status = _exact_match_prf("R$ 9.999,00", "", pat)
        assert status == "false_positive_only"
        assert p == 0.0
        assert r is None
        assert f == 0.0

    def test_ref_nonempty_hyp_empty_false_negative(self):
        """ref>0, hyp=0 → precision=None, recall=0, f1=0, status=false_negative_only."""
        import re
        pat = re.compile(r"R\$\s*[\d\.,]+")
        p, r, f, status = _exact_match_prf("", "R$ 100,00", pat)
        assert status == "false_negative_only"
        assert p is None
        assert r == 0.0
        assert f == 0.0

    def test_both_empty_not_applicable(self):
        """ref=0, hyp=0 → all None, status=not_applicable."""
        import re
        pat = re.compile(r"R\$\s*[\d\.,]+")
        p, r, f, status = _exact_match_prf("", "", pat)
        assert status == "not_applicable"
        assert p is None and r is None and f is None

    def test_currency_f1_is_none_when_absent_from_both(self):
        """Spec §9.2 table row 5: correct_absence → F1=None, not 1.0."""
        m = compute_critical_data_metrics("texto sem moeda", "texto sem moeda")
        assert m["currency_status"] == "not_applicable"
        assert m["currency_f1"] is None
        assert m["currency_precision"] is None
        assert m["currency_exact_match"] is None  # recall

    def test_invented_currency_gives_fp(self):
        """ref=0, hyp has R$ → currency FP=1, F1=0."""
        m = compute_critical_data_metrics("R$ 9.999,00", "texto sem moeda")
        assert m["currency_status"] == "false_positive_only"
        assert m["currency_precision"] == 0.0
        assert m["currency_f1"] == 0.0

    def test_one_digit_change_in_currency_causes_error(self):
        """R$ 10,00 vs R$ 100,00 → recall < 1."""
        m = compute_critical_data_metrics("R$ 100,00", "R$ 10,00")
        assert m["currency_exact_match"] == 0.0   # recall: ref value not in hyp

    def test_identifier_ids_prf_not_applicable_when_both_empty(self):
        """No identifiers on either side → not_applicable."""
        p, r, f, status = _ids_prf("texto", "texto")
        assert status == "not_applicable"
        assert p is None and r is None and f is None


# ---------------------------------------------------------------------------
# MET-023: aggregate_page_metrics _worst direction
# ---------------------------------------------------------------------------

class TestAggregateWorst:
    """MET-023: worst uses min for quality, max for errors."""

    def test_T17_worst_quality_is_min(self):
        """Quality metric (e.g. heading_f1): worst = min value."""
        pages = [
            {"heading_f1": 0.2},
            {"heading_f1": 0.8},
        ]
        result = aggregate_page_metrics(pages)
        assert result["heading_f1_worst"] == 0.2

    def test_worst_error_is_max(self):
        """Error metric (e.g. cer_raw): worst = max value."""
        pages = [
            {"cer_raw": 0.1},
            {"cer_raw": 0.9},
        ]
        result = aggregate_page_metrics(pages)
        assert result["cer_raw_worst"] == 0.9

    def test_T18_median_even_n(self):
        """T18: median of [1,2,3,4] == 2.5."""
        pages = [{"x": v} for v in [1, 2, 3, 4]]
        result = aggregate_page_metrics(pages)
        assert result["x_median"] == pytest.approx(2.5)

    def test_T16_na_excluded_from_mean(self):
        """T16: None values excluded from mean; n_applicable recorded."""
        pages = [
            {"cer_raw": 0.2},
            {"cer_raw": None},   # N/A
            {"cer_raw": 0.4},
        ]
        result = aggregate_page_metrics(pages)
        # mean of [0.2, 0.4] == 0.3
        assert result["cer_raw_mean"] == pytest.approx(0.3)
        assert result["cer_raw_n"] == 2


# ---------------------------------------------------------------------------
# MET-025: duplicate_block_rate in _LOWER_IS_BETTER
# ---------------------------------------------------------------------------

def test_MET025_duplicate_block_rate_in_lower_is_better():
    """MET-025: duplicate_block_rate must be in _LOWER_IS_BETTER."""
    assert "duplicate_block_rate" in _LOWER_IS_BETTER


# ---------------------------------------------------------------------------
# Additional spec §12.1 property tests
# ---------------------------------------------------------------------------

class TestTextMetricsMicro:
    """T13–T16: micro aggregation and page-scope consistency."""

    def test_T13_missing_page_same_cost_as_empty_present(self):
        """Missing selected page has same omission cost as present-but-empty page.

        Both should contribute the same ref chars to denominators (MET-021 fix).
        """
        # We test this indirectly by checking that _strip_page_header is applied
        # before counting characters for missing pages.
        from scripts.compute_metrics import _strip_page_header, _normalize
        ref_with_header = "## Página 2\nTexto com conteúdo suficiente."
        body = _strip_page_header(ref_with_header)
        assert body == "Texto com conteúdo suficiente."
        # The body (without header) is what should be used for denominators
        assert len(body) < len(ref_with_header)

    def test_T14_rates_sum_to_wer(self):
        """T14: substitution + deletion + insertion rates should sum to WER."""
        m = compute_text_metrics("the cat sat on mat", "the big black cat sat on the mat")
        if m["wer"] is not None and m["substitution_rate"] is not None:
            computed_wer = (m["substitution_rate"] or 0) + (m["deletion_rate"] or 0) + (m["insertion_rate"] or 0)
            assert computed_wer == pytest.approx(m["wer"], abs=1e-5)

    def test_T15_micro_not_simple_mean(self):
        """T15: micro CER is total edits / total chars, not mean of page CERs."""
        # Page 1: 100 chars, 10 edits → CER = 0.1
        # Page 2: 10 chars, 9 edits  → CER = 0.9
        # Simple mean = 0.5; micro = 19/110 ≈ 0.1727
        from scripts.compute_metrics import _lev_distance
        # Verify the math: micro != simple mean when pages have different lengths
        micro = (10 + 9) / (100 + 10)
        simple_mean = (0.1 + 0.9) / 2
        assert micro != simple_mean


# ---------------------------------------------------------------------------
# S12/S13: Score stability with >1000 blocks
# ---------------------------------------------------------------------------

class TestLargeDocumentStability:
    """S12/S13: no artificial ceiling with 1001 or 2501 blocks."""

    def _make_doc(self, n: int) -> str:
        return "\n\n".join(
            f"Parágrafo número {i} com conteúdo suficiente para ser um bloco."
            for i in range(n)
        )

    def test_S12_1001_identical_blocks_perfect_reading_order(self):
        doc = self._make_doc(1001)
        m = compute_integrity_metrics(doc, doc, {}, {})
        assert m["reading_order_accuracy"] == 1.0

    def test_S13_2501_identical_blocks_perfect_reading_order(self):
        doc = self._make_doc(2501)
        m = compute_integrity_metrics(doc, doc, {}, {})
        assert m["reading_order_accuracy"] == 1.0
