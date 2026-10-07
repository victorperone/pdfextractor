"""Backend independent OCR image views and reversible affine geometry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.geometry import BBox, Point


@dataclass(frozen=True, slots=True)
class ImageTransform:
    """2D affine transform stored as row-major ``(a,b,c,d,e,f)``."""
    forward_matrix: tuple[float, float, float, float, float, float]
    inverse_matrix: tuple[float, float, float, float, float, float]

    def forward(self, point: Point) -> Point:
        a, b, c, d, e, f = self.forward_matrix
        return Point(a * point.x + b * point.y + c, d * point.x + e * point.y + f)

    def inverse(self, point: Point) -> Point:
        a, b, c, d, e, f = self.inverse_matrix
        return Point(a * point.x + b * point.y + c, d * point.x + e * point.y + f)

    def map_bbox_inverse(self, bbox: BBox) -> BBox:
        points = [
            self.inverse(Point(bbox.x0, bbox.y0)), self.inverse(Point(bbox.x1, bbox.y0)),
            self.inverse(Point(bbox.x1, bbox.y1)), self.inverse(Point(bbox.x0, bbox.y1)),
        ]
        return BBox(min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points))

    @classmethod
    def identity(cls) -> "ImageTransform":
        values = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        return cls(values, values)


@dataclass(frozen=True, slots=True)
class OcrImageView:
    image: Any
    transform_to_page: ImageTransform
    source_bbox: BBox
    scale: float = 1.0
    rotation: float = 0.0
    tile_id: str | None = None
    preprocessing: tuple[str, ...] = ()


def canonicalize_page_image(image: Any, page_rotation: int) -> Any:
    """Undo PDF page presentation rotation so raster axes match canonical boxes."""
    rotation = page_rotation % 360
    if rotation == 0:
        return image
    if rotation not in (90, 180, 270):
        return image
    try:
        import numpy as np

        rotated = np.rot90(np.asarray(image), k={90: 1, 180: 2, 270: 3}[rotation])
        return rotated.copy()
    except (ImportError, TypeError, ValueError):
        transpose = getattr(image, "transpose", None)
        if not callable(transpose):
            return image
        from PIL import Image

        operation = {
            90: Image.Transpose.ROTATE_90,
            180: Image.Transpose.ROTATE_180,
            270: Image.Transpose.ROTATE_270,
        }[rotation]
        return transpose(operation)


def tile_image_views(
    image: Any,
    page_bbox: BBox,
    *,
    rows: int = 2,
    columns: int = 2,
    overlap: float = 0.15,
) -> list[OcrImageView]:
    """Create overlapping raster tiles with their page-coordinate boxes."""
    import numpy as np
    arr = np.asarray(image)
    height, width = arr.shape[:2]
    if height <= 0 or width <= 0 or rows < 1 or columns < 1:
        return []
    overlap = min(0.20, max(0.10, float(overlap)))
    tile_w = min(width, max(1, round(width / columns * (1.0 + overlap))))
    tile_h = min(height, max(1, round(height / rows * (1.0 + overlap))))
    xs = _tile_starts(width, tile_w, columns)
    ys = _tile_starts(height, tile_h, rows)
    views: list[OcrImageView] = []
    for row, y0 in enumerate(ys):
        for column, x0 in enumerate(xs):
            x1, y1 = min(width, x0 + tile_w), min(height, y0 + tile_h)
            crop = arr[y0:y1, x0:x1].copy()
            bbox = BBox(
                page_bbox.x0 + x0 / width * page_bbox.width,
                page_bbox.y0 + y0 / height * page_bbox.height,
                page_bbox.x0 + x1 / width * page_bbox.width,
                page_bbox.y0 + y1 / height * page_bbox.height,
            )
            views.append(OcrImageView(
                image=crop,
                transform_to_page=ImageTransform.identity(),
                source_bbox=bbox,
                scale=1.0,
                tile_id=f"r{row}c{column}",
            ))
    return views


def _tile_starts(length: int, tile_size: int, count: int) -> list[int]:
    if count == 1 or tile_size >= length:
        return [0]
    return [round(index * (length - tile_size) / (count - 1)) for index in range(count)]
