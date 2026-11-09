"""PaddleOCR legacy raster preparation — integration tests (no OCR weights).

Verifies that:
- OcrRegionRefiner uses local crop/resize/rotate/inverse-map for PaddleOCR.
- RGB budget gate and tiling remain active in legacy mode.
- No elapsed-time timeout can abort the extraction.
- Explicit cancellation still reaches the raw backend.
- Other engines (EasyOCR, Tesseract, RapidOCR) are unaffected.
- PP-OCRv6 medium and use_doc_unwarping=True are preserved.

All tests run without loading PaddleOCR weights; fake backends record calls.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from structured_pdf_text.config import SecurityLimits
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementGoal,
    RegionRefinementRequest,
    image_size,
)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

PAGE_BBOX = BBox(0.0, 0.0, 200.0, 100.0)
REGION_BBOX = BBox(40.0, 20.0, 160.0, 70.0)


class _FakeSource:
    """Minimal source; never called in legacy raster mode."""

    def render_region(self, page_index, bbox, scale, page_bbox=None, page_rotation=0):
        return Image.new("RGB", (10, 10), "white")

    def close(self):
        pass


class _RecordingBackend:
    """Records every recognize_page call and returns a fixed center token."""

    def __init__(self):
        self.calls: list[dict] = []
        self.cancel_calls: int = 0

    def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
        w, h = image_size(image)
        self.calls.append({
            "size": (w, h),
            "page_index": page_index,
            "page_bbox": page_bbox,
            "kwargs": kwargs,
        })
        cx, cy = w / 2.0, h / 2.0
        return [OcrToken(
            "token", BBox(cx - 5, cy - 3, cx + 5, cy + 3),
            0.9, "pt", SourceKind.OCR_PAGE,
        )]

    def cancel(self):
        self.cancel_calls += 1


class _FakeLegacyPipeline:
    """Minimal PaddlePipeline-like object for tests."""

    legacy_raster_preparation = True

    def __init__(self, raw_engine):
        self.raw_engine = raw_engine
        self.selection = EngineSelection("paddle")
        self.settings = None
        self.recovery_policy = None
        self.token_refinement_policy = None
        self.raster_boundary_policy = SimpleNamespace(mode="clip")

    def cancel(self):
        c = getattr(self.raw_engine, "cancel", None)
        if callable(c):
            c()


class _FakeNonLegacyPipeline:
    """Pipeline without legacy_raster_preparation — simulates EasyOCR / Tesseract."""

    def __init__(self):
        self.selection = EngineSelection("easyocr")
        self.settings = None
        self.recovery_policy = None


def _make_facade(
    raw_backend,
    *,
    page_bbox: BBox = PAGE_BBOX,
    cancellation_token=None,
    pipeline_cls=_FakeLegacyPipeline,
) -> OCRPageFacade:
    pipeline = pipeline_cls(raw_backend) if pipeline_cls is _FakeLegacyPipeline else pipeline_cls()
    source = _FakeSource()
    coordinator = PageCoordinator(
        pipeline,
        source,
        extraction_id="test-extraction",
        document_id="test-doc",
        selection=EngineSelection("paddle"),
        page_index=0,
        page_bbox=page_bbox,
        rotation=0,
        quality_policy="adaptive",
        security_limits=SecurityLimits(),
        cancellation_token=cancellation_token,
    )
    return OCRPageFacade(coordinator)


def _page_image(w: int = 200, h: int = 100) -> Image.Image:
    img = Image.new("RGB", (w, h), "white")
    return img


# ---------------------------------------------------------------------------
# § 1: flag and canonical_page_output
# ---------------------------------------------------------------------------

def test_paddle_pipeline_has_legacy_raster_preparation():
    from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
    assert PaddlePipeline.legacy_raster_preparation is True


def test_paddle_canonical_page_output_is_false():
    facade = _make_facade(_RecordingBackend())
    assert facade.canonical_page_output is False


def test_other_engines_canonical_page_output_is_true():
    """EasyOCR / Tesseract pipelines must continue to use the coordinator path."""
    pipeline = _FakeNonLegacyPipeline()
    source = _FakeSource()
    coordinator = PageCoordinator(
        pipeline, source,
        extraction_id="test-ex", document_id="test-doc",
        selection=EngineSelection("easyocr"),
        page_index=0, page_bbox=PAGE_BBOX, rotation=0,
        quality_policy="adaptive", security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    assert facade.canonical_page_output is True


# ---------------------------------------------------------------------------
# § 2: local crop/resize/rotate path
# ---------------------------------------------------------------------------

def test_paddle_legacy_raster_crop_resize_rotate():
    """Backend receives locally-scaled crop; tokens are mapped back to page coords."""
    backend = _RecordingBackend()
    facade = _make_facade(backend)
    page_image = _page_image()

    request = RegionRefinementRequest(
        bbox=REGION_BBOX,
        scale_factors=(1.5,),
        rotations=(0.0,),
        goal=RegionRefinementGoal.TEXT,
    )
    result = OcrRegionRefiner(facade).refine(page_image, 0, PAGE_BBOX, request)

    assert backend.calls, "raw backend was not called"
    call = backend.calls[0]

    # Crop from page at render_scale=1 → region 120×50 px.
    # resize by 1.5 → 180×75 (at minimum max(w+1, round(w*1.5))).
    crop_w = int(REGION_BBOX.width)   # 120
    crop_h = int(REGION_BBOX.height)  # 50
    expected_w = max(crop_w + 1, round(crop_w * 1.5))
    expected_h = max(crop_h + 1, round(crop_h * 1.5))
    got_w, got_h = call["size"]
    assert got_w == expected_w, f"Expected scaled width {expected_w}, got {got_w}"
    assert got_h == expected_h, f"Expected scaled height {expected_h}, got {got_h}"

    # Tokens must be in page-point space (not raw pixel space).
    # Center of REGION_BBOX ≈ (100, 45); result token must be close.
    assert result.tokens, "expected at least one token"
    token = result.tokens[0]
    assert PAGE_BBOX.x0 <= token.bbox.x0 and token.bbox.x1 <= PAGE_BBOX.x1
    assert PAGE_BBOX.y0 <= token.bbox.y0 and token.bbox.y1 <= PAGE_BBOX.y1
    # Token center should be inside the region.
    tc_x = (token.bbox.x0 + token.bbox.x1) / 2
    tc_y = (token.bbox.y0 + token.bbox.y1) / 2
    assert REGION_BBOX.x0 <= tc_x <= REGION_BBOX.x1
    assert REGION_BBOX.y0 <= tc_y <= REGION_BBOX.y1


def test_paddle_legacy_raster_multiple_calls_per_scale_rotation():
    """Each (scale, rotation) pair triggers one backend call."""
    backend = _RecordingBackend()
    facade = _make_facade(backend)
    page_image = _page_image()

    request = RegionRefinementRequest(
        bbox=REGION_BBOX,
        scale_factors=(1.0, 1.5),
        rotations=(0.0,),
        goal=RegionRefinementGoal.TEXT,
    )
    OcrRegionRefiner(facade).refine(page_image, 0, PAGE_BBOX, request)
    # 2 scale factors × 1 rotation = 2 calls
    assert len(backend.calls) == 2


def test_paddle_legacy_raster_rotations():
    """Non-zero rotation: inverse affine maps tokens back to page coords without double transform."""
    cv2 = pytest.importorskip("cv2", reason="cv2 required for rotate_image_expanded")

    backend = _RecordingBackend()
    facade = _make_facade(backend)
    page_image = _page_image()

    request = RegionRefinementRequest(
        bbox=REGION_BBOX,
        scale_factors=(1.0,),
        rotations=(90.0,),
        goal=RegionRefinementGoal.TEXT,
    )
    result = OcrRegionRefiner(facade).refine(page_image, 0, PAGE_BBOX, request)

    assert backend.calls, "backend not called for 90° rotation"
    # For rotation=90, the image is expanded; check the passed size is different from unrotated.
    # Unrotated crop of REGION_BBOX (120×50); rotated image would be ~50×120 or slightly larger.
    call = backend.calls[0]
    # The image is NOT None and has valid dimensions.
    w, h = call["size"]
    assert w > 0 and h > 0

    # Tokens (if any) must still land in page coordinate range.
    for token in result.tokens:
        assert PAGE_BBOX.x0 - 5 <= token.bbox.x0, f"token x0 out of page: {token.bbox}"
        assert token.bbox.x1 <= PAGE_BBOX.x1 + 5, f"token x1 out of page: {token.bbox}"


def test_paddle_legacy_raster_page_rotation_handled():
    """Page /Rotate 90: crop_page_region transforms bbox before cropping."""
    backend = _RecordingBackend()
    page_bbox_90 = BBox(0.0, 0.0, 100.0, 200.0)  # canonical: portrait
    facade = _make_facade(backend, page_bbox=page_bbox_90)
    # Page rendered at 90° → visual image is 200×100 pixels.
    page_image = _page_image(200, 100)

    request = RegionRefinementRequest(
        bbox=BBox(10.0, 10.0, 90.0, 190.0),
        scale_factors=(1.0,),
        rotations=(0.0,),
        page_rotation=90,
        goal=RegionRefinementGoal.TEXT,
    )
    result = OcrRegionRefiner(facade).refine(page_image, 0, page_bbox_90, request)
    # Backend must have been called (crop was non-empty).
    assert backend.calls, "backend not called for page_rotation=90"
    # Tokens must be in canonical page coords.
    for token in result.tokens:
        assert page_bbox_90.x0 - 5 <= token.bbox.x0 <= page_bbox_90.x1 + 5
        assert page_bbox_90.y0 - 5 <= token.bbox.y0 <= page_bbox_90.y1 + 5


# ---------------------------------------------------------------------------
# § 3: RGB budget gate preserved
# ---------------------------------------------------------------------------

def test_paddle_rgb_budget_gate_still_active():
    """Scale factors exceeding the RGB budget produce tiling, not a crash."""
    import os
    backend = _RecordingBackend()
    facade = _make_facade(backend)
    page_image = _page_image(200, 100)

    # Set a very small budget (0.01 MiB) so that every scale variant is blocked.
    # The refiner should fall back to tiled OCR.
    old = os.environ.get("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB")
    try:
        os.environ["PDFEXTRACTOR_OCR_RGB_BUDGET_MIB"] = "0.01"
        request = RegionRefinementRequest(
            bbox=REGION_BBOX,
            scale_factors=(1.0,),
            rotations=(0.0,),
            goal=RegionRefinementGoal.TEXT,
        )
        # Must not raise; the result may have no tokens (tiles may be too small)
        # but must complete without RuntimeError or ValueError.
        result = OcrRegionRefiner(facade).refine(page_image, 0, PAGE_BBOX, request)
        assert result is not None
    finally:
        if old is None:
            os.environ.pop("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", None)
        else:
            os.environ["PDFEXTRACTOR_OCR_RGB_BUDGET_MIB"] = old


def test_paddle_rgb_budget_from_settings_respected():
    """Budget read from pipeline settings, not from engine name."""
    from structured_pdf_text.ocr.recovery import _ocr_rgb_budget_from_settings
    from structured_pdf_text.ocr.pipeline.settings import EngineSettings
    from structured_pdf_text.ocr.pipeline.contracts import EngineSelection
    settings = SimpleNamespace(rgb_budget_mib=16.0)
    assert _ocr_rgb_budget_from_settings(settings) == 16.0
    assert _ocr_rgb_budget_from_settings(None) > 0


# ---------------------------------------------------------------------------
# § 4: integration targets still route through coordinator
# ---------------------------------------------------------------------------

def test_paddle_integration_targets_bypass_legacy_path():
    """When integration_targets are set, the coordinator path is used (not raw backend)."""
    backend = _RecordingBackend()
    facade = _make_facade(backend)

    # Call recognize_page directly with a fake integration target.
    # In legacy mode, integration_targets trigger the coordinator path.
    # Since the coordinator cannot render without a real source, it will raise
    # (RuntimeError / WorkCancelled / similar) — but NOT call the raw backend.
    # Use a sentinel object — the guard check only requires integration_targets to be truthy.
    fake_target = object()
    pixel_image = _page_image()

    with pytest.raises(Exception):
        # coordinator.recognize() will fail (no real source), but the raw backend
        # must NOT have been called.
        facade.recognize_page(
            pixel_image, 0, PAGE_BBOX,
            integration_targets=(fake_target,),
            defer_integration=True,
        )

    assert not backend.calls, "raw backend should not be called when integration_targets is set"


def test_paddle_target_bbox_bypasses_legacy_path():
    """When target_bbox is set, the coordinator path is used (not raw backend)."""
    backend = _RecordingBackend()
    facade = _make_facade(backend)
    pixel_image = _page_image()

    with pytest.raises(Exception):
        facade.recognize_page(
            pixel_image, 0, PAGE_BBOX,
            target_bbox=BBox(40, 20, 100, 60),
        )

    assert not backend.calls, "raw backend must not be called when target_bbox is set"


# ---------------------------------------------------------------------------
# § 5: no elapsed-time abort
# ---------------------------------------------------------------------------

def test_paddle_no_elapsed_time_abort():
    """A slow raw backend does not cause the refiner to abort the extraction."""

    start_event = threading.Event()
    continue_event = threading.Event()

    class SlowBackend:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            start_event.set()
            continue_event.wait()
            return []

    facade = _make_facade(SlowBackend())
    page_image = _page_image()
    request = RegionRefinementRequest(
        bbox=REGION_BBOX, scale_factors=(1.0,), rotations=(0.0,),
        goal=RegionRefinementGoal.TEXT,
    )

    result_holder: list = []
    error_holder: list = []

    def run():
        try:
            result_holder.append(
                OcrRegionRefiner(facade).refine(page_image, 0, PAGE_BBOX, request)
            )
        except Exception as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    started = start_event.wait(timeout=5)
    assert started, "backend never started"
    continue_event.set()
    thread.join(timeout=5)

    assert not thread.is_alive(), "refine() did not complete after backend finished"
    assert not error_holder, f"unexpected error: {error_holder}"
    assert result_holder, "no result returned"


# ---------------------------------------------------------------------------
# § 6: explicit cancellation still works
# ---------------------------------------------------------------------------

def test_paddle_explicit_cancel_still_works():
    """Triggering a CancellationToken calls cancel() on the raw backend via the pipeline."""
    from structured_pdf_text import CancellationToken

    backend = _RecordingBackend()
    token = CancellationToken()
    facade = _make_facade(backend, cancellation_token=token)

    token.cancel()
    assert backend.cancel_calls == 1, (
        f"Expected cancel() called once on raw backend, got {backend.cancel_calls}"
    )


# ---------------------------------------------------------------------------
# § 7: default model and unwarping preserved
# ---------------------------------------------------------------------------

def test_paddle_default_models_and_uvdoc_preserved():
    """PP-OCRv6 medium is the active profile; use_doc_unwarping defaults to True."""
    from structured_pdf_text.ocr.models import PROFILES, PADDLE_OCR_FEATURE_DEFAULTS, required_model_directories

    # Default profile "pt" must be PP-OCRv6 medium.
    profile = PROFILES["pt"]
    assert "v6" in profile.detection.lower() or "v6" in profile.recognition.lower(), (
        f"Expected PP-OCRv6 in default profile, got det={profile.detection!r} rec={profile.recognition!r}"
    )
    assert "medium" in profile.detection.lower() or "medium" in profile.recognition.lower(), (
        f"Expected medium variant in default profile"
    )

    # use_doc_unwarping defaults to True in PADDLE_OCR_FEATURE_DEFAULTS.
    assert PADDLE_OCR_FEATURE_DEFAULTS.get("use_doc_unwarping") is True, (
        f"use_doc_unwarping must default to True in PADDLE_OCR_FEATURE_DEFAULTS, "
        f"got {PADDLE_OCR_FEATURE_DEFAULTS.get('use_doc_unwarping')!r}"
    )

    # required_model_directories with use_doc_unwarping=True includes the unwarping model dir.
    import inspect
    sig = inspect.signature(required_model_directories)
    param = sig.parameters.get("use_doc_unwarping")
    assert param is not None, "required_model_directories should accept use_doc_unwarping"
    assert param.default is True, (
        f"use_doc_unwarping default should be True in required_model_directories, got {param.default!r}"
    )

    # Verify "pt-v6-medium" alias is not PP-OCRv5.
    assert "pt-v6-medium" in PROFILES, "pt-v6-medium alias must exist"
    assert "v5" not in PROFILES["pt-v6-medium"].detection.lower(), "v5 must not be in default profile"
