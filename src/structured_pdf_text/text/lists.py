"""Conservative list segmentation and nested-list reconstruction.

The important unit here is a contiguous segment, rather than the whole
region.  A region may therefore contain prose, a list, and prose again while
every input line remains represented exactly once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import StructuredListItem, TextLine
from structured_pdf_text.geometry import BBox


_MARKER = re.compile(
    r"^\s*(?P<marker>[•◦▪‣*]|[-–—]|(?:\d+|[A-Za-z]|[IVXivx]+)[.)])"
    r"\s+(?P<text>.+?)\s*$"
)


@dataclass(frozen=True, slots=True)
class ListLineAssignment:
    line: TextLine
    role: str
    item_index: int | None
    level: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class ListSegment:
    lines: tuple[TextLine, ...]
    items: tuple[StructuredListItem, ...]
    is_list: bool


@dataclass(frozen=True, slots=True)
class ListSegmentationResult:
    segments: tuple[ListSegment, ...]
    assignments: tuple[ListLineAssignment, ...]
    input_line_count: int
    assigned_line_count: int

    @property
    def list_segments(self) -> int:
        return sum(segment.is_list for segment in self.segments)

    @property
    def list_item_count(self) -> int:
        return sum(len(segment.items) for segment in self.segments)

    @property
    def inferred_marker_count(self) -> int:
        return sum(
            1
            for assignment in self.assignments
            if assignment.role == "item" and assignment.reason == "inferred_marker"
        )

    @property
    def continuation_count(self) -> int:
        return sum(assignment.role == "continuation" for assignment in self.assignments)

    @property
    def unassigned_line_count(self) -> int:
        return self.input_line_count - self.assigned_line_count


def parse_list_marker(text: str) -> tuple[str, str] | None:
    """Extract the list marker and body text from a candidate line.

    Returns a ``(marker, text)`` tuple when the line starts with a recognised
    marker (bullet, dash, or ordered label), or ``None`` when the line does not
    match.
    """
    match = _MARKER.match(text)
    if match is None:
        return None
    return match.group("marker"), match.group("text")


def _cluster_list_indents(lines: list[TextLine], tolerance: float | None = None) -> list[float]:
    """Cluster marker x positions without turning small PDF jitter into levels."""
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    line_height = median(heights) if heights else 10.0
    tolerance = tolerance if tolerance is not None else max(3.0, 0.25 * line_height)
    centers: list[float] = []
    for x0 in sorted(line.bbox.x0 for line in lines):
        if not centers or x0 - centers[-1] > tolerance:
            centers.append(x0)
        else:
            centers[-1] = (centers[-1] + x0) / 2.0
    return centers


def segment_list_lines(lines: list[TextLine], *, allow_single: bool = False) -> ListSegmentationResult:
    """Partition lines into contiguous TEXT/LIST segments.

    Ambiguous isolated markers are kept in TEXT unless the caller explicitly
    says the source region is a list.  Inferred nested items are represented
    structurally, but their text is always copied from an observed line.
    """
    all_lines = list(lines)
    if not all_lines:
        return ListSegmentationResult((), (), 0, 0)
    candidates = [(index, line, parse_list_marker(line.text)) for index, line in enumerate(all_lines)]
    marker_positions = [index for index, _, parsed in candidates if parsed is not None]
    if len(marker_positions) < (1 if allow_single else 2):
        segment = ListSegment(tuple(all_lines), (), False)
        assignments = tuple(
            ListLineAssignment(line, "text", None, None, "not_a_list") for line in all_lines
        )
        return ListSegmentationResult((segment,), assignments, len(all_lines), len(all_lines))

    marker_lines = [all_lines[index] for index in marker_positions]
    indent_centers = _cluster_list_indents(marker_lines)
    line_heights = [line.bbox.height for line in all_lines if line.bbox.height > 0]
    median_height = median(line_heights) if line_heights else 10.0
    indent_tolerance = max(3.0, 0.25 * median_height)

    def level(x0: float) -> int:
        return min(range(len(indent_centers)), key=lambda index: abs(indent_centers[index] - x0))

    assignments: dict[int, ListLineAssignment] = {}
    item_records: dict[int, StructuredListItem] = {}
    list_positions: set[int] = set()
    item_index = -1
    for marker_number, position in enumerate(marker_positions):
        line = all_lines[position]
        parsed = parse_list_marker(line.text)
        if parsed is None:
            continue
        marker, text = parsed
        item_index += 1
        current_level = level(line.bbox.x0)
        item_lines = [line]
        list_positions.add(position)
        assignments[position] = ListLineAssignment(line, "item", item_index, current_level, "observed_marker")
        next_marker = marker_positions[marker_number + 1] if marker_number + 1 < len(marker_positions) else len(all_lines)
        for extra_position in range(position + 1, next_marker):
            extra = all_lines[extra_position]
            if not extra.text.strip():
                continue
            if _is_inferred_nested_item(line, extra, median_height, indent_tolerance):
                item_index += 1
                inferred_text = extra.text.strip()
                inferred_level = level(extra.bbox.x0)
                item_records[extra_position] = StructuredListItem(
                    marker="•",
                    text=inferred_text,
                    level=inferred_level,
                    bbox=extra.bbox,
                    order_index=item_index,
                    confidence=_line_confidence(extra),
                    marker_source="inferred",
                )
                assignments[extra_position] = ListLineAssignment(
                    extra, "item", item_index, inferred_level, "inferred_marker"
                )
                list_positions.add(extra_position)
                continue
            if _is_continuation(line, extra, median_height, indent_tolerance):
                item_lines.append(extra)
                list_positions.add(extra_position)
                assignments[extra_position] = ListLineAssignment(
                    extra, "continuation", item_index, current_level, "aligned_continuation"
                )
            else:
                assignments[extra_position] = ListLineAssignment(
                    extra, "text", None, None, "not_safe_continuation"
                )
        item_records[position] = StructuredListItem(
            marker=marker,
            text=" ".join(
                part for part in (_item_text(item_lines, marker),) if part
            ),
            level=current_level,
            bbox=BBox.union_all([item_line.bbox for item_line in item_lines]),
            order_index=item_index,
            confidence=_line_confidence(line),
        )

    # Build contiguous segments from the assignments. A line not claimed by a
    # list is still emitted in a text segment, never silently discarded.
    segments: list[ListSegment] = []
    position = 0
    while position < len(all_lines):
        is_list = position in list_positions
        end = position + 1
        while end < len(all_lines) and (end in list_positions) == is_list:
            end += 1
        segment_lines = tuple(all_lines[position:end])
        items = tuple(
            item_records[item_position]
            for item_position in range(position, end)
            if item_position in item_records
        )
        segments.append(ListSegment(segment_lines, items, is_list and bool(items)))
        position = end

    for position, line in enumerate(all_lines):
        assignments.setdefault(
            position,
            ListLineAssignment(line, "text", None, None, "outside_list_segment"),
        )
    ordered_assignments = tuple(assignments[index] for index in range(len(all_lines)))
    return ListSegmentationResult(
        tuple(segments),
        ordered_assignments,
        len(all_lines),
        len(ordered_assignments),
    )


def extract_list_items(lines: list[TextLine]) -> list[StructuredListItem]:
    """Compatibility helper returning items only from a multi-line list."""
    result = segment_list_lines(lines)
    return [item for segment in result.segments if segment.is_list for item in segment.items]


def _item_text(lines: list[TextLine], marker: str) -> str:
    first = parse_list_marker(lines[0].text)
    first_text = first[1] if first else lines[0].text.strip()
    return " ".join([first_text, *(line.text.strip() for line in lines[1:] if line.text.strip())])


def _is_continuation(previous: TextLine, extra: TextLine, median_height: float, tolerance: float) -> bool:
    """Return True when ``extra`` is a wrapped continuation of the ``previous`` item.

    Checks that the line has no marker of its own, sits within one line-height
    below the previous line, and starts at approximately the same x position as
    the text body of the previous item (not its marker).
    """
    if parse_list_marker(extra.text) is not None or not extra.text.strip():
        return False
    gap = extra.bbox.y0 - previous.bbox.y1
    if gap < -0.35 * median_height or gap > 1.45 * median_height:
        return False
    text_start = _text_start_x(previous, parse_list_marker(previous.text)[0] if parse_list_marker(previous.text) else "•")
    aligned = abs(extra.bbox.x0 - text_start) <= max(tolerance, 0.70 * median_height)
    return aligned and len(extra.text.strip()) <= 240


def _is_inferred_nested_item(parent: TextLine, extra: TextLine, median_height: float, tolerance: float) -> bool:
    """Return True when ``extra`` looks like an implicit nested list item under ``parent``.

    Applies conservative geometric guards: the candidate must be significantly
    indented relative to the parent, vertically adjacent, short, and must not
    end with a sentence-terminating character that would indicate prose rather
    than a compact nested entry.
    """
    if parse_list_marker(extra.text) is not None or not extra.text.strip():
        return False
    gap = extra.bbox.y0 - parent.bbox.y1
    stronger_indent = extra.bbox.x0 >= parent.bbox.x0 + max(2.0 * tolerance, 0.75 * median_height)
    return (
        stronger_indent
        and -0.25 * median_height <= gap <= 1.25 * median_height
        and len(extra.text.strip()) <= 140
        and not extra.text.rstrip().endswith((".", ";", ":"))
    )


def _text_start_x(line: TextLine, marker: str) -> float:
    """Approximate the text column after a marker without using exact glyph widths."""
    marker_width = max(len(marker), 1) * max(line.bbox.height * 0.65, 4.0)
    return line.bbox.x0 + min(max(line.bbox.height, marker_width), line.bbox.width * 0.40)


def _line_confidence(line: TextLine) -> float | None:
    values = [token.confidence for token in line.tokens if token.confidence is not None]
    return sum(values) / len(values) if values else None
