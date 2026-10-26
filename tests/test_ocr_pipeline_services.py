from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import math
import threading
from types import SimpleNamespace

from PIL import Image
import pytest

from structured_pdf_text import ExtractorConfig, SecurityLimits
from structured_pdf_text import PdfTextExtractor
from structured_pdf_text import CancellationToken, ExtractionCancelled
from structured_pdf_text.document import (
    LayoutRegion,
    OcrToken,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
)
from structured_pdf_text.errors import FatalExtractionError, ResourceExhaustedExtractionError
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource
from structured_pdf_text.ocr.contracts import OCRBackendIdentity
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline, LegacyInjectedPipeline, adapt_injection
from structured_pdf_text.ocr.pipeline.authorization import union_contains
from structured_pdf_text.ocr.pipeline.budgets import BudgetBlocked, ResourcePool, WorkCancelled
from structured_pdf_text.ocr.pipeline.contracts import (
    EffectiveIdentity,
    EngineSelection,
    IntegrationProposal,
    OCRPipelineResult,
    PipelineDescriptor,
    ProposalKind,
    ResourceAllocation,
    TargetRef,
    TokenEvidence,
    WorkStatus,
)
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.pipeline.integrator import RegionTarget
from structured_pdf_text.ocr.pipeline.services import AuthorizedOCRServices
from ocr_pipeline_helpers import make_work, pdf_with_markers


class SpySource:
    def __init__(self, source=None, *, fail=None):
        self.source = source
        self.config = source.config if source is not None else ExtractorConfig()
        self.calls = []
        self.closed = 0
        self.fail = fail

    def render_region(self, page_index, bbox, scale, page_bbox=None, page_rotation=0):
        self.calls.append((page_index, bbox, scale, page_rotation))
        if self.fail:
            raise self.fail
        if self.source is not None:
            return self.source.render_region(page_index, bbox, scale, page_bbox, page_rotation)
        visual = bbox.rotate_to_visual(page_rotation, page_bbox.width, page_bbox.height)
        visual_width, visual_height = (
            (page_bbox.height, page_bbox.width)
            if page_rotation % 180 else (page_bbox.width, page_bbox.height)
        )
        w = math.ceil(visual_width*scale)-math.ceil(visual.x0*scale)-math.ceil((visual_width-visual.x1)*scale)
        h = math.ceil(visual_height*scale)-math.ceil(visual.y0*scale)-math.ceil((visual_height-visual.y1)*scale)
        return Image.new("RGB", (w,h), "red")

    def extract_embedded_image(self, *args, **kwargs):
        pytest.fail("Original image extraction must not bypass visibility composition")

    def extract_page(self, page_index):
        if self.source is None:
            raise ValueError("no native evidence")
        return self.source.extract_page(page_index)

    def close(self):
        self.closed += 1
        if self.source is not None:
            self.source.close()


class LegacyEngine:
    def __init__(self, *, fail_on=None):
        self.calls = 0
        self.closed = 0
        self.fail_on = fail_on

    # Deliberately lacks quality_policy, quality_variants and page_rotation.
    def recognize_page(self, image, page_index, page_bbox=None):
        self.calls += 1
        if self.calls == self.fail_on:
            raise TypeError("internal recognition bug")
        box = page_bbox
        return [OcrToken("R$ -12,34", BBox(box.x0+box.width*.2, box.y0+box.height*.2,
                                         box.x0+box.width*.8, box.y0+box.height*.8), .9, "pt-BR", SourceKind.OCR_REGION)]

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("rotation", [0,90,180,270])
@pytest.mark.parametrize("crop", [False,True])
def test_real_source_adapter_only_observes_authorized_pixels_and_canonical_geometry(tmp_path, rotation, crop):
    path = pdf_with_markers(tmp_path/"markers.pdf", rotation=rotation, crop=crop)
    with PdfiumNativeEvidenceSource(path) as native:
        native_page = native.extract_page(0)
        box = BBox(2,15,28,75) if crop else BBox(12,20,38,80)
        registry, request, auth = make_work(regions=(box,), page_bbox=native_page.bbox, rotation=rotation)
        source = SpySource(native)
        with AuthorizedOCRServices(source, registry, request.selection) as services:
            class InspectPixels(LegacyEngine):
                def recognize_page(self, image, page_index, page_bbox=None):
                    assert image.width < 100 and image.height < 160
                    assert image.getpixel((image.width//2,image.height//2)) == (255,0,0)
                    assert not hasattr(image, "base")
                    return super().recognize_page(image,page_index,page_bbox)
            engine = InspectPixels()
            pipeline = LegacyInjectedPipeline(engine, render_scale=1.375)
            result = pipeline.execute(request, services)
            assert result.status == WorkStatus.OK
            assert len(result.tokens) == 1
            assert union_contains(auth.regions, result.tokens[0].bbox)
            assert services.observations()["live_raster_bytes"] == 0
            assert engine.calls == len(source.calls) == 1
            assert result.proposals
            pipeline.close()
            pipeline.close()
            assert engine.closed == 1


def test_builtin_region_request_uses_page_recognizer_on_already_authorized_crop():
    page_bbox = BBox(0, 0, 200, 100)
    region_bbox = BBox(20, 10, 120, 70)
    selection = EngineSelection("fake")
    registry, request, authorization = make_work(
        page_bbox=page_bbox,
        regions=(region_bbox,),
        selection=selection,
        selection_stage="region",
    )
    source = SpySource()

    class Backend:
        identity = None
        capabilities = {"recognition": True}

        def __init__(self):
            self.page_calls = 0
            self.region_calls = 0

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            self.page_calls += 1
            assert image.size == (100, 60)
            assert page_bbox is None
            return [OcrToken("authorized crop", BBox(10, 10, 50, 25), .9, "pt-BR", SourceKind.OCR_REGION)]

        def recognize_region(self, image, page_index, region_bbox, *, page_bbox=None):
            self.region_calls += 1
            raise AssertionError("already-cropped pixels must not be cropped a second time")

    engine = Backend()
    pipeline = AuthorizedBackendPipeline(engine, selection=selection)
    with AuthorizedOCRServices(source, registry, selection) as services:
        result = pipeline.execute(request, services)
        assert result.status == WorkStatus.OK
        assert len(result.tokens) == 1
        assert union_contains(authorization.regions, result.tokens[0].bbox)
        assert engine.page_calls == 1
        assert engine.region_calls == 0


def test_typed_request_diagnostics_capture_each_attempt_without_consuming_page_state():
    class DiagnosticEngine(LegacyEngine):
        last_pass_count = 3
        last_batch_count = 2
        last_attempt_errors = ["optional attempt failed"]
        last_selected_variant = "baseline"
        last_candidate_metrics = {"baseline": {"score": .9}}
        def consume_page_diagnostics(self):
            raise AssertionError("request snapshots must not consume page accumulators")

    registry, request, _ = make_work(regions=(BBox(10, 10, 40, 40), BBox(60, 10, 90, 40)))
    engine = DiagnosticEngine()
    with AuthorizedOCRServices(SpySource(), registry, request.selection) as services:
        result = LegacyInjectedPipeline(engine).execute(request, services)
    assert result.counters["passes"] == 6
    assert result.counters["batches"] == 4
    attempts = result.engine_diagnostics["custom"]["attempts"]
    assert len(attempts) == 2 and attempts[0]["attempt_id"] != attempts[1]["attempt_id"]
    assert attempts[0]["metrics"]["attempt_errors"] == ("optional attempt failed",)
    engine.last_candidate_metrics["baseline"]["score"] = .1
    assert attempts[0]["diagnostics"]["candidate_metrics"]["baseline"]["score"] == .9


@pytest.mark.parametrize("replacement_text,accepted", [("1 NOTA ABC -0,01", True), ("1 NOTA ABC 0,01", False)])
def test_production_footnote_refinement_uses_authorized_scale_and_typed_conservation(replacement_text, accepted):
    from structured_pdf_text.api import _refine_small_footnote_tokens
    from structured_pdf_text.config import OcrQualityPolicy

    tokens = [
        OcrToken("linha principal", BBox(10, 10, 130, 20), .9, "pt-BR", SourceKind.OCR_PAGE),
        OcrToken("outra linha", BBox(10, 40, 130, 50), .9, "pt-BR", SourceKind.OCR_PAGE),
        OcrToken("1 NOTA ABC -0,01", BBox(10, 85, 130, 89), .5, "pt-BR", SourceKind.OCR_PAGE),
    ]
    class Engine:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            return [replace(tokens[-1], text=replacement_text, confidence=.99)]
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="footnote", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy=OcrQualityPolicy.ADAPTIVE, security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        output, count = _refine_small_footnote_tokens(
            engine=facade, page_index=0, page_bbox=coordinator.page_bbox, tokens=tokens,
            quality_policy="adaptive", region_renderer=lambda *args: pytest.fail("render must pass through services"),
            base_scale=1.5,
        )
        assert count == int(accepted)
        assert output[:2] == tokens[:2]
        assert output[-1].text == (replacement_text if accepted else tokens[-1].text)
        assert output[-1].provenance == ("footnote_high_scale" if accepted else None)
        assert len(source.calls) == 1 and source.calls[0][2] == 3.
        work = coordinator.diagnostics()["requests"][0]
        assert work["goal"] == "note" and work["mandatory"] is False
        decision = work["integration_decisions"][0]
        assert decision["kind"] == "replace_tokens"
        assert decision["outcome"] == ("accepted" if accepted else "not_selected")
        assert not coordinator._deferred_tokens
    finally:
        facade.close()
        pipeline.close()


@pytest.mark.parametrize("replacement_text,accepted", [("NOTA ABC -0,01", True), ("NOTA", False)])
def test_production_weak_line_recovery_commits_only_selected_authorized_candidate(replacement_text, accepted):
    from structured_pdf_text.api import _recover_weak_ocr_regions
    from structured_pdf_text.config import OcrQualityPolicy, OcrQualityThresholds
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    old = OcrToken("NOTA ABC -0,01", BBox(20, 20, 130, 30), .35, "pt-BR", SourceKind.OCR_PAGE)
    class Engine:
        calls = 0
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("optional first candidate failed")
            return [replace(old, text=replacement_text, confidence=.99)]
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="weak", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy=OcrQualityPolicy.ADAPTIVE, security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        output, passes, _batches, stats = _recover_weak_ocr_regions(
            facade, Image.new("RGB", (200, 100)), 0, coordinator.page_bbox, [old],
            reconstruct_ocr_lines([old], 0, coordinator.page_bbox), "adaptive", OcrQualityThresholds(),
        )
        assert len(output) == 1 and output[0].text == (replacement_text if accepted else old.text)
        assert stats["weak-region-1"]["accepted"] is accepted
        assert passes >= 1
        requests = coordinator.diagnostics()["requests"]
        assert len(requests) >= 2 and all(not request["mandatory"] for request in requests)
        decisions = [decision for request in requests for decision in request["integration_decisions"]]
        assert all(decision["kind"] == "replace_tokens" for decision in decisions)
        assert sum(decision["outcome"] == "accepted" for decision in decisions) == int(accepted)
        assert coordinator.work_summary()["degraded"] is True
        assert not coordinator.work_summary()["mandatory_failures"]
        assert not coordinator._deferred_tokens
    finally:
        facade.close()
        pipeline.close()


@pytest.mark.parametrize("original_text,accepted", [("12,34", True), ("-12,34", False)])
def test_production_critical_refinement_composes_selected_evidence_in_place(original_text, accepted):
    from structured_pdf_text.api import _refine_critical_data_tokens

    before = OcrToken("cabeçalho", BBox(5, 5, 40, 15), .99, "pt-BR", SourceKind.OCR_PAGE)
    original = OcrToken(original_text, BBox(50, 20, 90, 30), .4, "pt-BR", SourceKind.OCR_PAGE)
    after = OcrToken("rodapé", BBox(5, 80, 40, 90), .99, "pt-BR", SourceKind.OCR_PAGE)
    class Engine:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            return [OcrToken("R$ 12,34", original.bbox, .8, "pt-BR", SourceKind.OCR_REGION)]
        def recognize_direct(self, image, page_index, page_bbox=None, **kwargs):
            return [OcrToken("R$", BBox(50, 20, 60, 30), .99, "pt-BR", SourceKind.OCR_REGION),
                    OcrToken("12,34", BBox(61, 20, 90, 30), .99, "pt-BR", SourceKind.OCR_REGION)]
    pipeline = LegacyInjectedPipeline(Engine(), capabilities=("page", "region", "direct_recognition"))
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="critical", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        output, count = _refine_critical_data_tokens(
            engine=facade, page_image=Image.new("RGB", (200, 100)), page_index=0,
            page_bbox=coordinator.page_bbox, tokens=[before, original, after],
            lines=[SimpleNamespace(text="VALOR:", bbox=BBox(5, 20, 40, 30))],
            quality_policy="adaptive", base_scale=2.,
            region_renderer=lambda *args: pytest.fail("critical crops must use authorized services"),
        )
        assert count == int(accepted) and len(output) == 3
        assert output[0] is before and output[2] is after
        assert output[1].text == ("R$ 12,34" if accepted else original_text)
        assert output[1].provenance == ("critical_data_refinement" if accepted else None)
        assert all(call[2] == 3.5 for call in source.calls) and len(source.calls) == 2
        work = coordinator.diagnostics()["requests"]
        assert all(item["goal"] == "critical_data" and not item["mandatory"] for item in work)
        assert work[0]["integration_decisions"][0]["outcome"] == "not_selected"
        assert work[1]["integration_decisions"][0]["outcome"] == ("accepted" if accepted else "not_selected")
        if accepted:
            lineage = work[1]["output_evidence_lineage"][0]
            assert lineage["evidence_id"].startswith("joined:") and len(lineage["derived_from_ids"]) == 3
            assert output[1].ocr_provenance.refinement_kind == "critical_data_refinement"
        assert not coordinator._deferred_tokens
    finally:
        facade.close()
        pipeline.close()


@pytest.mark.parametrize("has_region", [True, False])
@pytest.mark.parametrize("accepted", [True, False])
def test_dedicated_figure_refinement_commits_stream_and_owner_together(has_region, accepted):
    from structured_pdf_text.api import _refine_figure_ocr
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    box = BBox(20, 20, 120, 70)
    old = OcrToken("figura confiável completa", BBox(30, 30, 110, 40), .4, "pt-BR", SourceKind.OCR_PAGE)
    outside = OcrToken("preservado", BBox(5, 5, 70, 15), .99, "pt-BR", SourceKind.OCR_PAGE)
    tokens = [outside, old]
    region = LayoutRegion("figure-owner", RegionKind.FIGURE, box, .9, [], [old], RegionQuality(RegionDecision.OCR_REGION))
    region.ocr_lines = reconstruct_ocr_lines([old], 0, BBox(0, 0, 200, 100))
    original_lines = list(region.ocr_lines)
    class Engine:
        def recognize_page(self, *args, **kwargs):
            return [replace(old, text=old.text if accepted else "texto", confidence=.99)]
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="figures", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    page = SimpleNamespace(bbox=coordinator.page_bbox, objects=SimpleNamespace(images=[SimpleNamespace(bbox=box)]))
    try:
        results = _refine_figure_ocr(
            facade, Image.new("RGB", (200, 100)), page, 0, [], "adaptive",
            integration_tokens=tokens, integration_regions=[region] if has_region else [],
        )
        assert len(results) == 1 and bool(results[0][2]) is accepted
        assert tokens[0] is outside and len(tokens) == 2
        assert tokens[1].text == old.text
        assert tokens[1].provenance == ("figure_ocr" if accepted else None)
        if accepted and has_region:
            assert region.ocr_tokens == [tokens[1]]
        else:
            assert region.ocr_tokens == [old] and region.ocr_lines == original_lines
        assert all(call[1] == box for call in source.calls)
        work = coordinator.diagnostics()["requests"]
        assert all(item["goal"] == "figure" and item["mandatory"] is False for item in work)
        decisions = [decision for item in work for decision in item["integration_decisions"]]
        assert sum(item["outcome"] == "accepted" for item in decisions) == int(accepted)
        assert all(item["kind"] == "associate_figure" for item in decisions)
        assert not coordinator._deferred_tokens
    finally:
        facade.close()
        pipeline.close()


@pytest.mark.parametrize("defect", [None, "coarsened_grid", "scope", "value", "unobserved", "owner", "duplicate_grid", "stale"])
def test_visual_table_refinement_validates_core_draft_before_atomic_commit(monkeypatch, defect):
    from copy import deepcopy
    from structured_pdf_text import api
    from structured_pdf_text.document import StructuredTable, TableCell, TableFragment, TableMethod
    from structured_pdf_text.ocr.pipeline.table_integration import table_snapshot

    box = BBox(20, 20, 120, 60)
    old = OcrToken("R$ -12,34", BBox(80, 30, 110, 40), .8, "pt-BR", SourceKind.OCR_REGION)
    table = StructuredTable("visual-table", [TableFragment(0, box, 0, 1)], [
        TableCell(0, 0, 1, 1, BBox(20, 20, 70, 60), "", [], .35),
        TableCell(0, 1, 1, 1, BBox(70, 20, 120, 60), old.text, [old], .55),
    ], 2, 1, .8, TableMethod.VISUAL_MODEL)
    original_snapshot = table_snapshot(table)
    class Engine:
        def recognize_page(self, *args, **kwargs):
            return [OcrToken("AÇÃO", BBox(30, 30, 60, 40), .99, "pt-BR", SourceKind.OCR_REGION),
                    replace(old, text="R$ 12,34" if defect == "value" else old.text, confidence=.99)]
    def builder(*args, **kwargs):
        candidate = deepcopy(table)
        left, right = kwargs["tokens"]
        candidate.cells[0].tokens = [left]
        candidate.cells[0].text = left.text
        candidate.cells[1].tokens = [right]
        candidate.cells[1].text = right.text
        if defect == "coarsened_grid":
            candidate.column_count = 1
            candidate.cells = candidate.cells[:1]
        elif defect == "scope":
            candidate.page_fragments[0].bbox = BBox(0, 0, 200, 100)
        elif defect == "unobserved":
            candidate.cells[0].tokens = [replace(left, text="inventado")]
            candidate.cells[0].text = "inventado"
        elif defect == "owner":
            candidate.table_id = "different-table"
        elif defect == "duplicate_grid":
            candidate.cells.append(deepcopy(candidate.cells[0]))
        elif defect == "stale":
            table.confidence = .7
        return candidate
    monkeypatch.setattr(api, "detect_visual_table_in_region", builder)
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="visual", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        result = api._refine_visual_table_ocr(facade, Image.new("RGB", (200, 100)),
                                             SimpleNamespace(bbox=coordinator.page_bbox), 0, table, [], "adaptive")
        accepted = defect in (None, "coarsened_grid")
        assert bool(result) is accepted
        if accepted:
            assert result[0] is table and table.cells[0].text == "AÇÃO"
            assert table.cells[1].text == old.text and result[1] and result[2]
            assert all(token.derived_from_ids for cell in table.cells for token in cell.tokens)
            assert table.cells[0].tokens[0].owner_id == "table-cell:visual-table:0:0"
        elif defect == "stale":
            expected = list(original_snapshot)
            expected[3] = .7
            assert table_snapshot(table) == tuple(expected)
        else:
            assert table_snapshot(table) == original_snapshot
        requests = coordinator.diagnostics()["requests"]
        assert all(item["goal"] == "table" and not item["mandatory"] for item in requests)
        assert all(call[1] == box for call in source.calls)
        assert sum(decision["outcome"] == "accepted" for item in requests for decision in item["integration_decisions"]) == int(accepted)
        assert not coordinator._deferred_tokens
    finally:
        facade.close()
        pipeline.close()


def test_public_table_cell_helper_supports_authorized_pipeline_without_mutating_input():
    from structured_pdf_text.tables.cell_ocr import ocr_table_cells
    from structured_pdf_text.document import StructuredTable, TableCell, TableMethod

    cell = TableCell(0, 0, 1, 1, BBox(20, 10, 120, 60), "", [], .0)
    neighbor = TableCell(0, 1, 1, 1, BBox(120, 10, 180, 60), "preserved", [], .99)
    table = StructuredTable("public-table", [], [cell, neighbor], 2, 1, .8, TableMethod.STRICT_GRID)
    class Engine:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            return [OcrToken("AÇÃO -0,01", BBox(30, 20, 100, 30), .95, "pt-BR", SourceKind.OCR_REGION)]
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="public-cells", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        output = ocr_table_cells(table, None, coordinator.page_bbox, ocr_facade=facade)
        assert cell.text == "" and cell.tokens == []
        assert output.cells[0].text == "AÇÃO -0,01" and output.cells[0].tokens[0].derived_from_ids
        assert output.cells[1] is neighbor
        assert all(call[1] == cell.bbox for call in source.calls)
        with pytest.raises(ValueError, match="geometry"):
            ocr_table_cells(table, None, BBox(0, 0, 300, 200), ocr_facade=facade)
        with pytest.raises(ValueError, match="Choose"):
            ocr_table_cells(table, None, coordinator.page_bbox, lambda _: "unsafe", ocr_facade=facade)
    finally:
        facade.close()
        pipeline.close()


def test_optional_weak_refinement_cannot_replace_part_of_an_overlapping_outside_token():
    from structured_pdf_text.api import _recover_weak_ocr_regions
    from structured_pdf_text.config import OcrQualityThresholds

    weak = OcrToken("weak line", BBox(50, 20, 100, 30), .3, "pt-BR", SourceKind.OCR_PAGE)
    outside = OcrToken("wide trusted evidence", BBox(10, 20, 180, 30), .99, "pt-BR", SourceKind.OCR_PAGE)
    class Engine:
        def recognize_page(self, *args, **kwargs):
            pytest.fail("partial evidence must not authorize inference or replacement")
    pipeline = LegacyInjectedPipeline(Engine())
    source = SpySource()
    coordinator = PageCoordinator(pipeline, source, extraction_id="outside", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0,
                                  page_bbox=BBox(0, 0, 200, 100), rotation=0,
                                  quality_policy="adaptive", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    try:
        output, passes, batches, stats = _recover_weak_ocr_regions(
            facade, None, 0, coordinator.page_bbox, [weak, outside],
            [SimpleNamespace(bbox=weak.bbox, tokens=[weak])], "adaptive", OcrQualityThresholds(),
        )
        assert output == [outside, weak]
        assert passes == batches == 0 and source.calls == []
        assert stats["weak-region-1"]["reason"] == "existing_evidence_extends_outside_authorized_crop"
        assert not coordinator.results
    finally:
        facade.close()
        pipeline.close()


def test_page_facade_projects_effective_identity_to_legacy_backend_identity():
    selection = EngineSelection("rapidocr", "openvino")
    identity = EffectiveIdentity(
        selection,
        runtime="openvino",
        profile="pp-ocrv6-medium",
        language="pt-BR",
        device="cpu",
        package_versions={"rapidocr": "3.9.2"},
        artifact_hashes={"recognizer.onnx": "a" * 64},
        extra={"detector": "PP-OCRv6 medium", "nested": {"setting": "value"}},
    )
    facade = OCRPageFacade(SimpleNamespace(
        last_result=SimpleNamespace(identity=identity),
        pipeline=SimpleNamespace(identity=None),
    ))

    projected = facade.identity
    assert projected.engine == "rapidocr"
    assert projected.runtime == "openvino"
    assert projected.profile == "pp-ocrv6-medium"
    assert projected.package_versions == {"rapidocr": "3.9.2"}
    assert projected.artifact_hashes == {"recognizer.onnx": "a" * 64}
    assert projected.extra["provider"] == "openvino"
    import json
    assert json.loads(json.dumps(projected.extra))["nested"] == {"setting": "value"}


def test_adapter_does_not_infer_detector_from_candidate_label(tmp_path):
    path = pdf_with_markers(tmp_path/"provenance.pdf")
    with PdfiumNativeEvidenceSource(path) as native:
        page = native.extract_page(0)
        registry, request, _ = make_work(
            page_bbox=page.bbox, regions=(page.bbox,),
            selection=EngineSelection("easyocr"),
        )
        source = SpySource(native)

        class CandidateEngine(LegacyEngine):
            def recognize_page(self, image, page_index, page_bbox=None):
                self.calls += 1
                return [OcrToken(
                    "candidate text", BBox(20, 20, 80, 40), .9, "pt-BR",
                    SourceKind.OCR_REGION, provenance="candidate:dbnet18",
                )]

        with AuthorizedOCRServices(source, registry, request.selection) as services:
            result = LegacyInjectedPipeline(
                CandidateEngine(), selection=request.selection,
            ).execute(request, services)
        from structured_pdf_text.ocr.pipeline.converters import to_domain
        token = to_domain(result.tokens[0])
        assert token.ocr_provenance.engine == "easyocr"
        assert token.ocr_provenance.candidate_id == "dbnet18"
        assert token.ocr_provenance.detector is None


def test_legacy_diagnostics_are_projected_to_common_finite_data():
    from dataclasses import dataclass
    from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector

    @dataclass(frozen=True)
    class Quality:
        score: float
        reasons: tuple[str, ...]

    class Engine:
        last_baseline_quality = Quality(.75, ("weak_confidence",))
        last_candidate_metrics = {"baseline": {"quality_score": .75}}

        def consume_page_diagnostics(self):
            return {
                "easyocr_calls": 2,
                "easyocr_fallback_count": 1,
                "easyocr_fallback_reasons": ["primary_failed"],
            }

    diagnostics = LegacyDiagnosticsProjector.consume(
        Engine(), engine_name="easyocr", provider=None,
    )
    assert diagnostics["baseline_quality"] == {"score": .75, "reasons": ["weak_confidence"]}
    assert diagnostics["candidate_metrics"]["baseline"]["quality_score"] == .75
    assert diagnostics["fallback_count"] == 1
    assert diagnostics["fallback_rate"] == .5
    assert diagnostics["fallback_reasons"] == ["primary_failed"]
    assert LegacyDiagnosticsProjector.public_compatibility_aliases(diagnostics) == {
        "easyocr_fallback_used": True,
        "easyocr_fallback_reason": "primary_failed",
    }


def test_historical_easyocr_diagnostic_aliases_project_from_engine_neutral_values():
    from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector

    assert LegacyDiagnosticsProjector.public_compatibility_aliases({
        "fallback_count": 0,
        "fallback_reasons": [],
    }) == {
        "easyocr_fallback_used": False,
        "easyocr_fallback_reason": None,
    }


def test_pipeline_descriptor_does_not_advertise_unavailable_engine_capabilities():
    from structured_pdf_text.ocr.contracts import OCRCapabilities
    from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline

    class Backend:
        capabilities = OCRCapabilities(
            detection=True, recognition=True, line_orientation=True,
            page_orientation=False, quadrilateral_boxes=True,
            per_token_confidence=True, direct_recognition=True,
            multiple_detectors=False,
        )

    pipeline = AuthorizedBackendPipeline(
        Backend(), selection=EngineSelection("easyocr"),
        capabilities=("page", "region", "polygons", "direct_recognition", "multiple_detectors"),
    )
    descriptor = pipeline.descriptor
    assert descriptor.declared_capabilities == (
        "page", "region", "polygons", "direct_recognition", "multiple_detectors",
    )
    assert descriptor.available_capabilities == ("page", "region", "polygons", "direct_recognition")


def test_api_injected_engine_uses_authorized_service_and_pipeline_lifecycle(tmp_path):
    path = pdf_with_markers(tmp_path/"api-authorized.pdf")
    engine = LegacyEngine()
    extractor = PdfTextExtractor(ExtractorConfig(mode="ocr", language="pt"), ocr_engine=engine)
    document = extractor.extract(path)
    facade = extractor.ocr_engine
    assert facade is not None
    events = facade.coordinator.services.events
    assert any(event["kind"] == "raster" for event in events)
    assert any(event["kind"] == "attempt" for event in events)
    assert engine.calls == 1
    assert document.diagnostics.facts["ocr_engine_identity"]["engine"] == "custom"
    extractor.close()
    assert engine.closed == 1


def test_cancellation_interrupts_active_injected_recognition_and_publishes_no_document(tmp_path):
    path = pdf_with_markers(tmp_path/"cancel-api.pdf")
    started = threading.Event()
    released = threading.Event()

    class BlockingEngine(LegacyEngine):
        def recognize_page(self, image, page_index, page_bbox=None):
            started.set()
            released.wait()
            return super().recognize_page(image, page_index, page_bbox)
        def cancel(self):
            released.set()

    token = CancellationToken()
    extractor = PdfTextExtractor(ExtractorConfig(mode="ocr", language="pt"), ocr_engine=BlockingEngine())
    errors = []
    def run_extract():
        try:
            extractor.extract(path, cancellation_token=token)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run_extract)
    thread.start()
    assert started.wait(timeout=5)
    assert token.cancel() is True
    assert token.cancel() is False
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], ExtractionCancelled)
    extractor.close()


def test_union_denies_gap_before_render_and_inference_and_rejects_external_outputs(tmp_path):
    regions = (BBox(10,20,40,80),BBox(150,20,180,80))
    registry, request, auth = make_work(regions=regions)
    with PdfiumNativeEvidenceSource(pdf_with_markers(tmp_path/"gap.pdf")) as native:
        source = SpySource(native)
        with AuthorizedOCRServices(source,registry,request.selection) as services:
            engine = LegacyEngine()
            def denied_consumer():
                with services.render(request,auth.authorization_id,BBox(10,20,180,80),scale=1) as lease:
                    engine.recognize_page(lease.image,0,lease.frame.bbox)
            with pytest.raises(ValueError): denied_consumer()
            assert source.calls == []
            assert engine.calls == 0
            for region, expected in zip(regions,((255,0,0),(0,0,255))):
                with services.render(request,auth.authorization_id,region,scale=1) as lease:
                    assert lease.image.getpixel((lease.image.width//2,lease.image.height//2)) == expected
            for box,polygon in ((BBox(20,30,170,40),None),(BBox(20,30,30,40),(Point(20,30),Point(90,30),Point(30,40)))):
                token = TokenEvidence("t",auth.authorization_id,"a","must reject",box,polygon=polygon)
                with pytest.raises(ValueError): services.validate_token(request,token)
            denied = replace(request,authorizations=(replace(auth,regions=(BBox(0,0,200,100),)),))
            engine = LegacyEngine()
            with pytest.raises(ValueError): LegacyInjectedPipeline(engine).execute(denied,services)
            assert engine.calls == 0


def test_union_allows_adjacent_rectangles_but_not_a_sliver_gap():
    assert union_contains((BBox(0,0,10,10),BBox(10,0,20,10)),BBox(0,0,20,10))
    assert not union_contains((BBox(0,0,10,10),BBox(10.000000001,0,20,10)),BBox(0,0,20,10))
    assert not union_contains((BBox(0,0,10,4),BBox(0,6,10,10)),BBox(0,0,10,10))


def test_nonzero_canonical_origin_is_localized_without_modifying_source(tmp_path):
    box = BBox(112,220,138,280)
    registry,request,auth = make_work(page_bbox=BBox(100,200,300,300),regions=(box,))
    with PdfiumNativeEvidenceSource(pdf_with_markers(tmp_path/"origin.pdf")) as native:
        source = SpySource(native)
        with AuthorizedOCRServices(source,registry,request.selection) as services:
            with services.render(request,auth.authorization_id,box,scale=1.25) as lease:
                assert lease.image.getpixel((10,10)) == (255,0,0)
                assert source.calls[0][1] == BBox(12,20,38,80)
                point = Point(lease.image.width*.5,lease.image.height*.5)
                observed = lease.frame.page_point_to_pixel(lease.frame.point_to_page(point))
                assert (observed.x,observed.y) == pytest.approx((point.x,point.y))


def test_region_refinement_scale_and_rotation_reach_authorized_renderer(tmp_path):
    from structured_pdf_text.config import OcrQualityPolicy
    from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
    from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
    from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
    from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementRequest

    region = BBox(20, 10, 80, 50)
    registry, request, _ = make_work(regions=(region,))
    source = SpySource()

    class PixelCoordinatesEngine:
        def recognize_page(self, image, page_index, page_bbox=None, *, quality_policy=None):
            assert image.size == (80, 120)  # 2x render followed by 90° service rotation.
            assert page_bbox is None  # Frame owns pixel-to-page conversion.
            return [OcrToken(
                "regional", BBox(20, 20, 40, 40), .95, "pt-BR", SourceKind.OCR_REGION,
            )]

    pipeline = AuthorizedBackendPipeline(
        PixelCoordinatesEngine(), selection=request.selection, render_scale=1.0,
    )
    coordinator = PageCoordinator(
        pipeline, source, extraction_id=request.scope.extraction_id,
        document_id=request.scope.document_id, selection=request.selection,
        page_index=request.scope.page_index, page_bbox=request.scope.page_geometry.bbox,
        rotation=request.scope.page_geometry.rotation,
        quality_policy=OcrQualityPolicy.BASELINE, security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    image = Image.new("RGB", (200, 100), "white")
    try:
        result = OcrRegionRefiner(facade).refine(
            image, 0, BBox(0, 0, 200, 100), RegionRefinementRequest(
                bbox=region, scale_factors=(2.0,), rotations=(90.0,),
                quality_policy="baseline", page_rotation=0,
            ),
        )
        assert result.status == "ok"
        assert len(result.tokens) == 1
        assert region.x0 <= result.tokens[0].bbox.x0 < result.tokens[0].bbox.x1 <= region.x1
        assert region.y0 <= result.tokens[0].bbox.y0 < result.tokens[0].bbox.y1 <= region.y1
        assert source.calls == [(0, region, 2.0, 0)]
        events = coordinator.services.events
        assert any(event["kind"] == "raster_transform" for event in events)
        assert any(event["kind"] == "raster" and event["details"]["requested_scale"] == 2.0 for event in events)
    finally:
        image.close()
        facade.close()


def test_page_coordinator_applies_typed_region_proposal_to_core_bound_target():
    from structured_pdf_text.config import OcrQualityPolicy

    selection = EngineSelection("custom")
    page = BBox(0, 0, 200, 100)
    target_ref = TargetRef("region", "paragraph-1", 3)
    original = OcrToken("Total 42", BBox(20, 20, 60, 30), .7, "pt-BR", SourceKind.OCR_REGION)
    region = LayoutRegion(
        "paragraph-1", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    binding = RegionTarget(target_ref, region, ["old-evidence"], {"old-evidence"}, visible_target=True)

    class TypedPipeline:
        descriptor = PipelineDescriptor(selection, ("region",))
        render_scale = 1.0

        def execute(self, request, services):
            auth = request.authorizations[0]
            with services.render(request, auth.authorization_id, region.bbox, scale=1.0):
                attempt = services.consume_attempt(request)
            token = TokenEvidence(
                "new-evidence", auth.authorization_id, attempt, "Total 42", BBox(20, 20, 60, 30),
                confidence_normalized=.95, normalization="domain_0..1",
            )
            proposal = IntegrationProposal(
                "replace-paragraph", auth.authorization_id, request.scope.target,
                ProposalKind.REPLACE, (token.token_id,), "core-approved region refinement",
                ("old-evidence",),
            )
            result = OCRPipelineResult(
                request.request_id, request.scope, request.selection, EffectiveIdentity(selection),
                WorkStatus.OK, (auth.authorization_id,), (token,), (proposal,),
            )
            services.validate_result(request, result)
            return result

        def close(self):
            pass

    coordinator = PageCoordinator(
        TypedPipeline(), SpySource(), extraction_id="extract-typed", document_id="doc-typed",
        selection=selection, page_index=0, page_bbox=page, rotation=0,
        quality_policy=OcrQualityPolicy.ADAPTIVE, security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    image = Image.new("RGB", (200, 100), "white")
    try:
        accepted = facade.recognize_page(
            image, 0, page_bbox=page, integration_targets=(binding,),
        )
        assert len(accepted) == 1 and accepted[0].confidence == .95
        assert region.ocr_tokens == list(accepted)
        assert binding.evidence_ids == ["new-evidence"]
        assert coordinator.results[0]["integrated_token_count"] == 1
        assert coordinator.results[0]["integration_decisions"][0]["outcome"] == "accepted"
    finally:
        image.close()
        facade.close()


@pytest.mark.parametrize(
    ("region_kind", "expected_proposal"),
    [(RegionKind.TEXT, "complement_region"), (RegionKind.FIGURE, "associate_figure")],
)
def test_selected_region_candidate_is_integrated_once_after_variant_ranking(region_kind, expected_proposal):
    from structured_pdf_text.config import OcrQualityPolicy
    from structured_pdf_text.api import _recover_selected_regions

    selection = EngineSelection("custom")
    page_bbox = BBox(0, 0, 200, 100)
    region_bbox = BBox(20, 10, 120, 70)
    region = LayoutRegion(
        "paragraph-candidate",
        region_kind,
        region_bbox,
        .9,
        [],
        [],
        RegionQuality(RegionDecision.OCR_REGION),
    )

    class CandidateBackend:
        identity = None
        capabilities = {"recognition": True}

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            text = "selected longer candidate" if image.width >= 150 else "short"
            return [OcrToken(
                text,
                BBox(10, 10, 55, 25),
                .9,
                "pt-BR",
                SourceKind.OCR_REGION,
            )]

    source = SpySource()
    pipeline = AuthorizedBackendPipeline(CandidateBackend(), selection=selection)

    class TwoScaleRecoveryPolicy:
        def scale_factors(self, stage, **kwargs):
            return (1.0, 2.0) if stage == "region" else (1.0,)

    pipeline.recovery_policy = TwoScaleRecoveryPolicy()
    coordinator = PageCoordinator(
        pipeline,
        source,
        extraction_id="extract-selected-region",
        document_id="doc-selected-region",
        selection=selection,
        page_index=0,
        page_bbox=page_bbox,
        rotation=0,
        quality_policy=OcrQualityPolicy.ADAPTIVE,
        security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    image = Image.new("RGB", (200, 100), "white")
    try:
        tokens, _passes, _batches, stats = _recover_selected_regions(
            facade,
            image,
            0,
            page_bbox,
            [region],
            quality_variants=True,
            quality_policy="adaptive",
        )

        assert [token.text for token in tokens] == ["selected longer candidate"]
        assert [token.text for token in region.ocr_tokens] == ["selected longer candidate"]
        assert len(region.ocr_lines) == 1
        assert stats[region.region_id]["selected_scale_factor"] == 2.0
        assert sum(item["integrated_token_count"] for item in coordinator.results) == 1
        assert sum(not item["deferred_integration"] for item in coordinator.results) == 1
        selected_result = next(item for item in coordinator.results if item["integrated_token_count"])
        assert selected_result["integration_decisions"][0]["kind"] == expected_proposal
    finally:
        image.close()
        facade.close()


@pytest.mark.parametrize(
    ("original_text", "recognized", "confidence", "expected_applied"),
    [("R$ -0012", "R$ -0012", .95, True), ("R$ -0012", "R$ 0012", .95, False),
     ("", "1", .99, False), ("", "Ação", .95, True)],
)
def test_production_cell_recovery_uses_typed_selection_and_preserves_evidence(
    original_text, recognized, confidence, expected_applied,
):
    from structured_pdf_text.api import _refine_table_cells_ocr
    from structured_pdf_text.document import EvidenceRef, TableCell, TextToken

    page = BBox(0, 0, 200, 100)
    box = BBox(20, 10, 100, 50)
    source_token = TextToken(
        original_text, BBox(25, 15, 70, 25),
        [EvidenceRef(SourceKind.NATIVE_PDF, 0, "native-source")], .4, original_text,
    )
    cell = TableCell(1, 0, 1, 1, box, original_text, [source_token] if original_text else [], .4)
    table = SimpleNamespace(table_id="cell-table", cells=[cell], header_rows=())

    class CellBackend:
        identity = None
        capabilities = {"recognition": True, "direct_recognition": True}

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            assert page_bbox is None
            return [OcrToken(recognized, BBox(10, 10, 40, 25), .8, "pt-BR", SourceKind.OCR_REGION)]

        def recognize_direct(self, image, page_index, page_bbox=None, **kwargs):
            return [OcrToken(recognized, BBox(10, 10, 40, 25), confidence, "pt-BR", SourceKind.OCR_REGION)]

    selection = EngineSelection("custom")
    pipeline = AuthorizedBackendPipeline(
        CellBackend(), selection=selection, capabilities=("page", "region", "direct_recognition"),
    )
    source = SpySource()
    coordinator = PageCoordinator(
        pipeline, source, extraction_id="cell-extract", document_id="cell-doc",
        selection=selection, page_index=0, page_bbox=page, rotation=0,
        quality_policy="adaptive", security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    try:
        count = _refine_table_cells_ocr(
            engine=facade, page_image=None, page_index=0, page_bbox=page,
            tables=[table], quality_policy="adaptive", base_scale=1.0,
            region_renderer=lambda *_args: pytest.fail("cell OCR must render through services"),
        )
        assert count == int(expected_applied)
        assert cell.text == (recognized if expected_applied else original_text)
        assert source_token.evidence_id is None
        assert len(source.calls) == 3
        assert [call[2] for call in source.calls] == [1.75, 1.75, 3.5]
        assert all(call[1] == box for call in source.calls)
        assert all(entry["goal"] == "cell" for entry in coordinator.results)
        assert all(entry["integration_decisions"][0]["kind"] == "correct_cell" for entry in coordinator.results)
        assert not coordinator._deferred_tokens
        if expected_applied:
            assert cell.tokens[0].transformation_type == "table_cell_ocr"
            assert cell.tokens[0].derived_from_ids
            assert sum(entry["integrated_token_count"] for entry in coordinator.results) == 1
    finally:
        facade.close()


@pytest.mark.parametrize("fail_first", [False, True])
def test_extractor_accounts_for_cell_only_ocr_and_keeps_attempt_failures(tmp_path, monkeypatch, fail_first):
    from reportlab.pdfgen import canvas
    import structured_pdf_text.api as api
    from structured_pdf_text.document import EvidenceRef, StructuredTable, TableCell, TableFragment, TableMethod, TextToken

    pdf = tmp_path / "native-cell.pdf"
    writer = canvas.Canvas(str(pdf), pagesize=(240, 240))
    for index in range(8):
        writer.drawString(20, 220 - index * 20, "Texto nativo suficiente para manter a pagina sem OCR inicial.")
    writer.save()
    token = TextToken(
        "Valor -12,34", BBox(25, 25, 95, 40),
        [EvidenceRef(SourceKind.NATIVE_PDF, 0, "cell-source")], .4, "Valor -12,34",
    )
    cell = TableCell(0, 0, 1, 1, BBox(20, 20, 120, 60), token.text, [token], .4)
    table = StructuredTable("weak-cell", [TableFragment(0, cell.bbox, 0, 1)], [cell], 1, 1, .6, TableMethod.STRICT_GRID)
    monkeypatch.setattr(api, "assess_region_recovery", lambda *_args: SimpleNamespace(
        reasons=(), region_ids=(), promote_page_ocr=False, bad_region_ratio=0., bad_area_ratio=0.,
    ))
    monkeypatch.setattr(api, "detect_tables_native", lambda *_args, **_kwargs: [table])

    class CellOnlyEngine:
        def __init__(self):
            self.calls = 0

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            self.calls += 1
            if fail_first and self.calls == 1:
                raise RuntimeError("first cell attempt failed")
            assert page_bbox == cell.bbox
            return [OcrToken("Valor -12,34", BBox(25, 25, 95, 40), .95, "pt-BR", SourceKind.OCR_REGION)]

    backend = CellOnlyEngine()
    config = ExtractorConfig(
        mode="balanced", enable_table_cell_ocr=True, enable_tables=True, enable_layout=False,
        enable_complexity_render=False,
    )
    with PdfTextExtractor(config, ocr_engine=backend) as extractor:
        result = extractor.extract(pdf)
    diagnostics = result.pages[0].diagnostics
    facts = diagnostics.facts
    assert facts["page_ocr_requested"] is False
    assert facts["region_ocr_requested"] is False
    assert facts["ocr_requested"] is True
    assert facts["table_cell_refinements"] == 1
    assert diagnostics.ocr_tokens_added == 1
    assert facts["ocr_outcome"] == ("recovered" if fail_first else "success")
    assert facts["ocr_degraded"] is fail_first
    assert facts["ocr_pipeline_work"]["summary"]["cell_tokens_added"] == 1


def test_engine_local_ranking_changes_only_that_engines_selected_candidate():
    from structured_pdf_text.ocr.pipelines.easyocr.policy import RECOVERY_POLICY as easy
    from structured_pdf_text.ocr.pipelines.paddle.policy import RECOVERY_POLICY as paddle
    from structured_pdf_text.ocr.pipelines.rapidocr.policy import RECOVERY_POLICY as rapid
    from structured_pdf_text.ocr.pipelines.tesseract.policy import RECOVERY_POLICY as tesseract
    from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementRequest

    class Variants:
        def __init__(self, profile):
            self.recovery_policy = profile

        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            if image.width < 300:
                return [OcrToken("vertical", BBox(10, 10, 20, 50), .95, "pt", SourceKind.OCR_REGION)]
            return [OcrToken("horizontal", BBox(20, 20, 120, 40), .80, "pt", SourceKind.OCR_REGION)]

    image = Image.new("RGB", (200, 100), "white")
    request = RegionRefinementRequest(
        BBox(0, 0, 200, 100), scale_factors=(1., 2.),
    )
    try:
        def selected(profile):
            return OcrRegionRefiner(Variants(profile)).refine(image, 0, request.bbox, request).tokens[0].text

        assert all(selected(profile) == "vertical" for profile in (easy, paddle, rapid, tesseract))
        tuned_easy = replace(easy, candidates=replace(easy.candidates, horizontal_weight=1.0))
        assert selected(tuned_easy) == "horizontal"
        assert all(selected(profile) == "vertical" for profile in (easy, paddle, rapid, tesseract))
    finally:
        image.close()


@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("bound_target", [False, True])
def test_canonical_tiled_recovery_authorizes_visual_tiles_in_page_coordinates(monkeypatch, page_rotation, bound_target):
    from structured_pdf_text.config import OcrQualityPolicy
    from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
    from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
    from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementRequest

    import structured_pdf_text.ocr.recovery as recovery
    monkeypatch.setattr(recovery, "_ocr_rgb_budget_from_settings", lambda _settings=None: .05)
    page = BBox(0, 0, 200, 100)
    registry, request, _ = make_work(
        regions=(page,), page_bbox=page, rotation=page_rotation,
        selection=EngineSelection("easyocr"),
    )
    source = SpySource()

    class PixelCoordinatesEngine:
        identity = OCRBackendIdentity(
            engine="easyocr", runtime="torch-cpu", profile="test", language="pt",
            device="cpu",
        )

        def recognize_page(self, image, page_index, page_bbox=None, *, quality_policy=None):
            assert page_bbox is None
            return [OcrToken(
                "tile", BBox(image.width * .2, image.height * .2,
                             image.width * .4, image.height * .4), .9,
                "pt-BR", SourceKind.OCR_REGION,
            )]

    pipeline = AuthorizedBackendPipeline(
        PixelCoordinatesEngine(), selection=request.selection, render_scale=1.0,
    )
    coordinator = PageCoordinator(
        pipeline, source, extraction_id=request.scope.extraction_id,
        document_id=request.scope.document_id, selection=request.selection,
        page_index=0, page_bbox=page, rotation=page_rotation,
        quality_policy=OcrQualityPolicy.BASELINE, security_limits=SecurityLimits(),
    )
    facade = OCRPageFacade(coordinator)
    image_size = (100, 200) if page_rotation in (90, 270) else (200, 100)
    image = Image.new("RGB", image_size, "white")
    region = LayoutRegion(
        "tiled-region", RegionKind.TEXT, page, .9, [], [],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    binding = facade.make_region_target(region) if bound_target else None
    try:
        result = OcrRegionRefiner(facade).refine(
            image, 0, page, RegionRefinementRequest(
                bbox=page, scale_factors=(1.0,), rotations=(90.0,),
                quality_policy="baseline", page_rotation=page_rotation,
                integration_targets=(binding,) if binding else (),
            ),
        )
        assert result.tokens, result.attempts
        assert len(source.calls) == 4, (result.attempts, coordinator.results, source.calls)
        assert all(call[2] == 1.0 for call in source.calls)
        assert all(page.x0 <= token.bbox.x0 < token.bbox.x1 <= page.x1 for token in result.tokens)
        assert all(page.y0 <= token.bbox.y0 < token.bbox.y1 <= page.y1 for token in result.tokens)
        if bound_target:
            assert region.ocr_tokens == []
            accepted = facade.integrate_selected_tokens(result.tokens, binding)
            assert len(accepted) == len(result.tokens) == len(region.ocr_tokens)
            assert not coordinator._deferred_tokens
    finally:
        image.close()
        facade.close()


def test_fractional_edges_are_synthetic_when_a_pixel_footprint_crosses_scope():
    box = BBox(10.1,20,11.9,22)
    registry,request,auth = make_work(page_bbox=BBox(0,0,200.5,100),regions=(box,))
    source = SpySource()
    with AuthorizedOCRServices(source,registry,request.selection) as services:
        with services.render(request,auth.authorization_id,box,scale=1.25) as lease:
            assert lease.image.width == 2
            assert lease.image.getpixel((0,0)) == (255,0,0)
            assert lease.image.getpixel((1,0)) == (255,255,255)


def test_real_one_pixel_crop_is_nonempty_and_keeps_geometry(tmp_path):
    box = BBox(10,20,11,21)
    registry,request,auth = make_work(regions=(box,))
    with PdfiumNativeEvidenceSource(pdf_with_markers(tmp_path/"one.pdf")) as native:
        with AuthorizedOCRServices(native,registry,request.selection) as services:
            with services.render(request,auth.authorization_id,box,scale=1) as lease:
                assert lease.image.size == (1,1)
                assert lease.image.getpixel((0,0)) == (255,0,0)
                assert lease.frame.bbox == box


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("shift", [0, 17])
@pytest.mark.parametrize("outside", [False, True])
@pytest.mark.parametrize("engine,provider", [("rapidocr", "onnxruntime"), ("easyocr", None)])
def test_engine_boundary_policy_keeps_edge_detections_in_exact_scope(rotation, shift, outside, engine, provider):
    from structured_pdf_text.ocr.pipelines.rapidocr.pipeline import RapidOCRPipeline
    from structured_pdf_text.ocr.pipelines.easyocr.pipeline import EasyOCRPipeline

    page = BBox(shift, shift, 595.2755737304688+shift, 841.8897705078125+shift)
    box = BBox(148.8188934326172+shift, 280.6300048828125+shift,
               319.9439656985153+shift, 451.7550683757603+shift)
    registry, request, auth = make_work(page_bbox=page, rotation=rotation, regions=(box,),
                                       selection=EngineSelection(engine, provider), render_scale=6.,
                                       resource_allocation=ResourceAllocation(max_live_raster_bytes=64*1024*1024))
    class EdgeDetector:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            assert page_bbox is None
            assert image.getpixel((0, 0)) == (255, 0, 0)
            top = -image.height if outside else 0
            corners = (Point(0, top), Point(image.width, top),
                       Point(image.width, image.height), Point(0, image.height))
            return [OcrToken("edge", BBox(0, top, image.width, image.height), .9,
                             "pt-BR", SourceKind.OCR_REGION, polygon=corners)]
        def close(self):
            pass
    pipeline_type = RapidOCRPipeline if engine == "rapidocr" else EasyOCRPipeline
    pipeline = pipeline_type(SimpleNamespace(engine=engine, provider=provider,
                                settings=SimpleNamespace(render_scale=6.)), EdgeDetector())
    try:
        with AuthorizedOCRServices(SpySource(), registry, request.selection,
                                   allocation=request.resource_allocation) as services:
            result = pipeline.execute(request, services)
            if outside:
                assert result.status is WorkStatus.INVALID_REGION and result.failures
                assert not result.tokens
            else:
                assert result.status is WorkStatus.OK and not result.failures, result.failures
                assert len(result.tokens) == 1
                assert union_contains(auth.regions, result.tokens[0].bbox)
            assert services.observations()["live_raster_bytes"] == 0
    finally:
        pipeline.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_real_source_trim_removes_only_synthetic_pixels_and_keeps_transforms(tmp_path, rotation, crop):
    path = pdf_with_markers(tmp_path/"trim-markers.pdf", rotation=rotation, crop=crop)
    with PdfiumNativeEvidenceSource(path) as source:
        page = source.extract_page(0)
        box = BBox(2.1, 15.3, 27.9, 74.8) if crop else BBox(12.1, 20.3, 37.9, 79.8)
        registry, request, auth = make_work(page_bbox=page.bbox, rotation=rotation, regions=(box,))
        with AuthorizedOCRServices(source, registry, request.selection) as services:
            with services.render(request, auth.authorization_id, box, scale=1.375) as masked:
                original = masked.image.copy()
                original_frame = masked.frame
            try:
                with services.render(request, auth.authorization_id, box, scale=1.375, boundary_policy="trim") as trimmed:
                    assert union_contains(auth.regions, trimmed.frame.bbox)
                    offset = original_frame.page_point_to_pixel(Point(trimmed.frame.bbox.x0, trimmed.frame.bbox.y0))
                    left, top = round(offset.x), round(offset.y)
                    expected = original.crop((left, top, left+trimmed.image.width, top+trimmed.image.height))
                    try:
                        assert trimmed.image.tobytes() == expected.tobytes()
                    finally:
                        expected.close()
                    for point in (Point(0, 0), Point(trimmed.image.width, trimmed.image.height)):
                        mapped = trimmed.frame.point_to_page(point)
                        assert box.x0 <= mapped.x <= box.x1 and box.y0 <= mapped.y <= box.y1
                        recovered = trimmed.frame.page_point_to_pixel(mapped)
                        assert (recovered.x, recovered.y) == pytest.approx((point.x, point.y))
            finally:
                original.close()
            assert services.observations()["live_raster_bytes"] == 0


def test_trim_with_no_complete_pixel_and_unknown_boundary_policy_are_rejected():
    box = BBox(10.1, 20, 11.1, 22)
    registry, request, auth = make_work(page_bbox=BBox(0, 0, 200.5, 100), regions=(box,))
    source = SpySource()
    with AuthorizedOCRServices(source, registry, request.selection) as services:
        with pytest.raises(ValueError, match="Raster boundary policy"):
            services.render(request, auth.authorization_id, box, boundary_policy="unknown")
        assert not source.calls
        with pytest.raises(ValueError, match="fully authorized pixel"):
            services.render(request, auth.authorization_id, box, scale=1.25, boundary_policy="trim")
        assert len(source.calls) == 1
        assert services.observations()["live_raster_bytes"] == 0


def test_real_fractional_page_does_not_deliver_color_from_outside_boundary(tmp_path):
    from reportlab.pdfgen import canvas
    path = tmp_path/"fractional-boundary.pdf"
    pdf = canvas.Canvas(str(path),pagesize=(200.5,100))
    pdf.setFillColorRGB(1,0,0);pdf.rect(10,10,1.9,80,fill=1,stroke=0)
    pdf.setFillColorRGB(0,0,1);pdf.rect(11.9,10,100,80,fill=1,stroke=0)
    pdf.save()
    box = BBox(10.1,20,11.9,22)
    registry,request,auth = make_work(page_bbox=BBox(0,0,200.5,100),regions=(box,))
    with PdfiumNativeEvidenceSource(path) as native:
        with AuthorizedOCRServices(native,registry,request.selection) as services:
            with services.render(request,auth.authorization_id,box,scale=1.25) as lease:
                assert lease.image.size == (2,2)
                assert lease.image.getpixel((0,0)) == (255,0,0)
                assert lease.image.getpixel((1,0)) == (255,255,255)


@pytest.mark.parametrize("box,scale", [(BBox(-1,0,10,10),1), (BBox(0,0,0,1),1), (BBox(0,0,math.inf,1),1), (BBox(0,0,10,10),math.nan), (BBox(0,0,10,10),1e308), (BBox(0,0,.001,.001),1)])
def test_invalid_or_overflow_geometry_never_renders(box,scale):
    registry,request,auth = make_work()
    source = SpySource()
    with AuthorizedOCRServices(source,registry,request.selection) as services:
        with pytest.raises((ValueError,BudgetBlocked)):
            services.render(request,auth.authorization_id,box,scale=scale)
    assert source.calls == []


def test_forged_ids_stale_target_and_cross_engine_are_denied_before_pixels():
    registry,request,auth = make_work()
    source = SpySource()
    with AuthorizedOCRServices(source,registry,request.selection) as services:
        with pytest.raises(ValueError): services.render(request,"invented",auth.regions[0],scale=1)
        changed = replace(request,selection=EngineSelection("other"))
        with pytest.raises(ValueError): services.render(changed,auth.authorization_id,auth.regions[0],scale=1)
        registry.advance_scope(replace(request.scope,target=replace(request.scope.target,revision=2),evidence_version="evidence-v2"))
        with pytest.raises(ValueError): services.render(request,auth.authorization_id,auth.regions[0],scale=1)
    assert source.calls == []


def test_memory_releases_do_not_return_work_or_attempts_and_parent_cannot_be_bypassed():
    limits = SecurityLimits(max_render_pixels=1000,max_render_bytes=12000)
    root = ResourcePool(ResourceAllocation(max_live_raster_bytes=12000,max_work_pixels=200,max_attempts=1),limits)
    child = root.child(ResourceAllocation(max_live_raster_bytes=999999,max_attempts=999))
    first = child.reserve_raster(10,10,copies=2)
    assert root.observations()["live_raster_bytes"] == 600
    first.retain_rgb(10,10)
    assert root.observations()["live_raster_bytes"] == 300
    first.release(); first.release()
    assert root.observations()["work_pixels"] == 100
    second = child.reserve_raster(10,10)
    second.release()
    with pytest.raises(BudgetBlocked): child.reserve_raster(1,1)
    child.consume_attempt()
    with pytest.raises(BudgetBlocked): root.child(ResourceAllocation()).consume_attempt()


def test_concurrent_reservations_charge_one_shared_parent_atomically():
    root = ResourcePool(ResourceAllocation(max_live_raster_bytes=300),SecurityLimits())
    def reserve(_):
        try: return root.child(ResourceAllocation()).reserve_raster(10,10)
        except BudgetBlocked: return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(reserve,range(8)))
    assert sum(r is not None for r in reservations) == 1
    for r in reservations:
        if r is not None:r.release()
    assert root.observations()["live_raster_bytes"] == 0


def test_security_limits_and_stricter_source_limits_prevent_allocation():
    registry,request,auth = make_work()
    for source,limits in ((SpySource(),SecurityLimits(max_render_pixels=50)),(SpySource(),SecurityLimits())):
        if limits.max_render_pixels != 50:
            source.config = ExtractorConfig(security_limits=SecurityLimits(max_render_pixels=50))
        with AuthorizedOCRServices(source,registry,request.selection,limits=limits) as services:
            with pytest.raises(BudgetBlocked): services.render(request,auth.authorization_id,BBox(0,0,20,20),scale=1)
        assert source.calls == []


def test_resize_rotation_and_tiles_are_budgeted_and_keep_reversible_transforms():
    registry,request,auth = make_work(regions=(BBox(10,20,50,60),))
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        with services.render(request,auth.authorization_id,auth.regions[0],scale=1) as original:
            with services.tile(original,BBox(2,3,22,23)) as tile:
                with services.resize(tile,30,40) as resized:
                    with services.rotate(resized,37,padding=5) as rotated:
                        p = Point(rotated.image.width/2,rotated.image.height/2)
                        point = rotated.frame.point_to_page(p)
                        observed = rotated.frame.page_point_to_pixel(point)
                        assert (observed.x,observed.y) == pytest.approx((p.x,p.y))
                        assert rotated.image.getpixel((0,0)) == (255,255,255)
                        assert services.observations()["live_raster_bytes"] > original.image.width*original.image.height*3
            with pytest.raises(ValueError):services.tile(original,BBox(-1,0,2,2))
        assert services.observations()["live_raster_bytes"] == 0
        assert services.observations()["rasters"] == 4


@pytest.mark.parametrize("degrees", [37,90,180,270,-23])
def test_candidate_rotation_transform_matches_real_marker_pixels(degrees):
    registry,request,auth = make_work(regions=(BBox(10,20,50,60),))
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        with services.render(request,auth.authorization_id,auth.regions[0],scale=1) as original:
            original.image.paste("white",(0,0,40,40))
            original.image.paste("black",(12,8,18,14))
            with services.rotate(original,degrees,padding=5) as rotated:
                pixels = [(x+.5,y+.5) for y in range(rotated.image.height) for x in range(rotated.image.width) if max(rotated.image.getpixel((x,y))) < 64]
                assert pixels
                center = Point(sum(x for x,_ in pixels)/len(pixels),sum(y for _,y in pixels)/len(pixels))
                page = rotated.frame.point_to_page(center)
                assert (page.x,page.y) == pytest.approx((25,31),abs=.3)


def test_cancellation_after_render_releases_unpublished_image_and_reservation():
    registry,request,auth = make_work(regions=(BBox(10,20,30,40),))
    source = SpySource()
    with AuthorizedOCRServices(source,registry,request.selection) as services:
        original = source.render_region
        observed = []
        def render_then_cancel(*args,**kwargs):
            image = original(*args,**kwargs)
            observed.append(image)
            services.cancel()
            return image
        source.render_region = render_then_cancel
        with pytest.raises(WorkCancelled):services.render(request,auth.authorization_id,auth.regions[0],scale=1)
        assert services.observations()["live_raster_bytes"] == 0
        with pytest.raises(ValueError):observed[0].getpixel((0,0))


def test_expanded_canvas_rejected_before_rotation_allocation(monkeypatch):
    registry,request,auth = make_work(regions=(BBox(0,0,100,100),))
    with AuthorizedOCRServices(SpySource(),registry,request.selection,limits=SecurityLimits(max_render_pixels=11000)) as services:
        with services.render(request,auth.authorization_id,auth.regions[0],scale=1) as lease:
            def forbidden(*args,**kwargs):pytest.fail("rotation allocated before preflight")
            monkeypatch.setattr(lease.image,"rotate",forbidden)
            with pytest.raises(BudgetBlocked):services.rotate(lease,45)


@pytest.mark.parametrize("error", [RuntimeError("render failed"), MemoryError("real allocation failed")])
def test_render_failure_cleanup_and_real_oom_classification(error):
    registry,request,auth = make_work(regions=(BBox(10,20,30,40),))
    source = SpySource(fail=error)
    services = AuthorizedOCRServices(source,registry,request.selection,close_source=True)
    expected = ResourceExhaustedExtractionError if isinstance(error,MemoryError) else RuntimeError
    with pytest.raises(expected): services.render(request,auth.authorization_id,auth.regions[0],scale=1)
    assert services.observations()["live_raster_bytes"] == 0
    assert services.observations()["work_pixels"] == 400
    services.close();services.close()
    assert source.closed == 1


def test_close_releases_outstanding_leases_and_is_terminal():
    registry,request,auth = make_work(regions=(BBox(10,20,30,40),))
    services = AuthorizedOCRServices(SpySource(),registry,request.selection)
    lease = services.render(request,auth.authorization_id,auth.regions[0],scale=1)
    services.close();lease.close();services.close()
    assert services.observations()["live_raster_bytes"] == 0
    with pytest.raises(RuntimeError):lease.image
    with pytest.raises(RuntimeError):services.render(request,auth.authorization_id,auth.regions[0],scale=1)


def test_embedded_image_uses_visible_composition_and_never_calls_original_extraction(tmp_path):
    from reportlab.pdfgen import canvas
    path = tmp_path/"covered.pdf"
    pdf = canvas.Canvas(str(path),pagesize=(200,100))
    from reportlab.lib.utils import ImageReader
    pdf.drawImage(ImageReader(Image.new("RGB",(40,40),"red")),10,20,40,40)
    pdf.setFillColorRGB(1,1,1);pdf.rect(10,20,40,40,fill=1,stroke=0);pdf.save()
    box = BBox(10,40,50,80)
    registry,request,auth = make_work(regions=(box,))
    with PdfiumNativeEvidenceSource(path) as native:
        source = SpySource(native)
        with AuthorizedOCRServices(source,registry,request.selection) as services:
            with services.embedded_image(request,auth.authorization_id,box,object_index=0,scale=1) as lease:
                assert set(lease.image.tobytes()) == {255}


def test_embedded_image_rejects_unknown_or_unrelated_placement_before_render(tmp_path):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    path = tmp_path/"placement.pdf"
    pdf = canvas.Canvas(str(path),pagesize=(200,100))
    pdf.drawImage(ImageReader(Image.new("RGB",(20,20),"red")),10,20,20,20)
    pdf.save()
    box = BBox(10,10,30,30)
    registry,request,auth = make_work(regions=(box,))
    with PdfiumNativeEvidenceSource(path) as native:
        source = SpySource(native)
        with AuthorizedOCRServices(source,registry,request.selection) as services:
            with pytest.raises(ValueError, match="does not exist"):
                services.embedded_image(request,auth.authorization_id,box,object_index=99,scale=1)
            with pytest.raises(ValueError, match="does not intersect"):
                services.embedded_image(request,auth.authorization_id,box,object_index=0,scale=1)
            assert source.calls == []


def test_legacy_old_signature_partial_result_and_typeerror_is_not_retried():
    registry,request,_ = make_work(regions=(BBox(10,10,40,40),BBox(60,10,90,40)))
    engine = LegacyEngine(fail_on=2)
    pipeline = LegacyInjectedPipeline(engine)
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        result = pipeline.execute(request,services)
        assert result.status == WorkStatus.PARTIAL
        assert len(result.tokens) == 1
        assert result.failures[0].status == WorkStatus.RUNTIME_ERROR
        assert "TypeError" in result.failures[0].reason
        assert result.identity.selection.engine == "custom"
        assert result.identity.profile == "unknown"
        assert engine.calls == 2
        assert services.observations()["live_raster_bytes"] == 0
    pipeline.close();pipeline.close()
    assert engine.closed == 1
    with pytest.raises(RuntimeError):pipeline.execute(request,None)


def test_legacy_variadic_bbox_is_selected_once_and_close_is_optional():
    registry,request,_ = make_work(regions=(BBox(10,10,40,40),))
    class VariadicEngine:
        calls = 0
        def recognize_page(self, image, page_index, *args, **kwargs):
            self.calls += 1
            assert args and args[0] == BBox(10,10,40,40)
            assert "page_bbox" not in kwargs
            return []
    engine = VariadicEngine()
    pipeline = LegacyInjectedPipeline(engine)
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        result = pipeline.execute(request,services)
        assert result.status == WorkStatus.NO_TEXT
        assert engine.calls == 1
    pipeline.close()


def test_legacy_without_bbox_receives_only_local_crop_and_tokens_are_remapped():
    registry,request,auth = make_work(regions=(BBox(70,20,90,40),))
    class RasterOnly(LegacyEngine):
        def recognize_page(self,image,page_index):
            self.calls += 1
            assert image.size == (20,20)
            return [OcrToken("local",BBox(2,3,10,9),.8,None,SourceKind.OCR_REGION)]
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        result = LegacyInjectedPipeline(RasterOnly()).execute(request,services)
    assert result.tokens[0].bbox == BBox(72,23,80,29)


def test_legacy_modern_signature_identity_and_settings_do_not_leak():
    registry,request,_ = make_work(selection=EngineSelection("fifth","cpu-provider"),quality_policy="exhaustive")
    extra = {"decoder":["first"]}
    class Modern(LegacyEngine):
        @property
        def identity(self):
            assert self.calls > 0
            return OCRBackendIdentity("fifth","cpu-provider","profile","pt-BR","cpu",extra=extra)
        def recognize_page(self,image,page_index,page_bbox=None,*,quality_policy,page_rotation,quality_variants):
            assert (quality_policy,page_rotation,quality_variants) == ("exhaustive",0,True)
            return super().recognize_page(image,page_index,page_bbox)
    pipeline = LegacyInjectedPipeline(Modern(),selection=request.selection)
    assert pipeline.descriptor.available_capabilities is None
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        result = pipeline.execute(request,services)
    extra["decoder"].append("second")
    assert result.identity.extra["decoder"] == ("first",)


def test_fatal_error_and_cancellation_preserve_classification_and_prevent_inference():
    registry,request,_ = make_work()
    class Fatal(LegacyEngine):
        def recognize_page(self,*args,**kwargs):raise FatalExtractionError("fatal")
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        with pytest.raises(FatalExtractionError):LegacyInjectedPipeline(Fatal()).execute(request,services)
        assert services.observations()["live_raster_bytes"] == 0
    engine = LegacyEngine()
    with AuthorizedOCRServices(SpySource(),registry,request.selection) as services:
        services.cancel()
        with pytest.raises(WorkCancelled):LegacyInjectedPipeline(engine).execute(request,services)
        assert engine.calls == 0


def test_dual_injection_rejected_before_any_construction():
    engine = LegacyEngine()
    with pytest.raises(ValueError):adapt_injection(pipeline=object(),ocr_engine=engine)
    assert engine.calls == engine.closed == 0
