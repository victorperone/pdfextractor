from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.document import (
    ComplexityReason,
    LayoutRegion,
    RegionDecision,
    RegionKind,
    RegionQuality,
    TokenFlag,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.evidence.complexity import PageComplexity


@dataclass(frozen=True, slots=True)
class RegionRecoveryPlan:
    region_ids: tuple[str, ...]
    promote_page_ocr: bool
    bad_region_ratio: float
    bad_area_ratio: float
    reasons: tuple[str, ...]


def default_region_quality(page_complexity: PageComplexity) -> RegionQuality:
    if page_complexity.full_page_ocr_candidate:
        return RegionQuality(
            decision=RegionDecision.ESCALATE_PAGE_OCR,
            reasons=[reason.value for reason in sorted(page_complexity.reasons, key=lambda item: item.value)],
            confidence=page_complexity.native_text_score,
        )
    if page_complexity.visual_recovery_needed:
        return RegionQuality(
            decision=RegionDecision.MERGE_OCR,
            reasons=[reason.value for reason in sorted(page_complexity.reasons, key=lambda item: item.value)],
            confidence=page_complexity.native_text_score,
        )
    return RegionQuality(
        decision=RegionDecision.KEEP_NATIVE,
        reasons=[reason.value for reason in sorted(page_complexity.reasons, key=lambda item: item.value)],
        confidence=page_complexity.native_text_score,
    )


def assess_region_recovery(
    regions: list[LayoutRegion],
    page_complexity: PageComplexity,
    page_image: Any | None,
    page_bbox: BBox,
) -> RegionRecoveryPlan:
    """Assign local recovery decisions and decide whether crops should promote.

    The page complexity signal remains evidence, but does not automatically
    force every region through OCR. Local text, visible ink, semantic kind and
    occupied area determine whether native text is kept, merged or recovered.
    """
    selected: list[LayoutRegion] = []
    considered = [region for region in regions if region.bbox.area > 0]
    for region in considered:
        region.quality = _assess_one_region(
            region,
            page_complexity,
            page_image,
            page_bbox,
        )
        if region.quality.decision in {
            RegionDecision.MERGE_OCR,
            RegionDecision.OCR_REGION,
            RegionDecision.ESCALATE_PAGE_OCR,
        }:
            selected.append(region)

    total_area = sum(region.bbox.area for region in considered)
    bad_area = sum(region.bbox.area for region in selected)
    bad_region_ratio = len(selected) / max(len(considered), 1)
    bad_area_ratio = min(1.0, bad_area / max(total_area, 1.0))
    explicit_escalation = any(
        region.quality.decision == RegionDecision.ESCALATE_PAGE_OCR
        for region in selected
    )
    ratio_escalation = bad_region_ratio >= 0.60 and bad_area_ratio >= 0.60
    promote = page_complexity.full_page_ocr_candidate or explicit_escalation or ratio_escalation
    reasons: list[str] = []
    if page_complexity.full_page_ocr_candidate:
        reasons.append("page_complexity_full_ocr")
    if explicit_escalation:
        reasons.append("region_requested_page_escalation")
    if ratio_escalation:
        reasons.append("bad_region_count_and_area_threshold")
    return RegionRecoveryPlan(
        region_ids=tuple(region.region_id for region in selected),
        promote_page_ocr=promote,
        bad_region_ratio=round(bad_region_ratio, 6),
        bad_area_ratio=round(bad_area_ratio, 6),
        reasons=tuple(reasons),
    )


def _assess_one_region(
    region: LayoutRegion,
    page_complexity: PageComplexity,
    page_image: Any | None,
    page_bbox: BBox,
) -> RegionQuality:
    text = "".join(line.text for line in region.native_lines).strip()
    intersections = [
        intersection
        for line in region.native_lines
        if (intersection := line.bbox.intersection(region.bbox)) is not None
    ]
    text_area = min(region.bbox.area, sum(box.area for box in intersections))
    text_coverage = text_area / max(region.bbox.area, 1.0)
    ink_ratio = _region_ink_ratio(page_image, page_bbox, region.bbox)
    visible = ink_ratio is None or ink_ratio >= 0.008
    reasons: list[str] = []

    large_visual_region = (
        region.kind in {RegionKind.FIGURE, RegionKind.TABLE}
        and region.bbox.area / max(page_bbox.area, 1.0) >= 0.60
        and len(text) < 40
        and visible
    )
    if large_visual_region:
        reasons.extend(("large_visual_region", "native_text_missing"))
        return RegionQuality(RegionDecision.ESCALATE_PAGE_OCR, reasons, 0.90)

    if region.kind == RegionKind.FIGURE and visible:
        reasons.append("visual_figure_may_contain_text")
        if not text:
            reasons.append("native_text_missing")
            return RegionQuality(RegionDecision.OCR_REGION, reasons, 0.82)
        return RegionQuality(RegionDecision.MERGE_OCR, reasons, 0.72)

    if region.kind == RegionKind.TABLE and visible and (not text or text_coverage < 0.008):
        reasons.extend(("visual_table_text_sparse", "native_text_missing"))
        return RegionQuality(RegionDecision.OCR_REGION, reasons, 0.84)

    if not text and visible and region.kind not in {RegionKind.HEADER, RegionKind.FOOTER}:
        reasons.extend(("native_text_missing", "visible_ink_present"))
        return RegionQuality(RegionDecision.OCR_REGION, reasons, 0.78)

    damaged_layer = bool(
        {
            ComplexityReason.GARBLED_UNICODE,
            ComplexityReason.DUPLICATE_TEXT_LAYER,
        }
        & page_complexity.reasons
    )
    unicode_mapping_failed = any(
        TokenFlag.UNICODE_MAPPING_FAILED in token.flags
        for line in region.native_lines
        for token in line.tokens
    )
    if unicode_mapping_failed:
        reasons.append("unicode_mapping_failed")
        return RegionQuality(RegionDecision.MERGE_OCR, reasons, 0.64)
    if text and damaged_layer:
        reasons.append("page_text_layer_damaged")
        return RegionQuality(RegionDecision.MERGE_OCR, reasons, 0.68)

    reasons.append("native_region_has_text" if text else "no_visible_recovery_signal")
    confidence = min(1.0, max(0.0, page_complexity.native_text_score + (0.10 if text else 0.0)))
    return RegionQuality(RegionDecision.KEEP_NATIVE, reasons, confidence)


def _region_ink_ratio(image: Any | None, page_bbox: BBox, region_bbox: BBox) -> float | None:
    if image is None:
        return None
    try:
        import numpy as np

        array = np.asarray(image)
        if array.ndim == 3:
            gray = array[..., :3].mean(axis=2)
        else:
            gray = array
        height, width = gray.shape[:2]
        left = max(0, int((region_bbox.x0 - page_bbox.x0) * width / max(page_bbox.width, 1.0)))
        top = max(0, int((region_bbox.y0 - page_bbox.y0) * height / max(page_bbox.height, 1.0)))
        right = min(width, max(left + 1, int((region_bbox.x1 - page_bbox.x0) * width / max(page_bbox.width, 1.0))))
        bottom = min(height, max(top + 1, int((region_bbox.y1 - page_bbox.y0) * height / max(page_bbox.height, 1.0))))
        sample = gray[top:bottom, left:right]
        return float((sample < 245).mean()) if sample.size else 0.0
    except (AttributeError, ImportError, TypeError, ValueError):
        return None
