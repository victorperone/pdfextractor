from __future__ import annotations

from structured_pdf_text.document import (
    Baseline,
    LayoutRegion,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.evidence.complexity import PageComplexity
from structured_pdf_text.evidence.decision import assess_region_recovery
from structured_pdf_text.geometry import BBox


def _region(region_id: str, bbox: BBox, text: str = "") -> LayoutRegion:
    lines = []
    if text:
        line_bbox = BBox(bbox.x0, bbox.y0, bbox.x1, min(bbox.y1, bbox.y0 + 10))
        lines.append(
            TextLine(
                tokens=[TextToken(text, line_bbox, [], 1.0, text)],
                bbox=line_bbox,
                baseline=Baseline(line_bbox.y1),
                direction=WritingDirection.LEFT_TO_RIGHT,
                native_order_min=None,
                native_order_max=None,
            )
        )
    return LayoutRegion(
        region_id=region_id,
        kind=RegionKind.TEXT,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def _native_page_complexity() -> PageComplexity:
    return PageComplexity(
        reasons=set(),
        native_text_score=1.0,
        visual_recovery_needed=False,
        layout_needed=False,
        full_page_ocr_candidate=False,
        recommended_strategy=PageStrategy.NATIVE,
    )


def test_overlapping_bad_regions_do_not_inflate_page_ocr_area_ratio():
    page_bbox = BBox(0, 0, 100, 100)
    overlapping_bad = [
        _region(f"bad-{index}", BBox(0, 0, 60, 60))
        for index in range(3)
    ]
    good = _region("good", BBox(0, 0, 80, 80), "texto nativo")

    plan = assess_region_recovery(
        [*overlapping_bad, good],
        _native_page_complexity(),
        page_image=None,
        page_bbox=page_bbox,
    )

    assert plan.bad_region_ratio == 0.75
    assert plan.bad_area_ratio == 0.5625
    assert not plan.promote_page_ocr
    assert "bad_region_count_and_area_threshold" not in plan.reasons


def test_region_union_area_is_clipped_to_page_bounds():
    page_bbox = BBox(0, 0, 100, 100)
    region = _region("outside", BBox(-50, -50, 50, 50))
    good = _region("good", page_bbox, "texto nativo")

    plan = assess_region_recovery(
        [region, good],
        _native_page_complexity(),
        page_image=None,
        page_bbox=page_bbox,
    )

    assert plan.bad_area_ratio == 0.25
    assert plan.promote_page_ocr is False


def test_native_text_without_visible_ink_is_preserved_without_ocr():
    page_bbox = BBox(0, 0, 100, 100)
    region = _region("native-only", page_bbox, "camada nativa")
    blank_image = [[255 for _ in range(10)] for _ in range(10)]

    plan = assess_region_recovery(
        [region],
        _native_page_complexity(),
        page_image=blank_image,
        page_bbox=page_bbox,
    )

    assert region.quality.decision is RegionDecision.KEEP_NATIVE
    assert region.quality.reasons == [
        "native_text_not_visible",
        "preserve_native_evidence",
    ]
    assert plan.region_ids == ()
