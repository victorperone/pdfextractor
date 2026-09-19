"""Ruled and borderless table detection — Level 2: grid reconstruction.

Responsibility
--------------
Given a search region (a ``TableGeometryCandidate`` bbox or the full page bbox),
reconstruct the logical cell grid: cluster horizontal and vertical edges, assess
coherence, handle partial separators, assign tokens to cells, and produce a
``StructuredTable``.

This module operates inside a *known search window*. It does NOT perform
connected-component analysis of path segments across the whole page — that is
the role of ``tables/geometry.py``.

Division of labour
------------------
``tables/geometry.py``
    Connected-component analysis → candidate bounding boxes (Level 1).
    Used by ``layout/engine.py`` to label TABLE regions before detection.

``tables/detector.py``  (this file)
    Grid reconstruction inside a region or full page (Level 2).
    Three tiers, attempted in order:

    1. ``_detect_strict_grid`` — full H/V grid with coherence score ≥ 0.65.
       Deliberately uses its own edge-level logic (independent from
       geometry.py) so it can accept tables where vertical separators are
       partial or absent for some rows. Merging with geometry.py's stricter
       H+V requirement would reduce recall for these cases.

    2. ``detect_relaxed_table`` — fallback when strict coherence fails.

    3. ``detect_borderless_table`` — alignment-track detection for tables
       with no path-based borders at all.

When to integrate
-----------------
See ``tables/geometry.py`` docstring. Shared segment APIs should be considered
only after real-document validation confirms the two levels are fully redundant.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    RegionKind,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.text_join import join_table_tokens, recover_cell_text
from structured_pdf_text.tables.cells import tokens_in_cell
from structured_pdf_text.tables.relaxed import detect_relaxed_table
from structured_pdf_text.tables.text_tracks import detect_borderless_table


@dataclass(frozen=True, slots=True)
class _Grid:
    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    bbox: BBox
    coherence: float
    rectangular_cells: tuple[tuple[int, int, int, int], ...] = ()


def detect_tables_native(
    page: NativePageEvidence,
    regions: list[LayoutRegion] | None = None,
) -> list[StructuredTable]:
    """Run strict, relaxed and borderless deterministic table tiers."""
    candidates = [region for region in (regions or []) if region.kind == RegionKind.TABLE]
    tables: list[StructuredTable] = []
    if not candidates:
        grid = _detect_strict_grid(page, page.bbox)
        if grid is not None:
            lines = [line for region in (regions or []) for line in region.native_lines]
            tables.append(
                _table_from_grid(
                    page,
                    f"page-{page.page_index + 1}:native-grid",
                    grid,
                    lines,
                )
            )
    else:
        for region in candidates:
            grid = _detect_strict_grid(page, region.bbox)
            if grid is None:
                relaxed = detect_relaxed_table(page, region)
                if relaxed is not None:
                    tables.append(relaxed)
                continue
            lines = [
                line
                for line in region.native_lines
                if line.bbox.overlap_ratio(grid.bbox) > 0.10
            ]
            tables.append(_table_from_grid(page, region.region_id, grid, lines))

    # A borderless table has no path-derived TABLE region. Search only
    # text-bearing region kinds and reject candidates through recurrent-track
    # and prose-continuity evidence before accepting them.
    eligible_kinds = {
        RegionKind.TEXT,
        RegionKind.LIST,
        RegionKind.UNKNOWN,
        RegionKind.TABLE,
    }
    for index, region in enumerate(
        [region for region in (regions or []) if region.kind in eligible_kinds],
        start=1,
    ):
        if any(_region_overlaps_table(region, table) for table in tables):
            continue
        borderless = detect_borderless_table(
            page,
            region,
            table_id=f"page-{page.page_index + 1}:borderless-{index}",
        )
        if borderless is not None:
            tables.append(borderless)
    for table in tables:
        for cell in table.cells:
            cell.text = recover_cell_text(cell.text, page.extracted_text)
    return tables


def _region_overlaps_table(region: LayoutRegion, table: StructuredTable) -> bool:
    boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    return any(
        region.bbox.overlap_ratio(box) >= 0.50
        or box.overlap_ratio(region.bbox) >= 0.50
        for box in boxes
    )


def _detect_strict_grid(page: NativePageEvidence, limit: BBox) -> _Grid | None:
    paths = [path.bbox for path in page.objects.paths if path.bbox is not None]
    paths = [path for path in paths if path.overlap_ratio(limit) > 0.50 and not _is_background(path, page.bbox)]
    if not paths:
        return None
    median_stroke = median(
        [min(path.width, path.height) for path in paths if min(path.width, path.height) > 0]
        or [2.0]
    )
    if median_stroke > 8.0:
        return _detect_rectangular_grid(paths, limit)
    thickness = max(4.0, median_stroke * 2.5)
    # Candidate horizontal lines: thin, wide enough to span most of the limit.
    horizontal = [
        path
        for path in paths
        if path.height <= thickness and path.width >= max(40.0, limit.width * 0.45)
    ]
    if not horizontal:
        return _detect_rectangular_grid(paths, limit)
    # Derive grid height for vertical line filtering. Using all horizontal
    # lines inflates the estimate when header/footer rules are present. Instead
    # use any short-height vertical line candidates (height >= 30 pt) to anchor
    # the actual table extent. Fall back to the horizontal span if none exist.
    vertical_seeds = [
        path for path in paths if path.width <= thickness and path.height >= 30.0
    ]
    if vertical_seeds:
        v_y0 = min(path.y0 for path in vertical_seeds)
        v_y1 = max(path.y1 for path in vertical_seeds)
        grid_height = max(v_y1 - v_y0, 1.0)
        # Exclude horizontal rules that lie outside the vertical grid extent;
        # page-level header/footer rules would inflate the grid otherwise.
        horizontal = [
            path for path in horizontal if v_y0 - thickness <= path.cy <= v_y1 + thickness
        ]
    else:
        h_y0 = min(path.cy for path in horizontal)
        h_y1 = max(path.cy for path in horizontal)
        grid_height = max(h_y1 - h_y0, 1.0)
    vertical = [
        path
        for path in paths
        if path.width <= thickness and path.height >= max(30.0, grid_height * 0.50)
    ]
    x_edges = _cluster_edges([path.cx for path in vertical], tolerance=max(2.0, thickness))
    y_edges = _cluster_edges([path.cy for path in horizontal], tolerance=max(2.0, thickness))
    if len(x_edges) < 2 or len(y_edges) < 2:
        return _detect_rectangular_grid(paths, limit)
    x0 = min(path.x0 for path in horizontal + vertical)
    y0 = min(path.y0 for path in horizontal + vertical)
    x1 = max(path.x1 for path in horizontal + vertical)
    y1 = max(path.y1 for path in horizontal + vertical)
    if x1 <= x0 or y1 <= y0:
        return _detect_rectangular_grid(paths, limit)
    table_width = x1 - x0
    table_height = y1 - y0
    # Coherence: each horizontal line should span the full table width, and
    # each vertical line should span the full table height.
    horizontal_coverage = sum(path.width for path in horizontal) / max(table_width * len(horizontal), 1.0)
    if vertical:
        vertical_coverage = sum(path.height for path in vertical) / max(table_height * len(vertical), 1.0)
    else:
        # Tables with only horizontal rules (no vertical separators) are
        # still valid — treat missing verticals as partial evidence.
        vertical_coverage = 0.70
    coherence = min(1.0, max(0.0, (horizontal_coverage + vertical_coverage) / 2.0))
    if coherence < 0.65:
        return _detect_rectangular_grid(paths, limit)
    return _Grid(
        x_edges=tuple(x_edges),
        y_edges=tuple(y_edges),
        bbox=BBox(x0, y0, x1, y1),
        coherence=coherence,
    )


def _detect_rectangular_grid(paths: list[BBox], limit: BBox) -> _Grid | None:
    """Infer a grid from repeated rectangular cell outlines.

    Some producers draw each cell as a filled/stroked rectangle rather than
    emitting thin horizontal and vertical path segments.  PDFium exposes the
    rectangle bounds in that case, so the edge coordinates still provide a
    reliable grid signal even though the segment-based detector cannot use
    them directly.

    The occupancy requirement is deliberately strict enough to avoid treating
    a handful of unrelated rectangles as a table.  Merged cells are accepted
    because one rectangle may span more than one adjacent edge pair.
    """

    if len(paths) < 4:
        return None
    tolerance = 2.5
    x_edges = _cluster_edges(
        [value for path in paths for value in (path.x0, path.x1)],
        tolerance=tolerance,
    )
    y_edges = _cluster_edges(
        [value for path in paths for value in (path.y0, path.y1)],
        tolerance=tolerance,
    )
    if len(x_edges) < 3 or len(y_edges) < 3:
        return None

    cells: set[tuple[int, int, int, int]] = set()
    for path in paths:
        left = _nearest_edge(path.x0, x_edges, tolerance)
        right = _nearest_edge(path.x1, x_edges, tolerance)
        top = _nearest_edge(path.y0, y_edges, tolerance)
        bottom = _nearest_edge(path.y1, y_edges, tolerance)
        if None in {left, right, top, bottom}:
            continue
        assert left is not None and right is not None
        assert top is not None and bottom is not None
        if right > left and bottom > top:
            cells.add((top, left, bottom, right))

    possible_cells = max((len(x_edges) - 1) * (len(y_edges) - 1), 1)
    occupancy = len(cells) / possible_cells
    if len(cells) < 4 or occupancy < 0.55:
        return None

    x0 = min(path.x0 for path in paths)
    y0 = min(path.y0 for path in paths)
    x1 = max(path.x1 for path in paths)
    y1 = max(path.y1 for path in paths)
    if not (x0 < x1 and y0 < y1):
        return None
    if not BBox(x0, y0, x1, y1).overlap_ratio(limit) > 0.50:
        return None
    return _Grid(
        x_edges=tuple(x_edges),
        y_edges=tuple(y_edges),
        bbox=BBox(x0, y0, x1, y1),
        coherence=min(1.0, 0.65 + 0.35 * occupancy),
        rectangular_cells=tuple(sorted(cells)),
    )


def _nearest_edge(value: float, edges: list[float], tolerance: float) -> int | None:
    index = min(range(len(edges)), key=lambda item: abs(edges[item] - value))
    return index if abs(edges[index] - value) <= tolerance else None


def _table_from_grid(
    page: NativePageEvidence,
    region_id: str,
    grid: _Grid,
    lines: list[TextLine],
) -> StructuredTable:
    # Derive the same thickness used by _detect_strict_grid so span checks
    # use consistent tolerances. Filter paths to the grid bbox to avoid
    # inflating median_stroke with decorative borders outside the table.
    grid_bbox = BBox(
        min(grid.x_edges),
        min(grid.y_edges),
        max(grid.x_edges),
        max(grid.y_edges),
    )
    region_paths = [
        p for p in page.objects.paths
        if p.bbox is not None and p.bbox.overlap_ratio(grid_bbox) > 0.30
    ]
    median_stroke = median(
        [min(p.bbox.width, p.bbox.height) for p in region_paths
         if min(p.bbox.width, p.bbox.height) > 0] or [2.0]
    )
    thickness = max(4.0, median_stroke * 2.5)
    region_path_bboxes: list[BBox] = [p.bbox for p in region_paths]

    n_rows = len(grid.y_edges) - 1
    n_cols = len(grid.x_edges) - 1

    # Detect span extents for each grid cell. A cell is "covered" when it falls
    # inside the bounding box of a previously computed merged cell. Rectangular
    # path producers already expose these spans directly; use them instead of
    # treating every thick rectangle as a segment crossing all rows/columns.
    covered: set[tuple[int, int]] = set()
    span_map: dict[tuple[int, int], tuple[int, int]] = {}  # (row,col) -> (rs, cs)

    if grid.rectangular_cells:
        for row, col, row_end, col_end in grid.rectangular_cells:
            if 0 <= row < n_rows and 0 <= col < n_cols and row_end <= n_rows and col_end <= n_cols:
                span_map[(row, col)] = (row_end - row, col_end - col)
    else:
        for row in range(n_rows):
            for col in range(n_cols):
                if (row, col) in covered:
                    continue
                # Colspan: extend right while the internal vertical separator at the
                # next x_edge is absent for this row band.
                cs = 1
                while col + cs < n_cols:
                    x_inner = grid.x_edges[col + cs]
                    if _segment_exists_at_x(
                        region_path_bboxes, x_inner,
                        grid.y_edges[row], grid.y_edges[row + 1],
                        thickness,
                    ):
                        break
                    cs += 1
                # Rowspan: extend down while the internal horizontal separator at the
                # next y_edge is absent for this (possibly merged) column band.
                rs = 1
                while row + rs < n_rows:
                    y_inner = grid.y_edges[row + rs]
                    if _segment_exists_at_y(
                        region_path_bboxes, y_inner,
                        grid.x_edges[col], grid.x_edges[col + cs],
                        thickness,
                    ):
                        break
                    rs += 1
                span_map[(row, col)] = (rs, cs)

    for (row, col), (rs, cs) in span_map.items():
        for r in range(row, row + rs):
            for c in range(col, col + cs):
                if r != row or c != col:
                    covered.add((r, c))

    cells: list[TableCell] = []
    for row in range(n_rows):
        for col in range(n_cols):
            if (row, col) in covered:
                continue
            rs, cs = span_map.get((row, col), (1, 1))
            bbox = BBox(
                grid.x_edges[col],
                grid.y_edges[row],
                grid.x_edges[col + cs],
                grid.y_edges[row + rs],
            )
            tokens = tokens_in_cell(lines, bbox)
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=rs,
                    colspan=cs,
                    bbox=bbox,
                    text=join_table_tokens(tokens),
                    tokens=tokens,
                    confidence=0.95 if tokens else 0.85,
                )
            )
    return StructuredTable(
        table_id=region_id,
        page_fragments=[
            TableFragment(
                page_index=page.page_index,
                bbox=grid.bbox,
                row_start=0,
                row_end=n_rows - 1,
            )
        ],
        cells=cells,
        column_count=n_cols,
        row_count=n_rows,
        confidence=grid.coherence,
        method=TableMethod.STRICT_GRID,
    )


def _segment_exists_at_x(
    paths: list[BBox],
    x_center: float,
    y0: float,
    y1: float,
    thickness: float,
) -> bool:
    """Return True if a vertical path segment exists at x≈x_center covering [y0, y1]."""
    for p in paths:
        if (
            p.width <= thickness
            and abs(p.cx - x_center) <= thickness
            and p.y0 <= y0 + thickness
            and p.y1 >= y1 - thickness
        ):
            return True
    return False


def _segment_exists_at_y(
    paths: list[BBox],
    y_center: float,
    x0: float,
    x1: float,
    thickness: float,
) -> bool:
    """Return True if a horizontal path segment exists at y≈y_center covering [x0, x1]."""
    for p in paths:
        if (
            p.height <= thickness
            and abs(p.cy - y_center) <= thickness
            and p.x0 <= x0 + thickness
            and p.x1 >= x1 - thickness
        ):
            return True
    return False


def _cluster_edges(values: list[float], tolerance: float) -> list[float]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - median(clusters[-1])) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [float(median(cluster)) for cluster in clusters]


def _is_background(box: BBox, page_bbox: BBox) -> bool:
    return box.overlap_ratio(page_bbox) >= 0.98 and box.area / max(page_bbox.area, 1.0) >= 0.90
