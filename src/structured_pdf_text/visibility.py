"""Visibility checks that reconcile native objects with the rendered page."""

from __future__ import annotations

from collections.abc import Iterable

from .document import NativeCharacter, NativePageEvidence
from .geometry import BBox


def detect_opaque_occlusion_boxes(
    page: NativePageEvidence,
    rendered_image: object | None,
) -> list[BBox]:
    """Return path boxes that visually behave as opaque redaction/occlusion.

    PDF paths do not expose their paint state consistently across PDFium
    bindings.  We therefore combine conservative geometry (a substantial,
    wide path) with pixels from the rendered page.  Thin rules, text outlines,
    and ordinary page furniture fail the area/darkness checks.
    """
    if rendered_image is None:
        return []
    width = int(getattr(rendered_image, "width", 0) or 0)
    height = int(getattr(rendered_image, "height", 0) or 0)
    if width <= 0 or height <= 0 or page.bbox.width <= 0 or page.bbox.height <= 0:
        return []

    candidates: list[BBox] = []
    for path in page.objects.paths:
        box = path.bbox
        if box is None or box.area < 500.0:
            continue
        if box.width < 80.0 or box.height < 4.0 or box.width / max(box.height, 1.0) < 3.0:
            continue
        visual_box = box.rotate_to_visual(
            page.objects.rotation,
            page.bbox.width,
            page.bbox.height,
        )
        pixels = _crop_pixels(rendered_image, visual_box, page.bbox, width, height)
        if pixels is None:
            continue
        dark_ratio, mean_luma = pixels
        if dark_ratio >= 0.82 and mean_luma <= 65.0:
            if not any(box.iou(previous) >= 0.85 for previous in candidates):
                candidates.append(box)
    return candidates


def characters_occluded(
    characters: Iterable[NativeCharacter],
    boxes: Iterable[BBox],
) -> tuple[list[NativeCharacter], int]:
    """Filter characters substantially covered by an opaque path."""
    occlusion_boxes = tuple(boxes)
    if not occlusion_boxes:
        return list(characters), 0
    visible: list[NativeCharacter] = []
    suppressed = 0
    for character in characters:
        covered = any(character.bbox.overlap_ratio(box) >= 0.35 for box in occlusion_boxes)
        if covered:
            suppressed += 1
        else:
            visible.append(character)
    return visible, suppressed


def _crop_pixels(
    image: object,
    box: BBox,
    page_bbox: BBox,
    width: int,
    height: int,
) -> tuple[float, float] | None:
    left = max(0, min(width - 1, int(box.x0 / page_bbox.width * width)))
    top = max(0, min(height - 1, int(box.y0 / page_bbox.height * height)))
    right = max(left + 1, min(width, int(box.x1 / page_bbox.width * width + 1)))
    bottom = max(top + 1, min(height, int(box.y1 / page_bbox.height * height + 1)))
    try:
        pixels = image.crop((left, top, right, bottom)).convert("RGB")
        values = list(pixels.getdata())
    except Exception:
        return None
    if not values:
        return None
    luminances = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in values]
    dark_ratio = sum(value <= 65.0 for value in luminances) / len(luminances)
    return dark_ratio, sum(luminances) / len(luminances)
