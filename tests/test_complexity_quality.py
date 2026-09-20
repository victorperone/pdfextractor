from __future__ import annotations

from types import SimpleNamespace

from structured_pdf_text.api import _is_raster_primary_candidate


def _complexity(native_length: int, image_coverage: float = 0.90):
    return SimpleNamespace(
        facts={
            "image_coverage": image_coverage,
            "largest_image_coverage": image_coverage,
            "useful_text_length": native_length,
        },
        reasons=set(),
    )


def test_raster_primary_promotion_accepts_short_native_residue_through_200_chars():
    assert _is_raster_primary_candidate(_complexity(80))
    assert _is_raster_primary_candidate(_complexity(200))


def test_raster_primary_promotion_rejects_long_native_layer_or_non_dominant_image():
    assert not _is_raster_primary_candidate(_complexity(201))
    assert not _is_raster_primary_candidate(_complexity(120, image_coverage=0.74))
