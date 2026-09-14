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
from structured_pdf_text.tables.cells import tokens_in_cell
from structured_pdf_text.tables.relaxed import detect_relaxed_table
from structured_pdf_text.tables.text_tracks import detect_borderless_table


@dataclass(frozen=True, slots=True)
class _Grid:
    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    bbox: BBox
    coherence: float


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
    thickness = max(4.0, median_stroke * 2.5)
    horizontal = [
        path
        for path in paths
        if path.height <= thickness and path.width >= max(40.0, limit.width * 0.45)
    ]
    vertical = [
        path
        for path in paths
        if path.width <= thickness and path.height >= max(30.0, limit.height * 0.45)
    ]
    x_edges = _cluster_edges([path.cx for path in vertical], tolerance=max(2.0, thickness))
    y_edges = _cluster_edges([path.cy for path in horizontal], tolerance=max(2.0, thickness))
    if len(x_edges) < 2 or len(y_edges) < 2:
        return None
    x0 = min(path.x0 for path in horizontal + vertical)
    y0 = min(path.y0 for path in horizontal + vertical)
    x1 = max(path.x1 for path in horizontal + vertical)
    y1 = max(path.y1 for path in horizontal + vertical)
    if x1 <= x0 or y1 <= y0:
        return None
    expected_horizontal = len(x_edges)
    expected_vertical = len(y_edges)
    horizontal_coverage = sum(path.width for path in horizontal) / max(limit.width * expected_horizontal, 1.0)
    vertical_coverage = sum(path.height for path in vertical) / max(limit.height * expected_vertical, 1.0)
    coherence = min(1.0, max(0.0, (horizontal_coverage + vertical_coverage) / 2.0))
    if coherence < 0.70:
        return None
    return _Grid(
        x_edges=tuple(x_edges),
        y_edges=tuple(y_edges),
        bbox=BBox(x0, y0, x1, y1),
        coherence=coherence,
    )


def _table_from_grid(
    page: NativePageEvidence,
    region_id: str,
    grid: _Grid,
    lines: list[TextLine],
) -> StructuredTable:
    cells: list[TableCell] = []
    for row in range(len(grid.y_edges) - 1):
        for col in range(len(grid.x_edges) - 1):
            bbox = BBox(
                grid.x_edges[col],
                grid.y_edges[row],
                grid.x_edges[col + 1],
                grid.y_edges[row + 1],
            )
            tokens = tokens_in_cell(lines, bbox)
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=1,
                    colspan=1,
                    bbox=bbox,
                    text="".join(token.text for token in tokens).strip(),
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
                row_end=len(grid.y_edges) - 2,
            )
        ],
        cells=cells,
        column_count=len(grid.x_edges) - 1,
        row_count=len(grid.y_edges) - 1,
        confidence=grid.coherence,
        method=TableMethod.STRICT_GRID,
    )


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
