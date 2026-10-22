"""Core table reading order shared by OCR integration and legacy consumers."""
from typing import Any
from structured_pdf_text.document import Baseline, EvidenceRef, SourceKind, TextLine, TextToken, WritingDirection
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.text_join import join_table_tokens


def _rebuild_table_ocr_lines(table: Any, lines: list[TextLine], page_index: int, *, bind_cell_evidence: bool = False) -> list[TextLine]:
    """Serialize OCR text by detected table row/column instead of OCR line order.

    OCR line reconstruction is intentionally page-oriented. On dense raster
    tables it can interleave neighboring rows or split a row into fragments.
    Once a visual grid has assigned tokens to cells, the cell coordinates are
    the stronger ordering signal. Only unmatched OCR tokens are used here so
    the hybrid path does not duplicate authoritative native text.
    """
    if not getattr(table, "cells", None):
        return lines
    source_tokens = [token for line in lines for token in line.tokens]
    if not source_tokens:
        return lines
    # token.rotation is provenance metadata, not a command to mutate the table
    # structure.  By the time tokens reach this function their bbox coordinates
    # are already in canonical page space (box_transform was applied in
    # paddle._tokens_from_result).  Applying any metadata-based axis reversal on
    # top of already-canonical bboxes would produce a double-rotation.  Source geometry
    # validated by _validate_detected_tables is the sole authority for cell order.
    cell_tokens: dict[tuple[int, int], list[TextToken]] = {}
    for cell in table.cells:
        if cell.bbox is None:
            continue
        selected = [
            token
            for token in source_tokens
            if cell.bbox.x0 <= token.bbox.cx <= cell.bbox.x1
            and cell.bbox.y0 <= token.bbox.cy <= cell.bbox.y1
        ]
        while selected and selected[0].text.isspace():
            selected.pop(0)
        while selected and selected[-1].text.isspace():
            selected.pop()
        if selected:
            cell.text = join_table_tokens(selected)
            if bind_cell_evidence:
                cell.tokens = list(selected)
            cell_tokens[(cell.row, cell.col)] = selected

    if not cell_tokens:
        return lines
    rebuilt: list[TextLine] = []
    for row in range(max(0, int(table.row_count))):
        row_tokens: list[TextToken] = []
        occupied_cells = [
            cell
            for cell in table.cells
            if cell.row == row and (cell.row, cell.col) in cell_tokens
        ]
        for cell_index, cell in enumerate(sorted(occupied_cells, key=lambda item: item.col)):
            if cell_index and row_tokens:
                previous = row_tokens[-1]
                current = cell_tokens[(cell.row, cell.col)][0]
                row_tokens.append(
                    TextToken(
                        text=" ",
                        bbox=(
                            BBox(previous.bbox.x1, cell.bbox.y0, current.bbox.x0, cell.bbox.y1)
                            if previous.bbox.x1 <= current.bbox.x0
                            else BBox(previous.bbox.x1, cell.bbox.y0, previous.bbox.x1, cell.bbox.y1)
                        ),
                        sources=[
                            EvidenceRef(
                                SourceKind.TABLE_MODEL,
                                page_index,
                                f"table-gap:{table.table_id}:{cell.row}:{cell.col}",
                            )
                        ],
                        confidence=0.55,
                        normalized_text=" ",
                        flags=set(),
                    )
                )
            row_tokens.extend(cell_tokens[(cell.row, cell.col)])
        if not row_tokens:
            continue
        row_tokens = _merge_short_table_fragments(row_tokens)
        row_bbox = BBox.union_all([token.bbox for token in row_tokens])
        rebuilt.append(
            TextLine(
                tokens=row_tokens,
                bbox=row_bbox,
                baseline=Baseline(y=row_bbox.y1),
                direction=WritingDirection.LEFT_TO_RIGHT,
                native_order_min=row,
                native_order_max=row,
            )
        )
    return rebuilt or lines


def _merge_short_table_fragments(tokens: list[TextToken]) -> list[TextToken]:
    """Remove spaces caused by splitting one alphanumeric word into boxes."""
    compacted: list[TextToken] = []
    for index, token in enumerate(tokens):
        if token.text.isspace():
            previous = next((item for item in reversed(compacted) if not item.text.isspace()), None)
            following = next(
                (item for item in tokens[index + 1 :] if not item.text.isspace()),
                None,
            )
            if (
                previous is not None
                and following is not None
                and len(previous.text.strip()) >= 4
                and len(following.text.strip()) <= 3
                and previous.text.strip()[-1].isalnum()
                and following.text.strip()[0].isalnum()
            ):
                continue
        compacted.append(token)
    return compacted


