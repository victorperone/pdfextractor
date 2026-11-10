"""PaddleOCR historical raster preparation through the authorized pipeline (ADR 0002).

The pre-decoupling geometry still exists as the local branch of
``OcrRegionRefiner`` used by raw (non-facade) engines; it is the exact code of
54885e69 (crop_page_region -> resize_image -> rotate_image_expanded ->
_map_token_to_page). These tests run the same request through that branch and
through ``PaddlePipeline`` behind ``PageCoordinator``/``OCRPageFacade`` and
compare the pixels delivered to the backend and the canonical token geometry.

No OCR weights are loaded: a fake backend reports the pixel box of the red
marker it receives, so geometry errors move the box.
"""
from __future__ import annotations

from functools import partial
import logging
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from structured_pdf_text import CancellationToken, ExtractionCancelled
from structured_pdf_text.config import OcrQualityPolicy, SecurityLimits
from structured_pdf_text.document import (
    LayoutRegion, OcrToken, RegionDecision, RegionKind, RegionQuality, SourceKind,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource
from structured_pdf_text.ocr.coordinates import map_tokens_to_page
from structured_pdf_text.ocr.image_views import canonicalize_page_image
from structured_pdf_text.ocr.pipeline import adapters
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.budgets import WorkCancelled
from structured_pdf_text.ocr.pipeline.contracts import (
    EngineSelection, ResourceAllocation, raster_preparation_context,
)
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.pipeline.policies import SOURCE_RASTER_PREPARATION
from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
from structured_pdf_text.ocr.pipelines.paddle.policy import RASTER_PREPARATION
from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementRequest
from structured_pdf_text.ocr import slow_work
from ocr_pipeline_helpers import pdf_with_markers

BASE_SCALE = 2.0
# Canonical PDF box of the red marker drawn by pdf_with_markers.
RED_MARKER = BBox(10.0, 10.0, 40.0, 90.0)


class MarkerBackend:
    """Report the pixel box of the red marker; map it only if asked to (historic page OCR)."""

    def __init__(self, *, budget_mib=8.0, blocker=None):
        self.images = []
        self.kwargs = []
        self.settings = SimpleNamespace(render_scale=BASE_SCALE, rgb_budget_mib=budget_mib)
        self.blocker = blocker
        self.cancelled = threading.Event()

    def recognize_page(self, image, page_index, page_bbox=None, *, quality_variants=None,
                       quality_policy=None, page_rotation=0):
        if self.blocker is not None:
            self.blocker()
        pixels = np.asarray(image.convert("RGB"))
        self.images.append(pixels.copy())
        self.kwargs.append({"page_bbox": page_bbox, "page_rotation": page_rotation})
        red = (pixels[..., 0] > 200) & (pixels[..., 1] < 80) & (pixels[..., 2] < 80)
        ys, xs = np.nonzero(red)
        if not len(xs):
            return []
        token = OcrToken("marker", BBox(float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)),
                         .9, "pt", SourceKind.OCR_PAGE)
        if page_bbox is not None and page_rotation is not None:
            # Historical PaddleOCR mapped page OCR itself from the visual raster.
            token = map_tokens_to_page([token], page_bbox, image.width, image.height, page_rotation)[0]
        return [token]

    def cancel(self):
        self.cancelled.set()


class SpySource:
    def __init__(self, native):
        self.native = native
        self.config = native.config
        self.calls = []

    def render_region(self, page_index, bbox, scale, page_bbox=None, page_rotation=None):
        self.calls.append(scale)
        return self.native.render_region(page_index, bbox, scale, page_bbox, page_rotation)


def _paddle_pipeline(backend):
    resolved = SimpleNamespace(engine="paddle", provider=None, settings=backend.settings)
    return PaddlePipeline(resolved, backend)


def _facade(pipeline, source, page, *, allocation=None, cancellation_token=None):
    coordinator = PageCoordinator(
        pipeline, source, extraction_id="paddle-raster", document_id="markers",
        selection=pipeline.descriptor.selection, page_index=0, page_bbox=page.bbox,
        rotation=page.objects.rotation, quality_policy=OcrQualityPolicy.ADAPTIVE,
        security_limits=SecurityLimits(), allocation=allocation, cancellation_token=cancellation_token,
    )
    return OCRPageFacade(coordinator)


def _region_request(page, *, scale=1.0, rotation=0.0, offset=5.0):
    box = page.bbox
    return RegionRefinementRequest(
        bbox=BBox(box.x0 + offset, box.y0 + offset, box.x0 + 60, box.y0 + 80),
        scale_factors=scale if isinstance(scale, tuple) else (scale,), rotations=(rotation,), quality_variants=False,
        page_rotation=page.objects.rotation,
    )


def _compare_with_history(path, request_for, *, budget_mib=8.0):
    """Run one refinement through the historical branch and the Paddle pipeline."""
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        request = request_for(page)
        page_image = native.render_page(0, scale=BASE_SCALE)
        historic = MarkerBackend(budget_mib=budget_mib)
        historic_result = OcrRegionRefiner(historic).refine(page_image, 0, page.bbox, request)
        current = MarkerBackend(budget_mib=budget_mib)
        source = SpySource(native)
        facade = _facade(_paddle_pipeline(current), source, page)
        try:
            current_result = OcrRegionRefiner(facade).refine(page_image, 0, page.bbox, request)
            return SimpleNamespace(
                historic=historic, current=current, historic_result=historic_result,
                current_result=current_result, source=source, coordinator=facade.coordinator,
                services=facade.coordinator.services,
            )
        finally:
            facade.close()


def _assert_same_pixels_and_geometry(run):
    assert len(run.current.images) == len(run.historic.images) > 0
    for historic, current in zip(run.historic.images, run.current.images):
        assert historic.shape == current.shape
        assert np.array_equal(historic, current)
    historic_boxes = [token.bbox for token in run.historic_result.tokens]
    current_boxes = [token.bbox for token in run.current_result.tokens]
    assert len(historic_boxes) == len(current_boxes) > 0
    for historic, current in zip(historic_boxes, current_boxes):
        for name in ("x0", "y0", "x1", "y1"):
            assert getattr(current, name) == pytest.approx(getattr(historic, name), abs=1e-6)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0, 3.0])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_paddle_legacy_raster_crop_resize_rotate(tmp_path, rotation, scale):
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=rotation)
    run = _compare_with_history(path, lambda page: _region_request(page, scale=scale))

    _assert_same_pixels_and_geometry(run)
    marker = run.current_result.tokens[0].bbox
    assert (marker.x0, marker.y0, marker.x1) == pytest.approx((RED_MARKER.x0, RED_MARKER.y0, RED_MARKER.x1))
    # Pixels come from one authorized render at the base scale, enlarged locally:
    # the source is never asked for a higher-resolution re-render.
    assert run.source.calls == [BASE_SCALE]
    [request] = run.coordinator.results
    attempt = request["engine_diagnostics"]["paddle"]["attempts"][0]
    assert attempt["raster_preparation"]["strategy"] == "paddle-legacy-raster-v1"
    assert attempt["raster_preparation"]["origin"] == "page_crop"
    assert attempt["raster_preparation"]["visual_rotation"] == rotation
    assert attempt["raster_preparation"]["local_upsample"] == scale
    assert request["render_scale"] == BASE_SCALE * scale
    assert run.services.observations()["live_raster_bytes"] == 0


def test_paddle_legacy_raster_local_rotation(tmp_path):
    pytest.importorskip("cv2")
    path = pdf_with_markers(tmp_path / "markers.pdf")
    run = _compare_with_history(path, lambda page: _region_request(page, scale=1.5, rotation=90.0))

    _assert_same_pixels_and_geometry(run)
    attempt = run.coordinator.results[0]["engine_diagnostics"]["paddle"]["attempts"][0]
    assert attempt["raster_preparation"]["local_rotation"] == 90.0


def test_paddle_legacy_raster_non_zero_origin(tmp_path):
    path = pdf_with_markers(tmp_path / "markers.pdf", crop=True)
    run = _compare_with_history(path, lambda page: _region_request(page, scale=1.5, offset=2.0))

    assert run.current_result.tokens
    _assert_same_pixels_and_geometry(run)


@pytest.mark.parametrize("rotation", [0, 90])
def test_paddle_legacy_raster_tile_geometry(tmp_path, rotation):
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=rotation)
    # The RGB budget admits the 1x crop (110x150 px) but not 2x, so the 2x
    # variant takes the tiled fallback in both implementations.
    run = _compare_with_history(path, lambda page: _region_request(page, scale=(1.0, 2.0)), budget_mib=0.1)

    _assert_same_pixels_and_geometry(run)
    tiles = [item for item in run.coordinator.results if item["selection_stage"] == "tile"]
    assert len(tiles) > 1
    assert len(run.current.images) == len(tiles) + 1
    assert all(image.size <= 0.1 * 1024 * 1024 for image in run.current.images[1:])
    for item in tiles:
        preparation = item["engine_diagnostics"]["paddle"]["attempts"][0]["raster_preparation"]
        assert preparation["origin"] == "page_crop"
        assert preparation["local_upsample"] == 2.0
    assert run.services.observations()["live_raster_bytes"] == 0


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_paddle_legacy_raster_rotations_full_page(tmp_path, rotation):
    """Full-page OCR sees the visual raster; the physical rotation is mapped once."""
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=rotation)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        page_image = native.render_page(0, scale=BASE_SCALE)
        historic = MarkerBackend()
        historic_tokens = historic.recognize_page(page_image, 0, page.bbox, page_rotation=rotation)
        current = MarkerBackend()
        facade = _facade(_paddle_pipeline(current), SpySource(native), page)
        try:
            # api.py forwards page_rotation to the facade, exactly as before.
            tokens = facade.recognize_page(page_image, 0, page.bbox, quality_policy="adaptive",
                                           page_rotation=rotation)
            assert np.array_equal(current.images[0], historic.images[0])
            # The backend gets raster coordinates of the visual image; the
            # RasterFrame alone maps them (no second rotation in the engine).
            assert current.kwargs[0] == {"page_bbox": None, "page_rotation": 0}
            assert len(tokens) == 1
            for name in ("x0", "y0", "x1", "y1"):
                assert getattr(tokens[0].bbox, name) == pytest.approx(getattr(historic_tokens[0].bbox, name), abs=1e-6)
            [request] = facade.coordinator.results
            assert request["goal"] == "page" and request["integrated_token_count"] == 1
            preparation = request["engine_diagnostics"]["paddle"]["attempts"][0]["raster_preparation"]
            assert preparation["origin"] == "page_crop" and preparation["visual_rotation"] == rotation
            assert facade.operation_metrics["passes"] >= 0
        finally:
            facade.close()


def test_paddle_recover_selected_regions_integrates_deferred_tokens(tmp_path):
    """P0-01: region evidence returns through the deferred, ledger-backed path."""
    from structured_pdf_text.api import _recover_selected_regions

    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=90)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        region = LayoutRegion("marker-region", RegionKind.TEXT, BBox(5, 5, 60, 80), .9, [], [],
                              RegionQuality(RegionDecision.OCR_REGION))
        backend = MarkerBackend()
        facade = _facade(_paddle_pipeline(backend), SpySource(native), page)
        page_image = native.render_page(0, scale=BASE_SCALE)
        try:
            tokens, passes, batches, stats = _recover_selected_regions(
                facade, page_image, 0, page.bbox, [region], quality_variants=False,
                quality_policy="adaptive", page_rotation=90,
            )
            assert [token.text for token in tokens] == ["marker"]
            assert region.ocr_tokens == tokens
            assert tokens[0].bbox.x0 == pytest.approx(RED_MARKER.x0)
            assert stats[region.region_id]["tokens"] == 1
            coordinator = facade.coordinator
            # P1-02: requests, receipts and counters reflect the real attempt.
            assert coordinator.calls == len(backend.images) == 1
            assert coordinator.last_result is not None
            assert sum(item["integrated_token_count"] for item in coordinator.results) == 1
            assert all(not item["deferred_integration"] for item in coordinator.results)
            assert [decision["outcome"] for decision in coordinator.results[0]["integration_decisions"]] == ["accepted"]
            assert not coordinator._deferred_tokens
            # Evidence that did not come from a deferred pipeline result is refused.
            binding = facade.make_region_target(LayoutRegion(
                "other-region", RegionKind.TEXT, BBox(5, 5, 60, 80), .9, [], [],
                RegionQuality(RegionDecision.OCR_REGION),
            ))
            forged = OcrToken("forged", BBox(10, 10, 20, 20), .99, "pt", SourceKind.OCR_REGION)
            with pytest.raises(ValueError, match="did not come from a deferred pipeline result"):
                facade.integrate_selected_tokens([forged], binding)
        finally:
            facade.close()


def test_paddle_authorization_and_rgb_budget_preserved(tmp_path):
    path = pdf_with_markers(tmp_path / "markers.pdf")
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        region = BBox(5, 5, 60, 80)

        class OutsideBackend(MarkerBackend):
            def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
                super().recognize_page(image, page_index, page_bbox, **kwargs)
                return [OcrToken("outside", BBox(-50, 0, image.width + 50, 10), .9, "pt", SourceKind.OCR_PAGE)]

        facade = _facade(_paddle_pipeline(OutsideBackend()), SpySource(native), page)
        try:
            with pytest.raises(RuntimeError, match="outside the exact authorization"):
                facade.recognize_page(None, 0, region, target_bbox=region, render_scale=BASE_SCALE * 1.5,
                                      selection_stage="region", raster_preparation="page_crop")
            [request] = facade.coordinator.results
            assert request["status"] == "invalid_region" and request["integrated_token_count"] == 0
            assert facade.coordinator.services.observations()["live_raster_bytes"] == 0
        finally:
            facade.close()

        # The locally enlarged raster is reserved against the request budget:
        # the base render fits, the 3x enlargement does not.
        tight = ResourceAllocation(max_live_raster_bytes=256 * 1024 * 1024, max_raster_pixels=40_000,
                                   max_work_pixels=400_000_000, max_attempts=8, max_rasters=16)
        backend = MarkerBackend()
        facade = _facade(_paddle_pipeline(backend), SpySource(native), page, allocation=tight)
        try:
            with pytest.raises(RuntimeError):
                facade.recognize_page(None, 0, region, target_bbox=region, render_scale=BASE_SCALE * 3.0,
                                      selection_stage="region", raster_preparation="page_crop")
            [request] = facade.coordinator.results
            assert request["status"] == "budget_blocked"
            assert not backend.images
            assert facade.coordinator.services.observations()["live_raster_bytes"] == 0
        finally:
            facade.close()


class FullImageBackend(MarkerBackend):
    """Detector box touching every image edge, like PaddleOCR on a tight crop."""

    def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
        super().recognize_page(image, page_index, page_bbox, **kwargs)
        return [OcrToken("edge", BBox(0, 0, image.width, image.height), .9, "pt", SourceKind.OCR_PAGE)]


def _a4_with_box(path, box, *, rotation=0):
    """A4 page (fractional size) with a dark box: its 2x render has partial border pixels."""
    from reportlab.pdfgen import canvas
    import pypdfium2 as pdfium

    width, height = 595.2755737304688, 841.8897705078125
    pdf = canvas.Canvas(str(path), pagesize=(width, height))
    pdf.setFillColorRGB(0.1, 0.1, 0.1)
    pdf.rect(box.x0 + 2, height - box.y1 + 2, box.width - 4, box.height - 4, fill=1, stroke=0)
    pdf.save()
    if rotation:
        doc = pdfium.PdfDocument(path)
        doc[0].set_rotation(rotation)
        target = path.with_stem(path.stem + "-rotated")
        doc.save(target)
        doc.close()
        return target
    return path


@pytest.mark.parametrize("rotation", [0, 90])
def test_paddle_clips_tokens_to_masked_partial_pixel_border(tmp_path, rotation):
    """A box reaching the white partial-pixel border is clipped, never widened (ADR 0002).

    Geometry of V5 page 32 (figure at x0=300.47 pt): the first crop pixel
    starts at 300.39 pt, so it is masked and a detector box from x=0 overshoots.
    """
    region = BBox(300.472412109375, 521.2543334960938, 538.5826416015625, 550.2416687011719)
    path = _a4_with_box(tmp_path / "a4.pdf", region, rotation=rotation)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        facade = _facade(_paddle_pipeline(FullImageBackend()), SpySource(native), page)
        try:
            [token] = facade.recognize_page(None, 0, region, target_bbox=region, render_scale=BASE_SCALE * 1.5,
                                            selection_stage="region", raster_preparation="page_crop")
            [request] = facade.coordinator.results
            assert request["status"] == "ok"
            assert token.bbox.x0 >= region.x0 and token.bbox.y0 >= region.y0
            assert token.bbox.x1 <= region.x1 and token.bbox.y1 <= region.y1
            # Only the sub-pixel masked border was removed.
            for name in ("x0", "y0", "x1", "y1"):
                assert getattr(token.bbox, name) == pytest.approx(getattr(region, name), abs=1 / BASE_SCALE)
        finally:
            facade.close()

        # The unclipped box really overshoots: without confinement it is rejected.
        unconfined = _paddle_pipeline(FullImageBackend())
        unconfined.raster_boundary_policy = adapters.LEGACY_RASTER_BOUNDARY_POLICY
        facade = _facade(unconfined, SpySource(native), page)
        try:
            with pytest.raises(RuntimeError, match="outside the exact authorization"):
                facade.recognize_page(None, 0, region, target_bbox=region, render_scale=BASE_SCALE * 1.5,
                                      selection_stage="region", raster_preparation="page_crop")
        finally:
            facade.close()


def test_masked_border_confinement_is_bounded_by_the_rendered_frame():
    from structured_pdf_text.geometry import Point
    from structured_pdf_text.ocr.pipeline.geometry import RasterFrame
    from structured_pdf_text.ocr.pipeline.policies import RasterBoundaryPolicy

    authorized = BBox(10.2, 10.2, 50.3, 30.3)
    frame = RasterFrame(BBox(10.0, 10.0, 50.5, 30.5), 81, 41)
    policy = RasterBoundaryPolicy(confine_masked_border=True)
    inside = OcrToken("x", BBox(10.0, 12.0, 50.5, 20.0), .9, "pt", SourceKind.OCR_REGION,
                      polygon=(Point(10.0, 12.0), Point(50.5, 20.0)))
    clipped = policy.confine(inside, authorized, frame)
    assert clipped.bbox == BBox(10.2, 12.0, 50.3, 20.0)
    assert clipped.polygon == (Point(10.2, 12.0), Point(50.3, 20.0))
    # Beyond the rendered pixels nothing is clipped; validation still rejects it.
    beyond = OcrToken("x", BBox(9.0, 12.0, 30.0, 20.0), .9, "pt", SourceKind.OCR_REGION)
    assert policy.confine(beyond, authorized, frame) is beyond
    # Trim mode, and the shared default, never touch tokens.
    for other in (RasterBoundaryPolicy(), RasterBoundaryPolicy("trim", confine_masked_border=True)):
        assert other.confine(inside, authorized, frame) is inside


def test_paddle_figure_admission_rejects_shape_glyphs_only():
    from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
    from structured_pdf_text.ocr.pipelines.paddle.policy import ADMISSION_POLICY

    def token(text, box, confidence=.99):
        return SimpleNamespace(text=text, bbox=box, confidence_normalized=confidence, authorization_id="a")

    figure = SimpleNamespace(goal=WorkGoal.FIGURE, authorizations=(SimpleNamespace(authorization_id="a", regions=(BBox(0, 0, 300, 300),)),))
    region = SimpleNamespace(goal=WorkGoal.REGION, authorizations=figure.authorizations)
    # A textless emblem read as one huge glyph (V5 p.21/31) and a chart circle (V6 p.62).
    assert ADMISSION_POLICY.rejection_reason(token("é", BBox(0, 0, 297, 297)), figure) == "paddle_oversized_lone_figure_glyph"
    assert ADMISSION_POLICY.rejection_reason(token("O", BBox(10, 10, 22, 22), .98), figure) == "paddle_geometric_shape_glyph"
    # Labels and text inside figures, and every non-figure goal, stay admitted.
    assert ADMISSION_POLICY.rejection_reason(token("C", BBox(10, 10, 20, 22)), figure) is None
    assert ADMISSION_POLICY.rejection_reason(token("REGISTRO DE CAMPO", BBox(0, 0, 80, 8)), figure) is None
    assert ADMISSION_POLICY.rejection_reason(token("é", BBox(0, 0, 297, 297)), region) is None
    assert PaddlePipeline.admission_policy is ADMISSION_POLICY
    assert AuthorizedBackendPipeline.admission_policy is adapters.LEGACY_EVIDENCE_ADMISSION_POLICY


def test_paddle_rotated_local_variant_never_widens_authorization(tmp_path):
    """Known difference: history clamped rotated-variant boxes; the ledger rejects them."""
    pytest.importorskip("cv2")
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=90)
    run = _compare_with_history(path, lambda page: _region_request(page, scale=2.0, rotation=7.5))

    assert np.array_equal(run.historic.images[0], run.current.images[0])
    assert run.historic_result.tokens and not run.current_result.tokens
    assert run.current_result.status == "runtime_error"
    assert run.coordinator.results[0]["status"] == "invalid_region"


def test_paddle_enlarged_cell_matches_historic_lanczos(tmp_path):
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=90)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        cell = BBox(12, 20, 38, 80)
        # Historic weak-cell attempt: source render at base * 1.75, canonical
        # orientation, then an exact 2x LANCZOS enlargement.
        render = canonicalize_page_image(native.render_region(0, cell, BASE_SCALE * 1.75, page.bbox, 90), 90)
        crop = np.asarray(render)
        expected = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.Resampling.LANCZOS))
        backend = MarkerBackend()
        source = SpySource(native)
        facade = _facade(_paddle_pipeline(backend), source, page)
        try:
            tokens = facade.recognize_page(None, 0, cell, target_bbox=cell, render_scale=BASE_SCALE * 1.75 * 2.0,
                                           selection_stage="cell", raster_preparation="enlarged_source",
                                           raster_enlargement=2.0)
            assert np.array_equal(backend.images[0], expected)
            assert source.calls == [BASE_SCALE * 1.75]
            assert tokens and tokens[0].bbox.x0 == pytest.approx(cell.x0, abs=.5)
        finally:
            facade.close()


def test_paddle_footnote_keeps_historic_visual_source_render(tmp_path):
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=270)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        box = BBox(5, 5, 60, 80)
        expected = np.asarray(native.render_region(0, box, BASE_SCALE * 2.0, page.bbox, 270).convert("RGB"))
        backend = MarkerBackend()
        facade = _facade(_paddle_pipeline(backend), SpySource(native), page)
        try:
            facade.recognize_page(None, 0, box, target_bbox=box, render_scale=BASE_SCALE * 2.0,
                                  selection_stage="footnote", raster_preparation="visual_source")
            assert np.array_equal(backend.images[0], expected)
        finally:
            facade.close()


def test_core_states_raster_intent_for_cell_and_footnote_attempts():
    from structured_pdf_text.ocr.pipeline.cell_recovery import refine_cell
    from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, TokenRefinementPolicy
    from structured_pdf_text.ocr.pipeline.token_refinement import refine_footnote

    class RecordingFacade:
        capabilities = {"direct_recognition": False}
        coordinator = SimpleNamespace(page_index=0)

        def __init__(self):
            self.calls = []

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            self.calls.append(kwargs)
            return []

        def discard_selected_tokens(self, target):
            pass

    policy = CellRecoveryPolicy()
    facade = RecordingFacade()
    target = SimpleNamespace(page_index=0, cell=SimpleNamespace(bbox=BBox(1, 1, 9, 9), text=""))
    refine_cell(facade, target, quality_policy="adaptive", base_scale=BASE_SCALE, header_text="", policy=policy)
    assert [call.get("raster_preparation") for call in facade.calls] == [None, "enlarged_source"]
    assert facade.calls[1]["raster_enlargement"] == policy.enlarged_scale_factor
    assert facade.calls[1]["render_scale"] == BASE_SCALE * policy.render_scale_factor * policy.enlarged_scale_factor

    facade = RecordingFacade()
    target = SimpleNamespace(tokens=(), evidence_ids=(), selected_evidence_ids=set(), bbox=BBox(1, 1, 9, 9))
    refine_footnote(facade, target, base_scale=BASE_SCALE, quality_policy="adaptive", policy=TokenRefinementPolicy())
    assert facade.calls[0]["raster_preparation"] == "visual_source"


def test_raster_preparation_context_is_validated():
    assert raster_preparation_context(None) == {}
    assert raster_preparation_context("page_crop") == {"raster_preparation": "page_crop"}
    assert raster_preparation_context("enlarged_source", 2) == {"raster_preparation": "enlarged_source", "raster_enlargement": 2.0}
    for kind, enlargement in (("unknown", None), ("page_crop", 2.0), ("enlarged_source", None),
                              ("enlarged_source", 1.0), (None, 2.0), ("enlarged_source", float("nan"))):
        with pytest.raises(ValueError):
            raster_preparation_context(kind, enlargement)


def test_paddle_default_models_keep_orientation_without_uvdoc():
    """PP-OCRv6 medium stays; UVDoc is opt-in since the 09/11/2026 campaign (ADR 0002)."""
    from structured_pdf_text import ExtractorConfig
    from structured_pdf_text.ocr.models import PADDLE_OCR_FEATURE_DEFAULTS, PROFILES, required_model_directories

    default = ExtractorConfig().paddle_model_profile
    assert (PROFILES[default].detection, PROFILES[default].recognition) == ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec")
    assert PADDLE_OCR_FEATURE_DEFAULTS == {
        "use_doc_orientation_classify": True, "use_doc_unwarping": False, "use_textline_orientation": True,
    }
    required = required_model_directories(PROFILES[default], **PADDLE_OCR_FEATURE_DEFAULTS)
    assert "doc_unwarping_model_dir" not in required
    assert required["doc_orientation_classify_model_dir"] == PROFILES[default].doc_orientation
    assert not any("v5" in name.lower() or "v5" in profile.detection.lower() or "v5" in profile.recognition.lower()
                   for name, profile in PROFILES.items())


def test_paddle_no_elapsed_time_abort(tmp_path, monkeypatch, caplog):
    """A slow inference only logs a warning; it is neither cancelled nor reclassified."""
    warned = threading.Event()

    class WarningSeen(logging.Handler):
        def emit(self, record):
            if "no timeout is applied" in record.getMessage():
                warned.set()

    handler = WarningSeen()
    logging.getLogger(slow_work.__name__).addHandler(handler)
    monkeypatch.setattr(adapters, "warn_if_slow", partial(slow_work.warn_if_slow, interval_s=0.01))
    observed = {}

    def block_until_reported_slow():
        # The inference stays busy until the slow-work watchdog has spoken.
        observed["warned"] = warned.wait(timeout=30)

    path = pdf_with_markers(tmp_path / "markers.pdf")
    try:
        with PdfiumNativeEvidenceSource(path) as native:
            page = native.extract_page(0)
            backend = MarkerBackend(blocker=block_until_reported_slow)
            facade = _facade(_paddle_pipeline(backend), SpySource(native), page)
            try:
                tokens = facade.recognize_page(None, 0, page.bbox, quality_policy="adaptive")
                assert observed["warned"] is True
                assert [token.text for token in tokens] == ["marker"]
                [request] = facade.coordinator.results
                assert request["status"] == "ok" and not request["failures"]
                assert not backend.cancelled.is_set()
            finally:
                facade.close()
    finally:
        logging.getLogger(slow_work.__name__).removeHandler(handler)


def test_paddle_explicit_cancel_still_works(tmp_path):
    started, released = threading.Event(), threading.Event()
    path = pdf_with_markers(tmp_path / "markers.pdf")
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)

        class CancellableBackend(MarkerBackend):
            def cancel(self):
                super().cancel()
                released.set()

        def block_until_cancelled():
            started.set()
            released.wait(timeout=30)

        backend = CancellableBackend(blocker=block_until_cancelled)
        token = CancellationToken()
        facade = _facade(_paddle_pipeline(backend), SpySource(native), page, cancellation_token=token)
        errors = []

        def run():
            try:
                facade.recognize_page(None, 0, page.bbox, quality_policy="adaptive")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            assert started.wait(timeout=30)
            assert token.cancel() is True
            thread.join(timeout=30)
            assert not thread.is_alive()
            assert backend.cancelled.is_set()
            assert errors and isinstance(errors[0], (WorkCancelled, ExtractionCancelled))
        finally:
            released.set()
            thread.join(timeout=30)
            facade.close()


def test_other_engines_raster_paths_unchanged(tmp_path):
    from structured_pdf_text.ocr.pipelines.easyocr.pipeline import EasyOCRPipeline
    from structured_pdf_text.ocr.pipelines.rapidocr.pipeline import RapidOCRPipeline
    from structured_pdf_text.ocr.pipelines.tesseract.pipeline import TesseractPipeline

    for pipeline in (EasyOCRPipeline, RapidOCRPipeline, TesseractPipeline, AuthorizedBackendPipeline):
        assert pipeline.raster_preparation is SOURCE_RASTER_PREPARATION
        assert not pipeline.raster_boundary_policy.confine_masked_border
    assert PaddlePipeline.raster_preparation is RASTER_PREPARATION

    # Even when the core states a page_crop origin, the shared strategy keeps
    # re-rendering canonical pixels from the source at the requested scale.
    path = pdf_with_markers(tmp_path / "markers.pdf", rotation=90)
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        backend = MarkerBackend()
        source = SpySource(native)
        pipeline = AuthorizedBackendPipeline(backend, selection=EngineSelection("custom"), render_scale=BASE_SCALE)
        facade = _facade(pipeline, source, page)
        try:
            region = BBox(5, 5, 60, 80)
            facade.recognize_page(None, 0, region, target_bbox=region, render_scale=BASE_SCALE * 1.5,
                                  selection_stage="region", raster_preparation="page_crop")
            expected = np.asarray(canonicalize_page_image(
                native.render_region(0, region, BASE_SCALE * 1.5, page.bbox, 90), 90))
            assert source.calls == [BASE_SCALE * 1.5]
            assert np.array_equal(backend.images[0], expected)
            [request] = facade.coordinator.results
            assert "raster_preparation" not in request["engine_diagnostics"]["custom"]["attempts"][0]
        finally:
            facade.close()
