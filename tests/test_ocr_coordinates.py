from __future__ import annotations

import pytest

from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
from structured_pdf_text.ocr.coordinates import PageTransform, RasterGeometry, map_tokens_to_page, offset_tokens


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


@pytest.mark.parametrize("scale", [1, 2, 3, 4])
def test_region_crop_uses_page_points_and_maps_tokens_back(scale: int) -> None:
    import numpy as np

    page = BBox(17.0, 23.0, 629.0, 815.0)
    region = BBox(117.0, 223.0, 217.0, 323.0)
    image = np.zeros((792 * scale, 612 * scale, 3), dtype=np.uint8)
    crop, (x0, y0, _x1, _y1), (width, height) = crop_region_in_raster(image, region, page)
    assert crop.shape[:2] == (100 * scale, 100 * scale)
    local_token = OcrToken(
        "word", BBox(10 * scale, 20 * scale, 30 * scale, 40 * scale),
        0.9, "pt-BR", SourceKind.OCR_REGION,
    )
    page_token = map_tokens_to_page(offset_tokens([local_token], x0, y0), page, width, height)[0]
    assert page_token.bbox == BBox(127, 243, 147, 263)


def test_map_tokens_to_page_transforms_polygon() -> None:
    """map_tokens_to_page must apply the raster-to-page transform to polygon points."""
    # Page: 0..100 pt x 0..50 pt; raster: 200×100 px → scale 0.5 in both axes
    page = BBox(0.0, 0.0, 100.0, 50.0)
    polygon = (Point(0, 0), Point(200, 0), Point(200, 100), Point(0, 100))
    token = OcrToken(
        "word", BBox(0, 0, 200, 100), 0.9, "pt", SourceKind.OCR_PAGE,
        polygon=polygon,
    )
    mapped = map_tokens_to_page([token], page, 200, 100)[0]
    assert mapped.polygon is not None
    assert len(mapped.polygon) == 4
    # All corners must lie within page bounds
    for p in mapped.polygon:
        assert 0.0 <= p.x <= 100.0, f"x {p.x} out of page bounds"
        assert 0.0 <= p.y <= 50.0, f"y {p.y} out of page bounds"


def test_map_tokens_to_page_preserves_polygon_none() -> None:
    """map_tokens_to_page must not invent a polygon when OcrToken.polygon is None."""
    page = BBox(0.0, 0.0, 100.0, 50.0)
    token = OcrToken("word", BBox(0, 0, 200, 100), 0.9, "pt", SourceKind.OCR_PAGE)
    mapped = map_tokens_to_page([token], page, 200, 100)[0]
    assert mapped.polygon is None


def test_offset_tokens_shifts_polygon() -> None:
    """offset_tokens must shift polygon points by the same (x, y) delta as bbox."""
    polygon = (Point(0, 0), Point(10, 0), Point(10, 5), Point(0, 5))
    token = OcrToken(
        "word", BBox(0, 0, 10, 5), 0.9, "pt", SourceKind.OCR_REGION,
        polygon=polygon,
    )
    shifted = offset_tokens([token], 30.0, 15.0)[0]
    assert shifted.polygon is not None
    xs = [p.x for p in shifted.polygon]
    ys = [p.y for p in shifted.polygon]
    assert min(xs) == pytest.approx(30.0)
    assert max(xs) == pytest.approx(40.0)
    assert min(ys) == pytest.approx(15.0)
    assert max(ys) == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, BBox(30, 40, 80, 70)),
        (90, BBox(90, 30, 120, 80)),
        (180, BBox(160, 90, 210, 120)),
        (270, BBox(40, 160, 70, 210)),
    ],
)
def test_rotated_page_bbox_maps_to_visual_space(rotation: int, expected: BBox) -> None:
    # PDFium renders /Rotate-applied pages. This pins the canonical-to-visual
    # crop transform for a non-square page and a box away from every edge.
    canonical = BBox(30, 40, 80, 70)
    assert canonical.rotate_to_visual(rotation, page_width=240, page_height=160) == expected
