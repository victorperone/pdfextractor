"""Geometry-aware text joining shared by table reconstruction tiers."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from structured_pdf_text.document import TextToken
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class _CellTextView:
    text: str
    bbox: BBox


def _reading_cell_tokens(tokens):
    rotations = {getattr(t, "rotation", 0) % 360 for t in tokens if t.text.strip()}
    if len(rotations) != 1 or not rotations.issubset({90, 180, 270}):
        return tokens
    rotation = rotations.pop()
    # A shared translation does not change ordering or spacing. Zero-size
    # reference dimensions give the inverse turn without a page dependency.
    return [_CellTextView(t.text, t.bbox.rotate_to_visual((-rotation) % 360, 0., 0.)) for t in tokens]


def join_table_tokens(tokens: Iterable[TextToken]) -> str:
    """Join a collection of tokens into a single cell-text string.

    Two paths are taken depending on token content:

    * **Character tokens** — when every non-space token is a single character
      (common in OCR output for individual glyph boxes), delegates to
      :func:`_join_character_tokens`, which groups characters into rows by
      their y-centre before assembling the string.
    * **Word tokens** — sorts tokens left-to-right, inserts a space whenever
      the horizontal gap between consecutive tokens exceeds a height-relative
      threshold, and concatenates the result.

    Returns the stripped assembled string.
    """
    source = [token for token in tokens if token.text]
    source = _reading_cell_tokens(source)
    nonspace = [token for token in source if token.text.strip()]
    if nonspace and all(len(token.text.strip()) == 1 for token in nonspace):
        return _join_character_tokens(source)
    # A cell can contain several visual lines. Sorting all words by x first
    # interleaves their contents (including amounts); order lines before words.
    rows: list[list[TextToken]] = []
    for token in sorted(source, key=lambda t: (t.bbox.y0, t.bbox.x0)):
        matching = next((row for row in rows if any(
            max(0., min(token.bbox.y1, t.bbox.y1)-max(token.bbox.y0, t.bbox.y0))
            >= .3*min(token.bbox.height, t.bbox.height)
            for t in row if min(token.bbox.height, t.bbox.height) > 0)), None)
        if matching is None:
            rows.append([token])
        else:
            matching.append(token)
    ordered = [(row_index, token) for row_index, row in enumerate(rows)
               for token in sorted(row, key=lambda t: (t.bbox.x0, t.bbox.y0))]
    output: list[str] = []
    previous: TextToken | None = None
    previous_row = None
    for row_index, token in ordered:
        text = token.text
        if previous_row is not None and row_index != previous_row:
            if output and not output[-1].endswith(" "):
                output.append(" ")
            previous = None
        previous_row = row_index
        if not text.strip():
            if output and not output[-1].endswith(" "):
                output.append(" ")
            previous = token
            continue
        if previous is not None and previous.text.strip():
            gap = token.bbox.x0 - previous.bbox.x1
            height = min(
                value for value in (previous.bbox.height, token.bbox.height)
                if value > 0
            ) if previous.bbox.height > 0 and token.bbox.height > 0 else 8.0
            # Character tracking is commonly around 0.10–0.20 of the glyph
            # height, while a word gap is materially larger. A conservative
            # threshold avoids inventing spaces inside tracked words such as
            # ``Código`` while still separating OCR word boxes.
            threshold = max(1.5, height * 0.25)
            if gap > threshold and not output[-1].endswith((" ", "-", "\n")):
                output.append(" ")
        output.append(text)
        previous = token
    return "".join(output).strip()


def join_native_cell_tokens(tokens: Iterable[TextToken]) -> str:
    """Join tokens already assigned to one native cell/line.

    PDF text extraction can return each glyph as a separate token and give
    punctuation a slightly different vertical box. Within a word span those
    boxes still belong to one baseline, so use their horizontal order and do
    not infer spaces from ordinary glyph advances. Explicit whitespace is
    honored when present; multi-character tokens use the general joiner.
    """
    source = [token for token in tokens if token.text]
    visible = [token for token in source if token.text.strip()]
    if visible and all(len(token.text.strip()) == 1 for token in visible):
        ordered = sorted(source, key=lambda token: (token.bbox.x0, token.bbox.y0))
        return "".join(token.text for token in ordered).strip()
    return join_table_tokens(source)


def recover_cell_text(text: str, extracted_text: str) -> str:
    """Restore semantic punctuation/spaces from the page text stream.

    Cell geometry determines ownership; the text-page stream often retains
    punctuation and word boundaries that character boxes lose. Only an exact
    compact match is accepted, so this cannot invent a value absent from the
    immutable native text evidence. The match is case-sensitive: the glyphs
    owned by the cell keep their case even when the page repeats the word
    elsewhere with another capitalization.
    """
    target = _compact(text)
    if len(target) < 2:
        return text
    words = re.findall(r"\S+", extracted_text.replace("\r", " ").replace("\n", " "))
    matches: list[tuple[int, int, str]] = []
    for start in range(len(words)):
        compact = ""
        for end in range(start, min(len(words), start + 12)):
            compact += _compact(words[end])
            if compact == target:
                candidate = " ".join(words[start : end + 1]).strip()
                matches.append((0, end - start, candidate))
            if len(compact) > len(target):
                break
    if matches:
        return min(matches, key=lambda item: (item[0], item[1]))[2]
    return text


def _compact(value: str) -> str:
    # Ignore whitespace only. Case, punctuation, decimal values, signs and
    # identifiers carry meaning and must not be normalized away.
    return "".join(value.split())


def _join_character_tokens(tokens: list[TextToken]) -> str:
    """Assemble a string from single-character OCR tokens.

    Clusters visible tokens into horizontal rows based on their y0 coordinates
    (using the median glyph height as the row-membership tolerance), then sorts
    each row by x0 and concatenates characters.  A space is emitted between
    tokens in the same row when the inter-token gap exceeds the height-relative
    threshold, and between tokens on different rows.
    """
    visible = [token for token in tokens if token.text.strip()]
    if not visible:
        return ""
    heights = [token.bbox.height for token in visible if token.bbox.height > 0]
    line_height = sorted(heights)[len(heights) // 2] if heights else 8.0
    rows: list[list[TextToken]] = []
    row_centers: list[float] = []
    for token in sorted(visible, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        nearest = min(
            range(len(row_centers)),
            key=lambda index: abs(row_centers[index] - token.bbox.y0),
            default=None,
        )
        if nearest is None or abs(row_centers[nearest] - token.bbox.y0) > line_height * 0.55:
            rows.append([token])
            row_centers.append(token.bbox.y0)
        else:
            rows[nearest].append(token)
            row_centers[nearest] = sum(item.bbox.y0 for item in rows[nearest]) / len(rows[nearest])
    row_by_token = {
        id(token): index
        for index, row in enumerate(rows)
        for token in row
    }
    ordered = [token for row in rows for token in sorted(row, key=lambda item: item.bbox.x0)]
    output: list[str] = []
    previous: TextToken | None = None
    previous_row: int | None = None
    for token in ordered:
        row = row_by_token[id(token)]
        if previous is not None:
            gap = token.bbox.x0 - previous.bbox.x1
            threshold = max(1.8, line_height * 0.22)
            if row != previous_row or gap > threshold:
                if output and not output[-1].endswith(" "):
                    output.append(" ")
        output.append(token.text)
        previous = token
        previous_row = row
    return "".join(output).strip()
