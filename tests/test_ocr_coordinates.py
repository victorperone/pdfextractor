from __future__ import annotations

import pytest

from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.ocr.coordinates import PageTransform, RasterGeometry


@pytest.mark.parametrize("scale", [1, 2, 3, 4])
def test_raster_page_round_trip_with_nonzero_origin(scale: int) -> None:
    page = BBox(17.0, 23.0, 629.0, 815.0)
    transform = PageTransform(page, RasterGeometry(612 * scale, 792 * scale))
    original = BBox(100.0, 200.0, 250.0, 320.0)
    raster = transform.page_bbox_to_raster(original)
    recovered = transform.raster_bbox_to_page(raster)
    assert recovered.x0 == pytest.approx(original.x0)
    assert recovered.y0 == pytest.approx(original.y0)
    assert recovered.x1 == pytest.approx(original.x1)
    assert recovered.y1 == pytest.approx(original.y1)


def test_page_transform_preserves_page_edges_and_points() -> None:
    transform = PageTransform(BBox(10, 20, 110, 220), RasterGeometry(1000, 2000))
    assert transform.raster_point_to_page(Point(0, 0)) == Point(10, 20)
    assert transform.raster_bbox_to_page(BBox(1000, 2000, 1000, 2000)) == BBox(110, 220, 110, 220)


def test_page_transform_rejects_zero_sized_raster() -> None:
    transform = PageTransform(BBox(0, 0, 10, 10), RasterGeometry(0, 10))
    with pytest.raises(ValueError, match="Raster dimensions"):
        transform.raster_point_to_page(Point(1, 1))
