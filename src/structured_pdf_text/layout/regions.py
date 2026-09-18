from __future__ import annotations

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine
from structured_pdf_text.evidence.decision import default_region_quality
from structured_pdf_text.evidence.complexity import PageComplexity
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.assign import assign_lines_to_regions
from structured_pdf_text.layout.engine import LayoutRegionPrediction
from structured_pdf_text.document import NativePageEvidence


def full_page_text_region(
    page_index: int,
    page_bbox: BBox,
    lines: list[TextLine],
    complexity: PageComplexity,
) -> LayoutRegion:
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
    return assigned_regions


def _clamp_bbox(box: BBox, page_bbox: BBox) -> BBox:
    def clamp(value: float, lower: float, upper: float) -> float:
        return min(upper, max(lower, value))

    return BBox(
        clamp(box.x0, page_bbox.x0, page_bbox.x1),
        clamp(box.y0, page_bbox.y0, page_bbox.y1),
        clamp(box.x1, page_bbox.x0, page_bbox.x1),
        clamp(box.y1, page_bbox.y0, page_bbox.y1),
    )
