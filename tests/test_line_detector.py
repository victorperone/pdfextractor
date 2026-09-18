from __future__ import annotations

import math

from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    NativeCharacter,
    SourceKind,
    TextLine,
    TextToken,
    TokenFlag,
    WritingDirection,
)
from structured_pdf_text.text.normalize import normalize_text
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.line_detector import (
    _is_ghost_punctuation_line,
    _mark_ghost_punctuation_candidates,
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


# ── P1-1: ghost punctuation preserved pre-ledger ──────────────────────────────

def _text_line(text: str, width: float = 8.0, height: float = 10.0) -> TextLine:
    bbox = BBox(0.0, 0.0, width, height)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"ghost-test:{text}")],
        confidence=1.0,
        normalized_text=normalize_text(text),
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=height),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
    )


def test_ghost_punctuation_line_is_marked_not_filtered() -> None:
    """A comma in a narrow box is marked as a candidate, never removed."""
    comma = _text_line(",", width=6.0, height=8.0)
    result = _mark_ghost_punctuation_candidates([comma])

    assert len(result) == 1
    assert result[0].ghost_punctuation_candidate is True


def test_ghost_punctuation_survives_reconstruct_native_lines() -> None:
    """After full reconstruction, ghost-candidate lines must be present in output."""
    characters = (
        _char(index=0, text="A", bbox=BBox(0.0, 0.0, 10.0, 10.0), angle=0.0),
        _char(index=1, text=",", bbox=BBox(12.0, 3.0, 17.0, 8.0), angle=0.0),
    )
    lines = reconstruct_native_lines(characters)

    texts = [ln.text.strip() for ln in lines]
    assert "," in " ".join(texts) or any("," in t for t in texts), (
        "Comma must not be silently removed before the conservation ledger"
    )


def test_non_ghost_punctuation_is_not_marked() -> None:
    """A colon in a wide box (e.g. form label) is not flagged as ghost."""
    wide_colon = _text_line(":", width=30.0, height=10.0)
    result = _mark_ghost_punctuation_candidates([wide_colon])

    assert result[0].ghost_punctuation_candidate is False


def test_ghost_candidate_flag_false_by_default() -> None:
    """TextLine.ghost_punctuation_candidate defaults to False."""
    line = _text_line("texto normal")
    assert line.ghost_punctuation_candidate is False


def test_is_ghost_punctuation_line_detects_narrow_comma() -> None:
    """_is_ghost_punctuation_line correctly identifies a narrow comma."""
    narrow = _text_line(",", width=6.0, height=8.0)
    wide = _text_line(",", width=30.0, height=8.0)

    assert _is_ghost_punctuation_line(narrow) is True
    assert _is_ghost_punctuation_line(wide) is False
