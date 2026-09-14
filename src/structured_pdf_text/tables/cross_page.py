from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from statistics import mean

from structured_pdf_text.document import StructuredTable, TableCell
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.model import (
    CrossPageTableResolution,
    RowSignature,
    TableContinuationDecision,
    TableSignature,
)


def resolve_cross_page_tables(
    tables: list[StructuredTable],
    *,
    page_bboxes: dict[int, BBox] | None = None,
    page_titles: dict[int, tuple[str, ...]] | None = None,
) -> list[StructuredTable]:
    """Merge compatible fragments while retaining source-page fragments."""
    resolution = resolve_cross_page_tables_with_diagnostics(
        tables,
        page_bboxes=page_bboxes,
        page_titles=page_titles,
    )
    return list(resolution.tables)


def resolve_cross_page_tables_with_diagnostics(
    tables: list[StructuredTable],
    *,
    page_bboxes: dict[int, BBox] | None = None,
    page_titles: dict[int, tuple[str, ...]] | None = None,
) -> CrossPageTableResolution:
    """Resolve continuations and retain reasons for accepted/rejected pairs."""
    if len(tables) < 2:
        return CrossPageTableResolution(tuple(tables), ())

    page_bboxes = page_bboxes or {}
    page_titles = page_titles or {}
    resolved: list[StructuredTable] = []
    decisions: list[TableContinuationDecision] = []
    ordered = sorted(tables, key=lambda table: (_first_page(table), _table_y(table)))
    for table in ordered:
        following_page = _first_page(table)
        candidates = [
            (index, previous)
            for index, previous in enumerate(resolved)
            if _last_page(previous) is not None
            and following_page == _last_page(previous) + 1
        ]
        evaluated = [
            (
                index,
                _continuation_decision(
                    previous,
                    table,
                    page_bboxes=page_bboxes,
                    page_titles=page_titles,
                ),
            )
            for index, previous in candidates
        ]
        decisions.extend(decision for _, decision in evaluated)
        accepted = [item for item in evaluated if item[1].accepted]
        if accepted:
            index, _decision = max(accepted, key=lambda item: item[1].score)
            resolved[index] = _merge(resolved[index], table)
        else:
            resolved.append(table)
    return CrossPageTableResolution(tuple(resolved), tuple(decisions))


def table_signature(
    table: StructuredTable,
    page_bbox: BBox | None = None,
    page_index: int | None = None,
) -> TableSignature:
    """Expose the signature for diagnostics and future table adapters."""
    header_row = _header_row(table)
    header = _row_signature(table, header_row)
    bbox = _table_bbox(table, page_index) or BBox(0.0, 0.0, 0.0, 0.0)
    signature_page = page_index if page_index is not None else _first_page(table)
    if page_bbox is not None and page_bbox.height > 0:
        top_gap = max(0.0, bbox.y0 - page_bbox.y0) / page_bbox.height
        bottom_gap = max(0.0, page_bbox.y1 - bbox.y1) / page_bbox.height
        touches_top = top_gap <= 0.20
        touches_bottom = bottom_gap <= 0.15
    else:
        touches_top = bbox.y0 <= max(bbox.height * 0.15, 80.0)
        touches_bottom = bbox.y1 >= max(bbox.height * 0.85, 500.0)
    return TableSignature(
        column_count=table.column_count,
        normalized_x_tracks=_x_tracks(table, header_row),
        header_rows=(header,) if header is not None else (),
        first_data_row=_row_signature(table, header_row + 1 if header_row is not None else None),
        last_data_row=_row_signature(table, max((cell.row for cell in table.cells), default=-1)),
        bbox=bbox,
        page_index=signature_page,
        touches_top=touches_top,
        touches_bottom=touches_bottom,
    )


def _can_continue(previous: StructuredTable, following: StructuredTable) -> bool:
    return _continuation_decision(
        previous,
        following,
        page_bboxes={},
        page_titles={},
    ).accepted


def _continuation_decision(
    previous: StructuredTable,
    following: StructuredTable,
    *,
    page_bboxes: dict[int, BBox],
    page_titles: dict[int, tuple[str, ...]],
) -> TableContinuationDecision:
    previous_page = _last_page(previous)
    following_page = _first_page(following)
    reasons: list[str] = []
    facts: dict[str, object] = {}
    score = 0.0
    hard_rejection = False

    adjacent = (
        previous_page is not None
        and following_page is not None
        and following_page == previous_page + 1
    )
    facts["adjacent_pages"] = adjacent
    if adjacent:
        reasons.append("adjacent_pages")
    else:
        reasons.append("pages_not_adjacent")
        hard_rejection = True

    first = table_signature(
        previous,
        page_bboxes.get(previous_page) if previous_page is not None else None,
        previous_page,
    )
    second = table_signature(
        following,
        page_bboxes.get(following_page) if following_page is not None else None,
        following_page,
    )
    columns_match = first.column_count == second.column_count
    facts["previous_columns"] = first.column_count
    facts["following_columns"] = second.column_count
    if columns_match:
        reasons.append("column_count_match")
        score += 2.0
    else:
        reasons.append("column_count_mismatch")
        hard_rejection = True

    track_delta = _track_delta(first.normalized_x_tracks, second.normalized_x_tracks)
    facts["mean_track_delta"] = track_delta
    if track_delta is None:
        reasons.append("x_tracks_unavailable")
    elif track_delta <= 0.08:
        reasons.append("x_tracks_match")
        score += 2.0
    elif track_delta <= 0.14:
        reasons.append("x_tracks_weak_match")
        score += 0.5
    else:
        reasons.append("x_tracks_mismatch")
        hard_rejection = True

    header_similarity = _header_similarity(first, second)
    facts["header_similarity"] = round(header_similarity, 6)
    if header_similarity >= 0.80:
        reasons.append("repeated_header_match")
        score += 3.0
    elif header_similarity >= 0.60:
        reasons.append("similar_header_match")
        score += 2.0
    elif not second.header_rows and _following_starts_with_data(following):
        reasons.append("following_page_starts_with_data")
        score += 1.0
    else:
        reasons.append("headers_not_compatible")
        score -= 1.0

    both_boundaries = first.touches_bottom and second.touches_top
    facts["previous_touches_bottom"] = first.touches_bottom
    facts["following_touches_top"] = second.touches_top
    if both_boundaries:
        reasons.append("both_page_boundaries")
        score += 3.0
    elif first.touches_bottom or second.touches_top:
        reasons.append("one_page_boundary")
        score += 0.5
    else:
        reasons.append("not_near_page_boundaries")
        score -= 2.0

    titles = page_titles.get(following_page, ()) if following_page is not None else ()
    marker = _continuation_marker(following) or any(
        _has_continuation_marker(title) for title in titles
    )
    facts["continuation_marker"] = marker
    if marker:
        reasons.append("continuation_marker")
        score += 4.0

    strong_new_title = bool(titles) and not any(
        _has_continuation_marker(title) for title in titles
    )
    facts["strong_new_title"] = strong_new_title
    if strong_new_title:
        reasons.append("strong_new_title_between_tables")
        score -= 3.0
        if not marker:
            hard_rejection = True
    else:
        reasons.append("no_new_strong_title")

    width_ratio = _width_similarity(first.bbox, second.bbox)
    facts["width_similarity"] = round(width_ratio, 6)
    if width_ratio >= 0.85:
        reasons.append("table_widths_match")
        score += 1.0
    elif width_ratio >= 0.70:
        reasons.append("table_widths_weak_match")
    else:
        reasons.append("table_widths_mismatch")
        score -= 2.0

    type_similarity = _column_type_similarity(previous, following)
    facts["column_type_similarity"] = round(type_similarity, 6)
    if type_similarity >= 0.75:
        reasons.append("column_types_match")
        score += 1.5
    elif type_similarity >= 0.50:
        reasons.append("column_types_weak_match")
        score += 0.5
    else:
        reasons.append("column_types_mismatch")
        score -= 0.5

    if not both_boundaries and not marker:
        # No direct boundary/marker evidence — apply a score penalty instead of
        # a hard rejection. Tables with identical headers and matching column
        # structure score well above the threshold despite the missing evidence.
        reasons.append("missing_boundary_or_marker_evidence")
        score -= 3.0
    threshold = 7.0
    accepted = not hard_rejection and score >= threshold
    if not accepted and not hard_rejection:
        reasons.append("continuation_score_below_threshold")
    facts["threshold"] = threshold
    return TableContinuationDecision(
        previous_table_id=previous.table_id,
        following_table_id=following.table_id,
        previous_page=previous_page,
        following_page=following_page,
        accepted=accepted,
        score=round(score, 6),
        reasons=tuple(reasons),
        facts=facts,
    )


def _merge(previous: StructuredTable, following: StructuredTable) -> StructuredTable:
    first_signature = table_signature(previous)
    second_signature = table_signature(following)
    repeated_header = (
        bool(first_signature.header_rows)
        and bool(second_signature.header_rows)
        and first_signature.header_rows[0].normalized_cells
        == second_signature.header_rows[0].normalized_cells
    )
    skipped_row = _header_row(following) if repeated_header else None
    row_offset = previous.row_count

    merged_cells = list(previous.cells)
    for cell in following.cells:
        if skipped_row is not None and cell.row == skipped_row:
            continue
        row = row_offset + cell.row - (1 if skipped_row is not None and cell.row > skipped_row else 0)
        merged_cells.append(replace(cell, row=row))

    fragments = list(previous.page_fragments)
    row_shift = -1 if skipped_row is not None else 0
    fragments.extend(
        replace(
            fragment,
            row_start=fragment.row_start + row_offset,
            row_end=fragment.row_end + row_offset + row_shift,
        )
        for fragment in following.page_fragments
    )
    return StructuredTable(
        table_id=previous.table_id,
        page_fragments=fragments,
        cells=merged_cells,
        column_count=max(previous.column_count, following.column_count),
        row_count=previous.row_count + following.row_count + row_shift,
        confidence=min(previous.confidence, following.confidence),
        method=previous.method,
        continued_from_previous_page=True,
        continues_to_next_page=following.continues_to_next_page,
    )


def _first_page(table: StructuredTable) -> int:
    pages = [fragment.page_index for fragment in table.page_fragments]
    return min(pages) if pages else 10**9


def _last_page(table: StructuredTable) -> int | None:
    pages = [fragment.page_index for fragment in table.page_fragments]
    return max(pages) if pages else None


def _table_y(table: StructuredTable) -> float:
    bbox = _table_bbox(table)
    return bbox.y0 if bbox is not None else float("inf")


def _header_row(table: StructuredTable) -> int | None:
    if not table.cells or table.row_count <= 0:
        return None
    rows: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)
    candidates: list[tuple[float, int]] = []
    for row in sorted(rows)[: min(table.row_count, 8)]:
        values = [cell.text.strip() for cell in rows[row]]
        nonempty = [value for value in values if value]
        if len(nonempty) < max(2, min(3, table.column_count)):
            continue
        if max((len(value) for value in nonempty), default=0) > 48:
            continue
        score = len(nonempty) * 3.0 - row * 0.05
        next_values = [cell.text.strip() for cell in rows.get(row + 1, [])]
        if any(_looks_like_data(value) for value in next_values):
            score += 10.0
        if any(_looks_like_data(value) for value in nonempty):
            # Data rows are often denser than headers. Penalize them strongly
            # so a numeric first record cannot masquerade as the signature.
            score -= 12.0
        candidates.append((score, row))
    return max(candidates)[1] if candidates else None


def _row_signature(table: StructuredTable, row: int | None) -> RowSignature | None:
    if row is None:
        return None
    cells = sorted((cell for cell in table.cells if cell.row == row), key=lambda cell: cell.col)
    if not cells:
        return None
    return RowSignature(
        effective_columns=sum(max(1, cell.colspan) for cell in cells),
        colspans=tuple(cell.colspan for cell in cells),
        rowspans=tuple(cell.rowspan for cell in cells),
        normalized_cells=tuple(_normalize_header(cell.text) for cell in cells),
    )


def _x_tracks(table: StructuredTable, row: int | None) -> tuple[float, ...]:
    cells = [cell for cell in table.cells if row is None or cell.row == row]
    if not cells:
        return ()
    table_box = _table_bbox(table)
    if table_box is None or table_box.width <= 0:
        return tuple(float(cell.col) for cell in sorted(cells, key=lambda item: item.col))
    return tuple(
        round((cell.bbox.cx - table_box.x0) / table_box.width, 3)
        for cell in sorted(cells, key=lambda item: item.col)
        if cell.bbox is not None
    )


def _table_bbox(table: StructuredTable, page_index: int | None = None):
    boxes = [
        fragment.bbox
        for fragment in table.page_fragments
        if fragment.bbox is not None
        and (page_index is None or fragment.page_index == page_index)
    ]
    if not boxes:
        return None
    x0 = min(box.x0 for box in boxes)
    y0 = min(box.y0 for box in boxes)
    x1 = max(box.x1 for box in boxes)
    y1 = max(box.y1 for box in boxes)
    return type(boxes[0])(x0, y0, x1, y1)


def _headers_compatible(first: TableSignature, second: TableSignature) -> bool:
    return _header_similarity(first, second) >= 0.60


def _header_similarity(first: TableSignature, second: TableSignature) -> float:
    if not first.header_rows or not second.header_rows:
        return 0.0
    first_values = [value for value in first.header_rows[0].normalized_cells if value]
    second_values = [value for value in second.header_rows[0].normalized_cells if value]
    if not first_values or not second_values:
        return 0.0
    matches = sum(
        1
        for left, right in zip(first.header_rows[0].normalized_cells, second.header_rows[0].normalized_cells)
        if left and left == right
    )
    return matches / max(len(first_values), len(second_values), 1)


def _tracks_compatible(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    delta = _track_delta(first, second)
    return delta is None or delta <= 0.08


def _track_delta(first: tuple[float, ...], second: tuple[float, ...]) -> float | None:
    if not first or not second or len(first) != len(second):
        return None
    return mean(abs(left - right) for left, right in zip(first, second))


def _boundary_proximity(previous: StructuredTable, following: StructuredTable) -> bool:
    previous_signature = table_signature(previous)
    following_signature = table_signature(following)
    return previous_signature.touches_bottom and following_signature.touches_top


def _continuation_marker(table: StructuredTable) -> bool:
    text = " ".join(cell.text for cell in table.cells[: min(len(table.cells), 40)])
    return _has_continuation_marker(text)


def _has_continuation_marker(value: str) -> bool:
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    if re.search(r"\bcont\.(?:\s|$)", folded):
        return True
    normalized = _normalize_header(value)
    return any(
        marker in normalized
        for marker in (
            "continu",
            "parte2",
            "arte2",
            "proxima",
            "ultimalinha",
        )
    )


def _following_starts_with_data(table: StructuredTable) -> bool:
    first_row = min((cell.row for cell in table.cells), default=None)
    if first_row is None:
        return False
    values = [cell.text for cell in table.cells if cell.row == first_row and cell.text.strip()]
    return bool(values) and sum(_looks_like_data(value) for value in values) / len(values) >= 0.40


def _width_similarity(first: BBox, second: BBox) -> float:
    return min(first.width, second.width) / max(first.width, second.width, 1.0)


def _column_type_similarity(first: StructuredTable, second: StructuredTable) -> float:
    first_profile = _column_types(first)
    second_profile = _column_types(second)
    columns = min(len(first_profile), len(second_profile))
    if columns <= 0:
        return 0.0
    scores = []
    for left, right in zip(first_profile[:columns], second_profile[:columns]):
        if left == right:
            scores.append(1.0)
        elif {left, right} <= {"numeric", "mixed"}:
            scores.append(0.5)
        else:
            scores.append(0.0)
    return sum(scores) / columns


def _column_types(table: StructuredTable) -> tuple[str, ...]:
    header_row = _header_row(table)
    columns: list[str] = []
    for column in range(table.column_count):
        values = [
            _cell_type(cell.text)
            for cell in table.cells
            if cell.col == column
            and cell.row != header_row
            and cell.text.strip()
        ]
        if not values:
            columns.append("empty")
            continue
        columns.append(max(set(values), key=lambda value: (values.count(value), value)))
    return tuple(columns)


def _cell_type(value: str) -> str:
    text = value.strip()
    if not text:
        return "empty"
    digits = sum(character.isdigit() for character in text)
    letters = sum(character.isalpha() for character in text)
    if digits and letters <= 3:
        return "numeric"
    if digits and letters:
        return "mixed"
    return "text"


def _looks_like_data(value: str) -> bool:
    return bool(re.search(r"\d", value)) or "R$" in value or "%" in value


def _normalize_header(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_value)
