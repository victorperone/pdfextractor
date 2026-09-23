"""Cell-level token assignment for reconstructed table grids.

Provides :func:`tokens_in_cell`, which maps raw page tokens to an individual
table cell bounding box using overlap heuristics.  The function retains
explicit whitespace tokens that fall within a cell's x range, which is
necessary for accurate text reconstruction in cells that contain deliberate
spacing.
"""
from __future__ import annotations

from structured_pdf_text.document import TextLine, TextToken
from structured_pdf_text.geometry import BBox


def tokens_in_cell(
    lines: list[TextLine],
    cell: BBox,
    minimum_overlap: float = 0.35,
) -> list[TextToken]:
    """Assign native tokens to a cell without losing explicit whitespace."""
    tokens: list[TextToken] = []
    for line in sorted(lines, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        for token in line.tokens:
            if token.bbox.area == 0:
                if token.text.isspace() and cell.x0 <= token.bbox.x0 <= cell.x1:
                    tokens.append(token)
                continue
            if (
                token.bbox.overlap_ratio(cell) >= minimum_overlap
                or cell.overlap_ratio(token.bbox) >= minimum_overlap
            ) and cell.x0 <= token.bbox.cx <= cell.x1 and cell.y0 <= token.bbox.cy <= cell.y1:
                tokens.append(token)
    return tokens
