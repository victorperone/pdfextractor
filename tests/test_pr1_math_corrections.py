"""PR 1 — exact edit-distance direction and S/D/I invariants (MET-001, MET-002).

The legacy calculator's formula tests were removed with scripts/compute_metrics.py's
diagnostic implementation; canonical metric behaviour is covered by the
test_evaluation_* and test_pr2..8 suites.
"""
from __future__ import annotations

from structured_pdf_text.evaluation.edit_distance import (
    levenshtein_distance as _lev_distance,
    levenshtein_ops as _lev_ops,
)


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
