from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from statistics import median

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    OcrToken,
    RegionKind,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
)
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class _WordSpan:
    tokens: tuple[TextToken, ...]
    bbox: BBox


def detect_relaxed_table(
    page: NativePageEvidence,
    region: LayoutRegion,
) -> StructuredTable | None:
    """Infer a conservative table from repeated text x-tracks.

    This tier is only called after strict vector-grid detection fails. It
    requires repeated anchors across several lines and therefore declines
    ordinary prose instead of forcing a table shape.
    """
    lines = sorted(region.native_lines, key=lambda line: (line.bbox.y0, line.bbox.x0))
    from structured_pdf_text.tables.text_tracks import assess_borderless_region

    # A path-derived TABLE region is only a candidate. Reuse the same
    # prose-versus-table evidence as the borderless tier before inferring a
    # relaxed grid; decorative rules around ordinary paragraphs are common.
    if not any(assessment.accepted for assessment in assess_borderless_region(region)):
        return None
    spans_by_line = [_word_spans(line) for line in lines]
    usable = [spans for spans in spans_by_line if len(spans) >= 2]
    if len(usable) < 3:
        return None
    anchors = _repeated_anchors(usable, region.bbox.width)
    if len(anchors) < 2:
        return None
    table_bbox = BBox.union_all([region.bbox, *[line.bbox for line in lines]])
    edges = _track_edges(anchors, table_bbox)
    if len(edges) < 3:
        return None
    cells: list[TableCell] = []
    for row, (line, spans) in enumerate(zip(lines, spans_by_line)):
        row_cells = [_cell_bbox(edges, col, line.bbox) for col in range(len(edges) - 1)]
        grouped: list[list[TextToken]] = [[] for _ in row_cells]
        for span in spans:
            col = _column_for_x(span.bbox.cx, edges)
            if col is not None:
                grouped[col].extend(span.tokens)
        for col, tokens in enumerate(grouped):
            bbox = row_cells[col]
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=1,
                    colspan=1,
                    bbox=bbox,
                    text="".join(token.text for token in tokens).strip(),
                    tokens=tokens,
                    confidence=0.62 if tokens else 0.45,
                )
            )
    nonempty = sum(bool(cell.text) for cell in cells)
    coverage = nonempty / max(len(cells), 1)
    if coverage < 0.45:
        return None
    return StructuredTable(
        table_id=f"{region.region_id}:relaxed-grid",
        page_fragments=[
            TableFragment(
                page_index=page.page_index,
                bbox=table_bbox,
                row_start=0,
                row_end=len(lines) - 1,
            )
        ],
        cells=cells,
        column_count=len(edges) - 1,
        row_count=len(lines),
        confidence=0.55 * coverage + 0.45 * min(1.0, len(anchors) / 4.0),
        method=TableMethod.RELAXED_GRID,
    )


def _word_spans(line: TextLine) -> list[_WordSpan]:
    spans: list[_WordSpan] = []
    current: list[TextToken] = []
    for token in line.tokens:
        if token.text.isspace():
            if current:
                spans.append(_WordSpan(tuple(current), BBox.union_all([item.bbox for item in current])))
                current = []
        else:
            current.append(token)
    if current:
        spans.append(_WordSpan(tuple(current), BBox.union_all([item.bbox for item in current])))
    return spans


def _repeated_anchors(spans_by_line: list[list[_WordSpan]], width: float) -> list[float]:
    values = [span.bbox.x0 for spans in spans_by_line for span in spans]
    if not values:
        return []
    tolerance = max(5.0, width * 0.035)
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - median(clusters[-1])) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    required = max(2, ceil(len(spans_by_line) * 0.30))
    return [float(median(cluster)) for cluster in clusters if len(cluster) >= required]


def _track_edges(anchors: list[float], region: BBox) -> list[float]:
    anchors = sorted(set(anchors))
    if len(anchors) < 2:
        return []
    interior: list[float] = []
    for left, right in zip(anchors, anchors[1:]):
        midpoint = (left + right) / 2.0
        if region.x0 < midpoint < region.x1:
            interior.append(midpoint)
    return sorted(set([region.x0, *interior, region.x1]))


def _column_for_x(value: float, edges: list[float]) -> int | None:
    for index, (left, right) in enumerate(zip(edges, edges[1:])):
        if left <= value <= right:
            return index
    return None


def _cell_bbox(edges: list[float], col: int, line_bbox: BBox) -> BBox:
    # The row bbox remains line-local; page fragment retains the whole region.
    return BBox(edges[col], line_bbox.y0, edges[col + 1], line_bbox.y1)
