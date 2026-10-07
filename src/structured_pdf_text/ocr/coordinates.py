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


def map_tokens_to_page(
    tokens: list[OcrToken],
    page_bbox: BBox | None,
    width_px: int,
    height_px: int,
    page_rotation: int = 0,
) -> list[OcrToken]:
    """Convert backend raster-space token boxes to canonical PDF point boxes.

    The rendered image is always in visual orientation (PDFium applies /Rotate
    before producing pixels). For rotated pages the axes are swapped relative
    to canonical PDF space, so a plain linear scale would misplace every token.
    ``page_rotation`` is the page's /Rotate value (0, 90, 180, or 270) and is
    used to apply the correct inverse transform.
    """
    if page_bbox is None:
        return tokens
    rotation = page_rotation % 360
    if rotation == 0:
        transform = PageTransform(page_bbox, RasterGeometry(width_px, height_px))

        def _to_bbox(b: BBox) -> BBox:
            return transform.raster_bbox_to_page(b)

        def _to_point(p: Point) -> Point:
            return transform.raster_point_to_page(p)

    elif rotation == 90:
        # Visual dims: width_px = canonical_height * scale, height_px = canonical_width * scale.
        # Inverse of rotate_to_visual(90): canonical_x ← raster_y, canonical_y ← flipped raster_x.
        pw, ph = page_bbox.width, page_bbox.height

        def _to_bbox(b: BBox) -> BBox:
            cx0 = page_bbox.x0 + b.y0 * pw / height_px
            cy0 = page_bbox.y1 - b.x1 * ph / width_px
            cx1 = page_bbox.x0 + b.y1 * pw / height_px
            cy1 = page_bbox.y1 - b.x0 * ph / width_px
            return BBox(min(cx0, cx1), min(cy0, cy1), max(cx0, cx1), max(cy0, cy1))

        def _to_point(p: Point) -> Point:
            return Point(
                page_bbox.x0 + p.y * pw / height_px,
                page_bbox.y1 - p.x * ph / width_px,
            )

    elif rotation == 180:
        # Visual dims same as canonical; both axes are flipped.
        # Inverse of rotate_to_visual(180): canonical_x ← flipped raster_x, canonical_y ← flipped raster_y.
        pw, ph = page_bbox.width, page_bbox.height

        def _to_bbox(b: BBox) -> BBox:
            cx0 = page_bbox.x1 - b.x1 * pw / width_px
            cy0 = page_bbox.y1 - b.y1 * ph / height_px
            cx1 = page_bbox.x1 - b.x0 * pw / width_px
            cy1 = page_bbox.y1 - b.y0 * ph / height_px
            return BBox(min(cx0, cx1), min(cy0, cy1), max(cx0, cx1), max(cy0, cy1))

        def _to_point(p: Point) -> Point:
            return Point(
                page_bbox.x1 - p.x * pw / width_px,
                page_bbox.y1 - p.y * ph / height_px,
            )

    else:  # 270
        # Visual dims: width_px = canonical_height * scale, height_px = canonical_width * scale.
        # Inverse of rotate_to_visual(270): canonical_y ← raster_x, canonical_x ← flipped raster_y.
        pw, ph = page_bbox.width, page_bbox.height

        def _to_bbox(b: BBox) -> BBox:
            cx0 = page_bbox.x1 - b.y1 * pw / height_px
            cy0 = page_bbox.y0 + b.x0 * ph / width_px
            cx1 = page_bbox.x1 - b.y0 * pw / height_px
            cy1 = page_bbox.y0 + b.x1 * ph / width_px
            return BBox(min(cx0, cx1), min(cy0, cy1), max(cx0, cx1), max(cy0, cy1))

        def _to_point(p: Point) -> Point:
            return Point(
                page_bbox.x1 - p.y * pw / height_px,
                page_bbox.y0 + p.x * ph / width_px,
            )

    return [
        OcrToken(
            text=t.text,
            bbox=_to_bbox(t.bbox),
            confidence=t.confidence,
            language=t.language,
            source=t.source,
            rotation=t.rotation,
            provenance=t.provenance,
            polygon=tuple(_to_point(p) for p in t.polygon) if t.polygon else None,
            level=t.level,
            ocr_provenance=t.ocr_provenance,
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
            ocr_provenance=t.ocr_provenance,
        )
        for t in tokens
    ]
