from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine, WritingDirection
from structured_pdf_text.text.normalize import normalize_text


@dataclass(frozen=True, slots=True)
class ReadingOrderDecision:
    region_order: tuple[str, ...]
    column_groups: int
    rotated_lines: int
    table_regions: int
    native_order_consistency: float | None = None
    region_edges: tuple[tuple[str, str, float], ...] = ()
    deduplicated_lines: int = 0


def order_region_lines(regions: list[LayoutRegion]) -> tuple[list[TextLine], ReadingOrderDecision]:
    """Order page lines using region semantics and selective column splitting."""
    consistency = _native_order_consistency(regions)
    ordered_regions, region_edges = _order_region_graph(regions, consistency)
    output: list[TextLine] = []
    column_groups = 0
    rotated_lines = 0
    table_regions = 0
    for region in ordered_regions:
        if not region.native_lines:
            continue
        if region.kind == RegionKind.TABLE:
            lines = _order_table_lines(region.native_lines)
            table_regions += 1
        elif region.kind in {RegionKind.TEXT, RegionKind.TITLE, RegionKind.LIST, RegionKind.CAPTION, RegionKind.FOOTNOTE, RegionKind.UNKNOWN}:
            lines, groups = _order_prose_lines(region.native_lines, region.bbox.width)
            column_groups += groups
        else:
            lines = sorted(region.native_lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
        rotated_lines += sum(1 for line in lines if line.direction != WritingDirection.LEFT_TO_RIGHT)
        output.extend(lines)
    output, deduplicated_lines = _deduplicate_adjacent_region_lines(output)
    return output, ReadingOrderDecision(
        region_order=tuple(region.region_id for region in ordered_regions),
        column_groups=column_groups,
        rotated_lines=rotated_lines,
        table_regions=table_regions,
        native_order_consistency=consistency,
        region_edges=region_edges,
        deduplicated_lines=deduplicated_lines,
    )


def _deduplicate_adjacent_region_lines(lines: list[TextLine]) -> tuple[list[TextLine], int]:
    """Drop only long, exact duplicates at nearly the same page position.

    Layout crops may include a caption that also exists as a native edge line.
    Short labels and repeated table values are deliberately excluded because
    their repetition can be semantically meaningful.
    """
    output: list[TextLine] = []
    keys: list[str] = []
    removed = 0
    for line in lines:
        key = " ".join(normalize_text(line.text).casefold().split())
        duplicate_index = next(
            (
                index
                for index, (existing, existing_key) in enumerate(zip(output, keys))
                if key == existing_key
                and (len(key) >= 12 or len(key.split()) >= 3)
                and _same_reading_line_position(line, existing)
            ),
            None,
        )
        if duplicate_index is None:
            output.append(line)
            keys.append(key)
            continue
        removed += 1
        if _native_line_score(line) > _native_line_score(output[duplicate_index]):
            output[duplicate_index] = line
    return output, removed


def _same_reading_line_position(first: TextLine, second: TextLine) -> bool:
    if first.bbox.iou(second.bbox) >= 0.10:
        return True
    horizontal_overlap = _axis_overlap(
        first.bbox.x0,
        first.bbox.x1,
        second.bbox.x0,
        second.bbox.x1,
    )
    horizontal_ratio = horizontal_overlap / max(
        min(first.bbox.width, second.bbox.width),
        1.0,
    )
    vertical_gap = max(
        0.0,
        first.bbox.y0 - second.bbox.y1,
        second.bbox.y0 - first.bbox.y1,
    )
    return horizontal_ratio >= 0.65 and vertical_gap <= max(
        6.0,
        first.bbox.height,
        second.bbox.height,
    ) * 2.0


def _native_line_score(line: TextLine) -> int:
    return int(line.native_order_min is not None) + sum(
        source.source.value.startswith("native")
        for token in line.tokens
        for source in token.sources
    )


def _order_region_graph(
    regions: list[LayoutRegion],
    native_consistency: float | None,
) -> tuple[list[LayoutRegion], tuple[tuple[str, str, float], ...]]:
    """Resolve weighted pairwise evidence into an acyclic region graph."""
    if len(regions) < 2:
        return list(regions), ()
    base = sorted(regions, key=_region_key)
    base_index = {id(region): index for index, region in enumerate(base)}
    candidates: list[tuple[float, LayoutRegion, LayoutRegion]] = []
    for index, first in enumerate(regions):
        for second in regions[index + 1 :]:
            score = _region_pair_score(first, second, native_consistency)
            if score > 0:
                candidates.append((score, first, second))
            elif score < 0:
                candidates.append((-score, second, first))
            elif base_index[id(first)] <= base_index[id(second)]:
                candidates.append((0.01, first, second))
            else:
                candidates.append((0.01, second, first))

    graph: dict[int, set[int]] = {id(region): set() for region in regions}
    accepted: list[tuple[str, str, float]] = []
    for weight, before, after in sorted(candidates, key=lambda item: item[0], reverse=True):
        before_id = id(before)
        after_id = id(after)
        if _has_path(graph, after_id, before_id):
            continue
        graph[before_id].add(after_id)
        accepted.append((before.region_id, after.region_id, round(weight, 4)))

    incoming = {node: 0 for node in graph}
    for destinations in graph.values():
        for destination in destinations:
            incoming[destination] += 1
    by_id = {id(region): region for region in regions}
    ready = sorted(
        (node for node, count in incoming.items() if count == 0),
        key=lambda node: base_index[node],
    )
    ordered: list[LayoutRegion] = []
    while ready:
        node = ready.pop(0)
        ordered.append(by_id[node])
        for destination in sorted(graph[node], key=lambda item: base_index[item]):
            incoming[destination] -= 1
            if incoming[destination] == 0:
                ready.append(destination)
                ready.sort(key=lambda item: base_index[item])
    if len(ordered) != len(regions):
        return base, tuple(accepted)
    return ordered, tuple(accepted)


def _region_pair_score(
    first: LayoutRegion,
    second: LayoutRegion,
    native_consistency: float | None,
) -> float:
    """Positive means first-before-second; negative means the reverse."""
    if first.kind == RegionKind.HEADER or second.kind == RegionKind.FOOTER:
        return 100.0
    if second.kind == RegionKind.HEADER or first.kind == RegionKind.FOOTER:
        return -100.0

    score = 0.0
    horizontal_overlap = _axis_overlap(
        first.bbox.x0,
        first.bbox.x1,
        second.bbox.x0,
        second.bbox.x1,
    ) / max(min(first.bbox.width, second.bbox.width), 1.0)
    vertical_overlap = _axis_overlap(
        first.bbox.y0,
        first.bbox.y1,
        second.bbox.y0,
        second.bbox.y1,
    ) / max(min(first.bbox.height, second.bbox.height), 1.0)
    tolerance = max(3.0, min(first.bbox.height, second.bbox.height) * 0.10)
    if first.bbox.y1 <= second.bbox.y0 + tolerance and horizontal_overlap >= 0.15:
        score += 8.0
    elif second.bbox.y1 <= first.bbox.y0 + tolerance and horizontal_overlap >= 0.15:
        score -= 8.0
    elif vertical_overlap >= 0.20:
        if first.bbox.x1 <= second.bbox.x0 + tolerance:
            score += 5.0
        elif second.bbox.x1 <= first.bbox.x0 + tolerance:
            score -= 5.0

    first_order = _region_native_order(first)
    second_order = _region_native_order(second)
    if first_order is not None and second_order is not None and first_order != second_order:
        native_weight = 1.0 + 6.0 * (native_consistency or 0.0)
        score += native_weight if first_order < second_order else -native_weight

    first_priority = _semantic_priority(first.kind)
    second_priority = _semantic_priority(second.kind)
    if first_priority != second_priority:
        semantic_weight = min(3.0, abs(second_priority - first_priority) * 0.5)
        score += semantic_weight if first_priority < second_priority else -semantic_weight
    return score


def _native_order_consistency(regions: list[LayoutRegion]) -> float | None:
    lines = [
        line
        for region in regions
        for line in region.native_lines
        if line.native_order_min is not None
    ]
    if len(lines) < 3:
        return None
    ordered = sorted(lines, key=lambda line: line.native_order_min or 0)
    plausible = 0
    transitions = 0
    for previous, current in zip(ordered, ordered[1:]):
        transitions += 1
        tolerance = max(previous.bbox.height, current.bbox.height, 2.0) * 1.5
        vertical_forward = current.bbox.y0 >= previous.bbox.y0 - tolerance
        column_forward = (
            current.bbox.x0 >= previous.bbox.x0 + min(previous.bbox.width, current.bbox.width) * 0.50
            and current.bbox.y0 < previous.bbox.y0 - tolerance
        )
        if vertical_forward or column_forward:
            plausible += 1
    return round(plausible / max(transitions, 1), 6)


def _region_native_order(region: LayoutRegion) -> int | None:
    values = [
        line.native_order_min
        for line in region.native_lines
        if line.native_order_min is not None
    ]
    return min(values) if values else None


def _semantic_priority(kind: RegionKind) -> int:
    return {
        RegionKind.HEADER: 0,
        RegionKind.TITLE: 1,
        RegionKind.TEXT: 2,
        RegionKind.LIST: 2,
        RegionKind.TABLE: 3,
        RegionKind.FIGURE: 4,
        RegionKind.CAPTION: 5,
        RegionKind.FOOTNOTE: 6,
        RegionKind.MARGINALIA: 7,
        RegionKind.UNKNOWN: 8,
        RegionKind.FOOTER: 99,
    }.get(kind, 50)


def _axis_overlap(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _has_path(graph: dict[int, set[int]], start: int, target: int) -> bool:
    pending = [start]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(graph.get(node, ()))
    return False


def _region_key(region: LayoutRegion) -> tuple[int, float, float]:
    return _semantic_priority(region.kind), region.bbox.y0, region.bbox.x0


def _order_table_lines(lines: list[TextLine]) -> list[TextLine]:
    """Use row geometry only; never apply prose column splitting to tables."""
    if not lines:
        return []
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    tolerance = max(2.0, (median(heights) if heights else 10.0) * 0.60)
    rows: list[list[TextLine]] = []
    centers: list[float] = []
    for line in sorted(lines, key=lambda item: (item.bbox.cy, item.bbox.x0, item.native_order_min or 0)):
        index = min(range(len(centers)), key=lambda candidate: abs(centers[candidate] - line.bbox.cy), default=None)
        if index is None or abs(centers[index] - line.bbox.cy) > tolerance:
            rows.append([line])
            centers.append(line.bbox.cy)
        else:
            rows[index].append(line)
            centers[index] = median([item.bbox.cy for item in rows[index]])
    return [line for row in sorted(rows, key=lambda row: min(item.bbox.y0 for item in row)) for line in sorted(row, key=lambda item: (item.bbox.x0, item.native_order_min or 0))]


def _order_prose_lines(lines: list[TextLine], region_width: float) -> tuple[list[TextLine], int]:
    if len(lines) < 4 or region_width <= 0:
        return sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0)), 0
    if any(line.baseline is not None and abs(line.baseline.angle) > 0.01 for line in lines):
        # OCR already ordered these lines in its upright coordinate system.
        # Sorting by the original page bbox would undo that correction.
        return list(lines), 0
    ordered = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    result: list[TextLine] = []
    current: list[TextLine] = []
    column_groups = 0

    def flush_chunk(chunk: list[TextLine]) -> None:
        nonlocal column_groups
        if not chunk:
            return
        columns = _split_columns(chunk, region_width)
        if len(columns) == 1:
            result.extend(columns[0])
        else:
            column_groups += 1
            result.extend(line for column in columns for line in column)

    # Full-width lines are hard boundaries: emit the preceding prose chunk,
    # then the band itself, so titles and section dividers retain their
    # vertical position instead of being appended after all columns.
    for line in ordered:
        if line.bbox.width >= region_width * 0.72:
            flush_chunk(current)
            current = []
            result.append(line)
        else:
            current.append(line)
    flush_chunk(current)
    return result, column_groups


def _split_columns(lines: list[TextLine], region_width: float) -> list[list[TextLine]]:
    if len(lines) < 4:
        return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]
    # Cluster x0 starts rather than splitting at one accidental large gap.
    # This is stable when a paragraph begins with an indent or a short bullet.
    starts = sorted(line.bbox.x0 for line in lines)
    tolerance = max(12.0, region_width * 0.045)
    clusters: list[list[float]] = []
    for start in starts:
        if not clusters or abs(start - median(clusters[-1])) > tolerance:
            clusters.append([start])
        else:
            clusters[-1].append(start)
    if len(clusters) < 2:
        return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]

    # Build column edges and validate minimum separation between each pair.
    column_edges = [median(c) for c in clusters]
    min_col_separation = max(18.0, region_width * 0.10)
    for i in range(len(column_edges) - 1):
        if column_edges[i + 1] - column_edges[i] < min_col_separation:
            return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]

    # Assign each line to the nearest column edge.
    columns: list[list[TextLine]] = [[] for _ in column_edges]
    for line in lines:
        nearest = min(range(len(column_edges)), key=lambda i: abs(line.bbox.x0 - column_edges[i]))
        columns[nearest].append(line)

    # Filter empty slots and require at least 2 lines per column.
    valid_columns = [
        sorted(col, key=lambda line: (line.bbox.y0, line.bbox.x0))
        for col in columns
        if len(col) >= 2
    ]
    if len(valid_columns) < 2:
        return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]

    # A short punctuation mark or an indented continuation is not a column.
    # Requiring a meaningful median line width on all sides keeps the split
    # conservative while still accepting ordinary narrow newspaper columns.
    min_col_width = max(24.0, region_width * 0.12)
    if any(
        median(line.bbox.width for line in col) < min_col_width
        for col in valid_columns
    ):
        return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]

    return valid_columns
