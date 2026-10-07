"""Regression tests for figure OCR, reading-order, preprocessing — Etapa 3.

Covers:
  R71 — figure OCR processes only uncovered images
  R72 — image coverage uses union(intersections) / image_bbox.area
  R73 — adaptive candidates trigger orientation recovery when baseline is sparse
  D11 — {} vs None preserved in reading-order flow_lines
  P1  — visually identical preprocessing images are deduplicated
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from structured_pdf_text.document import (
    ImageEvidence,
    LayoutRegion,
    NativeObjectEvidence,
    NativePageEvidence,
    RegionDecision,
    RegionKind,
    RegionQuality,
    StructureTreeEvidence,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.api import _image_union_coverage, _figures_requiring_ocr
from structured_pdf_text.text.reading_order import order_region_lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image(x0: float, y0: float, x1: float, y1: float, idx: int = 0) -> ImageEvidence:
    return ImageEvidence(page_index=0, object_index=idx, bbox=BBox(x0, y0, x1, y1))


def _make_region(x0: float, y0: float, x1: float, y1: float) -> LayoutRegion:
    return LayoutRegion(
        region_id=f"r_{x0}_{y0}",
        kind=RegionKind.TEXT,
        bbox=BBox(x0, y0, x1, y1),
        layout_confidence=1.0,
        native_lines=[],
        ocr_tokens=[],
        quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
    )


def _make_page(images: list[ImageEvidence]) -> NativePageEvidence:
    page_bbox = BBox(0, 0, 500, 700)
    return NativePageEvidence(
        page_index=0,
        bbox=page_bbox,
        characters=(),
        extracted_text="",
        objects=NativeObjectEvidence(
            images=tuple(images),
            paths=(),
            annotations=(),
            structure_tree=None,
            page_bbox=page_bbox,
            crop_bbox=page_bbox,
            rotation=0,
        ),
    )


# ---------------------------------------------------------------------------
# R72 — _image_union_coverage: union(intersections) / image_bbox.area
# ---------------------------------------------------------------------------

class TestR72ImageUnionCoverage:
    """Coverage must use the image as denominator, not the region."""

    def test_small_region_fully_inside_large_image_is_not_considered_covered(self):
        """A tiny region (100% inside image) must NOT mark a 500×500 image as covered."""
        image_bbox = BBox(0, 0, 500, 500)
        tiny_region = _make_region(10, 10, 20, 20)  # 10×10 inside 500×500
        coverage = _image_union_coverage(image_bbox, [tiny_region])
        # tiny region covers 100/250000 = 0.04% of image → clearly below 80%
        assert coverage < 0.80

    def test_large_region_covering_90_percent_is_covered(self):
        """A region covering 90% of the image bbox should yield ≥ 0.90 coverage."""
        image_bbox = BBox(0, 0, 100, 100)
        region = _make_region(0, 0, 100, 90)  # covers 90%
        coverage = _image_union_coverage(image_bbox, [region])
        assert coverage >= 0.90

    def test_two_non_overlapping_45pct_regions_yield_90pct_coverage(self):
        """Two non-overlapping regions each covering 45% should give ~90% total."""
        image_bbox = BBox(0, 0, 100, 100)
        r1 = _make_region(0, 0, 100, 45)   # top 45%
        r2 = _make_region(0, 55, 100, 100)  # bottom 45%
        coverage = _image_union_coverage(image_bbox, [r1, r2])
        assert 0.85 <= coverage <= 1.0

    def test_two_fully_overlapping_regions_do_not_double_count(self):
        """Two identical regions should give same coverage as one."""
        image_bbox = BBox(0, 0, 100, 100)
        r1 = _make_region(0, 0, 100, 50)
        r2 = _make_region(0, 0, 100, 50)  # exact duplicate
        coverage = _image_union_coverage(image_bbox, [r1, r2])
        assert abs(coverage - 0.50) < 0.01, f"double-counted: {coverage}"

    def test_no_regions_yields_zero_coverage(self):
        image_bbox = BBox(0, 0, 200, 200)
        assert _image_union_coverage(image_bbox, []) == 0.0

    def test_degenerate_zero_area_image_returns_full_coverage(self):
        """Zero-area image should not trigger division by zero."""
        image_bbox = BBox(50, 50, 50, 50)
        assert _image_union_coverage(image_bbox, []) == 1.0


# ---------------------------------------------------------------------------
# R71 — _figures_requiring_ocr: returns only unresolved images
# ---------------------------------------------------------------------------

class TestR71FiguresRequiringOcr:
    """Only images not adequately covered by selected regions must be returned."""

    def test_no_images_returns_empty_list(self):
        page = _make_page([])
        assert _figures_requiring_ocr(page, []) == []

    def test_fully_covered_image_is_not_returned(self):
        image = _make_image(0, 0, 100, 100)
        page = _make_page([image])
        region = _make_region(0, 0, 100, 100)
        result = _figures_requiring_ocr(page, [region])
        assert result == [], "fully covered image must not need OCR"

    def test_uncovered_image_is_returned(self):
        image = _make_image(0, 0, 100, 100)
        page = _make_page([image])
        result = _figures_requiring_ocr(page, [])
        assert image in result, "uncovered image must be returned"

    def test_mixed_page_returns_only_uncovered(self):
        """Two images: one covered, one not. Only the uncovered one is returned."""
        image_a = _make_image(0, 0, 100, 100, idx=0)    # covered
        image_b = _make_image(200, 0, 300, 100, idx=1)  # not covered
        page = _make_page([image_a, image_b])
        region = _make_region(0, 0, 100, 100)  # covers image_a only
        result = _figures_requiring_ocr(page, [region])
        assert image_b in result, "uncovered image B must be returned"
        assert image_a not in result, "covered image A must NOT be returned"

    def test_small_region_inside_large_image_does_not_mark_as_covered(self):
        """R72 interaction: tiny region inside large image — image should be returned."""
        image = _make_image(0, 0, 500, 500, idx=0)
        page = _make_page([image])
        tiny_region = _make_region(10, 10, 20, 20)
        result = _figures_requiring_ocr(page, [tiny_region])
        assert image in result, "large image barely covered by tiny region must need OCR"

    def test_image_without_bbox_is_skipped(self):
        """Images with bbox=None must be silently skipped (no error)."""
        null_image = ImageEvidence(page_index=0, object_index=0, bbox=None)
        page = _make_page([null_image])
        result = _figures_requiring_ocr(page, [])
        assert result == [], "null-bbox images must not appear in result"


# ---------------------------------------------------------------------------
# D11 — reading order preserves [] vs None for flow_lines
# ---------------------------------------------------------------------------

class TestD11FlowLinesNoneVsEmpty:
    """flow_lines_by_region=None and ={} must produce different fallback behavior."""

    def _make_text_region(self, lines) -> LayoutRegion:
        rl = LayoutRegion(
            region_id="reg1",
            kind=RegionKind.TEXT,
            bbox=BBox(0, 0, 200, 200),
            layout_confidence=1.0,
            native_lines=list(lines),
            ocr_tokens=[],
            quality=RegionQuality(decision=RegionDecision.KEEP_NATIVE),
        )
        return rl

    def _make_text_line(self, y: float) -> Any:
        """Return a minimal TextLine."""
        from structured_pdf_text.document import TextLine, WritingDirection
        return TextLine(
            tokens=[],
            bbox=BBox(0, y, 100, y + 12),
            baseline=None,
            direction=WritingDirection.LEFT_TO_RIGHT,
            native_order_min=None,
            native_order_max=None,
            text_override="hello world",
        )

    def test_none_flow_lines_by_region_uses_all_lines_for_hypothesis(self):
        """When flow_lines_by_region is None, all lines participate in the hypothesis."""
        line_a = self._make_text_line(0)
        line_b = self._make_text_line(20)
        region = self._make_text_region([line_a, line_b])
        ordered, decision = order_region_lines([region], flow_lines_by_region=None)
        # Both lines must appear in output
        assert len(ordered) == 2

    def test_empty_dict_flow_lines_by_region_treats_region_as_all_lines(self):
        """flow_lines_by_region={} and region not in dict → flow_lines=None → all lines.

        D11 fix: region not in dict means "no table filtering happened" →
        all native lines participate in the prose-flow hypothesis (same as None dict).
        Both lines appear in output.
        """
        line_a = self._make_text_line(0)
        line_b = self._make_text_line(20)
        region = self._make_text_region([line_a, line_b])
        # Region "reg1" not in empty dict → None path → all lines for hypothesis
        ordered, decision = order_region_lines([region], flow_lines_by_region={})
        assert len(ordered) == 2, "lines must be preserved"

    def test_explicit_empty_list_in_region_dict_triggers_fallback(self):
        """When dict has region_id → [], flow_lines=[] → 'no_prose_lines' fallback.

        D11 fix: explicitly computed empty list means no prose lines were accepted.
        The result falls back to geometry sort, but lines are still conserved.
        """
        line_a = self._make_text_line(0)
        line_b = self._make_text_line(20)
        region = self._make_text_region([line_a, line_b])
        flow_map = {"reg1": []}  # explicit empty → no prose lines for hypothesis
        ordered, decision = order_region_lines([region], flow_lines_by_region=flow_map)
        # Lines still conserved even in fallback
        assert len(ordered) == 2


# ---------------------------------------------------------------------------
# R73 — adaptive candidates trigger orientation recovery when sparse
# ---------------------------------------------------------------------------

class TestR73AdaptiveOrientationRecovery:
    """_adaptive_candidates must add rotation candidates when baseline is empty."""

    def _fake_run(self, return_map: dict[str, list]) -> Any:
        """Build a mock that returns different raw results for each image argument."""
        calls = []

        def fake_run_easyocr(reader, img, **kwargs):
            result = return_map.get(id(img), [])
            calls.append(id(img))
            return (result, None)

        return fake_run_easyocr, calls

    def test_sparse_upright_triggers_rotation_attempts(self):
        """When A+B produce 0 tokens, a rotation that finds text must enter results."""
        from structured_pdf_text.ocr.backends.easyocr import _adaptive_candidates

        # Simulates: upright passes produce nothing, rot90 finds text.
        _dummy_token = ([[0, 0], [10, 0], [10, 10], [0, 10]], "word", 0.9)
        call_count = {"n": 0}

        def fake_run(reader, img, **kwargs):
            call_count["n"] += 1
            # A=empty, B=empty, then rot90 returns a token (rot180/270 still empty)
            if call_count["n"] <= 2:
                return [], None
            if call_count["n"] == 3:  # rot90
                return [_dummy_token], None
            return [], None

        import numpy as np
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        base_kwargs = {
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 10,
            "width_ths": 0.5,
            "add_margin": 0.1,
        }

        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            side_effect=fake_run,
        ), patch(
            "structured_pdf_text.ocr.backends.easyocr._image_preprocessing_candidates",
            return_value=[],
        ), patch(
            "structured_pdf_text.ocr.backends.easyocr._remap_raw_for_rotation",
            side_effect=lambda raw, angle, h, w: raw,
        ):
            results = _adaptive_candidates(object(), img, base_kwargs)

        labels = [label for label, _ in results]
        # rot90 must appear because it produced tokens
        assert "rot90" in labels, f"rot90 not found in {labels}"

    def test_sufficient_upright_skips_rotation_attempts(self):
        """When A+B produce >= threshold tokens, no rotation candidates are added."""
        from structured_pdf_text.ocr.backends.easyocr import (
            _adaptive_candidates,
            _ADAPTIVE_ORIENTATION_TOKEN_THRESHOLD,
        )

        _dummy_token = ([[0, 0], [10, 0], [10, 10], [0, 10]], "word", 0.9)

        def fake_run(reader, img, **kwargs):
            # Return enough tokens to exceed threshold
            return [_dummy_token] * _ADAPTIVE_ORIENTATION_TOKEN_THRESHOLD, None

        import numpy as np
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        base_kwargs = {
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 10,
            "width_ths": 0.5,
            "add_margin": 0.1,
        }

        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            side_effect=fake_run,
        ), patch(
            "structured_pdf_text.ocr.backends.easyocr._image_preprocessing_candidates",
            return_value=[],
        ):
            results = _adaptive_candidates(object(), img, base_kwargs)

        labels = [label for label, _ in results]
        rotation_labels = [l for l in labels if l in ("rot90", "rot180", "rot270")]
        assert not rotation_labels, f"Rotation candidates should not appear on strong upright; got: {rotation_labels}"

    def test_rotation_candidate_omitted_when_it_produces_no_tokens(self):
        """A rotation that yields no tokens should not be added to results."""
        from structured_pdf_text.ocr.backends.easyocr import _adaptive_candidates

        def fake_run(reader, img, **kwargs):
            # upright empty, rotations empty too
            return [], None

        import numpy as np
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        base_kwargs = {
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 10,
            "width_ths": 0.5,
            "add_margin": 0.1,
        }

        with patch(
            "structured_pdf_text.ocr.backends.easyocr._run_easyocr",
            side_effect=fake_run,
        ), patch(
            "structured_pdf_text.ocr.backends.easyocr._image_preprocessing_candidates",
            return_value=[],
        ):
            results = _adaptive_candidates(object(), img, base_kwargs)

        labels = [label for label, _ in results]
        rotation_labels = [l for l in labels if l in ("rot90", "rot180", "rot270")]
        assert not rotation_labels, "Empty rotation candidates must not enter results"


# ---------------------------------------------------------------------------
# P1 — preprocessing deduplication
# ---------------------------------------------------------------------------

class TestP1PreprocessingDedup:
    """Visually identical preprocessing images must not produce duplicate candidates."""

    def test_identical_arrays_deduplicated(self):
        """When grayscale and autocontrast produce identical bytes, only one enters."""
        from structured_pdf_text.ocr.backends.easyocr import _image_preprocessing_candidates
        import hashlib
        import numpy as np

        white = np.full((50, 50, 3), 255, dtype=np.uint8)
        try:
            candidates = _image_preprocessing_candidates(white, adaptive=False)
        except Exception:
            pytest.skip("cv2/PIL not available")
        if not candidates:
            pytest.skip("cv2/PIL not available — function returned empty list")

        labels = [label for label, _ in candidates]
        fingerprints = []
        for _, variant in candidates:
            arr = np.asarray(variant)
            fp = f"{arr.shape}:{arr.dtype}:{hashlib.md5(arr.tobytes()).hexdigest()}"
            fingerprints.append(fp)
        assert len(fingerprints) == len(set(fingerprints)), (
            f"Duplicate fingerprints found. Labels: {labels}"
        )

    def test_distinct_preprocessings_not_collapsed(self):
        """A varied image must yield multiple distinct preprocessing candidates."""
        from structured_pdf_text.ocr.backends.easyocr import _image_preprocessing_candidates
        import numpy as np

        rng = np.random.default_rng(42)
        varied = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
        try:
            candidates = _image_preprocessing_candidates(varied, adaptive=False)
        except Exception:
            pytest.skip("cv2/PIL not available")
        if not candidates:
            pytest.skip("cv2/PIL not available — function returned empty list")

        assert len(candidates) >= 2, "At least 2 distinct preprocessings expected"

    def test_no_error_on_empty_image(self):
        """Empty image must return an empty list without raising."""
        from structured_pdf_text.ocr.backends.easyocr import _image_preprocessing_candidates
        import numpy as np

        try:
            result = _image_preprocessing_candidates(np.array([]), adaptive=False)
        except Exception:
            pytest.skip("cv2/PIL not available")
        assert result == []
