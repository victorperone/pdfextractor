"""Conservative table header inference from native typography evidence."""
from __future__ import annotations

from statistics import median

from structured_pdf_text.document import TableCell


def infer_header_rows(cells: list[TableCell]) -> tuple[int, ...]:
    """Return row zero only when native weight/size clearly marks a header."""
    rows = {cell.row for cell in cells}
    first = [cell for cell in cells if cell.row == 0 and cell.text.strip()]
    body = [cell for cell in cells if cell.row > 0 and cell.text.strip()]
    if len(rows) < 2 or not first or not body:
        return ()

    token_groups = [cell.tokens for cell in first]
    emphasized = sum(
        bool(tokens) and sum((token.font_weight or 0) >= 600 for token in tokens) >= max(1, len(tokens) // 2)
        for tokens in token_groups
    )
    if emphasized / len(first) >= 0.6:
        return (0,)

    first_sizes = [token.font_size for cell in first for token in cell.tokens if token.font_size]
    body_sizes = [token.font_size for cell in body for token in cell.tokens if token.font_size]
    if first_sizes and body_sizes and median(first_sizes) >= median(body_sizes) * 1.15:
        return (0,)
    return ()
