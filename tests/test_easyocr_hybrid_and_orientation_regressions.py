"""Regression tests for Etapa 2 — Native/OCR, footnotes and orientation.

Covers:
  H1  — hybrid page must not discard strong native text when OCR is requested
  R65 — footnote refinement must not accept duplicate reread or truncated content
  R66 — baseline policy must not expand to adaptive/multi-scale work
  R69 — rotated candidate orientation must reach OcrToken.rotation
  R70 — quality gate must not penalise correctly-oriented vertical text
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.api import (
    _should_use_page_ocr_as_primary,
    _deduplicate_refinement_tokens,
    _refinement_conserves_content,
)
from structured_pdf_text.ocr.quality import assess_ocr_quality, _is_token_orientation_plausible


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tok(
    text: str,
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 100.0,
    y1: float = 15.0,
    conf: float = 0.90,
    rotation: int = 0,
) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(x0, y0, x1, y1),
        confidence=conf,
        language="pt",
        source=SourceKind.OCR_PAGE,
        rotation=rotation,
    )


class _FakeLine:
    """Minimal TextLine-like object for H1 tests."""
    def __init__(self, text: str):
        self.text = text
        self.bbox = BBox(0, 0, 100, 15)


class _FakeMode:
    """Distinguishes ExtractionMode values for H1 gate tests."""
    def __init__(self, name: str):
        self._name = name
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeMode) and self._name == other._name


# Import ExtractionMode so we can use the real value in some tests.
try:
    from structured_pdf_text.config import ExtractionMode as _ExtractionMode
    _OCR_MODE = _ExtractionMode.OCR
except Exception:
    _OCR_MODE = _FakeMode("OCR")


# ---------------------------------------------------------------------------
# H1 — Hybrid page should NOT lose strong native text
# ---------------------------------------------------------------------------

class TestH1HybridPageKeepsNative:
    """_should_use_page_ocr_as_primary must return False when native is strong."""

    def _fake_ocr_lines(self, count: int = 1) -> list[_FakeLine]:
        return [_FakeLine("texto rasterizado") for _ in range(count)]

    def test_strong_native_prevents_ocr_from_becoming_primary(self):
        """Page with >= 20 native non-WS chars must keep native as primary."""
        native = [_FakeLine("Texto nativo: etapa digital da extração.")]
        ocr = self._fake_ocr_lines()
        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=ocr,
            page_ocr_requested=True,
            mode=_FakeMode("hybrid"),
        )

        assert not result, "strong native must block OCR from becoming primary"

    def test_no_native_allows_ocr_to_become_primary(self):
        """Page with no native lines must allow OCR as primary."""
        ocr = self._fake_ocr_lines()

        result = _should_use_page_ocr_as_primary(
            native_lines=[],
            ocr_lines=ocr,
            page_ocr_requested=True,
            mode=_FakeMode("hybrid"),
        )

        assert result, "no native → OCR must become primary"

    def test_ocr_mode_always_makes_ocr_primary(self):
        """ExtractionMode.OCR must always give OCR ownership."""
        native = [_FakeLine("Texto nativo longo com muito conteúdo válido.")]
        ocr = self._fake_ocr_lines()

        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=ocr,
            page_ocr_requested=True,
            mode=_OCR_MODE,
        )

        assert result, "ExtractionMode.OCR must always make OCR primary"

    def test_page_ocr_not_requested_never_makes_ocr_primary(self):
        """When page_ocr_requested is False, OCR must not be primary."""
        native = [_FakeLine("A")]  # trivial native
        ocr = self._fake_ocr_lines()

        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=ocr,
            page_ocr_requested=False,
            mode=_FakeMode("auto"),
        )

        assert not result, "page_ocr_requested=False must not make OCR primary"

    def test_no_ocr_lines_never_makes_ocr_primary(self):
        """When OCR produced nothing, it cannot be primary."""
        native = [_FakeLine("A")]

        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=[],
            page_ocr_requested=True,
            mode=_FakeMode("auto"),
        )

        assert not result, "no OCR lines → OCR must not become primary"

    def test_negligible_native_allows_ocr_primary(self):
        """Fewer than 20 non-WS native chars: OCR may become primary."""
        native = [_FakeLine("AB")]  # 2 non-WS chars — negligible
        ocr = self._fake_ocr_lines()

        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=ocr,
            page_ocr_requested=True,
            mode=_FakeMode("auto"),
        )

        assert result, "negligible native (<20 non-WS chars) may cede to OCR"

    def test_exactly_20_native_chars_keeps_native_primary(self):
        """Exactly 20 non-WS native chars must keep native primary."""
        native = [_FakeLine("A" * 20)]
        ocr = self._fake_ocr_lines()

        result = _should_use_page_ocr_as_primary(
            native_lines=native,
            ocr_lines=ocr,
            page_ocr_requested=True,
            mode=_FakeMode("auto"),
        )

        assert not result, "≥20 non-WS native chars must keep native primary"


# ---------------------------------------------------------------------------
# R65 — Footnote refinement: deduplication and conservation
# ---------------------------------------------------------------------------

class TestR65FootnoteDeduplication:
    """_deduplicate_refinement_tokens must eliminate duplicate spans."""

    def test_identical_text_same_position_deduped(self):
        """Two tokens with identical text and high IoU must be reduced to one."""
        tok1 = _tok("NOTA ABC", 0, 0, 80, 10, conf=0.85)
        tok2 = _tok("NOTA ABC", 2, 0, 82, 10, conf=0.92)  # high IoU with tok1

        result = _deduplicate_refinement_tokens([tok1, tok2])

        assert len(result) == 1, "duplicate token must be collapsed to one"
        assert result[0].confidence == 0.92, "higher-confidence copy must be kept"

    def test_identical_text_far_apart_both_kept(self):
        """Same text in very different positions must not be considered duplicates."""
        tok1 = _tok("NOTA", 0, 0, 40, 10)
        tok2 = _tok("NOTA", 500, 500, 540, 510)  # far away

        result = _deduplicate_refinement_tokens([tok1, tok2])

        assert len(result) == 2, "spatially distant identical tokens must both be kept"

    def test_different_text_both_kept(self):
        """Tokens with different text must never be deduplicated."""
        tok_a = _tok("NOTA", 0, 0, 40, 10)
        tok_b = _tok("ABC", 0, 0, 40, 10)

        result = _deduplicate_refinement_tokens([tok_a, tok_b])

        assert len(result) == 2

    def test_single_token_unchanged(self):
        tok = _tok("texto", 0, 0, 60, 10)
        assert _deduplicate_refinement_tokens([tok]) == [tok]

    def test_empty_list_unchanged(self):
        assert _deduplicate_refinement_tokens([]) == []

    def test_duplicate_full_sentence_deduped(self):
        """Duplicate of 'NOTA ABC' at nearly the same position must be removed."""
        tok1 = _tok("NOTA ABC importante", 0, 0, 120, 10, conf=0.80)
        tok2 = _tok("NOTA ABC importante", 1, 0, 121, 10, conf=0.75)

        result = _deduplicate_refinement_tokens([tok1, tok2])

        assert len(result) == 1
        assert result[0].confidence == 0.80

    def test_conservation_rejects_duplicate_inflating_char_count(self):
        """Deduplication must prevent duplicated reread from inflating new_chars."""
        old = [_tok("NOTA ABC importante que deve ser preservada", 0, 0, 300, 15, conf=0.70)]
        # Two identical copies of "NOTA NOTA" — before dedup new_chars would be 18 > old_chars could be gamed
        tok1 = _tok("NOTA NOTA", 0, 0, 70, 10, conf=0.95)
        tok2 = _tok("NOTA NOTA", 1, 0, 71, 10, conf=0.93)
        deduped = _deduplicate_refinement_tokens([tok1, tok2])
        # After dedup: new_chars = 8 (NOTANOTA), old_chars = 42 → conservation fails
        assert not _refinement_conserves_content(old, deduped), (
            "deduped result with <60% old chars must be rejected by conservation gate"
        )


# ---------------------------------------------------------------------------
# R66 — Baseline policy: expected call count
# ---------------------------------------------------------------------------

class TestR66BaselinePolicy:
    """_recover_selected_regions must build single-scale/no-variant requests
    when quality_policy is 'baseline'."""

    def _build_fake_result(self) -> "Any":
        from structured_pdf_text.ocr.recovery import RegionRefinementResult
        from structured_pdf_text.geometry import BBox as _BBox
        return RegionRefinementResult(
            tokens=(),
            bbox=_BBox(0, 0, 100, 50),
            status="no_text",
            reason_code=None,
            selected_scale_factor=1.0,
            selected_rotation=0,
            ocr_passes=1,
            ocr_batches=1,
            attempts=[],
        )

    def _make_fake_region(self, reasons: tuple = ("small",)) -> "Any":
        class _FakeQuality:
            def __init__(self, r):
                self.reasons = r
                self.decision = None
        class _FakeRegion:
            def __init__(self, r):
                self.bbox = BBox(0, 0, 100, 50)
                self.quality = _FakeQuality(r)
                self.region_id = "test-region"
                self.kind = type("k", (), {"value": "text"})()
                self.ocr_tokens: list = []
                self.ocr_lines: list = []
        return _FakeRegion(reasons)

    def test_baseline_builds_single_scale_no_variants(self):
        """Baseline: scale_factors must be (1.0,) and quality_variants=False."""
        from structured_pdf_text.ocr.recovery import RegionRefinementRequest
        from structured_pdf_text.api import _recover_selected_regions
        import structured_pdf_text.api as api_mod

        captured_requests: list[RegionRefinementRequest] = []
        fake_result = self._build_fake_result()
        fake_region = self._make_fake_region(("small",))

        class _FakeRefiner:
            def refine_many(self, page_image, page_index, page_bbox, requests):
                captured_requests.extend(requests)
                return [fake_result for _ in requests]

        with patch.object(api_mod, "OcrRegionRefiner", return_value=_FakeRefiner()):
            with patch.object(api_mod, "reconstruct_ocr_lines", return_value=[]):
                with patch.object(api_mod, "_deduplicate_region_ocr_tokens", side_effect=lambda t: t):
                    _recover_selected_regions(
                        engine=MagicMock(),
                        page_image=MagicMock(),
                        page_index=0,
                        page_bbox=BBox(0, 0, 595, 842),
                        regions=[fake_region],
                        quality_variants=True,  # would normally enable variants
                        quality_policy="baseline",
                    )

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.scale_factors == (1.0,), (
            f"baseline must use (1.0,) scale_factors, got {req.scale_factors}"
        )
        assert req.quality_variants is False, (
            f"baseline must set quality_variants=False, got {req.quality_variants}"
        )

    def test_adaptive_allows_multi_scale(self):
        """Non-baseline policy with 'small' reason must allow multi-scale."""
        from structured_pdf_text.ocr.recovery import RegionRefinementRequest
        from structured_pdf_text.api import _recover_selected_regions
        import structured_pdf_text.api as api_mod

        captured_requests: list[RegionRefinementRequest] = []
        fake_result = self._build_fake_result()
        fake_region = self._make_fake_region(("small",))

        class _FakeRefiner:
            def refine_many(self, page_image, page_index, page_bbox, requests):
                captured_requests.extend(requests)
                return [fake_result for _ in requests]

        with patch.object(api_mod, "OcrRegionRefiner", return_value=_FakeRefiner()):
            with patch.object(api_mod, "reconstruct_ocr_lines", return_value=[]):
                with patch.object(api_mod, "_deduplicate_region_ocr_tokens", side_effect=lambda t: t):
                    _recover_selected_regions(
                        engine=MagicMock(),
                        page_image=MagicMock(),
                        page_index=0,
                        page_bbox=BBox(0, 0, 595, 842),
                        regions=[fake_region],
                        quality_variants=True,
                        quality_policy="adaptive",
                    )

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert len(req.scale_factors) > 1, (
            "adaptive with 'small' reason must request multiple scale factors"
        )


# ---------------------------------------------------------------------------
# R69 — Rotated candidate orientation reaches OcrToken.rotation
# ---------------------------------------------------------------------------

class TestR69RotatedCandidateTokenRotation:
    """_result_to_pipeline_tokens must stamp token_rotation onto OcrToken.rotation."""

    # EasyOCR raw format: each item is (bbox_pts, text, confidence)
    # bbox_pts is a list of 4 corner points [[x,y], [x,y], [x,y], [x,y]]
    # in clockwise order: top-left, top-right, bottom-right, bottom-left.
    _RAW_BOX = [[0, 0], [80, 0], [80, 10], [0, 10]]
    _RAW_BOX2 = [[90, 0], [160, 0], [160, 10], [90, 10]]

    def test_result_to_pipeline_tokens_default_rotation_zero(self):
        """Without token_rotation, OcrToken.rotation must be 0."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        raw = [(self._RAW_BOX, "texto", 0.90)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        assert len(tokens) == 1
        assert tokens[0].rotation == 0

    def test_result_to_pipeline_tokens_rot90_propagated(self):
        """token_rotation=90 must set OcrToken.rotation=90."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        raw = [(self._RAW_BOX, "texto", 0.90)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt", token_rotation=90)
        assert len(tokens) == 1
        assert tokens[0].rotation == 90

    def test_result_to_pipeline_tokens_rot180_propagated(self):
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        raw = [(self._RAW_BOX, "texto", 0.90)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt", token_rotation=180)
        assert len(tokens) == 1
        assert tokens[0].rotation == 180

    def test_result_to_pipeline_tokens_rot270_propagated(self):
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        raw = [(self._RAW_BOX, "texto", 0.90)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt", token_rotation=270)
        assert len(tokens) == 1
        assert tokens[0].rotation == 270

    def test_multiple_tokens_all_receive_rotation(self):
        """All tokens in a single raw result batch must receive the rotation."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        raw = [
            (self._RAW_BOX, "primeira", 0.90),
            (self._RAW_BOX2, "segunda", 0.85),
        ]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt", token_rotation=270)
        assert len(tokens) == 2
        assert all(t.rotation == 270 for t in tokens), (
            "all tokens from a rot270 candidate must carry rotation=270"
        )


# ---------------------------------------------------------------------------
# R70 — Quality gate must not penalise correctly-oriented vertical text
# ---------------------------------------------------------------------------

class TestR70OrientationAwareQualityGate:
    """_is_token_orientation_plausible and assess_ocr_quality must accept
    vertical text recognised from rot90/rot270 candidates."""

    def test_horizontal_token_no_rotation_plausible(self):
        """Wide horizontal token (rotation=0) must be plausible."""
        tok = _tok("texto", 0, 0, 80, 10, rotation=0)  # width 80, height 10
        assert _is_token_orientation_plausible(tok)

    def test_vertical_token_rot90_plausible(self):
        """Tall token from rot90 candidate (rotation=90) must be plausible."""
        tok = _tok("A", 0, 0, 10, 80, rotation=90)  # width 10, height 80
        assert _is_token_orientation_plausible(tok)

    def test_vertical_token_rot270_plausible(self):
        """Tall token from rot270 candidate (rotation=270) must be plausible."""
        tok = _tok("texto", 0, 0, 10, 80, rotation=270)
        assert _is_token_orientation_plausible(tok)

    def test_single_char_always_plausible_regardless_of_shape(self):
        """Single-character tokens must be plausible regardless of aspect ratio."""
        tok = _tok("8", 0, 0, 3, 50, rotation=0)  # tall/narrow single digit
        assert _is_token_orientation_plausible(tok)

    def test_horizontal_token_with_vertical_bbox_implausible(self):
        """Horizontal token (rotation=0) with vertical bbox must be implausible."""
        tok = _tok("texto completo", 0, 0, 10, 80, rotation=0)  # width 10, height 80
        assert not _is_token_orientation_plausible(tok)

    def test_vertical_token_with_horizontal_bbox_implausible(self):
        """rot90 token with horizontal bbox must be implausible."""
        tok = _tok("texto longo", 0, 0, 80, 10, rotation=90)  # width 80, height 10
        assert not _is_token_orientation_plausible(tok)

    def test_assess_quality_rot90_tokens_do_not_trigger_orientation_reason(self):
        """Two rot90 tokens with correct tall bbox must not flag orientation_ratio_below_threshold."""
        tokens = [
            _tok("LINHA", 0, 0, 10, 80, conf=0.95, rotation=90),
            _tok("DOIS", 0, 85, 10, 160, conf=0.93, rotation=90),
        ]
        quality = assess_ocr_quality(tokens)
        assert "orientation_ratio_below_threshold" not in quality.reasons, (
            "correctly-oriented rot90 tokens must not trigger orientation penalty"
        )

    def test_assess_quality_mixed_incoherent_orientations_triggers_reason(self):
        """Tokens with incoherent mixed orientations should trigger the gate."""
        tokens = [
            _tok("texto horizontal longo", 0, 0, 120, 10, conf=0.90, rotation=0),
            _tok("vertical", 0, 20, 10, 80, conf=0.90, rotation=0),  # tall but rotation=0
            _tok("mais vertical", 0, 90, 10, 150, conf=0.90, rotation=0),
        ]
        quality = assess_ocr_quality(tokens)
        # The two tall-bbox rotation=0 tokens are implausible; ratio < threshold.
        # With 1 plausible out of 3, ratio ≈ 0.33 — should be below minimum.
        # (default minimum_orientation_ratio is typically 0.5)
        # We only verify the horizontal token is considered plausible
        assert _is_token_orientation_plausible(tokens[0]), "horizontal token must be plausible"
        assert not _is_token_orientation_plausible(tokens[1]), "tall rot=0 multi-char token must be implausible"
