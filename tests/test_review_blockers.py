from __future__ import annotations

import pytest
from PIL import Image

from structured_pdf_text.api import _recover_weak_ocr_regions
from structured_pdf_text.config import OcrQualityThresholds
from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    LayoutRegion,
    OcrToken,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.layout.engine import _decorative_reasons, _is_decorative
from structured_pdf_text.layout.heading import assign_heading_levels
from structured_pdf_text.ocr.image_quality import profile_image
from structured_pdf_text.ocr.paddle import (
    OcrImageVariant,
    PaddleOcrEngine,
    _half_turn_box_to_original,
    _merge_compact_candidate_tokens,
    _make_candidate,
    _normalize_deskew_angle,
    _overlap_conflict_count,
    _select_best_candidate,
    _spatial_consensus,
)
from structured_pdf_text.text.lists import extract_list_items
from structured_pdf_text.text.reading_order import _split_columns, order_regions


def _line(text: str, x: float, y: float, *, confidence: float = 1.0, width: float | None = None) -> TextLine:
    bbox = BBox(x, y, x + (width if width is not None else max(24.0, len(text) * 7.0)), y + 10.0)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"line:{x}:{y}")],
        confidence=confidence,
        normalized_text=text,
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=bbox.y1),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


def _region(region_id: str, bbox: BBox, lines: list[TextLine] | None = None) -> LayoutRegion:
    return LayoutRegion(
        region_id=region_id,
        kind=RegionKind.TEXT,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=lines or [],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def _ocr_token(text: str, confidence: float, x: float = 0.0, y: float = 0.0) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(x, y, x + max(8.0, len(text) * 8.0), y + 10.0),
        confidence=confidence,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )


def test_deskew_normalizes_min_area_rect_angles_to_nearest_axis() -> None:
    assert _normalize_deskew_angle(88.8) == pytest.approx(-1.2)
    assert _normalize_deskew_angle(-88.8) == pytest.approx(1.2)
    assert _normalize_deskew_angle(2.2) == pytest.approx(2.2)


def test_sidebar_main_flow_precedes_sidebar_even_when_input_is_reversed() -> None:
    main = _region("main", BBox(0, 0, 78, 100))
    sidebar = _region("sidebar", BBox(82, 10, 100, 90))
    ordered, _ = order_regions([sidebar, main])
    assert [region.region_id for region in ordered] == ["main", "sidebar"]


def test_vertical_stacking_dominates_sidebar_width_heuristic() -> None:
    upper = _region("upper", BBox(0, 0, 100, 35))
    lower = _region("lower", BBox(5, 60, 55, 100))
    ordered, _ = order_regions([lower, upper])
    assert [region.region_id for region in ordered] == ["upper", "lower"]


def test_split_columns_reassigns_singleton_without_losing_line() -> None:
    lines = [
        _line("left one", 10, 0, width=35),
        _line("left two", 10, 20, width=35),
        _line("spanning", 45, 10, width=20),
        _line("right one", 70, 0, width=25),
        _line("right two", 70, 20, width=25),
    ]
    columns = _split_columns(lines, 100.0)
    assert sorted(id(line) for column in columns for line in column) == sorted(id(line) for line in lines)
    assert len([line for column in columns for line in column]) == len(lines)


def test_decorative_classifier_does_not_use_narrow_glyph_as_rotation() -> None:
    line = _line("I", 40, 40, width=4)
    line.tokens[0].font_size = 36.0
    page = BBox(0, 0, 100, 100)
    assert not _is_decorative(line, page, 10.0)
    assert "rotated" not in _decorative_reasons(line, page, 10.0)


def test_decorative_classifier_uses_luminance_for_opaque_watermark() -> None:
    line = _line("WATERMARK", 20, 40, width=65)
    line.tokens[0].font_size = 30.0
    line.tokens[0].fill_color = (235, 235, 235, 255)
    page = BBox(0, 0, 100, 100)
    assert _is_decorative(line, page, 10.0)
    assert "light_luminance" in _decorative_reasons(line, page, 10.0)


def test_large_normal_title_is_not_decorative() -> None:
    line = _line("Título legítimo", 20, 20, width=65)
    line.tokens[0].font_size = 32.0
    line.tokens[0].fill_color = (0, 0, 0, 255)
    assert not _is_decorative(line, BBox(0, 0, 100, 100), 10.0)


def test_title_case_style_is_detected_and_two_fragments_merge() -> None:
    first_line = _line("Título", 20, 30, width=45)
    second_line = _line("em duas linhas", 20, 42, width=75)
    for line in (first_line, second_line):
        line.tokens[0].font_size = 18.0
        line.tokens[0].font_weight = 700
    first = _region("title-1", first_line.bbox)
    first.kind = RegionKind.TITLE
    first.native_lines = [first_line]
    second = _region("title-2", second_line.bbox)
    second.kind = RegionKind.TITLE
    second.native_lines = [second_line]
    page = StructuredPage(
        page_index=0,
        bbox=BBox(0, 0, 100, 100),
        regions=[first, second],
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=None,  # type: ignore[arg-type]
    )
    result = assign_heading_levels([page])[0]
    assert len(result.regions) == 1
    assert result.regions[0].heading_level == 1


def test_list_continuation_does_not_absorb_following_paragraph() -> None:
    lines = [
        _line("• item", 10, 0),
        _line("continuação", 17, 12),
        _line("• segundo", 10, 35),
        _line("parágrafo normal", 10, 65),
    ]
    items = extract_list_items(lines)
    assert len(items) == 2
    assert "continuação" in items[0].text
    assert "parágrafo" not in items[1].text


def test_list_indent_jitter_keeps_same_level() -> None:
    items = extract_list_items([
        _line("• um", 10, 0),
        _line("• dois", 12, 15),
        _line("◦ filho", 30, 30),
    ])
    assert [item.level for item in items] == [0, 0, 1]


def test_image_profile_failure_is_explicit_and_conservative() -> None:
    profile = profile_image(object())
    assert profile.available is False
    assert not profile.low_contrast
    assert not profile.likely_blurred_or_small
    assert not profile.likely_noisy


def test_candidate_selection_uses_the_same_quality_score_as_diagnostics() -> None:
    page = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    first = _make_candidate("first", [_ocr_token("texto longo", 0.92)], None, page, thresholds)
    second = _make_candidate("second", [_ocr_token("texto longo", 0.86)], None, page, thresholds)
    selected = _select_best_candidate([first, second], page, thresholds)
    assert selected.name == max((first, second), key=lambda candidate: candidate.quality.score).name
    assert selected.quality.score == max(first.quality.score, second.quality.score)


def test_public_quality_threshold_changes_observable_sufficiency() -> None:
    token = _ocr_token("texto", 0.82)
    relaxed = OcrQualityThresholds(strong_mean_confidence=0.80)
    strict = OcrQualityThresholds(strong_mean_confidence=0.90)
    relaxed_candidate = _make_candidate("relaxed", [token], None, BBox(0, 0, 200, 100), relaxed)
    strict_candidate = _make_candidate("strict", [token], None, BBox(0, 0, 200, 100), strict)
    assert relaxed_candidate.quality.sufficient is True
    assert strict_candidate.quality.sufficient is False


def test_spatial_consensus_requires_two_agreeing_alternates() -> None:
    page = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    primary = _make_candidate("primary", [_ocr_token("errado", 0.50)], None, page, thresholds)
    alternate_a = _make_candidate("a", [_ocr_token("correto", 0.72)], None, page, thresholds)
    alternate_b = _make_candidate("b", [_ocr_token("correto", 0.74)], None, page, thresholds)
    merged, replacements, _ = _spatial_consensus(primary, [primary, alternate_a, alternate_b], thresholds=thresholds)
    assert replacements == 1
    assert [token.text for token in merged] == ["correto"]


def test_single_alternate_small_delta_does_not_replace_primary() -> None:
    page = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    primary = _make_candidate("primary", [_ocr_token("primary", 0.60)], None, page, thresholds)
    alternate = _make_candidate("alternate", [_ocr_token("alternate", 0.68)], None, page, thresholds)
    merged, replacements, _ = _spatial_consensus(primary, [primary, alternate], thresholds=thresholds)
    assert replacements == 0
    assert merged[0].text == "primary"


def test_single_alternate_large_delta_can_replace_a_very_weak_primary() -> None:
    page = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    primary = _make_candidate("primary", [_ocr_token("primary", 0.40)], None, page, thresholds)
    alternate = _make_candidate("alternate", [_ocr_token("alternate", 0.60)], None, page, thresholds)
    merged, replacements, _ = _spatial_consensus(primary, [primary, alternate], thresholds=thresholds)
    assert replacements == 1
    assert merged[0].text == "alternate"


def test_spatial_consensus_rolls_back_large_area_loss() -> None:
    page = BBox(0, 0, 400, 100)
    thresholds = OcrQualityThresholds()

    primary_token = OcrToken(
        text="relatorio completo 2026",
        bbox=BBox(10, 10, 210, 20),
        confidence=0.40,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )
    alternate_token_a = OcrToken(
        text="relatorio 2026",
        bbox=BBox(10, 10, 90, 20),
        confidence=0.92,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )
    alternate_token_b = OcrToken(
        text="relatorio 2026",
        bbox=BBox(10, 10, 90, 20),
        confidence=0.94,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )

    primary = _make_candidate(
        "primary",
        [primary_token],
        None,
        page,
        thresholds,
        family="baseline",
    )
    alternate_a = _make_candidate(
        "alternate-a",
        [alternate_token_a],
        None,
        page,
        thresholds,
        family="contrast",
    )
    alternate_b = _make_candidate(
        "alternate-b",
        [alternate_token_b],
        None,
        page,
        thresholds,
        family="sharpness",
    )

    merged, replacements, insertions = _spatial_consensus(
        primary,
        [
            primary,
            alternate_a,
            alternate_b,
        ],
        thresholds=thresholds,
        page_bbox=page,
    )

    # Line count alone cannot detect this regression: both the primary and
    # replacement still contain exactly one reconstructed line. The much
    # smaller spatial footprint must therefore trigger the rollback.
    assert primary.fusion_lost_clusters == 0
    assert primary.fusion_replacements_attempted >= 1
    assert primary.fusion_replacements_accepted == 0
    assert primary.fusion_replacements_rolled_back >= 1

    assert replacements == 0
    assert insertions == 0
    assert len(merged) == 1
    assert merged[0].text == primary_token.text
    assert merged[0].bbox == primary_token.bbox


def test_spatial_consensus_accepts_modest_area_tightening() -> None:
    page = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()

    primary_token = OcrToken(
        text="relatorio completo 2026",
        bbox=BBox(10, 10, 110, 20),
        confidence=0.40,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )
    alternate_token_a = OcrToken(
        text="relatorio 2026",
        bbox=BBox(10, 10, 100, 20),
        confidence=0.92,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )
    alternate_token_b = OcrToken(
        text="relatorio 2026",
        bbox=BBox(10, 10, 100, 20),
        confidence=0.94,
        language="pt",
        source=SourceKind.OCR_PAGE,
    )

    primary = _make_candidate(
        "primary",
        [primary_token],
        None,
        page,
        thresholds,
        family="baseline",
    )
    alternate_a = _make_candidate(
        "alternate-a",
        [alternate_token_a],
        None,
        page,
        thresholds,
        family="contrast",
    )
    alternate_b = _make_candidate(
        "alternate-b",
        [alternate_token_b],
        None,
        page,
        thresholds,
        family="sharpness",
    )

    merged, replacements, insertions = _spatial_consensus(
        primary,
        [
            primary,
            alternate_a,
            alternate_b,
        ],
        thresholds=thresholds,
        page_bbox=page,
    )

    # A modest tightening of the OCR box is normal and must not cause an
    # unnecessary rollback.
    assert primary.fusion_replacements_rolled_back == 0

    assert replacements == 1
    assert insertions == 0
    assert len(merged) == 1
    assert merged[0].text == alternate_token_b.text


def test_overlap_conflict_count_distinguishes_conflicts_from_duplicates() -> None:
    first = _ocr_token(
        "RELATORIO",
        0.90,
        10,
        10,
    )
    same_text = _ocr_token(
        "RELATORIO",
        0.95,
        10,
        10,
    )
    conflicting_text = _ocr_token(
        "RELAT0RIO",
        0.95,
        10,
        10,
    )

    # Equal text in the same spatial cluster is duplicate evidence, not a
    # disagreement between OCR hypotheses.
    assert _overlap_conflict_count(
        [first, same_text]
    ) == 0

    # Different text occupying the same spatial cluster is a fusion conflict.
    assert _overlap_conflict_count(
        [first, conflicting_text]
    ) == 1


def test_engine_propagates_fusion_diagnostics(monkeypatch) -> None:
    page_bbox = BBox(0, 0, 200, 100)
    source_token = _ocr_token(
        "texto",
        0.90,
        10,
        10,
    )

    engine = PaddleOcrEngine(
        quality_policy="baseline",
    )

    # Keep this test completely independent from the real PaddleOCR runtime.
    monkeypatch.setattr(
        engine,
        "_get_ocr",
        lambda: object(),
    )
    monkeypatch.setattr(
        engine,
        "_predict_counted",
        lambda ocr, image: None,
    )
    monkeypatch.setattr(
        "structured_pdf_text.ocr.paddle._deskew_image",
        lambda image, *args, **kwargs: (image, 0.0),
    )
    monkeypatch.setattr(
        "structured_pdf_text.ocr.paddle._tokens_from_result",
        lambda *args, **kwargs: [source_token],
    )
    monkeypatch.setattr(
        "structured_pdf_text.ocr.paddle._rotation_candidates",
        lambda *args, **kwargs: [],
    )

    def fake_spatial_consensus(
        primary,
        candidates,
        *,
        thresholds=None,
        page_bbox=None,
    ):
        primary.fusion_replacements_attempted = 4
        primary.fusion_replacements_accepted = 2
        primary.fusion_replacements_rolled_back = 2
        primary.fusion_lost_clusters = 1
        primary.fusion_conflict_clusters = 3
        primary.fusion_duplicate_clusters = 3
        return list(primary.tokens), 2, 0

    monkeypatch.setattr(
        "structured_pdf_text.ocr.paddle._spatial_consensus",
        fake_spatial_consensus,
    )

    result = engine.recognize_page(
        Image.new("RGB", (200, 100), 255),
        page_index=0,
        page_bbox=page_bbox,
        quality_policy="baseline",
    )

    assert result == [source_token]

    assert engine.last_consensus_replacements == 2
    assert engine.last_consensus_insertions == 0

    assert engine.last_fusion_replacements_attempted == 4
    assert engine.last_fusion_replacements_accepted == 2
    assert engine.last_fusion_replacements_rolled_back == 2
    assert engine.last_fusion_lost_clusters == 1
    assert engine.last_fusion_conflict_clusters == 3
    # Historical alias remains synchronized.
    assert engine.last_fusion_duplicate_clusters == 3


def test_compact_candidate_merge_removes_residual_fragments() -> None:
    fragments = [
        _ocr_token("R$", 0.80, 10, 10),
        _ocr_token("3", 0.80, 22, 10),
        _ocr_token("8", 0.80, 30, 10),
        _ocr_token("36,00", 0.80, 38, 10),
    ]
    compact = _ocr_token("R$ 38 36,00", 0.99, 10, 10)
    merged = _merge_compact_candidate_tokens(fragments, [fragments, [compact]])
    assert [token.text for token in merged] == [compact.text]


def test_half_turn_transform_maps_coordinates_back_to_original() -> None:
    assert _half_turn_box_to_original(10, 20, 30, 40, 100, 80) == (70, 40, 90, 60)


def test_weak_ocr_region_recovery_replaces_only_when_score_improves() -> None:
    class FakeEngine:
        last_pass_count = 1
        last_batch_count = 1

        def recognize_page(self, image, page_index, bbox, **kwargs):
            return [_ocr_token("melhor", 0.99)]

    line = _line("ruído", 20, 20, confidence=0.30, width=40)
    result = _recover_weak_ocr_regions(
        engine=FakeEngine(),
        page_image=Image.new("L", (200, 100), 255),
        page_index=0,
        page_bbox=BBox(0, 0, 200, 100),
        tokens=[_ocr_token("ruído", 0.30, 20, 20)],
        lines=[line],
        quality_policy="baseline",
        thresholds=OcrQualityThresholds(),
    )
    tokens, passes, batches, stats = result
    assert passes > 0
    assert batches > 0
    assert stats["weak-region-1"]["accepted"] is True
    assert tokens[0].text == "melhor"


def test_strong_ocr_line_is_not_targeted_for_recovery() -> None:
    class FakeEngine:
        def recognize_page(self, *args, **kwargs):
            raise AssertionError("strong lines must not be reprocessed")

    line = _line("confiável", 20, 20, confidence=0.99, width=60)
    tokens, passes, batches, stats = _recover_weak_ocr_regions(
        engine=FakeEngine(),
        page_image=Image.new("L", (200, 100), 255),
        page_index=0,
        page_bbox=BBox(0, 0, 200, 100),
        tokens=[_ocr_token("confiável", 0.99, 20, 20)],
        lines=[line],
        quality_policy="baseline",
        thresholds=OcrQualityThresholds(),
    )
    assert tokens[0].text == "confiável"
    assert passes == 0 and batches == 0 and stats == {}


def test_orientation_context_runs_enhancement_on_selected_rotated_image(monkeypatch: pytest.MonkeyPatch) -> None:
    class RotatedImage:
        def __init__(self, angle: int):
            self.angle = angle
            self.size = (100, 100)

    seen: list[object] = []

    def fake_predict(_ocr, image):
        if isinstance(image, list):
            return [fake_predict(_ocr, item) for item in image]
        if isinstance(image, RotatedImage):
            return [[[10, 10, 30, 15], ["upright", 0.98]]]
        return [
            [[10, 10, 15, 35], ["a", 0.30]],
            [[30, 10, 35, 35], ["b", 0.30]],
            [[50, 10, 55, 35], ["c", 0.30]],
        ]

    monkeypatch.setattr(PaddleOcrEngine, "_predict", staticmethod(fake_predict))
    monkeypatch.setattr("structured_pdf_text.ocr.paddle._deskew_image", lambda image, **kwargs: (image, 0.0))
    monkeypatch.setattr(
        "structured_pdf_text.ocr.paddle._rotate_image",
        lambda image, angle: RotatedImage(angle),
    )

    def variants(image):
        seen.append(image)
        return [OcrImageVariant("contrast", image, "contrast")]

    monkeypatch.setattr("structured_pdf_text.ocr.paddle._enhancement_variants", variants)
    engine = PaddleOcrEngine(quality_policy="exhaustive")
    engine._get_ocr = lambda: object()
    engine.recognize_page(
        type("Image", (), {"size": (100, 100)})(),
        0,
        BBox(0, 0, 100, 100),
        quality_policy="exhaustive",
    )
    assert seen
    assert isinstance(seen[0], RotatedImage)
    assert engine.last_enhancement_orientation in (90, 270)
    assert engine.last_orientation_attempts[0] == "baseline"
