"""Regression tests for the existing 2x regional OCR path.

These tests deliberately use a fake OCR engine.  They protect the current
crop, upscale and coordinate-mapping contract without changing the protected
2x implementation or requiring Paddle model weights.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementRequest,
    plan_ocr_scales,
)


@dataclass
class _RecordingEngine:
    calls: list[tuple[tuple[int, int], BBox]]

    last_pass_count: int = 1
    last_batch_count: int = 1

    def recognize_page(
        self,
        image: Image.Image,
        page_index: int,
        page_bbox: BBox,
        **_: object,
    ) -> list[OcrToken]:
        del page_index
        self.calls.append((image.size, page_bbox))
        return [
            OcrToken(
                text="OCR",
                bbox=BBox(20.0, 40.0, 60.0, 80.0),
                confidence=0.99,
                language="pt",
                source=SourceKind.OCR_PAGE,
            )
        ]


def test_existing_2x_variant_receives_scaled_region_and_maps_back_to_page() -> None:
    page_bbox = BBox(0.0, 0.0, 400.0, 400.0)
    region_bbox = BBox(100.0, 100.0, 200.0, 200.0)
    engine = _RecordingEngine(calls=[])

    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (400, 400), "white"),
        page_index=3,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=region_bbox,
            scale_factors=(2.0,),
        ),
    )

    assert engine.calls == [((200, 200), BBox(0.0, 0.0, 200.0, 200.0))]
    assert result.selected_scale_factor == 2.0
    assert result.ocr_passes == 1
    assert result.ocr_batches == 1
    assert len(result.tokens) == 1
    assert result.tokens[0].bbox == BBox(110.0, 120.0, 130.0, 140.0)
    assert result.tokens[0].source is SourceKind.OCR_REGION


def test_2x_variant_keeps_visible_intersection_as_the_mapping_target() -> None:
    page_bbox = BBox(0.0, 0.0, 400.0, 400.0)
    engine = _RecordingEngine(calls=[])

    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (400, 400), "white"),
        page_index=4,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(-50.0, 50.0, 150.0, 250.0),
            scale_factors=(2.0,),
        ),
    )

    # The requested box is clipped to x=0..150 before rendering and scaling.
    assert engine.calls == [((300, 400), BBox(0.0, 0.0, 300.0, 400.0))]
    assert result.bbox == BBox(0.0, 50.0, 150.0, 250.0)
    assert result.tokens[0].bbox == BBox(10.0, 70.0, 30.0, 90.0)


def test_2x_scale_remains_subject_to_the_existing_rgb_budget_gate() -> None:
    allowed, blocked = plan_ocr_scales(
        100,
        100,
        (1.0, 2.0),
        max_rgb_mib=0.05,
    )

    assert [plan.scale for plan in allowed] == [1.0]
    assert [plan.scale for plan in blocked] == [2.0]


# ---------------------------------------------------------------------------
# §18 — Pipeline-owned RGB budget
# ---------------------------------------------------------------------------

from types import SimpleNamespace
from structured_pdf_text.ocr.recovery import _ocr_rgb_budget_from_settings  # noqa: E402


def test_easyocr_settings_capture_finite_rgb_budget_by_default(monkeypatch) -> None:
    """The EasyOCR resolver owns its configured 32 MiB default."""
    monkeypatch.delenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", raising=False)
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.pipelines.easyocr.settings import resolve_settings
    settings = resolve_settings(ExtractorConfig())
    assert settings.rgb_budget_mib == 32.0


def test_paddle_settings_capture_its_default_rgb_budget(monkeypatch) -> None:
    monkeypatch.delenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", raising=False)
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.pipelines.paddle.settings import resolve_settings
    settings = resolve_settings(ExtractorConfig())
    assert settings.rgb_budget_mib == 8.0


def test_easyocr_settings_capture_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "32.0")
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.pipelines.easyocr.settings import resolve_settings
    settings = resolve_settings(ExtractorConfig())
    assert settings.rgb_budget_mib == 32.0


def test_easyocr_settings_allow_large_budget_override(monkeypatch) -> None:
    """An explicit large env override is honoured without restriction."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "512.0")
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.pipelines.easyocr.settings import resolve_settings
    settings = resolve_settings(ExtractorConfig())
    assert settings.rgb_budget_mib == 512.0


def test_recovery_consumes_settings_without_engine_name_branching() -> None:
    assert _ocr_rgb_budget_from_settings(SimpleNamespace(rgb_budget_mib=0.05)) == 0.05


def test_easyocr_budget_tiling_used_for_large_crop(monkeypatch) -> None:
    """A large EasyOCR crop that exceeds the default budget is tiled instead of blocked."""
    monkeypatch.delenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", raising=False)

    class _FakeEasyOCREngine(_RecordingEngine):
        settings = SimpleNamespace(rgb_budget_mib=32.0)

    engine = _FakeEasyOCREngine(calls=[])
    page_bbox = BBox(0.0, 0.0, 1000.0, 1000.0)
    # A 1000×1000 crop at 4× is ~45.8 MiB, above the captured 32 MiB limit.
    # Tiling should keep the work available without an engine-name lookup.
    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (1000, 1000), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 1000.0, 1000.0),
            scale_factors=(4.0,),
        ),
    )
    # Result should not be budget_blocked: tiling provides an alternative.
    assert result.status in {"ok", "no_text"}, f"Unexpected status: {result.status}"
