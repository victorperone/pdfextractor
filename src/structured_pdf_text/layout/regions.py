"""Conversion from layout predictions to domain regions.

:func:`regions_from_predictions` is the main entry point: it materialises
vendor-neutral :class:`~structured_pdf_text.layout.engine.LayoutRegionPrediction`
objects into :class:`~structured_pdf_text.document.LayoutRegion` instances,
assigns text lines to each region, collects unassigned lines into an overflow
``UNKNOWN`` region, and coalesces strongly nested duplicate predictions of the
same semantic kind.
"""
from __future__ import annotations

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine
from structured_pdf_text.evidence.decision import default_region_quality
from structured_pdf_text.evidence.complexity import PageComplexity
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.assign import assign_lines_to_regions
from structured_pdf_text.layout.engine import LayoutRegionPrediction
from structured_pdf_text.document import NativePageEvidence


_NESTED_REGION_MIN_OVERLAP = 0.80
_NESTED_REGION_MAX_AREA_RATIO = 0.65


def full_page_text_region(
    page_index: int,
    page_bbox: BBox,
    lines: list[TextLine],
    complexity: PageComplexity,
) -> LayoutRegion:
    """Return a single ``TEXT`` region that spans the entire page.

    Used as a fallback when no layout model is available or when a page
    contains only a flat stream of native text lines.
    """
    return LayoutRegion(
        region_id=f"page-{page_index + 1}:region-1",
        kind=RegionKind.TEXT,
        bbox=page_bbox,
        layout_confidence=None,
        native_lines=lines,
        ocr_tokens=[],
        quality=default_region_quality(complexity),
    )


def regions_from_predictions(
    page: NativePageEvidence,
    lines: list[TextLine],
    predictions: list[LayoutRegionPrediction],
    complexity: PageComplexity,
) -> list[LayoutRegion]:
    """Turn vendor-neutral predictions into domain regions and assign lines.

    Assignment is based on the fraction of each line bbox covered by a region,
    rather than only the line center. This keeps lines near a table or image
    boundary from silently disappearing.
    """
    regions = [
        LayoutRegion(
            region_id=f"page-{page.page_index + 1}:region-{index}",
            kind=prediction.kind,
            bbox=prediction.bbox,
            layout_confidence=prediction.confidence,
            native_lines=[],
            ocr_tokens=[],
            quality=default_region_quality(complexity),
            semantic_role=prediction.semantic_role,
            edge_role=prediction.edge_role,
        )
        for index, prediction in enumerate(predictions, start=1)
    ]
    assigned_regions = assign_lines_to_regions(lines, regions)
    assigned_line_ids = {id(line) for region in assigned_regions for line in region.native_lines}
    unassigned = [line for line in lines if id(line) not in assigned_line_ids]
    if unassigned:
        quality = default_region_quality(complexity)
        quality.reasons.append("ambiguous_line_assignment")
        assigned_regions.append(
            LayoutRegion(
                region_id=f"page-{page.page_index + 1}:region-{len(assigned_regions) + 1}",
                kind=RegionKind.UNKNOWN,
                bbox=_clamp_bbox(BBox.union_all([line.bbox for line in unassigned]).expand(3.0), page.bbox),
                layout_confidence=0.0,
                native_lines=unassigned,
                ocr_tokens=[],
                quality=quality,
            )
        )
    return _coalesce_nested_regions(assigned_regions)


def _coalesce_nested_regions(regions: list[LayoutRegion]) -> list[LayoutRegion]:
    """Collapse duplicate semantic predictions around the same text band.

    A semantic detector can emit a broad region and a smaller, overlapping
    prediction of the same kind. Keeping both regions splits one logical band
    even though line assignment deliberately sends each line to only one of
    them. Coalescing is restricted to strongly nested boxes with identical
    semantic metadata; adjacent columns and different semantic roles remain
    independent.
    """
    coalesced: list[LayoutRegion] = []
    for region in regions:
        host = next(
            (
                candidate
                for candidate in coalesced
                if _can_coalesce_nested(candidate, region)
            ),
            None,
        )
        if host is None:
            coalesced.append(region)
            continue
        host.bbox = BBox.union_all([host.bbox, region.bbox])
        host.native_lines.extend(region.native_lines)
        host.native_lines.sort(key=lambda line: (line.bbox.y0, line.bbox.x0))
        host.ocr_lines.extend(region.ocr_lines)
        host.ocr_tokens.extend(region.ocr_tokens)
        if host.layout_confidence is None:
            host.layout_confidence = region.layout_confidence
    return coalesced


def _can_coalesce_nested(left: LayoutRegion, right: LayoutRegion) -> bool:
    """Return ``True`` when *right* should be merged into *left* (or vice versa).

    Coalescing is only allowed when:

    * Both regions have the same ``kind``, ``semantic_role``, and ``edge_role``
      (different semantic roles represent distinct content, not duplicates).
    * The smaller region's area is at most *_NESTED_REGION_MAX_AREA_RATIO* of
      the larger (prevents merging adjacent columns of comparable size).
    * The smaller region overlaps the larger by at least
      *_NESTED_REGION_MIN_OVERLAP* (ensures the smaller is genuinely contained,
      not merely nearby).
    """
    if left.kind != right.kind:
        return False
    if left.semantic_role != right.semantic_role or left.edge_role != right.edge_role:
        return False
    smaller, larger = (left, right) if left.bbox.area <= right.bbox.area else (right, left)
    if smaller.bbox.area > larger.bbox.area * _NESTED_REGION_MAX_AREA_RATIO:
        return False
    return smaller.bbox.overlap_ratio(larger.bbox) >= _NESTED_REGION_MIN_OVERLAP


def _clamp_bbox(box: BBox, page_bbox: BBox) -> BBox:
    """Return *box* with all coordinates clamped to the bounds of *page_bbox*.

    Prevents ``expand()`` calls from pushing region edges outside the page
    coordinate space, which would later cause zero-area or inverted boxes.
    """
    def clamp(value: float, lower: float, upper: float) -> float:
        return min(upper, max(lower, value))

    return BBox(
        clamp(box.x0, page_bbox.x0, page_bbox.x1),
        clamp(box.y0, page_bbox.y0, page_bbox.y1),
        clamp(box.x1, page_bbox.x0, page_bbox.x1),
        clamp(box.y1, page_bbox.y0, page_bbox.y1),
    )
