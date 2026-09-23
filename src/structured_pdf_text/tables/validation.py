"""Auditable geometry checks for reconstructed tables.

This module validates the canonical table model after a detector has built it.
It deliberately does not repair or reverse candidates: an uncertain structure
is rejected by the caller so the source lines can remain prose.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import (
    SourceKind,
    OcrToken,
    StructuredTable,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class TableGeometryValidation:
    """Summary result of :func:`validate_table_geometry`.

    Attributes:
        valid: ``True`` when no validation criterion was violated.
        row_monotonicity: Fraction of consecutive row-centre pairs that are
            non-decreasing (1.0 = perfectly ordered).
        column_monotonicity: Fraction of consecutive column-anchor pairs that
            are monotone in the reading direction.
        token_coverage: Fraction of source tokens whose centre falls inside
            any cell bbox.
        empty_cell_ratio: Fraction of cells with neither text nor tokens.
        source_row_assignment_monotonicity: Fraction of source-line-to-row
            assignments that are non-decreasing by line y-centre.
        source_column_assignment_monotonicity: Fraction of within-line
            token-to-column assignments that are monotone in reading direction.
        source_assignment_conflicts: Total count of ordering violations and
            unassigned tokens.
        reasons: Ordered set of validation failure labels; empty when valid.
    """

    valid: bool
    row_monotonicity: float
    column_monotonicity: float
    token_coverage: float
    empty_cell_ratio: float
    source_row_assignment_monotonicity: float
    source_column_assignment_monotonicity: float
    source_assignment_conflicts: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableConstructionDiagnostics:
    """Rich per-candidate diagnostic snapshot for page-level audit logs.

    Captures the full construction context — source lines, inferred row and
    column coordinates, geometry validation results, and token provenance — so
    that a failed or suspect table can be debugged without re-running detection.
    """

    candidate_id: str
    method: str
    region_bbox: BBox | None
    candidate_line_count: int
    line_ids: tuple[str, ...]
    line_y_centers: tuple[float, ...]
    line_x_ranges: tuple[tuple[float, float], ...]
    line_texts: tuple[str, ...]
    row_centers: tuple[float, ...]
    column_anchors: tuple[float, ...]
    table_bbox: BBox | None
    first_row_bbox: BBox | None
    last_row_bbox: BBox | None
    first_column_anchor: float | None
    last_column_anchor: float | None
    row_count: int
    column_count: int
    row_monotonic: bool
    column_monotonic: bool
    source_row_assignment_monotonicity: float
    source_column_assignment_monotonicity: float
    source_assignment_conflicts: int
    cell_count: int
    token_coverage: float
    token_sources: tuple[str, ...]
    reasons: tuple[str, ...]


def validate_table_geometry(
    table: StructuredTable,
    *,
    writing_direction: WritingDirection = WritingDirection.LEFT_TO_RIGHT,
    source_lines: list[TextLine] | tuple[TextLine, ...] = (),
) -> TableGeometryValidation:
    """Validate a table without changing its cells or coordinate system."""
    reasons: list[str] = []
    cells = list(table.cells)
    fragments = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    candidate_bbox = BBox.union_all(fragments) if fragments else None

    if table.row_count <= 0 or table.column_count <= 0:
        reasons.append("invalid_table_dimensions")
    if not cells:
        reasons.append("no_cells")

    row_centers = _row_centers(table)
    column_anchors = _column_anchors(table)
    row_indexes = {cell.row for cell in cells}
    column_indexes = {cell.col for cell in cells}
    if row_indexes != set(range(max(table.row_count, 0))):
        reasons.append("missing_table_row")
    if column_indexes != set(range(max(table.column_count, 0))):
        reasons.append("missing_table_column")
    row_monotonicity = _monotonicity(row_centers)
    columns_increasing = writing_direction != WritingDirection.RIGHT_TO_LEFT
    column_monotonicity = _monotonicity(column_anchors, increasing=columns_increasing)
    if row_monotonicity < 1.0:
        reasons.append("row_order_not_monotonic")
    if column_monotonicity < 1.0:
        reasons.append("column_order_not_monotonic")

    for cell in cells:
        if cell.row < 0 or cell.row >= table.row_count or cell.col < 0 or cell.col >= table.column_count:
            reasons.append("cell_index_out_of_range")
            break
        if cell.rowspan <= 0 or cell.colspan <= 0:
            reasons.append("invalid_cell_span")
            break
        if cell.row + cell.rowspan > table.row_count or cell.col + cell.colspan > table.column_count:
            reasons.append("cell_span_out_of_range")
            break
        if cell.bbox is None:
            reasons.append("cell_bbox_missing")
            continue
        if cell.bbox.width <= 0 or cell.bbox.height <= 0:
            reasons.append("cell_bbox_non_positive")
        if candidate_bbox is not None and not _contains_with_tolerance(candidate_bbox, cell.bbox):
            reasons.append("cell_outside_candidate_bbox")

    if _has_unexpected_overlap(cells):
        reasons.append("unexpected_cell_overlap")

    source_tokens = _source_tokens(source_lines, candidate_bbox)
    source_row_monotonicity, source_column_monotonicity, source_conflicts = (
        _source_assignment_metrics(source_lines, cells, writing_direction, candidate_bbox)
    )
    if source_row_monotonicity < 1.0:
        reasons.append("source_row_assignment_not_monotonic")
    if source_column_monotonicity < 1.0:
        reasons.append("source_column_assignment_not_monotonic")
    if source_conflicts:
        reasons.append("source_assignment_conflicts")
    if source_tokens:
        token_coverage = sum(
            any(
                cell.bbox is not None
                and cell.bbox.x0 <= token.bbox.cx <= cell.bbox.x1
                and cell.bbox.y0 <= token.bbox.cy <= cell.bbox.y1
                for cell in cells
            )
            for token in source_tokens
        ) / len(source_tokens)
    else:
        token_coverage = 1.0
    empty_cells = sum(not cell.text.strip() and not cell.tokens for cell in cells)
    empty_cell_ratio = empty_cells / max(len(cells), 1)
    if source_tokens and token_coverage < 0.50:
        reasons.append("low_token_coverage")
    if source_tokens and empty_cell_ratio > 0.90:
        reasons.append("excessive_empty_cells")

    return TableGeometryValidation(
        valid=not reasons,
        row_monotonicity=round(row_monotonicity, 6),
        column_monotonicity=round(column_monotonicity, 6),
        token_coverage=round(token_coverage, 6),
        empty_cell_ratio=round(empty_cell_ratio, 6),
        source_row_assignment_monotonicity=round(source_row_monotonicity, 6),
        source_column_assignment_monotonicity=round(source_column_monotonicity, 6),
        source_assignment_conflicts=source_conflicts,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def build_table_construction_diagnostics(
    table: StructuredTable,
    *,
    source_lines: list[TextLine] | tuple[TextLine, ...] = (),
    writing_direction: WritingDirection = WritingDirection.LEFT_TO_RIGHT,
    region_bbox: BBox | None = None,
) -> TableConstructionDiagnostics:
    """Create compact candidate facts suitable for page diagnostics."""
    validation = validate_table_geometry(
        table,
        writing_direction=writing_direction,
        source_lines=source_lines,
    )
    lines = tuple(source_lines)
    line_ids = tuple(_line_id(line, index) for index, line in enumerate(lines))
    row_boxes = _row_bboxes(table)
    table_bbox = _table_bbox(table)
    token_sources = sorted(
        {
            provenance
            for cell in table.cells
            for token in cell.tokens
            for provenance in _token_provenance(token)
        }
    )
    row_centers = _row_centers(table)
    anchors = _column_anchors(table)
    return TableConstructionDiagnostics(
        candidate_id=table.table_id,
        method=table.method.value,
        region_bbox=region_bbox,
        candidate_line_count=len(lines),
        line_ids=line_ids,
        line_y_centers=tuple(round(line.bbox.cy, 6) for line in lines),
        line_x_ranges=tuple((round(line.bbox.x0, 6), round(line.bbox.x1, 6)) for line in lines),
        line_texts=tuple(_summarize_text(line.text) for line in lines),
        row_centers=tuple(round(value, 6) for value in row_centers),
        column_anchors=tuple(round(value, 6) for value in anchors),
        table_bbox=table_bbox,
        first_row_bbox=row_boxes[0] if row_boxes else None,
        last_row_bbox=row_boxes[-1] if row_boxes else None,
        first_column_anchor=anchors[0] if anchors else None,
        last_column_anchor=anchors[-1] if anchors else None,
        row_count=table.row_count,
        column_count=table.column_count,
        row_monotonic=validation.row_monotonicity == 1.0,
        column_monotonic=validation.column_monotonicity == 1.0,
        source_row_assignment_monotonicity=validation.source_row_assignment_monotonicity,
        source_column_assignment_monotonicity=validation.source_column_assignment_monotonicity,
        source_assignment_conflicts=validation.source_assignment_conflicts,
        cell_count=len(table.cells),
        token_coverage=validation.token_coverage,
        token_sources=tuple(token_sources),
        reasons=validation.reasons,
    )


def _row_centers(table: StructuredTable) -> list[float]:
    output: list[float] = []
    for row in range(max(table.row_count, 0)):
        boxes = [cell.bbox for cell in table.cells if cell.row == row and cell.bbox is not None]
        if boxes:
            output.append(median(box.cy for box in boxes))
    return output


def _column_anchors(table: StructuredTable) -> list[float]:
    output: list[float] = []
    for column in range(max(table.column_count, 0)):
        boxes = [cell.bbox for cell in table.cells if cell.col == column and cell.bbox is not None]
        if boxes:
            output.append(median(box.x0 for box in boxes))
    return output


def _row_bboxes(table: StructuredTable) -> list[BBox]:
    output: list[BBox] = []
    for row in range(max(table.row_count, 0)):
        boxes = [cell.bbox for cell in table.cells if cell.row == row and cell.bbox is not None]
        if boxes:
            output.append(BBox.union_all(boxes))
    return output


def _table_bbox(table: StructuredTable) -> BBox | None:
    boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    if boxes:
        return BBox.union_all(boxes)
    cell_boxes = [cell.bbox for cell in table.cells if cell.bbox is not None]
    return BBox.union_all(cell_boxes) if cell_boxes else None


def _source_tokens(
    source_lines: list[TextLine] | tuple[TextLine, ...],
    candidate_bbox: BBox | None,
) -> list[TextToken]:
    tokens = [token for line in source_lines for token in line.tokens if token.text.strip()]
    if candidate_bbox is None:
        return tokens
    return [
        token
        for token in tokens
        if candidate_bbox.x0 <= token.bbox.cx <= candidate_bbox.x1
        and candidate_bbox.y0 <= token.bbox.cy <= candidate_bbox.y1
    ]


def _source_assignment_metrics(
    source_lines: list[TextLine] | tuple[TextLine, ...],
    cells: list,
    writing_direction: WritingDirection,
    candidate_bbox: BBox | None,
) -> tuple[float, float, int]:
    """Measure how consistently source lines map to table rows and columns.

    For each source line, tokens are assigned to cells by centre-point
    containment.  The row monotonicity is computed over (line_y_centre, row)
    pairs sorted by y; column monotonicity is delegated to
    :func:`_monotonicity_from_ordered_columns`.  Unassigned tokens increment
    the conflict counter.

    Returns ``(row_monotonicity, column_monotonicity, conflict_count)`` as a
    3-tuple of ``(float, float, int)``.
    """
    if not source_lines or not cells:
        return 1.0, 1.0, 0
    line_rows: list[tuple[float, int]] = []
    column_conflicts = 0
    assignment_conflicts = 0
    for line in source_lines:
        tokens = [token for token in line.tokens if token.text.strip()]
        if candidate_bbox is not None:
            tokens = [
                token for token in tokens
                if candidate_bbox.x0 <= token.bbox.cx <= candidate_bbox.x1
                and candidate_bbox.y0 <= token.bbox.cy <= candidate_bbox.y1
            ]
        assignments = [(_cell_for_token(token, cells), token) for token in tokens]
        assignments = [(cell, token) for cell, token in assignments if cell is not None]
        if not assignments:
            if tokens:
                assignment_conflicts += len(tokens)
            continue
        row_values = [cell.row for cell, _ in assignments]
        line_rows.append((line.bbox.cy, _median_int(row_values)))
        ordered = sorted(
            assignments,
            key=lambda item: item[1].bbox.x0,
            reverse=writing_direction == WritingDirection.RIGHT_TO_LEFT,
        )
        columns = [cell.col for cell, _ in ordered]
        column_conflicts += sum(
            first > second
            for first, second in zip(columns, columns[1:])
        )
    line_rows.sort(key=lambda item: item[0])
    row_values = [row for _, row in line_rows]
    row_conflicts = sum(first > second for first, second in zip(row_values, row_values[1:]))
    conflicts = row_conflicts + column_conflicts + assignment_conflicts
    return (
        _monotonicity(row_values),
        _monotonicity_from_ordered_columns(source_lines, cells, writing_direction, candidate_bbox),
        conflicts,
    )


def _monotonicity_from_ordered_columns(
    source_lines: list[TextLine] | tuple[TextLine, ...],
    cells: list,
    writing_direction: WritingDirection,
    candidate_bbox: BBox | None,
) -> float:
    """Compute the per-line column-ordering monotonicity across all source lines.

    Within each source line, tokens are sorted by x0 (reversed for
    right-to-left text) and their assigned column indices are checked for
    non-decreasing order.  Returns the fraction of consecutive column pairs
    that satisfy the ordering constraint, or 1.0 when no multi-token lines
    exist.
    """
    total = 0
    valid = 0
    for line in source_lines:
        tokens = [token for token in line.tokens if token.text.strip()]
        if candidate_bbox is not None:
            tokens = [
                token for token in tokens
                if candidate_bbox.x0 <= token.bbox.cx <= candidate_bbox.x1
                and candidate_bbox.y0 <= token.bbox.cy <= candidate_bbox.y1
            ]
        assignments = [(_cell_for_token(token, cells), token) for token in tokens]
        assignments = [(cell, token) for cell, token in assignments if cell is not None]
        ordered = sorted(
            assignments,
            key=lambda item: item[1].bbox.x0,
            reverse=writing_direction == WritingDirection.RIGHT_TO_LEFT,
        )
        columns = [cell.col for cell, _ in ordered]
        if len(columns) < 2:
            continue
        total += len(columns) - 1
        valid += sum(first <= second for first, second in zip(columns, columns[1:]))
    return valid / total if total else 1.0


def _cell_for_token(token: TextToken, cells: list):
    containing = [
        cell
        for cell in cells
        if cell.bbox is not None
        and cell.bbox.x0 <= token.bbox.cx <= cell.bbox.x1
        and cell.bbox.y0 <= token.bbox.cy <= cell.bbox.y1
    ]
    if containing:
        return min(containing, key=lambda cell: cell.bbox.area)
    overlaps = [
        cell
        for cell in cells
        if cell.bbox is not None and cell.bbox.intersection(token.bbox) is not None
    ]
    if not overlaps:
        return None
    return max(
        overlaps,
        key=lambda cell: cell.bbox.intersection(token.bbox).area,
    )


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _has_unexpected_overlap(cells: list) -> bool:
    for index, first in enumerate(cells):
        if first.bbox is None:
            continue
        for second in cells[index + 1 :]:
            if second.bbox is None:
                continue
            if first.bbox.intersection(second.bbox) is None:
                continue
            return True
    return False


def _contains_with_tolerance(
    container: BBox,
    content: BBox,
    tolerance: float = 1.0,
) -> bool:
    return (
        content.x0 >= container.x0 - tolerance
        and content.y0 >= container.y0 - tolerance
        and content.x1 <= container.x1 + tolerance
        and content.y1 <= container.y1 + tolerance
    )


def _monotonicity(values: list[float], *, increasing: bool = True) -> float:
    if len(values) < 2:
        return 1.0
    valid = sum(
        (first <= second) if increasing else (first >= second)
        for first, second in zip(values, values[1:])
    )
    return valid / (len(values) - 1)


def _line_id(line: TextLine, index: int) -> str:
    if line.native_order_min is not None:
        return f"native-line:{line.native_order_min}"
    return f"line:{index}"


def _summarize_text(text: str, limit: int = 80) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _token_provenance(token: TextToken | OcrToken) -> set[str]:
    provenance = getattr(token, "provenance", None)
    if provenance:
        return set(provenance.split("|"))
    sources = {ref.source for ref in getattr(token, "sources", [])}
    source = getattr(token, "source", None)
    if source is not None:
        sources.add(source)
    output: set[str] = set()
    if SourceKind.OCR_REGION in sources:
        output.add("targeted_region_recovery")
    if SourceKind.OCR_PAGE in sources:
        output.add("baseline")
    if SourceKind.TABLE_MODEL in sources:
        output.add("visual_table_refinement")
    if SourceKind.NATIVE_PDF in sources or SourceKind.NATIVE_GENERATED in sources:
        output.add("native")
    return output or {"unknown"}
