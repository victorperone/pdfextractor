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

    PDFium does not expose paint state consistently for paths. Candidate
    geometry is cross-checked against rendered pixels at both the object and
    glyph scale, so visible text on a solid background is retained while
    characters actually hidden by a solid fill are removed.
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
        pixels = _crop_pixels(rendered_image, visual_box, page.bbox, width, height, page.objects.rotation)
        if pixels is None:
            continue
        dark_ratio, mean_luma, luma_stddev = pixels
        # Antialiasing along a vector rectangle's edges raises its deviation
        # slightly even when its interior is a uniform black fill.
        solid_dark = mean_luma <= 18.0 and dark_ratio >= 0.98 and luma_stddev <= 30.0
        solid_white = mean_luma >= 248.0 and luma_stddev <= 8.0
        if not (solid_dark or solid_white):
            continue
        for character in page.characters:
            if not character.text.strip():
                # Whitespace has no ink to redact and its degenerate/small
                # PDFium box often samples only the surrounding fill.
                continue
            if character.bbox.overlap_ratio(box) < 0.35:
                continue
            visual_character = character.bbox.rotate_to_visual(
                page.objects.rotation, page.bbox.width, page.bbox.height
            )
            glyph_pixels = _crop_pixels(
                rendered_image, visual_character, page.bbox, width, height,
                page.objects.rotation,
            )
            if glyph_pixels is None:
                continue
            _, glyph_luma, glyph_stddev = glyph_pixels
            # A glyph's mean luminance is dominated by the background and
            # antialiasing when its box is small. A nominal white fill can
            # therefore differ greatly from the crop mean even though the
            # white glyph is plainly visible. Only a crop that is itself as
            # uniform as the candidate fill is evidence that the glyph was
            # covered.
            same_solid_fill = abs(glyph_luma - mean_luma) <= 28.0 and glyph_stddev <= 14.0
            if same_solid_fill:
                if not any(character.bbox.iou(previous) >= 0.85 for previous in candidates):
                    candidates.append(character.bbox)
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


def is_char_visible(character: NativeCharacter, *, opacity: float | None = None) -> bool:
    """Return True when a NativeCharacter should be included in line reconstruction.

    VQ-21: provides an explicit visibility policy:
    - Characters with opacity < 0.15 are treated as watermarks and suppressed.
    - Characters whose fill_color alpha channel is < 20 (near-transparent) are
      suppressed. The alpha is the 4th component of fill_color (0-255 scale).
    - All other characters are considered visible.

    ``opacity`` is an optional override in [0, 1]; when provided it takes
    precedence over fill_color inspection (used mainly in tests).
    """
    if opacity is not None:
        return opacity >= 0.15
    if character.fill_color is not None:
        _, _, _, alpha = character.fill_color
        if alpha < 20:
            return False
    return True


def _crop_pixels(
    image: object,
    box: BBox,
    page_bbox: BBox,
    width: int,
    height: int,
    page_rotation: int = 0,
) -> tuple[float, float, float] | None:
    # For 90°/270° rotations the visual image has swapped axes: width maps to
    # page_height and height maps to page_width.
    if page_rotation % 360 in (90, 270):
        visual_w = page_bbox.height
        visual_h = page_bbox.width
    else:
        visual_w = page_bbox.width
        visual_h = page_bbox.height
    # VQ-21: use round() for the right/bottom edges to avoid the +1 offset
    # that caused one extra pixel column/row from outside the region to be
    # included in the luminance sample, potentially biasing solid-fill
    # decisions against text that is visually clear but borders a dark element.
    left = max(0, min(width - 1, int(box.x0 / visual_w * width)))
    top = max(0, min(height - 1, int(box.y0 / visual_h * height)))
    right = max(left + 1, min(width, round(box.x1 / visual_w * width)))
    bottom = max(top + 1, min(height, round(box.y1 / visual_h * height)))
    try:
        pixels = image.crop((left, top, right, bottom)).convert("RGB")
        values = list(pixels.getdata())
    except MemoryError:
        raise
    except Exception:
        return None
    if not values:
        return None
    luminances = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in values]
    dark_ratio = sum(value <= 65.0 for value in luminances) / len(luminances)
    return dark_ratio, sum(luminances) / len(luminances), pstdev(luminances)
