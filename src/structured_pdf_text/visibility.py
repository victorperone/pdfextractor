"""Visibility checks that reconcile native objects with the rendered page."""

from __future__ import annotations

import math
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


# A glyph whose removal changes no rendered pixel by this many luminance
# levels contributes no visible ink. Covered, clipped, invisible-mode and
# same-color glyphs change nothing at all (difference 0); faint ink such as a
# 20% watermark changes ~13 levels and is visible, so the bar stays low.
GLYPH_INK_THRESHOLD = 4
# A visible glyph marks a substantial part of its box. Glyph overshoot (round
# letters, brackets) protruding one pixel past a covering bar marks only a
# sliver along the bar edge, which is not readable text.
GLYPH_INK_MIN_FRACTION = 0.06
# Pixels added around each glyph box at the analysis scale, so a thin glyph
# (i, l, punctuation) whose box rounds to one pixel column still sees its ink.
GLYPH_BOX_PADDING = 1


def glyph_pixel_box(
    bbox: BBox,
    page_bbox: BBox,
    rotation: int,
    image_size: tuple[int, int],
    padding: int = GLYPH_BOX_PADDING,
) -> tuple[int, int, int, int] | None:
    """Pixel box of a canonical glyph box in a rendered (visual) page image."""
    width, height = image_size
    if width <= 0 or height <= 0 or page_bbox.width <= 0 or page_bbox.height <= 0:
        return None
    visual = bbox.rotate_to_visual(rotation, page_bbox.width, page_bbox.height)
    swapped = rotation % 180 == 90
    visual_w = page_bbox.height if swapped else page_bbox.width
    visual_h = page_bbox.width if swapped else page_bbox.height
    left = int(visual.x0 / visual_w * width) - padding
    top = int(visual.y0 / visual_h * height) - padding
    right = round(visual.x1 / visual_w * width) + padding
    bottom = round(visual.y1 / visual_h * height) + padding
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, max(left + 1, right)), min(height, max(top + 1, bottom))
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def inkless_glyphs(
    rendered: object,
    rendered_without: object,
    boxes: dict[int, tuple[int, int, int, int]],
    threshold: int = GLYPH_INK_THRESHOLD,
) -> set[int]:
    """Keys of glyph boxes whose pixels are unchanged when the glyphs are removed.

    ``rendered`` and ``rendered_without`` are same-size page images (PIL),
    with and without the text objects that own the glyphs. Paint order is
    therefore taken into account: anything drawn later over a glyph hides it.
    """
    from PIL import ImageChops

    difference = ImageChops.difference(rendered.convert("L"), rendered_without.convert("L"))
    hidden: set[int] = set()
    for key, (left, top, right, bottom) in boxes.items():
        inked = sum(difference.crop((left, top, right, bottom)).histogram()[threshold:])
        area = (right - left) * (bottom - top)
        if inked < max(1, math.ceil(GLYPH_INK_MIN_FRACTION * area)):
            hidden.add(key)
    return hidden


def characters_without_ink(
    characters: Iterable[NativeCharacter],
    hidden_indices: Iterable[int],
) -> tuple[list[NativeCharacter], int]:
    """Drop characters whose rendered ink was proven absent (see ``inkless_glyphs``).

    Whitespace has no ink of its own. A space belongs to the hidden text when
    its nearest glyphs on both sides are hidden (or one side is hidden and the
    other is the end of the stream); a separator generated by the text layer
    already when one side is hidden. Keeping them would leave whitespace-only
    lines where the hidden words were.
    """
    characters = list(characters)
    hidden = set(hidden_indices)
    if not hidden:
        return characters, 0
    # Hidden state of the nearest glyph before/after each position; None at
    # the ends of the stream.
    before: list[bool | None] = []
    state: bool | None = None
    for character in characters:
        before.append(state)
        if not character.text.isspace():
            state = character.char_index in hidden
    after: list[bool | None] = [None] * len(characters)
    state = None
    for position in range(len(characters) - 1, -1, -1):
        after[position] = state
        if not characters[position].text.isspace():
            state = characters[position].char_index in hidden
    for position, character in enumerate(characters):
        sides = (before[position], after[position])
        if not character.text.isspace() or True not in sides:
            continue
        # A separator generated by the text layer has no content of its own;
        # next to hidden text it only marks where that text was.
        if False not in sides or getattr(character, "generated", False):
            hidden.add(character.char_index)
    visible = [character for character in characters if character.char_index not in hidden]
    return visible, len(characters) - len(visible)


def characters_inside_page(
    characters: Iterable[NativeCharacter],
    page_bbox: BBox,
) -> tuple[list[NativeCharacter], int]:
    """Keep characters with at least some glyph area inside the visible page box.

    A small y-axis tolerance (15 % of page height) accommodates form XObjects
    whose local coordinate space is not fully transformed to page space before
    pypdfium2 reports character positions.  Without this margin, list items
    physically placed via a form XObject can have cy slightly above y0 (screen
    top) and are incorrectly discarded even though they appear in the page's
    text stream.  BBox coordinates use screen space (y0=0 at top, y increases
    downward to y1=page_height at bottom).  Characters above the page top have
    negative cy values, so the tolerance extends y0 upward (negative direction).
    The tolerance is asymmetric: we allow modest overflow above y0 only, not
    below y1 or beyond x0/x1, because the only observed failure mode is
    y-axis overflow from vertical coordinate misalignment in form XObjects.
    Characters in this above-y0 tolerance zone are exempted from the inkless
    check in api.py (their pixel boxes map to negative image coordinates and
    would be incorrectly classified as hidden even though they are visible).
    """
    y_tolerance = (page_bbox.y1 - page_bbox.y0) * 0.15
    visible: list[NativeCharacter] = []
    outside = 0
    for character in characters:
        # Character boxes may extend a few points beyond the crop because of
        # font metrics. Treat a glyph as off-page only when its center lies
        # beyond the visible page; partial edge overlap is still visible.
        # BBox uses screen coords (y0=top, y increases downward, y1=bottom).
        # Tolerance extends y0 upward (negative) to capture form XObject
        # characters whose screen cy is slightly negative (above page top).
        if not (
            page_bbox.x0 <= character.bbox.cx <= page_bbox.x1
            and page_bbox.y0 - y_tolerance <= character.bbox.cy <= page_bbox.y1
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
