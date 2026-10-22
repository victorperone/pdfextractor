"""Validated raster frames composed with the existing OCR geometry helpers."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.ocr.coordinates import map_tokens_to_page
from structured_pdf_text.ocr.image_views import ImageTransform
from .contracts import finite, integer, valid_bbox


def affine(values: tuple[float, ...]) -> ImageTransform:
    if len(values) != 6:
        raise ValueError("Expected six affine coefficients")
    a, b, c, d, e, f = (finite(v) for v in values)
    determinant = finite(a * e - b * d)
    if determinant == 0:
        raise ValueError("Singular raster transform")
    inverse = (e / determinant, -b / determinant, (b * f - e * c) / determinant,
               -d / determinant, a / determinant, (d * c - a * f) / determinant)
    for value in inverse:
        finite(value)
    return ImageTransform((a, b, c, d, e, f), inverse)


def compose(outer: ImageTransform, inner: ImageTransform) -> ImageTransform:
    a, b, c, d, e, f = outer.forward_matrix
    g, h, i, j, k, l = inner.forward_matrix
    return affine((a*g+b*j, a*h+b*k, a*i+b*l+c, d*g+e*j, d*h+e*k, d*i+e*l+f))


@dataclass(frozen=True, slots=True)
class RasterFrame:
    """Current pixels -> base raster -> canonical PDF points, and its inverse.

The base dimensions are actual rendered dimensions, never a requested scale.
Candidate crop/resize/deskew transforms map current pixels back to that base.
"""
    bbox: BBox
    width_px: int
    height_px: int
    page_rotation: int = 0
    base_width_px: int | None = None
    base_height_px: int | None = None
    pixel_to_base: ImageTransform = field(default_factory=ImageTransform.identity)

    def __post_init__(self):
        object.__setattr__(self, "bbox", valid_bbox(self.bbox))
        integer(self.width_px, minimum=1)
        integer(self.height_px, minimum=1)
        if type(self.page_rotation) is not int or self.page_rotation not in (0, 90, 180, 270):
            raise ValueError("Unsupported physical page rotation")
        for name, default in (("base_width_px", self.width_px), ("base_height_px", self.height_px)):
            value = getattr(self, name)
            object.__setattr__(self, name, default if value is None else integer(value, minimum=1))
        # Recompute rather than trust an independently supplied inverse.
        checked = affine(tuple(self.pixel_to_base.forward_matrix))
        if len(self.pixel_to_base.inverse_matrix) != 6:
            raise ValueError("Invalid transform inverse")
        for value in self.pixel_to_base.inverse_matrix:
            finite(value)
        if any(abs(a-b) > 1e-9 * max(1, abs(a)) for a, b in zip(checked.inverse_matrix, self.pixel_to_base.inverse_matrix)):
            raise ValueError("Inconsistent transform inverse")
        object.__setattr__(self, "pixel_to_base", checked)

    def point_to_page(self, point: Point) -> Point:
        point = self.pixel_to_base.forward(Point(finite(point.x), finite(point.y)))
        dummy = OcrToken("", BBox(point.x, point.y, point.x, point.y), None, None, SourceKind.OCR_REGION, polygon=(point,))
        result = map_tokens_to_page([dummy], self.bbox, self.base_width_px, self.base_height_px, self.page_rotation)[0].polygon[0]
        return Point(finite(result.x), finite(result.y))

    def page_point_to_pixel(self, point: Point) -> Point:
        x = (finite(point.x) - self.bbox.x0) / self.bbox.width
        y = (finite(point.y) - self.bbox.y0) / self.bbox.height
        x, y = {0: (x, y), 90: (1-y, x), 180: (1-x, 1-y), 270: (y, 1-x)}[self.page_rotation]
        result = self.pixel_to_base.inverse(Point(x * self.base_width_px, y * self.base_height_px))
        return Point(finite(result.x), finite(result.y))

    def bbox_to_page(self, box: BBox) -> BBox:
        return _map_bbox(valid_bbox(box), self.point_to_page)

    def page_bbox_to_pixel(self, box: BBox) -> BBox:
        return _map_bbox(valid_bbox(box), self.page_point_to_pixel)

    def derived(self, width: int, height: int, current_to_parent: ImageTransform) -> RasterFrame:
        return replace(self, width_px=width, height_px=height,
                       pixel_to_base=compose(self.pixel_to_base, current_to_parent))


def _map_bbox(box: BBox, transform) -> BBox:
    points = tuple(transform(Point(x, y)) for x, y in ((box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)))
    return valid_bbox(BBox(min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points)))
