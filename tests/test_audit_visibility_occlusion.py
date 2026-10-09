"""VQ-21 — Scaffold: visibility/occlusion filter for overlapping text.

Documents expected behavior when native text is occluded by overlapping
vector graphics or image elements. Tests are partly live, partly xfail.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.document import NativeCharacter, SourceKind, TextToken, EvidenceRef, WritingDirection
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=50.0, y1=12.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _char(text: str, x0: float, x1: float, page_index: int = 0) -> NativeCharacter:
    return NativeCharacter(
        page_index=page_index,
        char_index=int(x0),
        text=text,
        unicode_codepoint=ord(text) if len(text) == 1 else None,
        bbox=BBox(x0=x0, y0=0, x1=x1, y1=12),
    )


class TestOcclusionFilterUnit:
    """Basic BBox geometry checks used by the occlusion filter."""

    def test_fully_contained_is_occluded(self):
        """A character bbox fully inside an occluding region is considered occluded."""
        char_bbox = _bbox(x0=10, y0=2, x1=40, y1=10)
        occluder = _bbox(x0=0, y0=0, x1=50, y1=12)
        overlap = char_bbox.overlap_ratio(occluder)
        assert overlap >= 0.95

    def test_not_overlapping_is_visible(self):
        """A character bbox outside the occluding region is visible."""
        char_bbox = _bbox(x0=100, y0=0, x1=150, y1=12)
        occluder = _bbox(x0=0, y0=0, x1=50, y1=12)
        overlap = char_bbox.overlap_ratio(occluder)
        assert overlap == 0.0

    def test_partial_overlap_is_borderline(self):
        """Partial overlap results in overlap ratio between 0 and 1."""
        char_bbox = _bbox(x0=25, y0=0, x1=75, y1=12)
        occluder = _bbox(x0=0, y0=0, x1=50, y1=12)
        overlap = char_bbox.overlap_ratio(occluder)
        assert 0.0 < overlap < 1.0


class TestOcclusionFilterIntegration:
    """VQ-21: occlusion-aware visibility filter is now integrated."""

    def test_occluded_chars_excluded_from_reconstruction(self):
        """Characters under vector graphics occlusion must be excluded from line reconstruction."""
        from structured_pdf_text.text.line_detector import reconstruct_native_lines

        chars = (
            _char("v", x0=0, x1=8),
            _char("a", x0=8, x1=16),
            _char("l", x0=16, x1=24),
            _char("o", x0=24, x1=32),
            _char("r", x0=32, x1=40),
        )
        occluded_bbox = _bbox(x0=8, y0=0, x1=40, y1=12)  # covers "alor"

        lines = reconstruct_native_lines(chars, occluded_regions=[occluded_bbox])
        text = " ".join(line.text for line in lines)
        # Only "v" should survive
        assert "v" in text
        assert "a" not in text
        assert "l" not in text

    def test_watermark_text_suppressed(self):
        """Text rendered at very low opacity or as watermark must be suppressed."""
        from structured_pdf_text.visibility import is_char_visible

        watermark_char = _char("C", x0=100, x1=108)
        assert not is_char_visible(watermark_char, opacity=0.05)

    def test_normal_opacity_is_visible(self):
        """Text at normal opacity must be considered visible."""
        from structured_pdf_text.visibility import is_char_visible

        normal_char = _char("A", x0=0, x1=8)
        assert is_char_visible(normal_char, opacity=1.0)

    def test_threshold_opacity_is_visible(self):
        """Opacity at exactly the threshold (0.15) must be visible."""
        from structured_pdf_text.visibility import is_char_visible

        char = _char("X", x0=0, x1=8)
        assert is_char_visible(char, opacity=0.15)

    def test_below_threshold_opacity_suppressed(self):
        """Opacity just below threshold must be suppressed."""
        from structured_pdf_text.visibility import is_char_visible

        char = _char("X", x0=0, x1=8)
        assert not is_char_visible(char, opacity=0.14)
