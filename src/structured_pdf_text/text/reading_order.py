from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine, WritingDirection
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.normalize import normalize_text


# Keep the sidebar heuristic centralized so its geometry and weight stay
# auditable.  The relation is only considered when blocks overlap vertically
# and are clearly lateral; vertical stacking remains the stronger signal.
SIDEBAR_WIDTH_RATIO = 1.7
SIDEBAR_WEIGHT = 10.0
SIDEBAR_MIN_VERTICAL_OVERLAP = 0.35
SIDEBAR_MAX_HORIZONTAL_OVERLAP = 0.20


@dataclass(frozen=True, slots=True)
class ReadingOrderDecision:
    region_order: tuple[str, ...]
    column_groups: int
    rotated_lines: int
    table_regions: int
    native_order_consistency: float | None = None
    region_edges: tuple[tuple[str, str, float], ...] = ()
    deduplicated_lines: int = 0
    flow_mode: str = "fallback"
    form_score: float = 0.0
    column_score: float = 0.0
    lane_count: int = 1
    gutter_count: int = 0
    spanning_band_count: int = 0
    flow_segment_count: int = 0
    fallback_used: bool = False
    line_preservation_ok: bool = True
    figure_caption_edges: tuple[tuple[str, str, float], ...] = ()
    mixed_content_edges: tuple[tuple[str, str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class FlowHypothesisScore:
    name: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProseFlowDecision:
    mode: str
    form_score: float
    column_score: float
    lane_count: int
    gutter_count: int
    reasons: tuple[str, ...]
    spanning_band_count: int = 0
    flow_segment_count: int = 0
    fallback_used: bool = False
    line_preservation_ok: bool = True


@dataclass(frozen=True, slots=True)
class ReadingLane:
    x0: float
    x1: float
    lines: tuple[TextLine, ...]


@dataclass(frozen=True, slots=True)
class _ProseFlowResult:
    lines: tuple[TextLine, ...]
    decision: ProseFlowDecision
    column_groups: int


def order_regions(
    regions: list[LayoutRegion],
) -> tuple[list[LayoutRegion], tuple[tuple[str, str, float], ...]]:
    """Return regions in reading order without flattening their lines.

    Separating region ordering from line ordering allows the canonical
    assembler to process each region independently — e.g. interleaving
    table blocks between prose lines — without re-running the full graph.
    """
    consistency = _native_order_consistency(regions)
    return _order_region_graph(regions, consistency)


def native_order_consistency(regions: list[LayoutRegion]) -> float | None:
    """Return the native reading-order consistency score for a set of regions.

    Exposes the private ``_native_order_consistency`` formula as a public API
    so the canonical assembler can populate ``ReadingOrderDecision`` without
    duplicating the formula or calling ``order_region_lines`` (which would run
    a second, independent ordering pass).
    """
    return _native_order_consistency(regions)


def order_lines_in_region(
    region: LayoutRegion,
) -> tuple[list[TextLine], int]:
    """Return the lines of a single region in reading order.

    Returns (ordered_lines, column_groups_detected).
    The caller is responsible for handling rotated lines and deduplication
    when combining lines from multiple regions.
    """
    if not region.native_lines:
        return [], 0
    if region.kind == RegionKind.TABLE:
        return _order_table_lines(region.native_lines), 0
    if region.kind in {
        RegionKind.TEXT,
        RegionKind.TITLE,
        RegionKind.LIST,
        RegionKind.CAPTION,
        RegionKind.UNKNOWN,
    }:
        lines, groups = _order_prose_lines(region.native_lines, region.bbox.width)
        return lines, groups
    return sorted(region.native_lines, key=lambda line: (line.bbox.y0, line.bbox.x0)), 0


def order_region_lines(regions: list[LayoutRegion]) -> tuple[list[TextLine], ReadingOrderDecision]:
    """Order page lines using region semantics and selective column splitting.

    Compatibility wrapper around order_regions() + order_lines_in_region().
    Existing callers continue to work unchanged.
    """
    consistency = _native_order_consistency(regions)
    ordered_regions, region_edges = _order_region_graph(regions, consistency)
    output: list[TextLine] = []
    column_groups = 0
    rotated_lines = 0
    table_regions = 0
    prose_decisions: list[ProseFlowDecision] = []
    for region in ordered_regions:
        if region.kind in {
            RegionKind.TEXT,
            RegionKind.TITLE,
            RegionKind.LIST,
            RegionKind.CAPTION,
            RegionKind.UNKNOWN,
        }:
            prose_result = _order_prose_lines_with_decision(
                region.native_lines,
                region.bbox.x0,
                region.bbox.width,
                region.bbox,
            )
            lines, groups = list(prose_result.lines), prose_result.column_groups
            prose_decisions.append(prose_result.decision)
        else:
            lines, groups = order_lines_in_region(region)
        if not lines:
            continue
        if region.kind == RegionKind.TABLE:
            table_regions += 1
        column_groups += groups
        rotated_lines += sum(1 for line in lines if line.direction != WritingDirection.LEFT_TO_RIGHT)
        output.extend(lines)
    ordered_line_set_preserved = _preserves_flat_line_set(
        [line for region in regions for line in region.native_lines], output
    )
    output, deduplicated_lines = _deduplicate_adjacent_region_lines(output)
    (
        flow_mode,
        form_score,
        column_score,
        lane_count,
        gutter_count,
        spanning_count,
        segment_count,
        fallback_used,
        preserved,
    ) = _aggregate_flow_decisions(prose_decisions)
    figure_caption_edges, mixed_content_edges = _classify_mixed_edges(ordered_regions, region_edges)
    return output, ReadingOrderDecision(
        region_order=tuple(region.region_id for region in ordered_regions),
        column_groups=column_groups,
        rotated_lines=rotated_lines,
        table_regions=table_regions,
        native_order_consistency=consistency,
        region_edges=region_edges,
        deduplicated_lines=deduplicated_lines,
        flow_mode=flow_mode,
        form_score=form_score,
        column_score=column_score,
        lane_count=lane_count,
        gutter_count=gutter_count,
        spanning_band_count=spanning_count,
        flow_segment_count=segment_count,
        fallback_used=fallback_used,
        line_preservation_ok=preserved and ordered_line_set_preserved,
        figure_caption_edges=figure_caption_edges,
        mixed_content_edges=mixed_content_edges,
    )


def _aggregate_flow_decisions(
    decisions: list[ProseFlowDecision],
) -> tuple[str, float, float, int, int, int, int, bool, bool]:
    if not decisions:
        return "fallback", 0.0, 0.0, 1, 0, 0, 0, False, True
    modes = [decision.mode for decision in decisions]
    mode = max(set(modes), key=modes.count)
    return (
        mode,
        round(max(decision.form_score for decision in decisions), 6),
        round(max(decision.column_score for decision in decisions), 6),
        max(decision.lane_count for decision in decisions),
        sum(decision.gutter_count for decision in decisions),
        sum(decision.spanning_band_count for decision in decisions),
        sum(decision.flow_segment_count for decision in decisions),
        any(decision.fallback_used for decision in decisions),
        all(decision.line_preservation_ok for decision in decisions),
    )


def _classify_mixed_edges(
    regions: list[LayoutRegion],
    edges: tuple[tuple[str, str, float], ...],
) -> tuple[tuple[tuple[str, str, float], ...], tuple[tuple[str, str, float], ...]]:
    by_id = {region.region_id: region for region in regions}
    figure_caption: list[tuple[str, str, float]] = []
    mixed: list[tuple[str, str, float]] = []
    for edge in edges:
        first = by_id.get(edge[0])
        second = by_id.get(edge[1])
        if first is None or second is None:
            continue
        kinds = {first.kind, second.kind}
        if kinds == {RegionKind.FIGURE, RegionKind.CAPTION}:
            figure_caption.append(edge)
        if RegionKind.FIGURE in kinds or RegionKind.CAPTION in kinds:
            mixed.append(edge)
    return tuple(figure_caption), tuple(mixed)


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
    if first.kind == RegionKind.FIGURE and second.kind == RegionKind.CAPTION:
        if second.bbox.y0 >= first.bbox.y1 - tolerance and horizontal_overlap >= 0.35:
            score += 30.0
        elif first.bbox.y0 >= second.bbox.y1 - tolerance:
            score -= 30.0
    elif second.kind == RegionKind.FIGURE and first.kind == RegionKind.CAPTION:
        if first.bbox.y0 >= second.bbox.y1 - tolerance and horizontal_overlap >= 0.35:
            score -= 30.0
        elif second.bbox.y0 >= first.bbox.y1 - tolerance:
            score += 30.0
    # A narrow block overlapping a wider body flow is most often a sidebar.
    # Positive means ``first`` comes first, so a wide main block must be
    # favored before a narrow lateral block regardless of input order.
    if _is_sidebar_relation(
        first,
        second,
        horizontal_overlap=horizontal_overlap,
        vertical_overlap=vertical_overlap,
        tolerance=tolerance,
    ):
        if first.bbox.width >= second.bbox.width * SIDEBAR_WIDTH_RATIO:
            score += SIDEBAR_WEIGHT
        elif second.bbox.width >= first.bbox.width * SIDEBAR_WIDTH_RATIO:
            score -= SIDEBAR_WEIGHT
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
    # Native order is a tie-breaker. It must not overturn strong geometric
    # evidence such as a persistent side-by-side column relation.
    if abs(score) < 4.0 and first_order is not None and second_order is not None and first_order != second_order:
        native_weight = 1.0 + 6.0 * (native_consistency or 0.0)
        score += native_weight if first_order < second_order else -native_weight

    # Geometry is primary. Semantic kind only resolves weak/tied relations.
    first_priority = _semantic_priority(first.kind)
    second_priority = _semantic_priority(second.kind)
    if first_priority != second_priority:
        semantic_weight = min(3.0, abs(second_priority - first_priority) * 0.5)
        score += semantic_weight if first_priority < second_priority else -semantic_weight
    return score


def _is_sidebar_relation(
    first: LayoutRegion,
    second: LayoutRegion,
    *,
    horizontal_overlap: float,
    vertical_overlap: float,
    tolerance: float,
) -> bool:
    """Require lateral overlap evidence before applying sidebar precedence."""
    if vertical_overlap < SIDEBAR_MIN_VERTICAL_OVERLAP:
        return False
    if horizontal_overlap >= SIDEBAR_MAX_HORIZONTAL_OVERLAP:
        return False
    # A clear above/below relation must always dominate width heuristics.
    if horizontal_overlap >= 0.15 and (
        first.bbox.y1 <= second.bbox.y0 + tolerance
        or second.bbox.y1 <= first.bbox.y0 + tolerance
    ):
        return False
    return first.bbox.x1 <= second.bbox.x0 + tolerance or second.bbox.x1 <= first.bbox.x0 + tolerance


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
        RegionKind.DECORATIVE: 8,
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
    return [
        line
        for row in sorted(rows, key=lambda row: min(item.bbox.y0 for item in row))
        for line in sorted(row, key=lambda item: (item.bbox.x0, item.native_order_min or 0))
    ]


def _order_prose_lines(lines: list[TextLine], region_width: float) -> tuple[list[TextLine], int]:
    if not lines:
        return [], 0
    result = _order_prose_lines_with_decision(
        lines,
        min(line.bbox.x0 for line in lines),
        region_width,
        BBox(
            min(line.bbox.x0 for line in lines),
            min(line.bbox.y0 for line in lines),
            max(line.bbox.x1 for line in lines),
            max(line.bbox.y1 for line in lines),
        ),
    )
    return list(result.lines), result.column_groups


def _order_prose_lines_with_decision(
    lines: list[TextLine],
    region_x0: float,
    region_width: float,
    region_bbox: BBox,
) -> _ProseFlowResult:
    if not lines:
        decision = ProseFlowDecision("FALLBACK", 0.0, 0.0, 1, 0, ("no_lines",), fallback_used=True)
        return _ProseFlowResult((), decision, 0)
    if region_width <= 0:
        ordered = tuple(sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0)))
        decision = ProseFlowDecision("FALLBACK", 0.0, 0.0, 1, 0, ("invalid_region_width",), fallback_used=True)
        return _ProseFlowResult(ordered, decision, 0)
    if any(line.baseline is not None and abs(line.baseline.angle) > 0.01 for line in lines):
        ordered = tuple(lines)
        decision = ProseFlowDecision(
            "FALLBACK", 0.0, 0.0, 1, 0,
            ("canonical_rotated_coordinates",),
            fallback_used=True,
            line_preservation_ok=_preserves_flat_line_set(lines, ordered),
        )
        return _ProseFlowResult(ordered, decision, 0)

    ordered = sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    gutters = _detect_persistent_gutters(ordered, region_bbox)
    lanes = _build_reading_lanes(ordered, region_bbox, gutters)
    form = _score_form_hypothesis(ordered, region_bbox, len(gutters))
    column = _score_column_hypothesis(ordered, region_bbox, gutters, lanes)
    margin = max(0.35, 0.12 * max(form.score, column.score, 1.0))
    if column.score >= 1.0 and column.score >= form.score + margin and len(lanes) >= 2:
        mode = "MULTI_COLUMN"
    elif form.score >= 1.0 and form.score >= column.score + margin:
        mode = "FORM"
    else:
        mode = "FALLBACK"

    reasons = tuple(dict.fromkeys((*form.reasons, *column.reasons)))
    if mode == "MULTI_COLUMN":
        output, spanning_count, segment_count = _order_lane_segments(ordered, lanes, region_bbox)
        groups = segment_count
    elif mode == "FORM":
        output = _order_form_rows(ordered)
        spanning_count = 0
        segment_count = 1
        groups = 0
    else:
        output = ordered
        spanning_count = 0
        segment_count = 1
        groups = 0
    preserved = _preserves_flat_line_set(lines, output)
    if not preserved:
        output = ordered
        mode = "FALLBACK"
        reasons = (*reasons, "line_set_preservation_failed")
    decision = ProseFlowDecision(
        mode,
        form.score,
        column.score,
        len(lanes),
        len(gutters),
        reasons,
        spanning_count,
        segment_count,
        fallback_used=mode == "FALLBACK",
        line_preservation_ok=preserved,
    )
    return _ProseFlowResult(tuple(output), decision, groups)


def _detect_persistent_gutters(
    lines: list[TextLine],
    region_bbox: BBox,
) -> list[tuple[float, float]]:
    """Find x bands empty across many adaptive vertical bands."""
    if len(lines) < 4 or region_bbox.width <= 0 or region_bbox.height <= 0:
        return []
    band_count = max(4, min(12, max(1, round(math.sqrt(len(lines)) * 2))))
    occupied_by_band: list[list[tuple[float, float]]] = [[] for _ in range(band_count)]
    for line in lines:
        start = int((line.bbox.cy - region_bbox.y0) / region_bbox.height * band_count)
        start = max(0, min(band_count - 1, start))
        occupied_by_band[start].append((line.bbox.x0, line.bbox.x1))
    candidate_gaps: list[tuple[float, float]] = []
    minimum_gap = max(6.0, region_bbox.width * 0.012)
    inner_left = region_bbox.x0 + region_bbox.width * 0.04
    inner_right = region_bbox.x1 - region_bbox.width * 0.04
    for intervals in occupied_by_band:
        merged = _merge_intervals(intervals)
        for first, second in zip(merged, merged[1:]):
            gap = (first[1], second[0])
            if gap[1] - gap[0] >= minimum_gap and gap[0] >= inner_left and gap[1] <= inner_right:
                candidate_gaps.append(gap)
    if not candidate_gaps:
        return []
    clusters: list[list[tuple[float, float]]] = []
    tolerance = max(6.0, region_bbox.width * 0.025)
    for gap in sorted(candidate_gaps):
        center = (gap[0] + gap[1]) / 2.0
        if not clusters or abs(center - (clusters[-1][0][0] + clusters[-1][0][1]) / 2.0) > tolerance:
            clusters.append([gap])
        else:
            clusters[-1].append(gap)
    # Sparse synthetic/test layouts may occupy only a few bands. Require at
    # least three observations when available, while keeping the threshold
    # adaptive to the selected number of bands.
    required_observations = min(3, max(2, len(lines) // 2))
    persistence = max(0.50, min(1.0, required_observations / band_count))
    return [
        (median(gap[0] for gap in cluster), median(gap[1] for gap in cluster))
        for cluster in clusters
        if len(cluster) / band_count >= persistence
    ]


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _build_reading_lanes(
    lines: list[TextLine],
    region_bbox: BBox,
    gutters: list[tuple[float, float]],
) -> list[ReadingLane]:
    edges = [region_bbox.x0]
    for left, right in gutters:
        edges.extend((left, right))
    edges.append(region_bbox.x1)
    lanes: list[ReadingLane] = []
    for left, right in zip(edges[::2], edges[1::2]):
        lane_lines = tuple(
            line for line in lines
            if _line_lane_overlap(line, left, right) > 0.0
        )
        if right > left:
            lanes.append(ReadingLane(left, right, lane_lines))
    return lanes


def _line_lane_overlap(line: TextLine, x0: float, x1: float) -> float:
    return _axis_overlap(line.bbox.x0, line.bbox.x1, x0, x1) / max(line.bbox.width, 1.0)


def _score_form_hypothesis(
    lines: list[TextLine], region_bbox: BBox, gutter_count: int,
) -> FlowHypothesisScore:
    rows = _group_form_rows(lines)
    pair_rows = [row for row in rows if len(row) >= 2]
    if not rows:
        return FlowHypothesisScore("FORM", 0.0, ("no_rows",))
    pair_ratio = len(pair_rows) / len(rows)
    char_counts = [len("".join(line.text.split())) for line in lines]
    widths = [line.bbox.width / max(region_bbox.width, 1.0) for line in lines]
    shortness = 1.0 - min(1.0, median(char_counts) / 120.0)
    local_gaps: list[float] = []
    for row in pair_rows:
        ordered = sorted(row, key=lambda line: line.bbox.x0)
        local_gaps.extend(
            max(0.0, second.bbox.x0 - first.bbox.x1) / max(region_bbox.width, 1.0)
            for first, second in zip(ordered, ordered[1:])
        )
    gap_locality = 1.0 - min(1.0, median(local_gaps) / 0.30) if local_gaps else 0.0
    long_prose = sum(width >= 0.55 or count >= 100 for width, count in zip(widths, char_counts)) / max(len(lines), 1)
    score = 2.2 * pair_ratio + 0.8 * shortness + 0.6 * gap_locality - 2.0 * long_prose - 1.2 * min(1, gutter_count)
    reasons = [f"paired_rows={len(pair_rows)}/{len(rows)}"]
    if long_prose > 0.25:
        reasons.append("long_prose_penalty")
    if gutter_count:
        reasons.append("persistent_gutter_penalty")
    return FlowHypothesisScore("FORM", max(0.0, score), tuple(reasons))


def _score_column_hypothesis(
    lines: list[TextLine], region_bbox: BBox, gutters: list[tuple[float, float]], lanes: list[ReadingLane],
) -> FlowHypothesisScore:
    if len(lanes) < 2 or not gutters:
        return FlowHypothesisScore("MULTI_COLUMN", 0.0, ("no_persistent_gutter",))
    populated = [lane for lane in lanes if len(lane.lines) >= 2]
    continuity = min(1.0, median(len(lane.lines) for lane in populated) / 4.0) if populated else 0.0
    stable = min(1.0, len(populated) / len(lanes))
    long_prose = sum(
        line.bbox.width >= region_bbox.width * 0.20 or len("".join(line.text.split())) >= 40
        for line in lines
    ) / max(len(lines), 1)
    gutter_ratio = median((right - left) / max(region_bbox.width, 1.0) for left, right in gutters)
    gutter_strength = min(1.0, gutter_ratio / 0.08)
    lane_widths = [lane.x1 - lane.x0 for lane in lanes]
    lane_balance = min(lane_widths) / max(lane_widths) if lane_widths and max(lane_widths) > 0 else 0.0
    score = (
        2.0 * min(1.0, len(gutters) / 2.0) * gutter_strength
        + 1.4 * stable * lane_balance
        + continuity * lane_balance
        + 0.8 * long_prose
    )
    reasons = [f"persistent_gutters={len(gutters)}", f"populated_lanes={len(populated)}/{len(lanes)}"]
    if long_prose >= 0.5:
        reasons.append("vertical_prose_continuity")
    if gutter_strength < 0.75:
        reasons.append("narrow_local_gap")
    if lane_balance < 0.50:
        reasons.append("unbalanced_form_like_lanes")
    return FlowHypothesisScore("MULTI_COLUMN", score, tuple(reasons))


def _group_form_rows(lines: list[TextLine]) -> list[list[TextLine]]:
    if not lines:
        return []
    heights = [line.bbox.height for line in lines if line.bbox.height > 0]
    tolerance = max(3.0, (median(heights) if heights else 10.0) * 0.75)
    rows: list[list[TextLine]] = []
    centers: list[float] = []
    for line in sorted(lines, key=lambda item: (item.bbox.cy, item.bbox.x0)):
        index = min(range(len(centers)), key=lambda item: abs(centers[item] - line.bbox.cy), default=None)
        if index is None or abs(centers[index] - line.bbox.cy) > tolerance:
            rows.append([line])
            centers.append(line.bbox.cy)
        else:
            rows[index].append(line)
            centers[index] = median(item.bbox.cy for item in rows[index])
    return rows


def _order_lane_segments(
    lines: list[TextLine], lanes: list[ReadingLane], region_bbox: BBox,
) -> tuple[list[TextLine], int, int]:
    assignments: dict[int, list[TextLine]] = {index: [] for index in range(len(lanes))}
    spanning: list[TextLine] = []
    for line in lines:
        overlaps = [_line_lane_overlap(line, lane.x0, lane.x1) for lane in lanes]
        relevant = [index for index, overlap in enumerate(overlaps) if overlap >= 0.20]
        covered = sum(_axis_overlap(line.bbox.x0, line.bbox.x1, lane.x0, lane.x1) for lane in lanes)
        combined = sum(lane.x1 - lane.x0 for lane in lanes)
        if len(relevant) >= 2 or covered / max(combined, 1.0) >= 0.70:
            spanning.append(line)
        else:
            assignments[max(range(len(lanes)), key=lambda index: overlaps[index])].append(line)

    spans = sorted(spanning, key=lambda line: (line.bbox.y0, line.bbox.x0))
    boundaries = [region_bbox.y0, *(line.bbox.cy for line in spans), region_bbox.y1]
    output: list[TextLine] = []
    segment_count = 0
    for segment_index in range(len(boundaries) - 1):
        top, bottom = boundaries[segment_index], boundaries[segment_index + 1]
        has_lines = any(
            top <= line.bbox.cy < bottom or (segment_index == len(boundaries) - 2 and top <= line.bbox.cy <= bottom)
            for lane in assignments.values() for line in lane
        )
        if has_lines:
            segment_count += 1
            for lane_index in range(len(lanes)):
                output.extend(
                    sorted(
                        (
                            line for line in assignments[lane_index]
                            if top <= line.bbox.cy < bottom
                            or (segment_index == len(boundaries) - 2 and top <= line.bbox.cy <= bottom)
                        ),
                        key=lambda line: (line.bbox.y0, line.bbox.x0),
                    )
                )
        if segment_index < len(spans):
            output.append(spans[segment_index])
    if not _preserves_flat_line_set(lines, output):
        return sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0)), 0, 1
    return output, len(spans), max(1, segment_count)


def _preserves_flat_line_set(
    lines: list[TextLine] | tuple[TextLine, ...],
    output: list[TextLine] | tuple[TextLine, ...],
) -> bool:
    input_ids = [id(line) for line in lines]
    output_ids = [id(line) for line in output]
    return len(output_ids) == len(input_ids) and sorted(output_ids) == sorted(input_ids)


def _split_columns(lines: list[TextLine], region_width: float) -> list[list[TextLine]]:
    # Keep this compatibility helper on the same geometry-first path as the
    # main flow decision. In particular, do not use x0 alone for assignment.
    if lines and region_width > 0:
        bbox = BBox(
            min(line.bbox.x0 for line in lines),
            min(line.bbox.y0 for line in lines),
            min(line.bbox.x0 for line in lines) + region_width,
            max(line.bbox.y1 for line in lines),
        )
        gutters = _detect_persistent_gutters(lines, bbox)
        lanes = _build_reading_lanes(lines, bbox, gutters)
        if len(lanes) >= 2 and all(len(lane.lines) >= 2 for lane in lanes):
            columns: list[list[TextLine]] = [[] for _ in lanes]
            for line in lines:
                overlaps = [_line_lane_overlap(line, lane.x0, lane.x1) for lane in lanes]
                columns[max(range(len(lanes)), key=lambda index: overlaps[index])].append(line)
            columns = [sorted(column, key=lambda line: (line.bbox.y0, line.bbox.x0)) for column in columns]
            if _preserves_line_set(lines, columns):
                return columns
    return [sorted(lines, key=lambda line: (line.bbox.y0, line.bbox.x0))]


def _preserves_line_set(lines: list[TextLine], columns: list[list[TextLine]]) -> bool:
    """Verify that a column split neither loses nor duplicates line objects."""
    input_ids = [id(line) for line in lines]
    output_ids = [id(line) for column in columns for line in column]
    return len(output_ids) == len(input_ids) and sorted(output_ids) == sorted(input_ids)


def _spans_lanes(line: TextLine, lines: list[TextLine], region_width: float) -> bool:
    if len(lines) < 6:
        return False
    narrow = [candidate for candidate in lines if candidate is not line and candidate.bbox.width < region_width * 0.55]
    if len(narrow) < 4:
        return False
    starts = sorted(candidate.bbox.x0 for candidate in narrow)
    if len(starts) < 4:
        return False
    midpoint = (starts[0] + starts[-1]) / 2
    has_left = any(start < midpoint - region_width * 0.08 for start in starts)
    has_right = any(start > midpoint + region_width * 0.08 for start in starts)
    return has_left and has_right and line.bbox.width >= region_width * 0.45


def _looks_like_form(lines: list[TextLine]) -> bool:
    if len(lines) < 4:
        return False
    starts: dict[int, int] = {}
    for line in lines:
        key = round(line.bbox.x0 / 8.0)
        starts[key] = starts.get(key, 0) + 1
    tracks = sorted(starts.values(), reverse=True)
    paired = sum(
        1 for left, right in zip(lines, lines[1:])
        if abs(left.bbox.cy - right.bbox.cy) <= max(left.bbox.height, right.bbox.height) * 0.8
        and left.bbox.x0 < right.bbox.x0
    )
    return len([value for value in tracks if value >= 2]) >= 2 and paired >= 2


def _order_form_rows(lines: list[TextLine]) -> list[TextLine]:
    rows: list[list[TextLine]] = []
    centers: list[float] = []
    tolerance = max(3.0, median([line.bbox.height for line in lines]) * 0.75)
    for line in sorted(lines, key=lambda item: (item.bbox.cy, item.bbox.x0)):
        index = min(range(len(centers)), key=lambda item: abs(centers[item] - line.bbox.cy), default=None)
        if index is None or abs(centers[index] - line.bbox.cy) > tolerance:
            rows.append([line])
            centers.append(line.bbox.cy)
        else:
            rows[index].append(line)
            centers[index] = median(item.bbox.cy for item in rows[index])
    return [
        line
        for row in sorted(rows, key=lambda item: min(line.bbox.y0 for line in item))
        for line in sorted(row, key=lambda item: item.bbox.x0)
    ]
