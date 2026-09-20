"""Independent structural audit for the B1 region/table/reading-order gate."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import math
import re
import unicodedata
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from structured_pdf_text.geometry import BBox


class B1Category(str, Enum):
    UNIT_MATCHED = "unit_matched"
    UNIT_FRAGMENTED = "unit_fragmented"
    UNIT_UNICODE_SUBSTITUTION = "unit_unicode_substitution"
    UNIT_GEOMETRY_MISMATCH = "unit_geometry_mismatch"
    UNIT_MISSING = "unit_missing"
    REGION_FRAGMENTED = "region_fragmented"
    REGION_PARTITIONED = "region_partitioned"
    READING_ORDER_MISMATCH = "reading_order_mismatch"
    TABLE_MATCHED = "table_matched"
    TABLE_MISSING = "table_missing"
    TABLE_CELL_MISMATCH = "table_cell_mismatch"
    NOT_ASSESSABLE = "not_assessable"


@dataclass(frozen=True, slots=True)
class B1Finding:
    page: int
    category: B1Category
    reference_id: str | None = None
    observed_id: str | None = None
    expected_text: str | None = None
    observed_text: str | None = None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "category": self.category.value,
            "reference_id": self.reference_id,
            "observed_id": self.observed_id,
            "expected_text": self.expected_text,
            "observed_text": self.observed_text,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class B1PageSummary:
    page: int
    reference_units: int
    matched_units: int
    reference_regions: int
    observed_regions: int
    reference_tables: int
    observed_tables: int
    categories: Mapping[str, int]

    @property
    def auditable(self) -> bool:
        return not any(
            self.categories.get(category, 0)
            for category in (
                B1Category.UNIT_MISSING.value,
                B1Category.UNIT_GEOMETRY_MISMATCH.value,
                B1Category.REGION_FRAGMENTED.value,
                B1Category.READING_ORDER_MISMATCH.value,
                B1Category.TABLE_MISSING.value,
                B1Category.TABLE_CELL_MISMATCH.value,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "reference_units": self.reference_units,
            "matched_units": self.matched_units,
            "reference_regions": self.reference_regions,
            "observed_regions": self.observed_regions,
            "reference_tables": self.reference_tables,
            "observed_tables": self.observed_tables,
            "categories": dict(sorted(self.categories.items())),
            "auditable": self.auditable,
        }


@dataclass(frozen=True, slots=True)
class B1DocumentSummary:
    page_count: int
    reference_units: int
    matched_units: int
    categories: Mapping[str, int]
    pages: tuple[B1PageSummary, ...]

    @property
    def auditable(self) -> bool:
        return all(page.auditable for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "reference_units": self.reference_units,
            "matched_units": self.matched_units,
            "categories": dict(sorted(self.categories.items())),
            "pages": [page.to_dict() for page in self.pages],
            "auditable": self.auditable,
        }


@dataclass(frozen=True, slots=True)
class _ObservedLine:
    line: Any
    region_id: str
    order_index: int
    region_kind: str = ""


@dataclass(frozen=True, slots=True)
class _FragmentedMatch:
    lines: tuple[_ObservedLine, ...]
    text: str
    token_indexes: tuple[tuple[int, tuple[int, ...]], ...] = ()


def audit_page_structure(reference_page: Mapping[str, Any], observed_page: Any) -> tuple[B1PageSummary, tuple[B1Finding, ...]]:
    """Compare structural relationships without requiring source draw order."""

    page = int(reference_page["page"])
    observed_lines = _observed_lines(observed_page)
    units = list(reference_page.get("units", []))
    findings: list[B1Finding] = []
    used: set[int] = set()
    used_tokens: dict[int, set[int]] = {}
    matched: dict[str, tuple[_ObservedLine, ...]] = {}
    matched_boxes: dict[str, tuple[tuple[float, float, float, float], ...]] = {}
    geometry_mismatched_units: set[str] = set()

    for unit in units:
        unit_id = str(unit.get("unit_id", ""))
        expected = str(unit.get("exact_text", ""))
        candidates = [
            item for item in observed_lines
            if item.order_index not in used
            and not used_tokens.get(item.order_index)
            and _normalized(item.line.text) == _normalized(expected)
        ]
        if not candidates:
            fragmented = _find_fragmented_unit(unit, expected, observed_lines, used, used_tokens)
            if fragmented:
                if fragmented.token_indexes:
                    for order_index, token_indexes in fragmented.token_indexes:
                        used_tokens.setdefault(order_index, set()).update(token_indexes)
                else:
                    for item in fragmented.lines:
                        used.add(item.order_index)
                matched[unit_id] = fragmented.lines
                matched_boxes[unit_id] = _fragmented_match_boxes(fragmented)
                findings.append(B1Finding(
                    page,
                    B1Category.UNIT_FRAGMENTED,
                    unit_id,
                    observed_id=",".join(str(item.line.line_id) for item in fragmented.lines),
                    expected_text=expected,
                    observed_text=fragmented.text,
                    reasons=(f"unit_reconstructed_from_{len(fragmented.lines)}_observed_lines",),
                ))
                continue
            unicode_match = None
            if _contains_compatibility_ligature(expected):
                unicode_match = _find_fragmented_unit(
                    unit,
                    expected,
                    observed_lines,
                    used,
                    used_tokens,
                    normalizer=_nfkc_normalized,
                )
            if unicode_match:
                if unicode_match.token_indexes:
                    for order_index, token_indexes in unicode_match.token_indexes:
                        used_tokens.setdefault(order_index, set()).update(token_indexes)
                else:
                    for item in unicode_match.lines:
                        used.add(item.order_index)
                matched[unit_id] = unicode_match.lines
                matched_boxes[unit_id] = _fragmented_match_boxes(unicode_match)
                findings.append(B1Finding(
                    page,
                    B1Category.UNIT_UNICODE_SUBSTITUTION,
                    unit_id,
                    observed_id=",".join(str(item.line.line_id) for item in unicode_match.lines),
                    expected_text=expected,
                    observed_text=unicode_match.text,
                    reasons=("compatibility_ligature_normalization_only",),
                ))
                continue
            findings.append(B1Finding(page, B1Category.UNIT_MISSING, unit_id, expected_text=expected, reasons=("no_unconsumed_exact_or_whitespace_equivalent_line",)))
            continue
        expected_box = _box(unit.get("bbox_top_origin_pt"))
        candidates.sort(key=lambda item: _distance_score(expected_box, _line_box(item.line)))
        chosen = candidates[0]
        used.add(chosen.order_index)
        matched[unit_id] = (chosen,)
        chosen_box = _line_box(chosen.line)
        if chosen_box is not None:
            matched_boxes[unit_id] = (chosen_box,)
        geometry_mismatch = _geometry_mismatch(expected_box, _line_box(chosen.line))
        if geometry_mismatch:
            geometry_mismatched_units.add(unit_id)
        findings.append(B1Finding(
            page,
            B1Category.UNIT_GEOMETRY_MISMATCH if geometry_mismatch else B1Category.UNIT_MATCHED,
            unit_id,
            observed_id=chosen.line.line_id,
            expected_text=expected,
            observed_text=chosen.line.text,
            reasons=("exact_text_associated_outside_expected_geometry",) if geometry_mismatch else (),
        ))

    # A reference region is reconstructed only from the geometry of its units;
    # this avoids inventing a region bbox contract that the reference does not
    # provide.  Splitting a region across output regions remains visible.
    expected_region_units: dict[str, list[Mapping[str, Any]]] = {}
    for unit in units:
        expected_region_units.setdefault(str(unit.get("region_id", "")), []).append(unit)
    for region_id, region_units in expected_region_units.items():
        observed_region_ids = {
            observed_region_id
            for unit in region_units
            if unit.get("unit_id") in matched
            for observed_region_id in {item.region_id for item in matched[unit["unit_id"]]}
        }
        if len(observed_region_ids) > 1:
            observed_regions = [
                region
                for region in getattr(observed_page, "regions", []) or []
                if str(getattr(region, "region_id", "")) in observed_region_ids
            ]
            observed_kinds = tuple(sorted({
                _region_kind(region)
                for region in observed_regions
            }))
            observed_roles = tuple(sorted({
                str(getattr(region, "semantic_role", "") or "")
                for region in observed_regions
            }))
            category = (
                B1Category.REGION_PARTITIONED
                if len(observed_kinds) > 1
                or len(observed_roles) > 1 and any(observed_roles)
                else B1Category.REGION_FRAGMENTED
            )
            findings.append(B1Finding(
                page,
                category,
                region_id,
                observed_id=",".join(sorted(observed_region_ids)),
                reasons=(
                    "reference_region_maps_to_multiple_observed_regions",
                    f"observed_region_kinds={','.join(observed_kinds) or 'unknown'}",
                    f"observed_region_roles={','.join(observed_roles) or 'none'}",
                ),
            ))
        elif not observed_region_ids and region_units:
            findings.append(B1Finding(page, B1Category.NOT_ASSESSABLE, region_id, reasons=("region_has_no_matched_text_unit",)))

    # Check logical order only among units successfully associated by text and
    # geometry.  source_draw_order/native char order is deliberately ignored.
    ordered_units = sorted(
        (
            unit for unit in units
            if unit.get("unit_id") in matched
            and unit.get("unit_id") not in geometry_mismatched_units
            and _is_order_assessable_unit(unit)
        ),
        key=lambda unit: int(unit.get("logical_reading_order", 0)),
    )
    order_items: list[_ObservedLine] = []
    order_lines: dict[str, Any] = {}
    for unit in ordered_units:
        unit_id = str(unit["unit_id"])
        boxes = matched_boxes.get(unit_id)
        if not boxes:
            continue
        proxy = _proxy_line(_union_boxes(boxes))
        order_lines[unit_id] = proxy
        first_line = matched[unit_id][0]
        order_items.append(_ObservedLine(proxy, first_line.region_id, 0, first_line.region_kind))
    ordered_observed = _infer_observed_reading_order(order_items, observed_page)
    observed_rank = {id(item.line): index for index, item in enumerate(ordered_observed)}
    observed_indexes = [
        observed_rank[id(order_lines[str(unit["unit_id"])] )]
        for unit in ordered_units
        if str(unit["unit_id"]) in order_lines
    ]
    if any(left > right for left, right in zip(observed_indexes, observed_indexes[1:])):
        reasons = ["observed_region_line_order_is_not_reference_logical_order"]
        if geometry_mismatched_units:
            reasons.append(f"excluded_geometry_mismatch_units={len(geometry_mismatched_units)}")
        findings.append(B1Finding(page, B1Category.READING_ORDER_MISMATCH, reasons=tuple(reasons)))

    reference_tables = list(reference_page.get("tables", []))
    observed_tables = list(getattr(observed_page, "tables", []) or [])
    for index, reference_table in enumerate(reference_tables):
        if index >= len(observed_tables):
            findings.append(B1Finding(page, B1Category.TABLE_MISSING, str(reference_table.get("table_id", "")), reasons=("no_observed_table_at_reference_ordinal",)))
            continue
        observed_table = observed_tables[index]
        expected_cells = _reference_cell_shape(reference_table.get("cells", []))
        observed_cells = {
            (int(cell.row), int(cell.col), int(cell.rowspan), int(cell.colspan))
            for cell in getattr(observed_table, "cells", [])
        }
        if expected_cells != observed_cells or int(reference_table.get("n_columns", 0)) != int(getattr(observed_table, "column_count", 0)):
            findings.append(B1Finding(page, B1Category.TABLE_CELL_MISMATCH, str(reference_table.get("table_id", "")), observed_id=str(getattr(observed_table, "table_id", "")), reasons=(f"expected_cells={len(expected_cells)}", f"observed_cells={len(observed_cells)}")))
        else:
            findings.append(B1Finding(page, B1Category.TABLE_MATCHED, str(reference_table.get("table_id", "")), observed_id=str(getattr(observed_table, "table_id", ""))))
    if len(observed_tables) > len(reference_tables):
        for table in observed_tables[len(reference_tables):]:
            findings.append(B1Finding(page, B1Category.NOT_ASSESSABLE, observed_id=str(getattr(table, "table_id", "")), reasons=("observed_extra_table_has_no_reference_ordinal",)))

    counts = Counter(finding.category.value for finding in findings)
    summary = B1PageSummary(
        page=page,
        reference_units=len(units),
        matched_units=len(matched),
        reference_regions=len(reference_page.get("regions", [])),
        observed_regions=len(getattr(observed_page, "regions", []) or []),
        reference_tables=len(reference_tables),
        observed_tables=len(observed_tables),
        categories=dict(counts),
    )
    return summary, tuple(findings)


def audit_document_structure(reference: Mapping[str, Any], observed_document: Any) -> tuple[B1DocumentSummary, tuple[B1Finding, ...]]:
    pages = list(reference.get("pages", []))
    observed_by_page = {int(page.page_index) + 1: page for page in observed_document.pages}
    page_summaries: list[B1PageSummary] = []
    findings: list[B1Finding] = []
    counts: Counter[str] = Counter()
    for reference_page in pages:
        observed_page = observed_by_page.get(int(reference_page["page"]))
        if observed_page is None:
            summary = B1PageSummary(
                int(reference_page["page"]), len(reference_page.get("units", [])), 0,
                len(reference_page.get("regions", [])), 0,
                len(reference_page.get("tables", [])), 0,
                {B1Category.UNIT_MISSING.value: len(reference_page.get("units", []))},
            )
            page_summaries.append(summary)
            finding = B1Finding(int(reference_page["page"]), B1Category.UNIT_MISSING, reasons=("observed_page_missing",))
            findings.append(finding)
            counts[finding.category.value] += 1
            continue
        summary, page_findings = audit_page_structure(reference_page, observed_page)
        page_summaries.append(summary)
        findings.extend(page_findings)
        counts.update(finding.category.value for finding in page_findings)
    document_summary = B1DocumentSummary(
        page_count=len(pages),
        reference_units=sum(summary.reference_units for summary in page_summaries),
        matched_units=sum(summary.matched_units for summary in page_summaries),
        categories=dict(counts),
        pages=tuple(page_summaries),
    )
    return document_summary, tuple(findings)


def _observed_lines(page: Any) -> list[_ObservedLine]:
    result: list[_ObservedLine] = []
    for region in getattr(page, "regions", []) or []:
        kind = _region_kind(region)
        for line in [*getattr(region, "native_lines", []), *getattr(region, "ocr_lines", [])]:
            result.append(_ObservedLine(line, str(getattr(region, "region_id", "")), -1, kind))
    ordered = _infer_observed_reading_order(result, page)
    return [
        _ObservedLine(item.line, item.region_id, index, item.region_kind)
        for index, item in enumerate(ordered)
    ]


def _region_kind(region: Any) -> str:
    value = getattr(region, "kind", "")
    return str(getattr(value, "value", value) or "")


def _is_order_assessable_unit(unit: Mapping[str, Any]) -> bool:
    role = str(unit.get("role", "") or "")
    if role in {
        "repeated_header",
        "repeated_footer",
        "table_cell",
        "table_header",
    }:
        return False
    return not role.startswith(("edge_", "rotation_", "vertical_"))


def _infer_observed_reading_order(items: Sequence[_ObservedLine], page: Any) -> list[_ObservedLine]:
    """Infer order from observed geometry, not region/list/native insertion order."""

    table_objects = [
        table
        for table in getattr(page, "tables", []) or []
        if any(fragment.bbox is not None for fragment in getattr(table, "page_fragments", []))
    ]
    table_boxes = [
        [fragment.bbox for fragment in getattr(table, "page_fragments", []) if fragment.bbox is not None]
        for table in table_objects
    ]
    table_index_by_item: dict[int, int] = {}
    table_cell_by_item: dict[int, tuple[int, int]] = {}
    for item_index, item in enumerate(items):
        for table_index, boxes in enumerate(table_boxes):
            if any(_line_box_overlaps(item.line, box) for box in boxes):
                table_index_by_item[item_index] = table_index
                cells = [
                    cell for cell in getattr(table_objects[table_index], "cells", [])
                    if getattr(cell, "bbox", None) is not None
                    and cell.bbox.x0 <= item.line.bbox.cx <= cell.bbox.x1
                    and cell.bbox.y0 <= item.line.bbox.cy <= cell.bbox.y1
                ]
                if cells:
                    cell = min(cells, key=lambda value: value.bbox.area)
                    table_cell_by_item[item_index] = (int(cell.row), int(cell.col))
                break

    column_anchors = _column_anchors(
        [
            item.line
            for index, item in enumerate(items)
            if index not in table_index_by_item
            and item.region_kind in {"text", "list", "unknown", ""}
            and item.line.bbox.y0 >= 60.0
            and item.line.bbox.y1 <= 800.0
        ]
    )
    column_mode = len(column_anchors) >= 2

    def key(index_and_item: tuple[int, _ObservedLine]) -> tuple[Any, ...]:
        index, item = index_and_item
        box = _line_box(item.line)
        assert box is not None
        x0, y0, _, _ = box
        table_index = table_index_by_item.get(index)
        if table_index is not None:
            table_y = min(box.y0 for box in table_boxes[table_index])
            cell = table_cell_by_item.get(index)
            if cell is not None:
                return (1, 100 + table_index, cell[0], cell[1], y0, x0)
            return (1, 100 + table_index, table_y, y0, x0)
        if y0 < 70.0:
            return (0, y0, x0)
        if item.region_kind in {"footer", "footnote", "caption"}:
            return (2, y0, x0)
        if item.region_kind == "marginalia":
            return (0, 100.0 + x0, y0)
        if column_mode:
            lane = _nearest_column(x0, column_anchors)
            return (1, 10 + lane, y0, x0)
        return (1, 50, y0, x0)

    return [item for _, item in sorted(enumerate(items), key=key)]


def _column_anchors(lines: Sequence[Any]) -> tuple[float, ...]:
    if len(lines) < 6:
        return ()
    values = sorted(float(line.bbox.x0) for line in lines)
    clusters: list[list[float]] = []
    for value in values:
        if not clusters or value - clusters[-1][-1] > 15.0:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    anchors = [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 3]
    anchors = [value for value in anchors if all(abs(value - other) >= 40.0 for other in anchors if other != value)]
    return tuple(anchors)


def _nearest_column(value: float, anchors: Sequence[float]) -> int:
    return min(range(len(anchors)), key=lambda index: abs(value - anchors[index]))


def _line_box_overlaps(line: Any, box: Any) -> bool:
    line_box = _line_box(line)
    if line_box is None:
        return False
    return not (
        line_box[2] < box.x0
        or line_box[0] > box.x1
        or line_box[3] < box.y0
        or line_box[1] > box.y1
    )


def _reference_cell_shape(cells: Sequence[Mapping[str, Any]]) -> set[tuple[int, int, int, int]]:
    """Normalize continued-table row coordinates to the current page fragment."""

    if not cells:
        return set()
    row_indexes = sorted({int(cell["row_index"]) for cell in cells})
    row_map = {row_index: ordinal for ordinal, row_index in enumerate(row_indexes)}
    return {
        (
            row_map[int(cell["row_index"])],
            int(cell["column_index"]),
            int(cell["rowspan"]),
            int(cell["colspan"]),
        )
        for cell in cells
    }


def _find_fragmented_unit(
    unit: Mapping[str, Any],
    expected: str,
    observed_lines: Sequence[_ObservedLine],
    used: set[int],
    used_tokens: Mapping[int, set[int]],
    *,
    normalizer: Callable[[str], str] | None = None,
) -> _FragmentedMatch | None:
    """Recover a reference unit split into adjacent observed lines.

    This is intentionally a conservative, geometry-bounded reconstruction:
    only unused lines (or unconsumed token subsets) whose boxes overlap the
    reference unit are considered, and the joined text must equal the
    reference text after whitespace normalization. It therefore distinguishes
    a complete one-to-many extraction from a genuinely missing unit without
    accepting arbitrary page substrings.
    """

    expected_box = _box(unit.get("bbox_top_origin_pt"))
    if expected_box is None:
        return None
    normalizer = normalizer or _normalized
    candidates = [
        item for item in observed_lines
        if item.order_index not in used
        and _has_available_tokens(item, used_tokens)
        and _normalized(item.line.text)
        and _boxes_overlap_with_tolerance(expected_box, _line_box(item.line))
    ]
    candidates.sort(key=lambda item: item.order_index)
    for item in candidates:
        if normalizer(item.line.text) == normalizer(expected):
            return _FragmentedMatch((item,), item.line.text)
    if len(candidates) < 2:
        token_match = _reconstruct_from_tokens(expected, expected_box, candidates, used_tokens, normalizer=normalizer)
        return token_match

    token_match = _reconstruct_from_tokens(expected, expected_box, candidates, used_tokens, normalizer=normalizer)
    if token_match is not None:
        return token_match

    normalized_expected = normalizer(expected)
    for start in range(len(candidates)):
        for end in range(start + 2, min(len(candidates), start + 8) + 1):
            window = tuple(candidates[start:end])
            if any(item.order_index in used_tokens for item in window):
                continue
            if normalizer(" ".join(item.line.text for item in window)) == normalized_expected:
                return _FragmentedMatch(window, " ".join(item.line.text for item in window))
    return None


def _has_available_tokens(item: _ObservedLine, used_tokens: Mapping[int, set[int]]) -> bool:
    consumed = used_tokens.get(item.order_index)
    if not consumed:
        return True
    tokens = getattr(item.line, "tokens", []) or []
    return any(index not in consumed for index, _ in enumerate(tokens))


def _fragmented_match_boxes(match: _FragmentedMatch) -> tuple[tuple[float, float, float, float], ...]:
    """Return the boxes actually consumed by a fragmented match."""
    if not match.token_indexes:
        return tuple(
            box
            for item in match.lines
            if (box := _line_box(item.line)) is not None
        )
    by_order = {item.order_index: item for item in match.lines}
    boxes: list[tuple[float, float, float, float]] = []
    for order_index, token_indexes in match.token_indexes:
        item = by_order.get(order_index)
        if item is None:
            continue
        tokens = getattr(item.line, "tokens", []) or []
        for token_index in token_indexes:
            if token_index >= len(tokens):
                continue
            bbox = getattr(tokens[token_index], "bbox", None)
            if bbox is not None:
                boxes.append((float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1)))
    if boxes:
        return tuple(boxes)
    return tuple(
        box
        for item in match.lines
        if (box := _line_box(item.line)) is not None
    )


def _union_boxes(boxes: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _proxy_line(box: tuple[float, float, float, float]) -> Any:
    return SimpleNamespace(bbox=BBox(*box), line_id=None, text="", tokens=[])


def _reconstruct_from_tokens(
    expected: str,
    expected_box: tuple[float, float, float, float],
    candidates: Sequence[_ObservedLine],
    used_tokens: Mapping[int, set[int]],
    *,
    normalizer: Callable[[str], str] | None = None,
) -> _FragmentedMatch | None:
    normalizer = normalizer or _normalized
    selected_by_line: dict[int, list[tuple[int, Any]]] = {}
    line_by_order: dict[int, _ObservedLine] = {}
    x_tolerance = max(2.0, min(8.0, (expected_box[2] - expected_box[0]) * 0.04))
    y_tolerance = max(1.0, min(3.0, (expected_box[3] - expected_box[1]) * 0.25))
    for item in candidates:
        for token_index, token in enumerate(getattr(item.line, "tokens", []) or []):
            if token_index in used_tokens.get(item.order_index, set()):
                continue
            bbox = getattr(token, "bbox", None)
            if bbox is None:
                continue
            if (
                expected_box[0] - x_tolerance <= bbox.cx <= expected_box[2] + x_tolerance
                and expected_box[1] - y_tolerance <= bbox.cy <= expected_box[3] + y_tolerance
            ):
                selected_by_line.setdefault(item.order_index, []).append((token_index, token))
                line_by_order[item.order_index] = item
    if not selected_by_line:
        return None

    # Native token baselines can vary by several points within one visual
    # line (ascenders, accents, and PDF font metrics are common causes).  The
    # observed line already provides the reliable line grouping, so grouping
    # again by token ``y`` can scramble otherwise valid text.  Keep observed
    # line order between visual rows and sort tokens horizontally within each
    # row.
    line_orders = sorted(selected_by_line)
    row_tolerance = max(2.0, min(6.0, (expected_box[3] - expected_box[1]) * 0.5))
    rows: list[list[int]] = []
    for order_index in line_orders:
        line_box = _line_box(line_by_order[order_index].line)
        assert line_box is not None
        line_center_y = (line_box[1] + line_box[3]) / 2.0
        if rows:
            previous_box = _line_box(line_by_order[rows[-1][-1]].line)
            assert previous_box is not None
            previous_center_y = (previous_box[1] + previous_box[3]) / 2.0
            if abs(line_center_y - previous_center_y) <= row_tolerance:
                rows[-1].append(order_index)
                continue
        rows.append([order_index])

    flattened: list[tuple[_ObservedLine, Any]] = []
    for row in rows:
        row_tokens = [
            (line_by_order[order_index], token)
            for order_index in row
            for _, token in selected_by_line[order_index]
        ]
        flattened.extend(sorted(row_tokens, key=_token_horizontal_order))
    reconstructed = "".join(str(token.text) for _, token in flattened)
    if (
        normalizer(reconstructed) != normalizer(expected)
        and _without_whitespace(reconstructed) != _without_whitespace(expected)
    ):
        return None
    lines_list: list[_ObservedLine] = []
    seen: set[int] = set()
    for item, _ in flattened:
        if item.order_index not in seen:
            lines_list.append(item)
            seen.add(item.order_index)
    lines = tuple(lines_list)
    token_indexes = tuple(
        (order_index, tuple(token_index for token_index, _ in selected_by_line[order_index]))
        for order_index in sorted(selected_by_line)
    )
    return _FragmentedMatch(lines, reconstructed, token_indexes)


def _without_whitespace(value: str) -> str:
    return "".join(character for character in value if not character.isspace())


def _token_horizontal_order(value: tuple[_ObservedLine, Any]) -> float:
    item, token = value
    direction = getattr(getattr(item.line, "direction", None), "value", getattr(item.line, "direction", ""))
    return -token.bbox.x0 if direction == "right_to_left" else token.bbox.x0


def _boxes_overlap_with_tolerance(
    expected: tuple[float, float, float, float],
    observed: tuple[float, float, float, float] | None,
) -> bool:
    if observed is None:
        return False
    width = expected[2] - expected[0]
    height = expected[3] - expected[1]
    tolerance = max(2.0, min(12.0, max(width, height) * 0.1))
    return not (
        observed[2] < expected[0] - tolerance
        or observed[0] > expected[2] + tolerance
        or observed[3] < expected[1] - tolerance
        or observed[1] > expected[3] + tolerance
    )


def _geometry_mismatch(
    expected: tuple[float, float, float, float] | None,
    observed: tuple[float, float, float, float] | None,
) -> bool:
    if expected is None or observed is None:
        return False
    if not (
        observed[2] < expected[0]
        or observed[0] > expected[2]
        or observed[3] < expected[1]
        or observed[1] > expected[3]
    ):
        return False
    scale = max(expected[2] - expected[0], expected[3] - expected[1], 1.0)
    expected_center = ((expected[0] + expected[2]) / 2, (expected[1] + expected[3]) / 2)
    observed_center = ((observed[0] + observed[2]) / 2, (observed[1] + observed[3]) / 2)
    return math.hypot(expected_center[0] - observed_center[0], expected_center[1] - observed_center[1]) > max(12.0, scale * 0.5)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\s+", " ", value).strip()


def _nfkc_normalized(value: str) -> str:
    return _normalized(unicodedata.normalize("NFKC", value))


def _contains_compatibility_ligature(value: str) -> bool:
    return any(character in "ﬀﬁﬂﬃﬄﬅﬆ" for character in value)


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    values = [value.get(key) for key in ("x0", "y0", "x1", "y1")] if isinstance(value, Mapping) else list(value)
    if len(values) != 4:
        return None
    try:
        box = tuple(float(item) for item in values)
    except (TypeError, ValueError):
        return None
    return box if all(math.isfinite(item) for item in box) and box[2] > box[0] and box[3] > box[1] else None


def _line_box(line: Any) -> tuple[float, float, float, float] | None:
    bbox = getattr(line, "bbox", None)
    if bbox is None:
        return None
    return (float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1))


def _distance_score(expected: tuple[float, float, float, float] | None, observed: tuple[float, float, float, float] | None) -> float:
    if expected is None or observed is None:
        return 1_000_000.0
    ex = (expected[0] + expected[2]) / 2, (expected[1] + expected[3]) / 2
    ox = (observed[0] + observed[2]) / 2, (observed[1] + observed[3]) / 2
    scale = max(1.0, expected[2] - expected[0], expected[3] - expected[1])
    return math.hypot(ex[0] - ox[0], ex[1] - ox[1]) / scale
