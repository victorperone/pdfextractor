"""Coordinate transforms between OCR raster pixels and canonical PDF points."""
from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.document import OcrToken


@dataclass(frozen=True, slots=True)
class RasterGeometry:
    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class PageTransform:
    """Map a full-page raster to its canonical page-space bounding box.

    The page bbox is in PDF points and may have a non-zero origin (for example,
    a CropBox). Raster coordinates use a top-left origin and pixel units.
    """

    page_bbox_pt: BBox
    raster: RasterGeometry

    def raster_point_to_page(self, point: Point) -> Point:
        if self.raster.width_px <= 0 or self.raster.height_px <= 0:
            raise ValueError("Raster dimensions must be positive")
        return Point(
            self.page_bbox_pt.x0 + point.x * self.page_bbox_pt.width / self.raster.width_px,
            self.page_bbox_pt.y0 + point.y * self.page_bbox_pt.height / self.raster.height_px,
        )

    def raster_bbox_to_page(self, bbox: BBox) -> BBox:
        p0 = self.raster_point_to_page(Point(bbox.x0, bbox.y0))
        p1 = self.raster_point_to_page(Point(bbox.x1, bbox.y1))
        return BBox(p0.x, p0.y, p1.x, p1.y)

    def raster_polygon_to_page(self, polygon: tuple[Point, ...]) -> tuple[Point, ...]:
        return tuple(self.raster_point_to_page(point) for point in polygon)

    def page_bbox_to_raster(self, bbox: BBox) -> BBox:
        if self.page_bbox_pt.width <= 0 or self.page_bbox_pt.height <= 0:
            raise ValueError("Page dimensions must be positive")
        return BBox(
            (bbox.x0 - self.page_bbox_pt.x0) * self.raster.width_px / self.page_bbox_pt.width,
            (bbox.y0 - self.page_bbox_pt.y0) * self.raster.height_px / self.page_bbox_pt.height,
            (bbox.x1 - self.page_bbox_pt.x0) * self.raster.width_px / self.page_bbox_pt.width,
            (bbox.y1 - self.page_bbox_pt.y0) * self.raster.height_px / self.page_bbox_pt.height,
        )


def map_tokens_to_page(tokens: list[OcrToken], page_bbox: BBox | None, width_px: int, height_px: int) -> list[OcrToken]:
    """Convert backend raster-space token boxes to canonical PDF point boxes."""
    if page_bbox is None:
        return tokens
    transform = PageTransform(page_bbox, RasterGeometry(width_px, height_px))
    return [
        OcrToken(
            text=t.text,
            bbox=transform.raster_bbox_to_page(t.bbox),
            confidence=t.confidence,
            language=t.language,
            source=t.source,
            rotation=t.rotation,
            provenance=t.provenance,
            polygon=transform.raster_polygon_to_page(t.polygon) if t.polygon else None,
            level=t.level,
        )
        for t in tokens
    ]


def offset_tokens(tokens: list[OcrToken], x: float, y: float) -> list[OcrToken]:
    """Translate raster-space token boxes by a crop's top-left pixel offset."""
    if not x and not y:
        return tokens
    return [
        OcrToken(
            text=t.text,
            bbox=BBox(t.bbox.x0 + x, t.bbox.y0 + y, t.bbox.x1 + x, t.bbox.y1 + y),
            confidence=t.confidence,
            language=t.language,
            source=t.source,
            rotation=t.rotation,
            provenance=t.provenance,
            polygon=tuple(Point(p.x + x, p.y + y) for p in t.polygon) if t.polygon else None,
            level=t.level,
        )
        for t in tokens
    ]
