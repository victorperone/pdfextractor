"""Visual (raster) table detection backed by OpenCV morphological analysis.

This module is the raster-grid tier.  It is called only when page images are
available and native path-based detection has not already claimed the region.
The :func:`detect_visual_table` function orchestrates the detection pipeline:

1. Delegate grid discovery to a
   :class:`~structured_pdf_text.tables.visual_engine.TableStructureEngine`
   (defaulting to the OpenCV-based implementation in :mod:`visual_engine`).
2. Scale pixel-space edges to PDF coordinate space.
3. Validate that the detected grid falls inside a page image object (to avoid
   promoting chart axes or decorative borders to table grids).
4. Assign OCR tokens to cells and verify minimum text support.

:func:`detect_visual_grid` is also exported for direct use by engine adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.document import NativePageEvidence, StructuredTable, TableCell, TableFragment, TableMethod, TextToken
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.text_join import join_table_tokens


@dataclass(frozen=True, slots=True)
class VisualGrid:
    """Pixel-space grid produced by a :class:`~structured_pdf_text.tables.visual_engine.TableStructureEngine`.

    All edge coordinates are in image pixel space and must be scaled to PDF
    coordinates before constructing cell bounding boxes.

    Attributes:
        x_edges: Sorted x-coordinates of vertical column separators.
        y_edges: Sorted y-coordinates of horizontal row separators.
        confidence: Detection confidence in [0, 1].
        horizontal_lines: Raw ``(x, y, width, height)`` tuples for each
            detected horizontal line contour.
        vertical_lines: Raw ``(x, y, width, height)`` tuples for each
            detected vertical line contour.
    """

    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    confidence: float
    horizontal_lines: tuple[tuple[int, int, int, int], ...] = ()
    vertical_lines: tuple[tuple[int, int, int, int], ...] = ()


def detect_visual_table(
    page: NativePageEvidence,
    image: Any,
    table_id: str | None = None,
    tokens: list[TextToken] | None = None,
    structure_engine: Any | None = None,
) -> StructuredTable | None:
    """Recover a strong raster grid without inventing cell text."""
    if structure_engine is None:
        from .visual_engine import OpenCvTableStructureEngine

        structure_engine = OpenCvTableStructureEngine()
    grid = structure_engine.recognize(image)
    if grid is None:
        return None
    width, height = _image_size(image)
    x_scale = page.bbox.width / max(float(width), 1.0)
    y_scale = page.bbox.height / max(float(height), 1.0)
    x_edges = tuple(page.bbox.x0 + value * x_scale for value in grid.x_edges)
    y_edges = tuple(page.bbox.y0 + value * y_scale for value in grid.y_edges)
    grid_bbox = BBox(x_edges[0], y_edges[0], x_edges[-1], y_edges[-1])
    image_boxes = [item.bbox for item in page.objects.images if item.bbox is not None]
    if not image_boxes:
        # This tier is the raster fallback. On native pages, text baselines
        # and decorative rules can mimic a grid and must remain the concern
        # of the strict/relaxed native detectors.
        return None
    tolerance = max(3.0, min(page.bbox.width, page.bbox.height) * 0.01)
    if not any(
        _contains_with_tolerance(box, grid_bbox, tolerance) for box in image_boxes
    ):
        # A partial overlap is insufficient: chart axes combined with nearby
        # captions or page rules can form a convincing synthetic grid. A
        # raster table's detected grid should remain inside one image object.
        return None
    cell_tokens = tokens or []
    native_text_length = len("".join(page.extracted_text.split()))
    if not cell_tokens and native_text_length > 200:
        # On a mixed native page, a chart or diagram is far more likely than
        # an otherwise empty table. Balanced mode can reconsider the same
        # grid after regional OCR supplies cell-level textual evidence.
        return None
    cells = _build_visual_cells(
        page=page,
        image=image,
        x_edges=x_edges,
        y_edges=y_edges,
        tokens=cell_tokens,
    )
    if cell_tokens and not _has_visual_table_text_support(cells, grid):
        return None
    return StructuredTable(
        table_id=table_id or f"page-{page.page_index + 1}:visual-grid",
        page_fragments=[
            TableFragment(
                page_index=page.page_index,
                bbox=grid_bbox,
                row_start=0,
                row_end=len(y_edges) - 2,
            )
        ],
        cells=cells,
        column_count=len(x_edges) - 1,
        row_count=len(y_edges) - 1,
        confidence=grid.confidence,
        method=TableMethod.VISUAL_MODEL,
    )


def _contains_with_tolerance(container: BBox, content: BBox, tolerance: float) -> bool:
    return (
        content.x0 >= container.x0 - tolerance
        and content.y0 >= container.y0 - tolerance
        and content.x1 <= container.x1 + tolerance
        and content.y1 <= container.y1 + tolerance
    )


def _has_visual_table_text_support(
    cells: list[TableCell],
    grid: VisualGrid,
) -> bool:
    """Return ``True`` when cell tokens provide sufficient evidence for the detected grid.

    Checks three occupancy thresholds: at least 10 % of all cells are
    non-empty, at least 30 % of rows contain text, and at least 30 % of
    columns contain text.  For very small grids with low confidence, also
    requires at least one cell to contain a recognisable alphabetic word.
    """
    populated = [cell for cell in cells if cell.text.strip()]
    if not populated:
        return False
    row_count = max(1, len(grid.y_edges) - 1)
    column_count = max(1, len(grid.x_edges) - 1)
    occupancy = len(populated) / max(row_count * column_count, 1)
    populated_rows = len({cell.row for cell in populated}) / row_count
    populated_columns = len({cell.col for cell in populated}) / column_count
    if occupancy < 0.10 or populated_rows < 0.30 or populated_columns < 0.30:
        return False
    if row_count * column_count <= 4 and grid.confidence < 0.70:
        return any(
            _contains_alphabetic_word(token.text)
            for cell in populated
            for token in cell.tokens
        )
    return True


def _contains_alphabetic_word(text: str) -> bool:
    run = 0
    for character in text:
        if character.isalpha():
            run += 1
            if run >= 3:
                return True
        else:
            run = 0
    return False


def _join_cell_tokens(tokens: list[Any]) -> str:
    """Join OCR tokens while distinguishing word fragments from word gaps."""
    return join_table_tokens(tokens)


def detect_visual_grid(image: Any) -> VisualGrid | None:
    """Detect a ruled grid in *image* using OpenCV morphological operations.

    Applies separate horizontal and vertical structuring elements to isolate
    ruling lines, clusters their centre coordinates into edge positions, and
    computes a confidence score from the line density and spatial extent of
    the detected grid.

    Returns ``None`` when OpenCV is unavailable, the image is too small, or
    fewer than three edges are detected in either dimension.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    array = np.asarray(image)
    if array.ndim == 3:
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    else:
        gray = array
    height, width = gray.shape[:2]
    if width < 80 or height < 80:
        return None
    binary = cv2.threshold(gray, 215, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, width // 24), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, height // 24)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    horizontal_lines = _line_rects(horizontal, horizontal=True, width=width, height=height)
    vertical_lines = _line_rects(vertical, horizontal=False, width=width, height=height)
    y_edges = _cluster([rect[1] + rect[3] / 2.0 for rect in horizontal_lines], max(2.0, height * 0.006))
    x_edges = _cluster([rect[0] + rect[2] / 2.0 for rect in vertical_lines], max(2.0, width * 0.006))
    if len(x_edges) < 3 or len(y_edges) < 3:
        return None
    bbox_width = max(rect[0] + rect[2] for rect in horizontal_lines) - min(rect[0] for rect in horizontal_lines)
    bbox_height = max(rect[1] + rect[3] for rect in vertical_lines) - min(rect[1] for rect in vertical_lines)
    if bbox_width < width * 0.20 or bbox_height < height * 0.12:
        return None
    line_score = min(1.0, (len(x_edges) * len(y_edges)) / 30.0)
    span_score = min(1.0, bbox_width / width) * min(1.0, bbox_height / height)
    return VisualGrid(
        tuple(x_edges),
        tuple(y_edges),
        0.45 + 0.35 * line_score + 0.20 * span_score,
        tuple(horizontal_lines),
        tuple(vertical_lines),
    )


def _build_visual_cells(
    page: NativePageEvidence,
    image: Any,
    x_edges: tuple[float, ...],
    y_edges: tuple[float, ...],
    tokens: list[Any],
) -> list[TableCell]:
    """Create cells and infer missing internal separators as spans.

    The raster detector may find an internal separator in the body but not in
    a merged header. Sampling the original image at each candidate boundary
    lets us preserve that colspan/rowspan instead of inventing one-cell
    fragments from the global edge list.
    """
    rows = len(y_edges) - 1
    cols = len(x_edges) - 1
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    for row in range(rows):
        for col in range(cols):
            if (row, col) in occupied:
                continue
            end_col = col + 1
            while end_col < cols and not _has_vertical_separator(
                image, page.bbox, x_edges[end_col], y_edges[row], y_edges[row + 1]
            ):
                end_col += 1
            end_row = row + 1
            while end_row < rows and not _has_horizontal_separator(
                image, page.bbox, y_edges[end_row], x_edges[col], x_edges[end_col]
            ):
                end_row += 1
            for covered_row in range(row, end_row):
                for covered_col in range(col, end_col):
                    occupied.add((covered_row, covered_col))
            bbox = BBox(x_edges[col], y_edges[row], x_edges[end_col], y_edges[end_row])
            selected = [
                token
                for token in tokens
                if bbox.x0 <= token.bbox.cx <= bbox.x1 and bbox.y0 <= token.bbox.cy <= bbox.y1
            ]
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=end_row - row,
                    colspan=end_col - col,
                    bbox=bbox,
                    text=_join_cell_tokens(selected),
                    tokens=selected,
                    confidence=0.55 if selected else 0.35,
                )
            )
    return cells


def _has_vertical_separator(image: Any, page_bbox: BBox, x: float, y0: float, y1: float) -> bool:
    return _separator_strength(image, page_bbox, BBox(x - 1.5, y0 + (y1 - y0) * 0.12, x + 1.5, y1 - (y1 - y0) * 0.12), vertical=True) >= 0.16


def _has_horizontal_separator(image: Any, page_bbox: BBox, y: float, x0: float, x1: float) -> bool:
    return _separator_strength(image, page_bbox, BBox(x0 + (x1 - x0) * 0.12, y - 1.5, x1 - (x1 - x0) * 0.12, y + 1.5), vertical=False) >= 0.16


def _separator_strength(image: Any, page_bbox: BBox, box: BBox, *, vertical: bool) -> float:
    """Sample the image under *box* and return the maximum dark-pixel fraction.

    Projects *box* from PDF coordinates to pixel coordinates, extracts the
    image slice, and returns the maximum column-wise (vertical separator) or
    row-wise (horizontal separator) fraction of pixels darker than 225.  A
    value of 0.0 means no dark pixels; 1.0 means the full strip is dark.
    Returns 1.0 on import or attribute errors so that missing separators are
    conservatively treated as present.
    """
    try:
        import cv2
        import numpy as np

        array = np.asarray(image)
        if array.ndim == 3:
            gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        else:
            gray = array
        width, height = _image_size(image)
        left = max(0, int((box.x0 - page_bbox.x0) * width / max(page_bbox.width, 1.0)))
        top = max(0, int((box.y0 - page_bbox.y0) * height / max(page_bbox.height, 1.0)))
        right = min(width, max(left + 1, int((box.x1 - page_bbox.x0) * width / max(page_bbox.width, 1.0))))
        bottom = min(height, max(top + 1, int((box.y1 - page_bbox.y0) * height / max(page_bbox.height, 1.0))))
        sample = gray[top:bottom, left:right]
        if sample.size == 0:
            return 0.0
        dark = sample < 225
        if vertical:
            return float(dark.mean(axis=1).max())
        return float(dark.mean(axis=0).max())
    except (ImportError, AttributeError, TypeError, ValueError):
        return 1.0


def _line_rects(mask: Any, *, horizontal: bool, width: int, height: int) -> list[tuple[int, int, int, int]]:
    import cv2

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    output: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, rect_width, rect_height = cv2.boundingRect(contour)
        if horizontal:
            if rect_width >= width * 0.18 and rect_height <= max(8, height * 0.025):
                output.append((x, y, rect_width, rect_height))
        elif rect_height >= height * 0.12 and rect_width <= max(8, width * 0.025):
            output.append((x, y, rect_width, rect_height))
    return output


def _cluster(values: list[float], tolerance: float) -> list[float]:
    values = sorted(values)
    clusters: list[list[float]] = []
    for value in values:
        if not clusters or abs(value - sum(clusters[-1]) / len(clusters[-1])) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _image_size(image: Any) -> tuple[int, int]:
    if hasattr(image, "size") and isinstance(image.size, tuple):
        return int(image.size[0]), int(image.size[1])
    return int(image.shape[1]), int(image.shape[0])
