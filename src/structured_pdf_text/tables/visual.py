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

from dataclasses import dataclass, replace
import math
from typing import Any

from structured_pdf_text.document import NativePageEvidence, StructuredTable, TableCell, TableFragment, TableMethod, TextToken
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.tables.text_join import join_table_tokens

# Search for a compressed/slightly curved rule in physical page units. Each
# search is additionally capped to 18% of its adjacent cell dimensions.
_SEPARATOR_TOLERANCE_PT = 4.5


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
    # Edge coordinates may live in a rectified raster. Its reversible affine
    # maps them back to the caller's original composed pixels.
    source_transform: tuple[float, ...] | None = None
    raster_size: tuple[int, int] | None = None


def detect_visual_table(
    page: NativePageEvidence,
    image: Any,
    table_id: str | None = None,
    tokens: list[TextToken] | None = None,
    structure_engine: Any | None = None,
    page_rotation: int | None = None,
    *,
    image_bbox: BBox | None = None,
) -> StructuredTable | None:
    """Recover a strong raster grid without inventing cell text.

    ``image_bbox`` describes the canonical page rectangle represented by the
    raster, including pixel rounding. Omit it for a whole-page raster.
    """
    raster_bbox = image_bbox if image_bbox is not None else page.bbox
    if (not all(math.isfinite(value) for value in (
            raster_bbox.x0, raster_bbox.y0, raster_bbox.x1, raster_bbox.y1))
            or raster_bbox.area <= 0
            or not _contains_with_tolerance(page.bbox, raster_bbox, 0)):
        raise ValueError("Visual table raster must represent a finite rectangle inside the page")
    if page_rotation is None:
        page_rotation = page.objects.rotation
    if page_rotation % 360:
        from structured_pdf_text.ocr.image_views import canonicalize_page_image

        image = canonicalize_page_image(image, page_rotation)
    if structure_engine is None:
        from .visual_engine import OpenCvTableStructureEngine

        structure_engine = OpenCvTableStructureEngine()
    from .visual_alignment import compose, point, quarter_turn_to_source

    recognition_image = image
    orientation = _grid_orientation(tokens or [], raster_bbox)
    if orientation:
        import numpy as np

        recognition_image = np.ascontiguousarray(np.rot90(np.asarray(image), orientation // 90))
    # Header shading is measured on the observed pixels: illumination recovery
    # flattens the header band together with the shadow (V4 p184, run 008).
    observed_image = image
    grid = structure_engine.recognize(recognition_image)
    recovery = getattr(structure_engine, "prepare_recovery_image", None)
    if grid is None and callable(recovery):
        recovered = recovery(recognition_image)
        if _image_size(recovered) != _image_size(recognition_image):
            raise ValueError("Table recovery must preserve the authorized raster frame")
        grid = structure_engine.recognize(recovered)
        if grid is not None:
            # Span sampling must see the same rule contrast as detection.
            if orientation:
                import numpy as np
                image = np.ascontiguousarray(np.rot90(np.asarray(recovered), -orientation // 90))
            else:
                image = recovered
    if grid is None:
        return None
    grid = _without_sliver_slots(grid)
    if len(grid.x_edges) < 2 or len(grid.y_edges) < 2:
        return None
    width, height = _image_size(image)
    if orientation:
        from .visual_alignment import IDENTITY

        grid = replace(grid, source_transform=compose(
            quarter_turn_to_source(orientation // 90, width, height),
            grid.source_transform or IDENTITY),
            raster_size=grid.raster_size or _image_size(recognition_image))
    x_scale = raster_bbox.width / max(float(width), 1.0)
    y_scale = raster_bbox.height / max(float(height), 1.0)
    def to_page(x, y):
        if grid.source_transform is not None:
            x, y = point(grid.source_transform, x, y)
        return raster_bbox.x0 + x*x_scale, raster_bbox.y0 + y*y_scale

    grid_bbox = _mapped_rectangle(grid.x_edges[0], grid.y_edges[0],
                                  grid.x_edges[-1], grid.y_edges[-1], to_page)
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
    if grid.source_transform is None:
        cells = _build_visual_cells(
            page=page, image=image,
            x_edges=tuple(raster_bbox.x0 + v*x_scale for v in grid.x_edges),
            y_edges=tuple(raster_bbox.y0 + v*y_scale for v in grid.y_edges),
            tokens=cell_tokens, raster_bbox=raster_bbox,
            horizontal_segments=_ruling_boxes(grid.horizontal_lines, raster_bbox, width, height),
            vertical_segments=_ruling_boxes(grid.vertical_lines, raster_bbox, width, height))
    else:
        from .visual_alignment import inverse, warp

        assert grid.raster_size is not None
        rectified = warp(image, grid.source_transform, grid.raster_size)
        cells = _build_visual_cells(
            page=page, image=rectified, x_edges=grid.x_edges, y_edges=grid.y_edges,
            tokens=[], raster_bbox=BBox(0, 0, *grid.raster_size),
            separator_tolerance=_SEPARATOR_TOLERANCE_PT/min(x_scale, y_scale),
            horizontal_segments=_ruling_boxes(grid.horizontal_lines),
            vertical_segments=_ruling_boxes(grid.vertical_lines))
        to_grid = inverse(grid.source_transform)
        for token in cell_tokens:
            x, y = point(to_grid, (token.bbox.cx-raster_bbox.x0)/x_scale,
                         (token.bbox.cy-raster_bbox.y0)/y_scale)
            cell = _cell_at(cells, x, y)
            if cell is not None:
                cell.tokens.append(token)
        for cell in cells:
            cell.polygon = tuple(Point(*to_page(x, y)) for x, y in (
                (cell.bbox.x0, cell.bbox.y0), (cell.bbox.x1, cell.bbox.y0),
                (cell.bbox.x1, cell.bbox.y1), (cell.bbox.x0, cell.bbox.y1)))
            cell.bbox = _mapped_rectangle(cell.bbox.x0, cell.bbox.y0,
                                           cell.bbox.x1, cell.bbox.y1, to_page)
            cell.text = _join_cell_tokens(cell.tokens)
            cell.confidence = .55 if cell.tokens else .35
    if cell_tokens and not _has_visual_table_text_support(cells, grid):
        return None
    from .headers import infer_visual_header_rows

    header_inference = getattr(structure_engine, "infer_header_rows", infer_visual_header_rows)
    header_rows = header_inference(cells, observed_image, raster_bbox)
    if not header_rows and image is not observed_image:
        header_rows = header_inference(cells, image, raster_bbox)
    recover_headers = getattr(structure_engine, "recover_header_rows", None)
    if not header_rows and callable(recover_headers):
        header_rows = recover_headers(cells)
    return StructuredTable(
        table_id=table_id or f"page-{page.page_index + 1}:visual-grid",
        page_fragments=[
            TableFragment(
                page_index=page.page_index,
                bbox=grid_bbox,
                row_start=0,
                row_end=len(grid.y_edges) - 2,
            )
        ],
        cells=cells,
        column_count=len(grid.x_edges) - 1,
        row_count=len(grid.y_edges) - 1,
        confidence=grid.confidence,
        method=TableMethod.VISUAL_MODEL,
        header_rows=header_rows,
    )


def _without_sliver_slots(grid: VisualGrid) -> VisualGrid:
    """Collapse separators closer than a third of the typical pitch.

    Double rules, a shaded band edge and its rule, or a JPEG ringing line
    yield two separators one or two pixels apart; the slot between them is
    not a row or column of the table. Legitimately empty rows keep their
    normal height and are unaffected.
    """
    def collapse(edges: tuple[float, ...]) -> tuple[float, ...]:
        if len(edges) < 4:
            return edges
        gaps = sorted(b - a for a, b in zip(edges, edges[1:]))
        typical = gaps[len(gaps) // 2]
        minimum = max(2.0, typical * 0.35)
        groups: list[list[float]] = [[edges[0]]]
        for value in edges[1:]:
            if value - groups[-1][-1] < minimum:
                groups[-1].append(value)
            else:
                groups.append([value])
        return tuple(sum(group) / len(group) for group in groups)

    x_edges, y_edges = collapse(tuple(grid.x_edges)), collapse(tuple(grid.y_edges))
    if x_edges == tuple(grid.x_edges) and y_edges == tuple(grid.y_edges):
        return grid
    return replace(grid, x_edges=x_edges, y_edges=y_edges)


def _mapped_rectangle(x0, y0, x1, y1, transform):
    points = [transform(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return BBox(min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points))


def _ruling_boxes(lines, raster_bbox=None, width=1, height=1):
    """Positive ruling segments in the same frame as the cells being built."""
    raster_bbox = raster_bbox if raster_bbox is not None else BBox(0, 0, 1, 1)
    sx, sy = raster_bbox.width/width, raster_bbox.height/height
    return tuple(BBox(raster_bbox.x0+x*sx, raster_bbox.y0+y*sy,
                      raster_bbox.x0+(x+dx)*sx, raster_bbox.y0+(y+dy)*sy)
                 for x, y, dx, dy in lines)


def _grid_orientation(tokens, raster_bbox):
    """Use an already recognized reading orientation, never a new OCR pass."""
    weights = {}
    for token in tokens:
        if not (raster_bbox.x0 <= token.bbox.cx <= raster_bbox.x1
                and raster_bbox.y0 <= token.bbox.cy <= raster_bbox.y1):
            continue
        rotation = getattr(token, "rotation", 0) % 360
        if rotation in (0, 90, 180, 270):
            weights[rotation] = weights.get(rotation, 0) + len(token.text.strip())
    if not weights:
        return 0
    rotation = max(weights, key=weights.get)
    return rotation if weights[rotation] >= .8*sum(weights.values()) else 0


def _cell_at(cells, x, y):
    # Half-open intervals prevent assigning an observation on a shared edge
    # to two cells. Only the outermost right/bottom edges are inclusive.
    right = max((c.bbox.x1 for c in cells), default=0)
    bottom = max((c.bbox.y1 for c in cells), default=0)
    return next((c for c in cells
                 if c.bbox.x0 <= x and (x < c.bbox.x1 or x == c.bbox.x1 == right)
                 and c.bbox.y0 <= y and (y < c.bbox.y1 or y == c.bbox.y1 == bottom)), None)


def detect_visual_table_in_region(
    page: NativePageEvidence,
    image: Any,
    region: BBox,
    **kwargs: Any,
) -> StructuredTable | None:
    """Discover structure in a region of an existing composed page raster.

    This does not authorize OCR or access embedded source pixels. Presentation
    rotation is undone before cropping, and the pixel-aligned crop frame is
    retained for both grid mapping and separator sampling.
    """
    from structured_pdf_text.ocr.image_views import canonicalize_page_image

    if (not all(math.isfinite(value) for value in (region.x0, region.y0, region.x1, region.y1))
            or region.area <= 0 or not _contains_with_tolerance(page.bbox, region, 0)):
        raise ValueError("Visual table region must be finite and inside the page")
    canonical = canonicalize_page_image(image, page.objects.rotation)
    crop = None
    try:
        width, height = _image_size(canonical)
        if width <= 0 or height <= 0:
            return None
        page_box = page.bbox
        left = max(0, math.floor((region.x0 - page_box.x0) * width / page_box.width))
        top = max(0, math.floor((region.y0 - page_box.y0) * height / page_box.height))
        right = min(width, math.ceil((region.x1 - page_box.x0) * width / page_box.width))
        bottom = min(height, math.ceil((region.y1 - page_box.y0) * height / page_box.height))
        if left >= right or top >= bottom:
            return None
        crop = (canonical.crop((left, top, right, bottom)) if hasattr(canonical, "crop")
                else canonical[top:bottom, left:right].copy())
        frame = BBox(page_box.x0 + left * page_box.width / width,
                     page_box.y0 + top * page_box.height / height,
                     page_box.x0 + right * page_box.width / width,
                     page_box.y0 + bottom * page_box.height / height)
        return detect_visual_table(page, crop, page_rotation=0, image_bbox=frame, **kwargs)
    finally:
        if crop is not None and hasattr(crop, "close"):
            crop.close()
        if canonical is not image and hasattr(canonical, "close"):
            canonical.close()


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
    from .visual_alignment import align_ruling_lines

    plain = _axis_aligned_grid(image)
    local = _local_grid(image)
    if local is not None and (plain is None
            or len(local.x_edges)*len(local.y_edges) > len(plain.x_edges)*len(plain.y_edges)
            or (local.source_transform is not None and
                len(local.x_edges)*len(local.y_edges) == len(plain.x_edges)*len(plain.y_edges))):
        plain = local
    aligned = align_ruling_lines(image)
    if aligned is not None:
        raster, to_source = aligned
        candidate = _axis_aligned_grid(raster)
        if candidate is not None and (plain is None or
                len(candidate.x_edges)*len(candidate.y_edges) >= len(plain.x_edges)*len(plain.y_edges)):
            return replace(candidate, source_transform=to_source, raster_size=_image_size(raster))
    return plain


def _local_grid(image: Any) -> VisualGrid | None:
    """Find a connected ruling network too small for whole-image kernels.

    Light gray rules are common in compact financial tables. Only connected
    horizontal/vertical networks are reconsidered; the regular grid and text
    support checks still apply inside their local raster frame.
    """
    import cv2
    import numpy as np

    array = np.asarray(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY) if array.ndim == 3 else array
    height, width = gray.shape[:2]
    if min(width, height) < 80:
        return None
    binary = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(16, width//120), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(16, height//120))))
    network = cv2.bitwise_or(horizontal, vertical)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(network)
    for label in sorted(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True):
        x, y, w, h, area = map(int, stats[label])
        if min(w, h) < 24 or area < 2*(w+h):
            continue
        left, top = max(0, x-3), max(0, y-3)
        right, bottom = min(width, x+w+3), min(height, y+h+3)
        # This frame contains a connected line network. Thresholding its
        # light rules must not alter the pixels used later for span sampling.
        local = np.where(gray[top:bottom, left:right] < 235, 0, 255).astype(np.uint8)
        # A page frame can dominate the whole-image slope estimate. Measure
        # the already discovered local network independently before deciding
        # whether its missing separators are real merged cells.
        from .visual_alignment import align_ruling_lines, compose

        aligned = align_ruling_lines(gray[top:bottom, left:right])
        if aligned is not None:
            raster, to_source = aligned
            improved = _axis_aligned_grid(raster)
            if improved is not None:
                return replace(improved, source_transform=compose(
                    (1., 0., float(left), 0., 1., float(top)), to_source),
                    raster_size=_image_size(raster))
        grid = _axis_aligned_grid(local)
        if grid is not None:
            return replace(grid,
                x_edges=tuple(x+left for x in grid.x_edges),
                y_edges=tuple(y+top for y in grid.y_edges),
                horizontal_lines=tuple((x+left, y+top, w, h) for x, y, w, h in grid.horizontal_lines),
                vertical_lines=tuple((x+left, y+top, w, h) for x, y, w, h in grid.vertical_lines))
    return None


def _axis_aligned_grid(image: Any) -> VisualGrid | None:
    """Keep the richer coherent network across dark and light ruling lines."""
    import numpy as np

    candidate = _axis_aligned_grid_at_threshold(image, 215)
    array = np.asarray(image)
    if not np.any((array > 215) & (array <= 235)):
        return candidate
    light = _axis_aligned_grid_at_threshold(image, 235)
    if light is not None and (candidate is None or
            len(light.x_edges)*len(light.y_edges) > len(candidate.x_edges)*len(candidate.y_edges)):
        return light
    return candidate


def _axis_aligned_grid_at_threshold(image: Any, threshold: int) -> VisualGrid | None:
    import cv2
    import numpy as np

    array = np.asarray(image)
    if array.ndim == 3:
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    else:
        gray = array
    height, width = gray.shape[:2]
    if width < 80 or height < 80:
        return None
    binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, width // 24), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, height // 24)))
    # Reconnect at most two missing pixels before extracting long rules.
    # Missing separators of merged cells are much larger and remain absent.
    horizontal_closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    vertical_closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    horizontal = cv2.morphologyEx(horizontal_closed, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(vertical_closed, cv2.MORPH_OPEN, vertical_kernel)
    horizontal_lines = _line_rects(horizontal, horizontal=True, width=width, height=height)
    vertical_lines = _line_rects(vertical, horizontal=False, width=width, height=height)
    horizontal_lines, vertical_lines = _connected_grid_lines(horizontal_lines, vertical_lines, width, height)
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


def _connected_grid_lines(horizontal, vertical, width, height):
    """Separate a table's ruling network from the surrounding form frame.

    At least three vertical separators must terminate at a common baseline,
    with two horizontal borders spanning them and a third row boundary.
    Partially missing header separators remain eligible inside that network.
    A pair of page borders cannot establish a table on its own.
    """
    tolerance = max(3., height*.006)
    groups = []
    for rect in sorted(vertical, key=lambda r: r[1]+r[3]):
        bottom = rect[1]+rect[3]
        group = next((g for g in groups if abs(bottom-(g[0][1]+g[0][3])) <= tolerance), None)
        if group is None:
            groups.append([rect])
        else:
            group.append(rect)
    candidates = []
    for group in groups:
        xs = _cluster([x+w/2 for x, y, w, h in group], max(2., width*.006))
        if len(xs) < 3:
            continue
        top = min(y for x, y, w, h in group)
        bottom = max(y+h for x, y, w, h in group)
        crossing = [(x, y, w, h) for x, y, w, h in horizontal
                    if top-tolerance <= y+h/2 <= bottom+tolerance
                    and x <= xs[0]+tolerance and x+w >= xs[-1]-tolerance]
        if len(_cluster([y+h/2 for x, y, w, h in crossing], tolerance)) < 2:
            continue
        from statistics import median

        left = median(x for x, y, w, h in crossing)
        right = median(x+w for x, y, w, h in crossing)
        hs = [(x, y, w, h) for x, y, w, h in horizontal
              if top-tolerance <= y+h/2 <= bottom+tolerance
              and min(x+w, right)-max(x, left) >= (right-left)*.30]
        if len(_cluster([y+h/2 for x, y, w, h in hs], tolerance)) < 3:
            continue
        vs = [(x, y, w, h) for x, y, w, h in vertical
              if left-tolerance <= x+w/2 <= right+tolerance
              and min(y+h, bottom)-max(y, top) >= (bottom-top)*.30]
        candidates.append((len(xs)*len(hs), hs, vs))
    if candidates:
        _, horizontal, vertical = max(candidates, key=lambda c: c[0])
    else:
        return [], []
    return horizontal, vertical


def _build_visual_cells(
    page: NativePageEvidence,
    image: Any,
    x_edges: tuple[float, ...],
    y_edges: tuple[float, ...],
    tokens: list[Any],
    raster_bbox: BBox | None = None,
    separator_tolerance: float = _SEPARATOR_TOLERANCE_PT,
    horizontal_segments: tuple[BBox, ...] = (),
    vertical_segments: tuple[BBox, ...] = (),
) -> list[TableCell]:
    """Create cells and infer missing internal separators as spans.

    The raster detector may find an internal separator in the body but not in
    a merged header. Sampling the original image at each candidate boundary
    lets us preserve that colspan/rowspan instead of inventing one-cell
    fragments from the global edge list.
    """
    rows = len(y_edges) - 1
    cols = len(x_edges) - 1
    # Convert once rather than once for every sampled separator.
    try:
        import cv2
        import numpy as np

        array = np.asarray(image)
        if array.ndim == 3:
            image = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    except ImportError:
        pass
    raster_bbox = raster_bbox if raster_bbox is not None else page.bbox
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []

    def x_tolerance(index):
        return min(separator_tolerance, .18*min(
            x_edges[index]-x_edges[index-1], x_edges[index+1]-x_edges[index]))

    def y_tolerance(index):
        return min(separator_tolerance, .18*min(
            y_edges[index]-y_edges[index-1], y_edges[index+1]-y_edges[index]))
    for row in range(rows):
        for col in range(cols):
            if (row, col) in occupied:
                continue
            end_col = col + 1
            while end_col < cols and (row, end_col) not in occupied and not _has_vertical_separator(
                image, raster_bbox, x_edges[end_col], y_edges[row], y_edges[row + 1],
                tolerance=x_tolerance(end_col), segments=vertical_segments,
            ):
                end_col += 1
            end_row = row + 1
            while (end_row < rows
                   and not any((end_row, c) in occupied for c in range(col, end_col))
                   and not any(_has_vertical_separator(image, raster_bbox, x_edges[c],
                                                       y_edges[end_row], y_edges[end_row+1],
                                                       tolerance=x_tolerance(c), segments=vertical_segments)
                               for c in range(col+1, end_col))
                   and not _has_horizontal_separator(
                image, raster_bbox, y_edges[end_row], x_edges[col], x_edges[end_col],
                tolerance=y_tolerance(end_row), segments=horizontal_segments,
            )):
                end_row += 1
            for covered_row in range(row, end_row):
                for covered_col in range(col, end_col):
                    occupied.add((covered_row, covered_col))
            bbox = BBox(x_edges[col], y_edges[row], x_edges[end_col], y_edges[end_row])
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=end_row - row,
                    colspan=end_col - col,
                    bbox=bbox,
                    text="",
                    tokens=[],
                    confidence=0.35,
                )
            )
    for token in tokens:
        cell = _cell_at(cells, token.bbox.cx, token.bbox.cy)
        if cell is not None:
            cell.tokens.append(token)
    for cell in cells:
        cell.text = _join_cell_tokens(cell.tokens)
        cell.confidence = .55 if cell.tokens else .35
    return cells


def _ruling_support(segments, coordinate, start, end, *, vertical, tolerance):
    if end <= start:
        return False
    for segment in segments:
        center = segment.cx if vertical else segment.cy
        low, high = (segment.y0, segment.y1) if vertical else (segment.x0, segment.x1)
        if (abs(center-coordinate) <= tolerance
                and max(0., min(end, high)-max(start, low)) >= .8*(end-start)):
            return True
    return False


def _has_vertical_separator(image: Any, page_bbox: BBox, x: float, y0: float, y1: float, *, tolerance: float = 1.5, segments=()) -> bool:
    # A detected long segment is positive evidence. A low local SNR cannot
    # erase it and turn several observed body cells into a fictitious span.
    # Segments starting in the body cannot claim a missing header divider.
    if _ruling_support(segments, x, y0, y1, vertical=True, tolerance=tolerance):
        return True
    return _separator_strength(image, page_bbox, BBox(x - tolerance, y0 + (y1 - y0) * 0.05, x + tolerance, y1 - (y1 - y0) * 0.05), vertical=True) >= 0.60


def _has_horizontal_separator(image: Any, page_bbox: BBox, y: float, x0: float, x1: float, *, tolerance: float = 1.5, segments=()) -> bool:
    if _ruling_support(segments, y, x0, x1, vertical=False, tolerance=tolerance):
        return True
    return _separator_strength(image, page_bbox, BBox(x0 + (x1 - x0) * 0.05, y - tolerance, x1 - (x1 - x0) * 0.05, y + tolerance), vertical=False) >= 0.60


def _separator_strength(image: Any, page_bbox: BBox, box: BBox, *, vertical: bool) -> float:
    """Sample the image under *box* and return the maximum dark-pixel fraction.

    Projects *box* from PDF coordinates to pixel coordinates, extracts the
    image slice, and returns the maximum column-wise (vertical separator) or
    row-wise (horizontal separator) fraction below the local background. A
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
        # Compare each sample with the background on the same cross-section.
        # A single percentile over a noisy/shaded header counts its grain as
        # a full separator. Robust local noise sets the required contrast;
        # both flanks keep a thick rule out of its own background estimate.
        if vertical:
            padding = max(3, (right-left)*2)
            flanks = (gray[top:bottom, max(0, left-padding):left],
                      gray[top:bottom, right:min(width, right+padding)])
            axis = 1
        else:
            padding = max(3, (bottom-top)*2)
            flanks = (gray[max(0, top-padding):top, left:right],
                      gray[bottom:min(height, bottom+padding), left:right])
            axis = 0
        backgrounds = [flank for flank in flanks if flank.size]
        if not backgrounds:
            return 1.0
        levels = [np.percentile(background, 65, axis=axis, keepdims=True) for background in backgrounds]
        deviations = [np.median(np.abs(background.astype(float)-level), axis=axis, keepdims=True)
                      for background, level in zip(backgrounds, levels)]
        # A shaded header and a white body are two backgrounds, not noise.
        # Pooling them would raise the noise floor enough to erase a real
        # header/body rule and falsely merge the first data row into the header.
        level = np.minimum.reduce(levels)
        deviation = np.maximum.reduce(deviations)
        threshold = level - np.maximum(4., 3.0 * 1.4826 * deviation)
        dark = sample < threshold
        if vertical:
            # A vertical line must persist down the strip; one horizontal
            # crossing is not sufficient evidence of a column boundary.
            return float(dark.mean(axis=0).max())
        # A horizontal line must persist across the strip.
        return float(dark.mean(axis=1).max())
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
