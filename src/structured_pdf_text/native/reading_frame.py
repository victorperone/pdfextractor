"""Upright reading frame for pages whose text is drawn upside down.

A page can draw its content rotated by 180 degrees and display it upright with
``/Rotate 180``. Layout, reading order and paragraphs work top-down in page
space, so they would read such a page from its last line to its first. When
the glyphs agree that the text is upside down, the page evidence is mirrored
into the frame where the glyphs stand upright; the declared rotation is
reduced by the same half turn, so rendering and OCR coordinates stay
consistent (PDFium renders the displayed page).
"""
from __future__ import annotations

import math
from dataclasses import replace

from structured_pdf_text.document import NativeCharacter, NativePageEvidence
from structured_pdf_text.geometry import BBox, Point

_MINIMUM_GLYPHS = 20
_MINIMUM_AGREEMENT = 0.8
_ANGLE_TOLERANCE = 0.2


def upside_down(characters: tuple[NativeCharacter, ...] | list[NativeCharacter]) -> bool:
    """Whether most visible glyphs are turned by a half turn."""
    angles = [
        character.angle % (2.0 * math.pi)
        for character in characters
        if character.text.strip() and character.angle is not None
    ]
    if len(angles) < _MINIMUM_GLYPHS:
        return False
    turned = sum(abs(angle - math.pi) <= _ANGLE_TOLERANCE for angle in angles)
    return turned >= _MINIMUM_AGREEMENT * len(angles)


def to_upright_frame(
    page: NativePageEvidence,
    characters: tuple[NativeCharacter, ...],
) -> tuple[NativePageEvidence, tuple[NativeCharacter, ...]]:
    """Mirror *page* and the selected *characters* by a half turn."""
    frame = page.bbox

    def box(value: BBox | None) -> BBox | None:
        if value is None:
            return None
        return BBox(
            frame.x0 + frame.x1 - value.x1,
            frame.y0 + frame.y1 - value.y1,
            frame.x0 + frame.x1 - value.x0,
            frame.y0 + frame.y1 - value.y0,
        )

    def character(value: NativeCharacter) -> NativeCharacter:
        origin = value.origin
        if origin is not None:
            origin = Point(frame.x0 + frame.x1 - origin.x, frame.y0 + frame.y1 - origin.y)
        angle = None if value.angle is None else (value.angle - math.pi) % (2.0 * math.pi)
        return replace(value, bbox=box(value.bbox), origin=origin, angle=angle)

    objects = page.objects
    turned_objects = replace(
        objects,
        images=tuple(replace(item, bbox=box(item.bbox)) for item in objects.images),
        paths=tuple(replace(item, bbox=box(item.bbox)) for item in objects.paths),
        annotations=tuple(replace(item, bbox=box(item.bbox)) for item in objects.annotations),
        objects=tuple(replace(item, bbox=box(item.bbox)) for item in objects.objects),
        rotation=(objects.rotation - 180) % 360,
    )
    return (
        replace(page, characters=tuple(character(item) for item in page.characters), objects=turned_objects),
        tuple(character(item) for item in characters),
    )
