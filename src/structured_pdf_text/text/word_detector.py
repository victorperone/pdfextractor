"""Word segmentation from assembled text lines.

Splits a ``TextLine`` into ``Word`` objects by treating whitespace tokens as
delimiters. Used downstream by table cell extraction and layout heuristics that
operate at word granularity rather than character granularity.
"""
from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.document import TextLine, TextToken
from structured_pdf_text.geometry import BBox


@dataclass(slots=True)
class Word:
    text: str
    bbox: BBox
    tokens: list[TextToken]


def words_from_line(line: TextLine) -> list[Word]:
    """Split a ``TextLine`` into individual ``Word`` objects at whitespace boundaries.

    Each ``Word`` carries the concatenated text, the union bounding box of its
    constituent tokens, and a reference to those tokens for downstream
    geometry-aware consumers.
    """
    words: list[Word] = []
    current: list[TextToken] = []
    for token in line.tokens:
        if token.text.isspace():
            _flush_word(words, current)
            current = []
        else:
            current.append(token)
    _flush_word(words, current)
    return words


def _flush_word(words: list[Word], tokens: list[TextToken]) -> None:
    if not tokens:
        return
    words.append(
        Word(
            text="".join(token.text for token in tokens),
            bbox=BBox.union_all([token.bbox for token in tokens]),
            tokens=list(tokens),
        )
    )
