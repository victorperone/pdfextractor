from __future__ import annotations

import math

from structured_pdf_text.document import (
    NativeCharacter,
    TokenFlag,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.line_detector import (
    reconstruct_native_lines,
)


def _char(
    *,
    index: int,
    text: str,
    bbox: BBox,
    angle: float,
) -> NativeCharacter:
    return NativeCharacter(
        page_index=0,
        char_index=index,
        text=text,
        unicode_codepoint=ord(text),
        bbox=bbox,
        angle=angle,
    )


def test_vertical_reverse_inferred_gap_has_valid_bbox():
    characters = (
        _char(
            index=0,
            text="A",
            bbox=BBox(
                100.0,
                100.0,
                110.0,
                110.0,
            ),
            angle=3 * math.pi / 2,
        ),
        _char(
            index=1,
            text="B",
            bbox=BBox(
                100.0,
                75.0,
                110.0,
                85.0,
            ),
            angle=3 * math.pi / 2,
        ),
    )

    lines = reconstruct_native_lines(characters)

    assert len(lines) == 1

    whitespace_tokens = [
        token
        for token in lines[0].tokens
        if TokenFlag.WHITESPACE_INFERRED in token.flags
    ]

    assert len(whitespace_tokens) == 1

    gap = whitespace_tokens[0].bbox

    assert gap.x0 <= gap.x1
    assert gap.y0 <= gap.y1

    assert gap.y0 == 85.0
    assert gap.y1 == 100.0


def test_horizontal_reverse_inferred_gap_has_valid_bbox():
    characters = (
        _char(
            index=0,
            text="A",
            bbox=BBox(
                100.0,
                100.0,
                110.0,
                110.0,
            ),
            angle=math.pi,
        ),
        _char(
            index=1,
            text="B",
            bbox=BBox(
                75.0,
                100.0,
                85.0,
                110.0,
            ),
            angle=math.pi,
        ),
    )

    lines = reconstruct_native_lines(characters)

    assert len(lines) == 1

    whitespace_tokens = [
        token
        for token in lines[0].tokens
        if TokenFlag.WHITESPACE_INFERRED in token.flags
    ]

    assert len(whitespace_tokens) == 1

    gap = whitespace_tokens[0].bbox

    assert gap.x0 <= gap.x1
    assert gap.y0 <= gap.y1

    assert gap.x0 == 85.0
    assert gap.x1 == 100.0
