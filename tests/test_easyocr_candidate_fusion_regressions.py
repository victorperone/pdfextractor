"""Regression tests for candidate fusion bugs — Etapa 1.

Covers R55 (admission gate), R56 (1:N / N:1 fragment suppression),
D8 (tile scorer quality), and D9 (contribution diagnostics).
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.candidate_fusion import (
    OcrCandidateFusionEngine,
    OcrCandidateResult,
    _candidate_score,
    _can_admit_novel_token,
    _is_explained_fragment,
    _suppressed_fragment_indices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tok(text: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.90) -> OcrToken:
    return OcrToken(text, BBox(x0, y0, x1, y1), conf, "pt", SourceKind.OCR_PAGE)


def _candidate(cid: str, tokens, score: float = 0.80, family: str = "default") -> OcrCandidateResult:
    return OcrCandidateResult(cid, family, tuple(tokens), score)


def _fuse(candidates) -> list[str]:
    evidence = OcrCandidateFusionEngine().fuse(candidates)
    return [t.text for t in evidence.tokens]


# ---------------------------------------------------------------------------
# R55 — candidate-aware admission gate
# ---------------------------------------------------------------------------

class TestR55CandidateAdmissionGate:
    """Non-primary candidates with catastrophically bad scores must not inject
    isolated low-quality tokens into the fusion result."""

    def test_catastrophically_bad_candidate_cannot_insert_isolated_token(self):
        """A candidate with score << 0 must not add an isolated short token."""
        main_tok = _tok("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", 0, 0, 400, 15)
        bad_tok = _tok("8", 500, 200, 510, 215, conf=0.30)  # far from main content

        primary = _candidate("default", [main_tok], score=0.75)
        bad = _candidate("bad_variant", [bad_tok], score=-0.55)

        texts = _fuse([primary, bad])

        assert any("Valor" in t for t in texts), "main content must be present"
        assert "8" not in texts, "isolated token from catastrophically bad candidate must be rejected"

    def test_bad_single_char_token_requires_high_confidence(self):
        """Single-char tokens from non-primary candidates need confidence >= 0.70."""
        main_tok = _tok("texto principal completo", 0, 0, 200, 15)
        low_conf_char = _tok("X", 300, 100, 310, 115, conf=0.50)  # far, low conf

        primary = _candidate("default", [main_tok], score=0.75)
        secondary = _candidate("secondary", [low_conf_char], score=0.65)

        texts = _fuse([primary, secondary])

        assert "X" not in texts, "low-confidence single char from non-primary must be rejected"

    def test_high_confidence_single_char_from_good_candidate_can_be_admitted(self):
        """A single char with very high confidence from a nearby-score candidate can enter."""
        main_tok = _tok("texto", 0, 0, 50, 15)
        single_char = _tok("A", 200, 0, 215, 15, conf=0.95)  # high conf, no spatial overlap

        primary = _candidate("default", [main_tok], score=0.75)
        secondary = _candidate("secondary", [single_char], score=0.70)  # close to primary

        texts = _fuse([primary, secondary])

        # Candidate is within score_gap=0.05, so single char with 0.95 conf should be admitted
        assert "A" in texts, "high-confidence single char from good secondary candidate must be admitted"

    def test_primary_candidate_always_admitted_regardless_of_token_score(self):
        """Every token from the primary candidate must be admitted without extra gates."""
        tok1 = _tok("palavra", 0, 0, 60, 15, conf=0.20)  # low confidence
        tok2 = _tok("X", 200, 0, 210, 15, conf=0.15)  # single char, low conf

        primary = _candidate("default", [tok1, tok2], score=0.50)

        texts = _fuse([primary])

        assert "palavra" in texts
        assert "X" in texts

    def test_reliable_novel_region_from_good_secondary_is_kept(self):
        """A secondary candidate with a good score can add new spatial coverage."""
        top_tok = _tok("primeira linha", 0, 0, 100, 15, conf=0.90)
        bottom_tok = _tok("segunda linha", 0, 200, 100, 215, conf=0.88)  # no overlap

        primary = _candidate("default", [top_tok], score=0.80)
        secondary = _candidate("tile:br", [bottom_tok], score=0.75, family="tile")

        texts = _fuse([primary, secondary])

        assert any("primeira linha" in t for t in texts)
        assert any("segunda linha" in t for t in texts), "secondary must recover non-overlapping region"

    def test_far_below_primary_candidate_with_low_conf_token_rejected(self):
        """A secondary candidate far below primary score cannot add low-confidence tokens."""
        main_tok = _tok("conteúdo principal", 0, 0, 150, 15)
        far_tok = _tok("ruído", 300, 100, 360, 115, conf=0.28)  # low conf, far away

        primary = _candidate("default", [main_tok], score=0.80)
        weak = _candidate("weak", [far_tok], score=0.25)  # gap = 0.55 > 0.50

        texts = _fuse([primary, weak])

        assert "ruído" not in texts, "tokens from candidate with score_gap > 0.50 must be rejected"


# ---------------------------------------------------------------------------
# R56 — 1:N and N:1 fragment suppression
# ---------------------------------------------------------------------------

class TestR56FragmentSuppression:
    """Fusion must not duplicate content when candidates segment the same line
    at different granularities (e.g. "ABC DEF" vs "ABC" + "DEF")."""

    def test_one_span_vs_two_spans_no_duplicate(self):
        """Primary has 'ABC DEF'; secondary has 'ABC'+'DEF'. No duplication."""
        full = _tok("ABC DEF", 0, 0, 80, 10)
        left = _tok("ABC", 0, 0, 40, 10)
        right = _tok("DEF", 40, 0, 80, 10)

        primary = _candidate("default", [full], score=0.80)
        secondary = _candidate("secondary", [left, right], score=0.70, family="tile")

        texts = _fuse([primary, secondary])
        combined = " ".join(texts)

        assert "ABC DEF" in combined or ("ABC" in combined and "DEF" in combined)
        # Must not see "DEF" or "ABC" as extra standalone fragments alongside "ABC DEF"
        assert combined.count("DEF") == 1, f"DEF appears more than once: {texts!r}"
        assert combined.count("ABC") == 1, f"ABC appears more than once: {texts!r}"

    def test_two_spans_vs_one_span_no_duplicate(self):
        """Primary has 'ABC'+'DEF'; secondary has 'ABC DEF'. No duplication."""
        left = _tok("ABC", 0, 0, 40, 10)
        right = _tok("DEF", 40, 0, 80, 10)
        full = _tok("ABC DEF", 0, 0, 80, 10)

        primary = _candidate("default", [left, right], score=0.80)
        secondary = _candidate("secondary", [full], score=0.75, family="tile")

        texts = _fuse([primary, secondary])
        combined = " ".join(texts)

        # Content must appear exactly once — either as "ABC DEF" or as "ABC"+"DEF"
        # but not both.
        assert combined.count("DEF") == 1, f"DEF duplicated: {texts!r}"
        assert combined.count("ABC") == 1, f"ABC duplicated: {texts!r}"

    def test_currency_split_vs_joined_no_duplicate(self):
        """'R$ 1.234,56' (joined) vs 'R$' + '1.234,56' (split) must not duplicate."""
        joined = _tok("R$ 1.234,56", 0, 0, 110, 10)
        currency = _tok("R$", 0, 0, 30, 10)
        amount = _tok("1.234,56", 30, 0, 110, 10)

        primary = _candidate("default", [joined], score=0.82)
        secondary = _candidate("tile", [currency, amount], score=0.73, family="tile")

        texts = _fuse([primary, secondary])
        combined = " ".join(texts)

        assert "1.234,56" in combined
        assert combined.count("1.234,56") == 1, f"amount duplicated: {texts!r}"

    def test_real_adjacent_distinct_text_not_suppressed(self):
        """Truly distinct adjacent tokens must not be incorrectly suppressed."""
        tok_a = _tok("PROCESSO", 0, 0, 60, 10)
        tok_b = _tok("000123", 70, 0, 130, 10)  # no spatial overlap, different text

        primary = _candidate("default", [tok_a, tok_b], score=0.80)

        texts = _fuse([primary])

        assert "PROCESSO" in texts
        assert "000123" in texts

    def test_is_explained_fragment_detects_substring_with_overlap(self):
        """_is_explained_fragment must detect a token that is a substring of a selected span."""
        full = _tok("ABC DEF", 0, 0, 80, 10)
        fragment = _tok("ABC", 0, 0, 40, 10)

        assert _is_explained_fragment(fragment, [full])

    def test_is_explained_fragment_not_triggered_for_unrelated_spans(self):
        """_is_explained_fragment must not flag distant tokens with substring-text."""
        far_full = _tok("ABC DEF", 0, 0, 80, 10)
        distant = _tok("ABC", 500, 500, 540, 510)  # far away

        assert not _is_explained_fragment(distant, [far_full])

    def test_suppressed_fragment_indices_finds_contained_tokens(self):
        """_suppressed_fragment_indices must identify fragments inside a larger span."""
        big = _tok("ABC DEF", 0, 0, 80, 10)
        small = _tok("DEF", 40, 0, 80, 10)  # contained within big

        suppressed = _suppressed_fragment_indices([big, small])

        assert 1 in suppressed  # small (index 1) is suppressed
        assert 0 not in suppressed  # big (index 0) is kept


# ---------------------------------------------------------------------------
# D8 — tile scorer quality
# ---------------------------------------------------------------------------

class TestD8TileScorerQuality:
    """_candidate_score must penalise hallucination signals."""

    def test_replacement_chars_reduce_score(self):
        """A candidate with replacement characters must score lower than a clean one."""
        clean = [_tok("texto limpo correto", 0, 0, 100, 10, conf=0.80)]
        dirty = [_tok("texto�limpo�correto", 0, 0, 100, 10, conf=0.80)]

        assert _candidate_score(clean) > _candidate_score(dirty)

    def test_duplicate_text_reduces_score(self):
        """A candidate where every token text is duplicated must score lower."""
        unique = [_tok("palavra1", 0, 0, 60, 10), _tok("palavra2", 70, 0, 130, 10)]
        duplicated = [_tok("palavra1", 0, 0, 60, 10), _tok("palavra1", 70, 0, 130, 10)]

        assert _candidate_score(unique) > _candidate_score(duplicated)

    def test_low_confidence_ratio_reduces_score(self):
        """A candidate where most tokens have confidence < 0.5 must score lower."""
        high_conf = [_tok("a" * 20, 0, 0, 100, 10, conf=0.90) for _ in range(5)]
        low_conf = [_tok("a" * 20, 0, 0, 100, 10, conf=0.30) for _ in range(5)]

        assert _candidate_score(high_conf) > _candidate_score(low_conf)

    def test_single_char_hallucinations_reduce_score(self):
        """Candidates with many single-char tokens score lower than substantive ones."""
        substantive = [
            _tok("primeiro", 0, 0, 60, 10),
            _tok("segundo", 0, 15, 60, 25),
            _tok("terceiro", 0, 30, 60, 40),
            _tok("quarto", 0, 45, 60, 55),
            _tok("quinto", 0, 60, 60, 70),
        ]
        single_chars = [_tok(c, i * 15, 0, i * 15 + 10, 10) for i, c in enumerate("ABCDE")]

        assert _candidate_score(substantive) > _candidate_score(single_chars)

    def test_empty_candidate_returns_negative_infinity(self):
        assert _candidate_score([]) == float("-inf")

    def test_clean_tile_beats_noisy_tile(self):
        """A clean smaller candidate must beat a noisy bigger one."""
        clean = [_tok("Valor: R$ 1.234,56", 0, 0, 150, 10, conf=0.92)]
        noisy = [
            _tok("Valor:", 0, 0, 50, 10, conf=0.88),
            _tok("���", 55, 0, 75, 10, conf=0.30),
            _tok("1.234,56", 80, 0, 150, 10, conf=0.87),
            _tok("1.234,56", 80, 0, 150, 10, conf=0.87),  # duplicate
        ]

        assert _candidate_score(clean) > _candidate_score(noisy)


# ---------------------------------------------------------------------------
# D9 — contribution diagnostics
# ---------------------------------------------------------------------------

class TestD9ContributionDiagnostics:
    """OcrEvidence.contribution_counts must reflect which candidates actually
    contributed tokens to the fusion output."""

    def test_primary_only_fusion_attributes_all_tokens_to_primary(self):
        toks = [_tok("palavra", 0, 0, 60, 10), _tok("frase", 70, 0, 130, 10)]
        primary = _candidate("primary", toks, score=0.80)

        evidence = OcrCandidateFusionEngine().fuse([primary])

        assert evidence.contribution_counts.get("primary", 0) == 2

    def test_two_candidates_contributing_distinct_regions(self):
        """When two candidates cover different regions, both should show contribution."""
        tok1 = _tok("primeira", 0, 0, 80, 10, conf=0.90)
        tok2 = _tok("segunda", 0, 200, 80, 210, conf=0.88)  # different spatial area

        primary = _candidate("default", [tok1], score=0.80)
        secondary = _candidate("tile:tl", [tok2], score=0.75, family="tile")

        evidence = OcrCandidateFusionEngine().fuse([primary, secondary])

        # Both candidates contributed at least one token each
        assert evidence.contribution_counts.get("default", 0) >= 1
        assert evidence.contribution_counts.get("tile:tl", 0) >= 1

    def test_contribution_counts_sum_equals_output_token_count(self):
        """Sum of all contribution_counts must equal the number of output tokens."""
        tok1 = _tok("A", 0, 0, 20, 10)
        tok2 = _tok("B", 30, 0, 50, 10)
        tok3 = _tok("C", 0, 100, 20, 110)

        primary = _candidate("p", [tok1, tok2], score=0.80)
        secondary = _candidate("s", [tok3], score=0.75, family="tile")

        evidence = OcrCandidateFusionEngine().fuse([primary, secondary])

        total_contributions = sum(evidence.contribution_counts.values())
        assert total_contributions == len(evidence.tokens)

    def test_suppressed_fragments_not_counted_in_contributions(self):
        """Fragments suppressed by R56 must not appear in contribution_counts."""
        full = _tok("ABC DEF", 0, 0, 80, 10)
        fragment = _tok("DEF", 40, 0, 80, 10)  # contained in full

        primary = _candidate("p", [full], score=0.80)
        secondary = _candidate("s", [fragment], score=0.70, family="tile")

        evidence = OcrCandidateFusionEngine().fuse([primary, secondary])

        total_contributions = sum(evidence.contribution_counts.values())
        assert total_contributions == len(evidence.tokens)

    def test_can_admit_novel_token_rejects_catastrophic_candidate(self):
        """_can_admit_novel_token must reject tokens from catastrophic candidates."""
        bad_tok = _tok("lixo", 0, 0, 30, 10, conf=0.30)
        bad_candidate = _candidate("bad", [bad_tok], score=-0.55)

        from structured_pdf_text.ocr.candidate_fusion import _can_admit_novel_token
        result = _can_admit_novel_token(bad_tok, bad_candidate, best_score=0.80)

        assert not result

    def test_can_admit_novel_token_accepts_reliable_secondary(self):
        """_can_admit_novel_token must admit a high-quality token from a decent secondary."""
        good_tok = _tok("texto confiável", 0, 0, 120, 10, conf=0.85)
        good_candidate = _candidate("secondary", [good_tok], score=0.70)

        from structured_pdf_text.ocr.candidate_fusion import _can_admit_novel_token
        result = _can_admit_novel_token(good_tok, good_candidate, best_score=0.80)

        assert result
