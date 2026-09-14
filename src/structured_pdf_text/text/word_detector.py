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
