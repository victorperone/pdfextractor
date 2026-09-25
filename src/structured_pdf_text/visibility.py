"""Visibility checks that reconcile native objects with the rendered page."""

from __future__ import annotations

from collections.abc import Iterable
from statistics import pstdev

from .document import NativeCharacter, NativePageEvidence
from .geometry import BBox


def detect_opaque_occlusion_boxes(
    page: NativePageEvidence,
    rendered_image: object | None,
) -> list[BBox]:
    """Find solid path/image regions that hide native text in the rendered page.

    PDFium does not expose paint state consistently for paths. Candidate geometry
    is therefore cross-checked against the rendered pixels. Dark boxes must be
    predominantly dark, while white boxes must be nearly uniform.
    """
    if rendered_image is None:
        return []
    width = int(getattr(rendered_image, "width", 0) or 0)
    height = int(getattr(rendered_image, "height", 0) or 0)
    if width <= 0 or height <= 0 or page.bbox.width <= 0 or page.bbox.height <= 0:
        return []

    object_boxes = [
        (path.bbox, "path") for path in page.objects.paths
    ] + [
        (image.bbox, "image") for image in page.objects.images
    ]
    candidates: list[BBox] = []
    for box, source in object_boxes:
        if box is None or box.area < 500.0:
            continue
        if box.width < 40.0 or box.height < 4.0:
            continue
        aspect = max(box.width, box.height) / max(min(box.width, box.height), 1.0)
        if source == "path" and aspect < 3.0:
            continue
        if source == "image" and aspect < 1.5:
            continue
        visual_box = box.rotate_to_visual(
            page.objects.rotation,
            page.bbox.width,
            page.bbox.height,
        )
        pixels = _crop_pixels(rendered_image, visual_box, page.bbox, width, height)
        if pixels is None:
            continue
        dark_ratio, mean_luma, luma_stddev = pixels
        solid_dark = dark_ratio >= 0.60 and mean_luma <= 70.0 and luma_stddev <= 100.0
        solid_white = mean_luma >= 248.0 and luma_stddev <= 8.0
        if solid_dark or solid_white:
            if not any(box.iou(previous) >= 0.85 for previous in candidates):
                candidates.append(box)
    return candidates


def characters_inside_page(
    characters: Iterable[NativeCharacter],
    page_bbox: BBox,
) -> tuple[list[NativeCharacter], int]:
    """Keep characters with at least some glyph area inside the visible page box."""
    visible: list[NativeCharacter] = []
    outside = 0
    for character in characters:
        # Character boxes may extend a few points beyond the crop because of
        # font metrics. Treat a glyph as off-page only when its center lies
        # beyond the visible page; partial edge overlap is still visible.
        if not (
            page_bbox.x0 <= character.bbox.cx <= page_bbox.x1
            and page_bbox.y0 <= character.bbox.cy <= page_bbox.y1
        ):
            outside += 1
        else:
            visible.append(character)
    return visible, outside


def characters_occluded(
    characters: Iterable[NativeCharacter],
    boxes: Iterable[BBox],
) -> tuple[list[NativeCharacter], int]:
    """Filter characters substantially covered by an opaque object."""
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
) -> tuple[float, float, float] | None:
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
    return dark_ratio, sum(luminances) / len(luminances), pstdev(luminances)
