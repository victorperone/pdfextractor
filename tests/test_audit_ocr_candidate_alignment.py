"""VQ-11 — Phase B scaffold: OCR candidate alignment and consensus.

Documents the expected behavior when multiple OCR candidates produce
conflicting readings for the same region. Phase B will implement
candidate-level consensus scoring; tests are xfail as specs.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


def _tok(text: str, conf: float, x0: float = 0.0, x1: float = 100.0) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(x0=x0, y0=0, x1=x1, y1=15),
        confidence=conf,
        language="pt",
        source=SourceKind.OCR_REGION,
    )


class TestOcrCandidateAlignmentUnit:
    """Basic sanity checks that run today (no xfail)."""

    def test_ocr_token_confidence_range(self):
        tok = _tok("texto", 0.95)
        assert 0.0 <= tok.confidence <= 1.0

    def test_low_confidence_token_identified(self):
        tok = _tok("garbled", 0.20)
        assert tok.confidence < 0.5

    def test_high_confidence_token_identified(self):
        tok = _tok("valor", 0.92)
        assert tok.confidence >= 0.80


@pytest.mark.xfail(reason="VQ-11 Phase B — candidate consensus not yet implemented", strict=False)
class TestOcrCandidateConsensusPhaseB:
    """Specification tests for multi-candidate consensus (Phase B)."""

    def test_majority_reading_wins(self):
        """When 2/3 candidates agree, majority reading is selected."""
        candidates = [
            [_tok("R$ 1.234,00", 0.88)],
            [_tok("R$ 1.234,00", 0.91)],
            [_tok("R$ 1234,00", 0.72)],   # outlier missing dot
        ]
        winner = _select_consensus_candidate(candidates)  # type: ignore[name-defined]
        assert winner[0].text == "R$ 1.234,00"

    def test_conflicting_sign_not_resolved_by_confidence(self):
        """A conflict involving sign change must not be resolved by confidence alone."""
        positive = [_tok("2,75%", 0.95)]
        negative = [_tok("-2,75%", 0.70)]
        # Phase B: sign conflicts must be flagged, not silently resolved.
        result = _select_consensus_candidate([positive, negative])  # type: ignore[name-defined]
        assert result is None or _has_conflict_flag(result)  # type: ignore[name-defined]

    def test_single_candidate_returned_as_is(self):
        """A single candidate needs no consensus — return it directly."""
        only = [_tok("valor único", 0.85)]
        result = _select_consensus_candidate([[only]])  # type: ignore[name-defined]
        assert result is not None

    def test_empty_candidate_list_returns_none(self):
        result = _select_consensus_candidate([])  # type: ignore[name-defined]
        assert result is None
